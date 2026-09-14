"""C3 doctor + 脱敏诊断包 / C4 备份恢复与迁移纪律测试。"""

import json
import sqlite3
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from openjob import diagnostics
from openjob.db import (
    SCHEMA_VERSION,
    backup_database,
    get_db,
    insert_job,
    integrity_check,
    restore_from_backup,
)


def _job(job_id: str) -> dict:
    return {"id": job_id, "title": "数据专员", "company": "诊断公司", "jd": "x", "url": "https://example.com/j"}


class DoctorCheckTests(unittest.TestCase):
    def test_all_pass_in_healthy_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.yaml").write_text(
                "search:\n  keywords: [测试]\nprofile:\n  resume_path: resume.md\nai:\n  provider: anthropic\n  api_key: sk-test123456\n",
                encoding="utf-8",
            )
            db = get_db(base / "data" / "openjob.db")
            db.close()
            healthy_chrome = diagnostics._result("Chrome 调试连接", diagnostics.PASS, "9222 在线")
            with patch("openjob.web.preflight.check_ai_connection", return_value=[{"status": "pass", "message": "ok"}]), \
                 patch.object(diagnostics, "check_chrome", return_value=healthy_chrome):
                results = diagnostics.run_doctor(base, {})
            failed = [r["item"] for r in results if r["status"] == diagnostics.FAIL]
            self.assertEqual(failed, [], results)

    def test_render_counts_failures(self):
        results = [
            diagnostics._result("A", diagnostics.PASS, "ok"),
            diagnostics._result("B", diagnostics.FAIL, "bad", "修一下"),
            diagnostics._result("C", diagnostics.WARN, "meh"),
        ]
        lines: list[str] = []
        self.assertEqual(diagnostics.render_doctor(results, lines.append), 1)
        self.assertTrue(any("❌" in line for line in lines))
        self.assertTrue(any("修复：修一下" in line for line in lines))

    def test_chrome_offline_reported_with_fix(self):
        import urllib.error

        def refuse(*args, **kwargs):
            raise urllib.error.URLError("refused")

        with patch("urllib.request.urlopen", side_effect=refuse):
            result = diagnostics.check_chrome()
        self.assertEqual(result["status"], diagnostics.FAIL)
        self.assertIn("9222", result["fix"])


class DiagnosticPackageTests(unittest.TestCase):
    def _sandbox_with_sensitive_data(self, tmp: Path):
        base = Path(tmp)
        (base / "config.yaml").write_text(
            "search:\n  keywords: [测试]\nprofile:\n  resume_path: data/resumes/我的真实简历.docx\n"
            "ai:\n  provider: anthropic\n  api_key: sk-abc123def456ghi\n",
            encoding="utf-8",
        )
        db = get_db(base / "data" / "openjob.db")
        insert_job(db, _job("s1"))
        db.execute(
            "INSERT INTO history (job_id, action, detail) VALUES ('s1', 'error', ?)",
            ("HR 电话 13812345678 与 mailbox@example.com，密钥 sk-secret999xyz",),
        )
        db.commit()
        db.close()
        (base / "data" / "resumes").mkdir(parents=True, exist_ok=True)
        (base / "data" / "resumes" / "我的真实简历.docx").write_bytes(b"real resume bytes")
        logs_dir = base / "data" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "task-1.log").write_text("日志里有手机 13912345678\n", encoding="utf-8")
        return base

    def test_default_package_contains_no_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._sandbox_with_sensitive_data(Path(tmp))
            payload = diagnostics.build_diagnostic_package(base, {})
            archive = zipfile.ZipFile(BytesIO(payload))
            names = archive.namelist()
            text = "".join(archive.read(name).decode("utf-8", errors="replace") for name in names)
            self.assertNotIn("sk-abc123def456ghi", text)
            self.assertNotIn("sk-secret999xyz", text)
            self.assertNotIn("13812345678", text)
            self.assertNotIn("mailbox@example.com", text)
            self.assertNotIn("13912345678", text)
            self.assertNotIn("真实简历.docx", text)
            self.assertTrue(any(name == "doctor.json" for name in names))
            manifest = json.loads(archive.read("doctor.json"))
            self.assertIn("row_counts", manifest)
            self.assertIn("doctor", manifest)

    def test_logs_opt_in_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._sandbox_with_sensitive_data(Path(tmp))
            payload = diagnostics.build_diagnostic_package(base, {}, include_logs=True)
            archive = zipfile.ZipFile(BytesIO(payload))
            self.assertTrue(any(name.startswith("logs/") for name in archive.namelist()))
            log_text = "".join(
                archive.read(name).decode("utf-8", errors="replace")
                for name in archive.namelist() if name.startswith("logs/")
            )
            self.assertNotIn("13912345678", log_text)
            self.assertIn("[电话已脱敏]", log_text)


