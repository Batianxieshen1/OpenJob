"""审查修复批次测试：/api/trends 趋势聚合、导出白名单含 Logo、doctor 画像覆盖、CSV 新列。"""

import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from openjob.data_export import build_export_archive
from openjob.db import get_db, get_weekly_trends, insert_job_if_new
from openjob.diagnostics import FAIL, PASS, WARN, check_company_profile_coverage
from openjob.job_export import build_csv


def _job_record(job_id: str, company: str, **overrides) -> dict:
    record = {
        "id": job_id,
        "title": "数据分析师",
        "company": company,
        "salary": "10-15K",
        "city": "广州",
        "jd": "负责业务数据看板",
        "url": f"https://www.zhipin.com/job_detail/{job_id}.html",
        "source_platform": "boss",
        "source_job_id": job_id,
        "source_channel": "search",
    }
    record.update(overrides)
    return record


class WeeklyTrendsTests(unittest.TestCase):
    def test_counts_today_events_in_local_day_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job_if_new(conn, _job_record("t-1", "美的"))
                insert_job_if_new(conn, _job_record("t-2", "格力"))
                conn.execute("UPDATE jobs SET scored_at = CURRENT_TIMESTAMP WHERE id = 't-1'")
                conn.execute("INSERT INTO history (job_id, action, detail) VALUES ('t-1', 'sent', NULL)")
                conn.commit()
                rows = get_weekly_trends(conn, 7)
                self.assertEqual(len(rows), 7)
                today = rows[-1]
                self.assertEqual(today["scraped"], 2)
                self.assertEqual(today["scored"], 1)
                self.assertEqual(today["sent"], 1)
                # 其余天全零
                for row in rows[:-1]:
                    self.assertEqual((row["scraped"], row["scored"], row["sent"]), (0, 0, 0))
            finally:
                conn.close()

    def test_backdated_job_lands_in_its_own_day_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job_if_new(conn, _job_record("old-1", "美的"))
                conn.execute("UPDATE jobs SET created_at = datetime('now', '-2 days') WHERE id = 'old-1'")
                conn.commit()
                rows = get_weekly_trends(conn, 7)
                self.assertEqual(rows[-3]["scraped"], 1)
                self.assertEqual(rows[-1]["scraped"], 0)
            finally:
                conn.close()

    def test_deleted_jobs_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job_if_new(conn, _job_record("del-1", "美的"))
                from openjob.db import soft_delete_jobs

                soft_delete_jobs(conn, ["del-1"], confirmed=True, reason="test")
                rows = get_weekly_trends(conn, 7)
                self.assertEqual(rows[-1]["scraped"], 0)
            finally:
                conn.close()


class TrendsRouteTests(unittest.TestCase):
    def test_api_trends_returns_seven_local_days(self):
        from openjob.web import server

        original = server.BASE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                import yaml

                (base / "config.yaml").write_text("profile: {}\n", encoding="utf-8")
                db = get_db(base / "data" / "openjob.db")
                insert_job_if_new(db, _job_record("r-1", "美的"))
                db.close()
                server.set_base_dir(base)
                captured = {}

                def start_response(status, headers, exc_info=None):
                    captured["status"] = status

                environ = {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/api/trends",
                    "QUERY_STRING": "",
                    "SERVER_NAME": "localhost",
                    "SERVER_PORT": "8686",
                    "SERVER_PROTOCOL": "HTTP/1.1",
                    "CONTENT_LENGTH": "0",
                    "CONTENT_TYPE": "",
                    "wsgi.version": (1, 0),
                    "wsgi.url_scheme": "http",
                    "wsgi.input": io.BytesIO(b""),
                    "wsgi.errors": io.StringIO(),
                    "wsgi.multithread": False,
                    "wsgi.multiprocess": False,
                    "wsgi.run_once": False,
                }
                chunks = server.app(environ, start_response)
                body = json.loads(b"".join(chunks).decode("utf-8"))
                self.assertTrue(captured["status"].startswith("200"))
                self.assertEqual(len(body), 7)
                self.assertEqual(body[-1]["scraped"], 1)
        finally:
            server.set_base_dir(original)


class ExportAssetsWhitelistTests(unittest.TestCase):
    def test_export_includes_company_logos(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.yaml").write_text("profile: {}\n", encoding="utf-8")
            db = get_db(base / "data" / "openjob.db")
            record = _job_record("logo-1", "美的", company_logo_path="assets/logos/abcdef1234567890.png")
            insert_job_if_new(db, record)
            db.close()
            logo_dir = base / "data" / "assets" / "logos"
            logo_dir.mkdir(parents=True)
            (logo_dir / "abcdef1234567890.png").write_bytes(b"\x89PNG fake")
            payload = build_export_archive(base)
            names = zipfile.ZipFile(io.BytesIO(payload)).namelist()
            self.assertIn("data/assets/logos/abcdef1234567890.png", names)
            manifest = zipfile.ZipFile(io.BytesIO(payload)).read("EXPORT-MANIFEST.txt").decode("utf-8")
            self.assertIn("公司Logo", manifest)


class DoctorCoverageTests(unittest.TestCase):
    def test_pass_when_no_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = check_company_profile_coverage(Path(tmp))
            self.assertEqual(result["status"], PASS)

    def test_warn_when_recent_jobs_have_no_intro(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = get_db(base / "data" / "openjob.db")
            for i in range(6):
                insert_job_if_new(db, _job_record(f"c-{i}", f"公司{i}"))
            db.close()
            result = check_company_profile_coverage(base)
            self.assertEqual(result["status"], WARN)
            self.assertIn("失效", result["summary"])

    def test_pass_when_recent_jobs_have_intro_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = get_db(base / "data" / "openjob.db")
            for i in range(6):
                insert_job_if_new(db, _job_record(f"p-{i}", f"公司{i}", company_intro="简介"))
            db.close()
            result = check_company_profile_coverage(base)
            self.assertEqual(result["status"], PASS)
            self.assertIn("简介覆盖 100%", result["summary"])


class JobExportColumnTests(unittest.TestCase):
    def test_csv_contains_company_intro_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                record = _job_record("csv-1", "美的", company_intro="简介文本", company_intro_url="https://www.zhipin.com/gongsi/x.html")
                insert_job_if_new(db, record)
                rows = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE id='csv-1'").fetchall()]
            finally:
                db.close()
            content = build_csv(rows)
            header = next(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
            self.assertIn("公司简介", header)
            self.assertIn("公司主页", header)
            intro_col = header.index("公司简介")
            data_row = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))[1]
            self.assertEqual(data_row[intro_col], "简介文本")


if __name__ == "__main__":
    unittest.main()
