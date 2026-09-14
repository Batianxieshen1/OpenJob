"""端到端冒烟（WP-C1）：干净环境 seed mock → WSGI 调用关键 API → 状态迁移断言。

不起真实端口、不碰 Chrome/AI：直接调用 server.app（WSGI），3 分钟内可跑完，
供 CI 的 PR 快车道与本地快速验证使用。运行：pytest tests/test_e2e_smoke.py -q
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from openjob.db import get_db, insert_base_resume
from openjob.web import server

BASE_MD = """冒烟测试底稿
数据分析方向 | 广州
教育经历
华南师范大学 | 大数据管理与应用 | 本科
技能特长
SQL | Python
"""


def _job(job_id: str, **overrides) -> dict:
    job = {
        "id": job_id,
        "title": "数据分析助理",
        "company": "冒烟公司",
        "salary": "10-15K",
        "city": "广州",
        "jd": "负责业务数据看板建设，需要 SQL 能力",
        "hr_name": "王HR",
        "url": "https://example.com/job",
    }
    job.update(overrides)
    return job


def _wsgi(path, method="GET", json_body=None):
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status

    body_bytes = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
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


class E2eSmokeTests(unittest.TestCase):
    """干净临时目录起服务，走一遍关键 API 与状态迁移主链。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base_dir = Path(self._tmp.name)
        (base_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "profile": {"resume_materials_enabled": False},
                    "search": {"keywords": ["冒烟岗位"]},
                    "throttle": {"send_windows": ["09:00-16:00"]},
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        db = get_db(base_dir / "data" / "openjob.db")
        insert_base_resume(
            db, base_id="smoke-base", name="冒烟底稿",
            direction="数据分析", content_md=BASE_MD, is_default=True,
        )
        db.close()
        self.original_base_dir = server.BASE_DIR
        server.set_base_dir(base_dir)

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)
        self._tmp.cleanup()

    def test_health_and_contracts_served(self):
        status, body = _wsgi("/api/health")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(body["status"], "ok")

        status, body = _wsgi("/api/contracts")
        self.assertTrue(status.startswith("200"))
        self.assertTrue(body["success"])
        self.assertIn("ready", body["data"]["job_status_transitions"])

    def test_seed_to_funnel_and_stats(self):
        # 直接写库模拟采集产物（seed mock）
        from openjob.db import (
            get_db as _get_db,
            insert_job as _insert_job,
            transition_job_status,
            update_job_score,
        )

        db = _get_db(Path(self._tmp.name) / "data" / "openjob.db")
        try:
            for index in range(3):
                job_id = f"smoke-{index}"
                _insert_job(db, _job(job_id, company=f"冒烟公司{index}"))
                update_job_score(db, job_id, 85, "冒烟种子评分")
                transition_job_status(db, job_id, "ready")
            transition_job_status(db, "smoke-0", "approved")
        finally:
            db.close()

        status, body = _wsgi("/api/stats")
        self.assertTrue(status.startswith("200"))
        self.assertGreaterEqual(int(body.get("ready", 0)), 2)
        self.assertGreaterEqual(int(body.get("approved", 0)), 1)

        status, workbench = _wsgi("/api/workbench")
        self.assertTrue(status.startswith("200"))
        self.assertGreaterEqual(len(workbench.get("pending_confirmation", [])), 2)

    def test_status_transition_whitelist_enforced_end_to_end(self):
        from openjob.db import (
            get_db as _get_db,
            insert_job as _insert_job,
            transition_job_status,
            update_job_score,
        )
        from openjob.contracts import IllegalJobStatusTransition

        db = _get_db(Path(self._tmp.name) / "data" / "openjob.db")
        try:
            _insert_job(db, _job("smoke-flow"))
            update_job_score(db, "smoke-flow", 85, "冒烟种子评分")
            transition_job_status(db, "smoke-flow", "ready")
            transition_job_status(db, "smoke-flow", "approved")
            transition_job_status(db, "smoke-flow", "sent")
            with self.assertRaises(IllegalJobStatusTransition):
                transition_job_status(db, "smoke-flow", "pending")
            row = db.execute("SELECT status FROM jobs WHERE id = 'smoke-flow'").fetchone()
            self.assertEqual(row["status"], "sent")
        finally:
            db.close()

        # API 层同样拒绝非法迁移：已回复（replied）岗位不能再标记"简历已发"
        db = _get_db(Path(self._tmp.name) / "data" / "openjob.db")
        try:
            transition_job_status(db, "smoke-flow", "replied")
        finally:
            db.close()
        status, body = _wsgi("/api/jobs/smoke-flow/mark-resume-sent", method="POST")
        self.assertTrue(status.startswith("409"), body)
        self.assertEqual(body.get("error", {}).get("code"), "ILLEGAL_STATUS_TRANSITION")

    def test_inbox_and_stale_exit_endpoints_live(self):
        status, body = _wsgi("/api/inbox")
        self.assertTrue(status.startswith("200"))
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["pending_count"], 0)

        status, body = _wsgi("/api/jobs/stale-exit", method="POST", json_body={"job_ids": ["ghost"]})
        # ghost 岗位不存在：stale-exit 对不存在岗位跳过而非 500
        self.assertTrue(status.startswith("200"), body)
        self.assertEqual(body["data"]["stale_count"], 0)


if __name__ == "__main__":
    unittest.main()