class BackupRestoreTests(unittest.TestCase):
    def test_integrity_check_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "openjob.db"
            db = get_db(db_path)
            insert_job(db, _job("r1"))
            db.close()
            ok, detail = integrity_check(db_path)
            self.assertTrue(ok, detail)
            bad = Path(tmp) / "bad.db"
            bad.write_bytes(b"not a sqlite file")
            ok, _ = integrity_check(bad)
            self.assertFalse(ok)

    def test_restore_rejects_corrupt_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "openjob.db"
            db = get_db(db_path)
            insert_job(db, _job("r1"))
            db.close()
            bad = Path(tmp) / "bad.db"
            bad.write_bytes(b"garbage")
            self.assertFalse(restore_from_backup(bad, db_path))
            db = get_db(db_path)
            count = db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"]
            db.close()
            self.assertEqual(count, 1)  # 现场未被破坏

    def test_restore_roundtrip_with_pre_restore_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "openjob.db"
            db = get_db(db_path)
            insert_job(db, _job("r1"))
            db.close()
            backup = backup_database(backup_dir=Path(tmp) / "backups", source=db_path)
            self.assertTrue(backup and backup.exists())
            db = get_db(db_path)
            insert_job(db, _job("r2"))
            db.close()
            self.assertTrue(restore_from_backup(backup, db_path))
            db = get_db(db_path)
            count = db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"]
            db.close()
            self.assertEqual(count, 1)
            # 恢复前现场已转正式备份命名（openjob-<stamp>-pre<pid>.db），可回查
            pre_backups = list((Path(tmp) / "data").glob("openjob-*-pre*.db"))
            self.assertEqual(len(pre_backups), 1)
            db2 = get_db(pre_backups[0])
            extra = db2.execute("SELECT COUNT(*) AS c FROM jobs WHERE id = 'r2'").fetchone()["c"]
            db2.close()
            self.assertEqual(extra, 1)  # 恢复前的现场可回查


class MigrationDisciplineTests(unittest.TestCase):
    def test_fresh_db_stamps_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            version = db.execute("PRAGMA user_version").fetchone()[0]
            db.close()
            self.assertEqual(version, SCHEMA_VERSION)

    def test_stale_legacy_db_backed_up_once_then_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            # 用真实初始化建库，再模拟"旧库"：版本号清 0
            db = get_db(db_path)
            db.close()
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA user_version=0")
            conn.commit()
            conn.close()
            db = get_db(db_path)
            db.close()
            backups = list((Path(tmp) / "backups").glob("openjob-*.db"))
            self.assertEqual(len(backups), 1)  # 迁移前自动备份
            probe = sqlite3.connect(db_path)
            version = probe.execute("PRAGMA user_version").fetchone()[0]
            probe.close()
            self.assertEqual(version, SCHEMA_VERSION)
            # 再次连接不再重复备份
            db = get_db(db_path)
            db.close()
            self.assertEqual(len(list((Path(tmp) / "backups").glob("openjob-*.db"))), 1)


if __name__ == "__main__":
    unittest.main()
