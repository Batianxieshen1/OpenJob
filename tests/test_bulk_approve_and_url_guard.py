"""批量放行（岗位池→确认队列）与发送边界 URL 守卫测试。"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.db import add_history, get_db, insert_job, transition_job_status, update_job_greeting, update_job_score
from openjob.executor.sender import _is_sendable_platform_url
from openjob.web import server


def _job(job_id: str, **overrides) -> dict:
    job = {
        "id": job_id,
        "title": "数据分析助理",
        "company": "放行公司",
        "salary": "10-15K",
        "city": "广州",
        "jd": "负责业务数据看板",
        "hr_name": "王HR",
        "url": "https://www.zhipin.com/job_detail/x.html",
    }
    job.update(overrides)
    return job


def _wsgi(path, method="GET", json_body=None):
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status

    if "?" in path:
        path_info, query_string = path.split("?", 1)
    else:
        path_info, query_string = path, ""
    body_bytes = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path_info,
        "QUERY_STRING": query_string,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8686",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "CONTENT_LENGTH": str(len(body_bytes)),
        "CONTENT_TYPE": "application/json" if body_bytes else "",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(body_bytes),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    chunks = server.app(environ, start_response)
    return captured["status"], json.loads(b"".join(chunks).decode("utf-8"))


class BulkApproveTests(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = server.BASE_DIR

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)

    def _setup(self, tmp: str):
        import yaml

        base = Path(tmp)
        (base / "config.yaml").write_text(yaml.dump({"profile": {}}, allow_unicode=True), encoding="utf-8")
        db = get_db(base / "data" / "openjob.db")
        for job_id in ("f1", "f2", "ready-1", "sent-1"):
            insert_job(db, _job(job_id))
            update_job_score(db, job_id, 55, "预筛不通过: 测试过滤")
        transition_job_status(db, "f1", "filtered")
        transition_job_status(db, "f2", "filtered")
        transition_job_status(db, "ready-1", "ready")
        transition_job_status(db, "sent-1", "ready")
        transition_job_status(db, "sent-1", "approved")
        transition_job_status(db, "sent-1", "sent")
        db.close()
        server.set_base_dir(base)

    def test_bulk_approve_only_filters_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            status, body = _wsgi(
                "/api/jobs/bulk-approve", method="POST",
                json_body={"job_ids": ["f1", "f2", "ready-1", "sent-1", "ghost"]},
            )
            payload = body["data"]
            self.assertTrue(str(status).startswith("200"), body)
            self.assertEqual(payload["approved_count"], 2)
            reasons = {item["job_id"]: item["reason"] for item in payload["skipped"]}
            self.assertIn("已进入回收站", reasons["ghost"])
            self.assertEqual(len(reasons), 3)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                statuses = {
                    row["id"]: row["status"]
                    for row in db.execute("SELECT id, status FROM jobs").fetchall()
                }
            finally:
                db.close()
            self.assertEqual(statuses["f1"], "ready")
            self.assertEqual(statuses["f2"], "ready")
            self.assertEqual(statuses["ready-1"], "ready")  # 已是 ready，同状态幂等
            self.assertEqual(statuses["sent-1"], "sent")  # sent 不可放行，保留原状态

    def test_bulk_approve_requires_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            status, body = _wsgi("/api/jobs/bulk-approve", method="POST", json_body={"job_ids": []})
            self.assertTrue(str(status).startswith("400"), body)


class SenderUrlGuardTests(unittest.TestCase):
    def test_real_platform_urls_pass(self):
        self.assertTrue(_is_sendable_platform_url({"source_platform": "boss", "url": "https://www.zhipin.com/job_detail/abc.html?lid=x"}))
        self.assertTrue(_is_sendable_platform_url({"source_platform": "zhilian", "url": "https://jobs.zhaopin.com/gz/123.htm"}))
        self.assertTrue(_is_sendable_platform_url({"source_platform": "51job", "url": "https://jobs.51job.com/gz/123.html"}))

    def test_mock_and_foreign_urls_blocked(self):
        self.assertFalse(_is_sendable_platform_url({"source_platform": "boss", "url": "https://example.com/mock/mock-1"}))
        self.assertFalse(_is_sendable_platform_url({"source_platform": "boss", "url": "https://evil.com/zhipin.com/fake"}))
        self.assertFalse(_is_sendable_platform_url({"source_platform": "boss", "url": ""}))
        self.assertFalse(_is_sendable_platform_url({"source_platform": "boss", "url": "not a url"}))
        self.assertFalse(_is_sendable_platform_url({"source_platform": "unknown", "url": "https://www.zhipin.com/x"}))


if __name__ == "__main__":
    unittest.main()
