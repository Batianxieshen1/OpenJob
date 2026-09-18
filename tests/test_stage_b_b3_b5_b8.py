"""B3 求职周报 / B5 招呼语效果标记 / B8 数据导出测试。"""

import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from openjob.ai import greeter
from openjob.data_export import assert_export_has_no_secrets, build_export_archive
from openjob.db import (
    add_history,
    get_db,
    insert_job,
    transition_job_status,
    update_job_greeting,
    update_job_score,
)
from openjob.weekly_report import (
    collect_weekly_stats,
    render_weekly_markdown,
    write_weekly_report,
)


def _job(job_id: str, **overrides) -> dict:
    job = {"id": job_id, "title": "数据专员", "company": "周报公司", "jd": "x", "url": "https://example.com/j"}
    job.update(overrides)
    return job


def _seed_week_activity(db, *, days_ago_start: int, job_count: int, sent: int, replied: int):
    """在报告期窗口内造采集/确认/发送/回复数据。

    锚点 = 上周六（now - weekday - 2 天）：无论今天星期几，都落在"上周一~本周一"
    报告期窗口内，测试对运行日期不敏感。
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    anchor = now - timedelta(days=now.weekday() + 2)
    for index in range(job_count):
        job_id = f"w{days_ago_start}-{index}"
        stamp = (anchor + timedelta(hours=index % 24)).strftime("%Y-%m-%d %H:%M:%S")
        insert_job(db, _job(job_id, company=f"公司{index}"))
        db.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (stamp, job_id))
        update_job_score(db, job_id, 80, "种子")
        transition_job_status(db, job_id, "ready")
        if index < sent:
            transition_job_status(db, job_id, "approved")
            transition_job_status(db, job_id, "sent")
            # 真实流程由调用方写 history：这里显式补台账并把时间戳搬进窗口
            add_history(db, job_id, "approved", "种子确认")
            add_history(db, job_id, "sent", "种子发送")
            db.execute(
                "UPDATE history SET created_at = ? WHERE job_id = ?",
                (stamp, job_id),
            )
            if index < replied:
                db.execute(
                    "UPDATE jobs SET status = 'replied', replied_at = ?, reply_count = 1 WHERE id = ?",
                    (stamp, job_id),
                )
    db.commit()


class WeeklyReportTests(unittest.TestCase):
    def test_no_write_on_collect_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            _seed_week_activity(db, days_ago_start=1, job_count=3, sent=2, replied=1)
            db.close()
            # 报告窗口用真实时钟（种子相对 -1 天，必然落在"上周一~本周一"窗口内）
            report = collect_weekly_stats(db_path)
            markdown = render_weekly_markdown(report, {})
            after_md = sorted(p.name for p in Path(tmp).rglob("*.md"))
            self.assertEqual(after_md, [])  # dry-run 零写入（WAL 副作用不算写入）
            self.assertIn("OpenJob 求职周报", markdown)
            self.assertIn("新增岗位：**3", markdown)
            self.assertIn("发送招呼语：**2", markdown)
            self.assertIn("HR 回复：**1", markdown)
            self.assertIn("回复率 50%", markdown)

    def test_write_only_on_explicit_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db_path = base / "data" / "openjob.db"
            db = get_db(db_path)
            _seed_week_activity(db, days_ago_start=1, job_count=1, sent=0, replied=0)
            db.close()
            report = collect_weekly_stats(db_path, now=datetime(2026, 9, 14, 10, 0))
            path = write_weekly_report(render_weekly_markdown(report, {}), {}, base, now=datetime(2026, 9, 14, 10, 0))
            self.assertTrue(path.exists())
            self.assertIn("OpenJob 求职周报", path.read_text(encoding="utf-8"))


class GreetingStyleMarkerTests(unittest.TestCase):
    def test_style_features_extracted(self):
        features = greeter._greeting_style_features("看过岗位想聊聊，做过完整用户调研。方便发简历吗？")
        self.assertEqual(features["length"], len("看过岗位想聊聊，做过完整用户调研。方便发简历吗？"))
        self.assertTrue(features["ends_with_question"])
        self.assertEqual(features["sentence_count"], 2)
        self.assertTrue(features["opening"])

    def test_effectiveness_api_buckets(self):
        from openjob.web import server

        original_base_dir = server.BASE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                (base / "config.yaml").write_text("profile: {}\n", encoding="utf-8")
                db = get_db(base / "data" / "openjob.db")
                for job_id, status, style in (
                    ("g1", "sent", {"length": 55, "ends_with_question": True}),
                    ("g2", "replied", {"length": 55, "ends_with_question": True}),
                    ("g3", "sent", {"length": 90, "ends_with_question": False}),
                ):
                    insert_job(db, _job(job_id))
                    transition_job_status(db, job_id, "ready")
                    transition_job_status(db, job_id, "approved")
                    if status == "replied":
                        transition_job_status(db, job_id, "sent")
                        transition_job_status(db, job_id, "replied")
                    else:
                        transition_job_status(db, job_id, "sent")
                    update_job_greeting(
                        db, job_id, "测试招呼语",
                        fact_status="verified",
                        source_json=json.dumps({"style": style}),
                    )
                db.close()
                server.set_base_dir(base)
                from tests.test_observability import _wsgi  # 复用 WSGI 客户端

                status_code, body = _wsgi("/api/greeting-effectiveness")
                self.assertTrue(str(status_code).startswith("200"), body)
                data = body["data"]
                self.assertEqual(data["total_with_style"], 3)
                self.assertEqual(data["buckets"]["≤60字"]["replied"], 1)
                self.assertIn("不做因果结论", data["note"])
        finally:
            server.set_base_dir(original_base_dir)


class DataExportTests(unittest.TestCase):
    def test_export_contains_data_and_no_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.yaml").write_text(
                "ai:\n  api_key: sk-super-secret-value-123\nprofile:\n  name: 某某\n",
                encoding="utf-8",
            )
            db = get_db(base / "data" / "openjob.db")
            insert_job(db, _job("e1"))
            db.close()
            resumes = base / "data" / "resumes"
            resumes.mkdir(parents=True)
            (resumes / "简历_v1.pdf").write_bytes(b"%PDF-1.4 fake")
            payload = build_export_archive(base)

            archive = zipfile.ZipFile(io.BytesIO(payload))
            names = archive.namelist()
            self.assertIn("openjob.db", names)
            self.assertIn("data/resumes/简历_v1.pdf", names)
            self.assertIn("EXPORT-MANIFEST.txt", names)
            self.assertNotIn("config.yaml", names)
            assert_export_has_no_secrets(payload, (base / "config.yaml").read_text(encoding="utf-8"))

            # 快照可独立打开且数据完整
            snapshot = archive.read("openjob.db")
            probe_tmp = Path(tmp) / "probe.db"
            probe_tmp.write_bytes(snapshot)
            conn = sqlite3.connect(probe_tmp)
            count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)

    def test_export_raises_without_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                build_export_archive(Path(tmp))


if __name__ == "__main__":
    unittest.main()
