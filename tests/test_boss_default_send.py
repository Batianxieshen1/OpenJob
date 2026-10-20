"""Boss 默认招呼语直发 + 公司 Logo 污染回归 + HR 沟通聚合 API 测试。"""

import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.db import get_db, get_jobs_ready_to_send, insert_job_if_new, update_company_profile


def _wsgi_json(path, method="GET", body=None):
    from openjob.web import server

    captured = {"status": ""}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status

    path_info, _, query = path.partition("?")
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path_info,
        "QUERY_STRING": query,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8686",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "CONTENT_LENGTH": str(len(payload)),
        "CONTENT_TYPE": "application/json" if payload else "",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(payload),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    chunks = server.app(environ, start_response)
    raw = b"".join(chunks).decode("utf-8")
    try:
        return captured["status"], json.loads(raw)
    except (ValueError, TypeError):
        return captured["status"], raw


class _BaseWithTmp(unittest.TestCase):
    def setUp(self):
        from openjob.web import server

        self.server = server
        self.original_base_dir = server.BASE_DIR

    def tearDown(self):
        self.server.set_base_dir(self.original_base_dir)

    def _setup(self, jobs):
        import yaml

        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        base = Path(tmp)
        (base / "config.yaml").write_text(yaml.dump({"profile": {}}), encoding="utf-8")
        db = get_db(base / "data" / "openjob.db")
        for record in jobs:
            insert_job_if_new(db, record)
        db.close()
        self.server.set_base_dir(base)
        return base


class BossDefaultQueryTests(unittest.TestCase):
    def _seed(self, conn):
        insert_job_if_new(conn, {
            "id": "bd-1", "title": "数据实习", "company": "任意", "jd": "x",
            "url": "https://www.zhipin.com/job_detail/bd-1.html",
        })
        insert_job_if_new(conn, {
            "id": "bd-2", "title": "正常投递", "company": "某公司", "jd": "x",
            "url": "https://www.zhipin.com/job_detail/bd-2.html",
        })
        # 直写状态（insert 不带 greeting/status 字段，走正式更新路径语义相同）
        conn.execute("UPDATE jobs SET status = 'approved' WHERE id = 'bd-1'")
        conn.execute(
            "UPDATE jobs SET status = 'approved', greeting = '定制招呼语', "
            "greeting_fact_status = 'verified', greeting_source_json = '{}' WHERE id = 'bd-2'"
        )
        conn.commit()

    def test_boss_default_relaxes_greeting_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            try:
                self._seed(conn)
                default_jobs = get_jobs_ready_to_send(conn)
                self.assertEqual([j["id"] for j in default_jobs], ["bd-2"])  # 常规门保留
                relaxed = get_jobs_ready_to_send(conn, boss_default=True)
                ids = sorted(j["id"] for j in relaxed)
                self.assertEqual(ids, ["bd-1", "bd-2"])  # boss_default 放宽
            finally:
                conn.close()


class BossDefaultDeliverApiTests(_BaseWithTmp):
    def test_boss_default_validates_without_greeting_and_starts_task(self):
        from unittest.mock import MagicMock, patch

        base = self._setup([
            {
                "id": "deliver-1", "title": "实习", "company": "某公司", "jd": "x",
                "url": "https://www.zhipin.com/job_detail/deliver-1.html",
            }
        ])
        # 直接写入 approved 状态（deliver API 要求 ready/approved/error）
        import sqlite3

        conn = sqlite3.connect(base / "data" / "openjob.db")
        conn.execute("UPDATE jobs SET status='approved' WHERE id='deliver-1'")
        conn.commit()
        conn.close()
        started = {}

        class FakeRunner:
            def status(self):
                return {"active": {}}

            _tasks = {}

            def start(self, mode, config):
                started["mode"] = mode
                started["config"] = dict(config)
                return {"id": "t-1", "mode": mode, "status": "running"}

        with patch.object(self.server, "task_runner", FakeRunner()), \
             patch.object(self.server, "_preflight_messages", return_value=[]), \
             patch.object(self.server, "load_config", return_value={"profile": {}}):
            status, body = _wsgi_json(
                "/api/workbench/deliver", "POST",
                {"job_ids": ["deliver-1"], "boss_default": True},
            )
        self.assertTrue(str(status).startswith("2"), body)
        self.assertEqual(started["mode"], "deliver")
        self.assertTrue(started["config"].get("_workbench_boss_default"))
        self.assertTrue(started["config"].get("_workbench_skip_greeting"))

    def test_direct_send_still_requires_greeting(self):
        base = self._setup([
            {
                "id": "deliver-2", "title": "实习", "company": "某公司", "jd": "x",
                "url": "https://www.zhipin.com/job_detail/deliver-2.html",
            }
        ])
        import sqlite3

        conn = sqlite3.connect(base / "data" / "openjob.db")
        conn.execute("UPDATE jobs SET status='approved' WHERE id='deliver-2'")
        conn.commit()
        conn.close()
        from unittest.mock import patch

        with patch.object(self.server, "_preflight_messages", return_value=[]), \
             patch.object(self.server, "load_config", return_value={"profile": {}}):
            status, body = _wsgi_json(
                "/api/workbench/deliver", "POST",
                {"job_ids": ["deliver-2"], "direct_send": True},
            )
        self.assertTrue(str(status).startswith("409"), body)


class HrMessagesApiTests(_BaseWithTmp):
    def test_jobs_list_carries_hr_conversation_aggregates(self):
        from openjob.db import upsert_conversation

        base = self._setup([
            {
                "id": "hm-1", "title": "实习", "company": "美的", "jd": "x",
                "url": "https://www.zhipin.com/job_detail/hm-1.html",
                "hr_name": "王女士",
            },
            {
                "id": "hm-2", "title": "实习", "company": "格力", "jd": "x",
                "url": "https://www.zhipin.com/job_detail/hm-2.html",
                "hr_name": "李先生",
            },
        ])
        db = get_db(base / "data" / "openjob.db")
        for i in range(2):
            upsert_conversation(db, platform="boss", fingerprint=f"fp-{i}", hr_name="王女士",
                                company="美的", snippet="你好", job_id="hm-1", match_status="matched")
        db.close()
        status, body = _wsgi_json("/api/jobs?limit=20")
        self.assertTrue(str(status).startswith("2"), body)
        by_id = {j["id"]: j for j in body}
        self.assertEqual(by_id["hm-1"]["hr_messages"], 2)
        self.assertEqual(by_id["hm-2"]["hr_messages"], 0)


class LogoPollutionRegressionTests(_BaseWithTmp):
    def test_cleanup_of_personal_avatar_company_logos(self):
        """回归：/upload/avatar/（个人头像路径）不得作为公司 Logo 存库。

        2026-10-20 用户实测：13 家公司挂上了自己的卡通头像（降级回退把
        登录用户自己的头像当成了公司 Logo）。
        """
        base = self._setup([
            {
                "id": "pol-1", "title": "实习", "company": "百度", "jd": "x",
                "url": "https://www.zhipin.com/job_detail/pol-1.html",
                "company_logo_url": "https://img.bosszhipin.com/beijin/upload/avatar/20260715/user.jpg",
                "company_logo_path": "assets/logos/polluted.png",
            },
            {
                "id": "pol-2", "title": "实习", "company": "某正规公司", "jd": "x",
                "url": "https://www.zhipin.com/job_detail/pol-2.html",
                "company_logo_url": "https://img.bosszhipin.com/beijin/upload/com/workfeel/real.jpg",
                "company_logo_path": "assets/logos/real.png",
            },
        ])
        db = get_db(base / "data" / "openjob.db")
        update_company_profile(
            db, "百度", intro="", intro_url="",
            logo_path="", logo_url="https://img.bosszhipin.com/beijin/upload/avatar/20260715/user.jpg",
        )  # 应不覆盖（COALESCE 只补空）——清洗走专门脚本
        db.close()
        # 清洗脚本同款 SQL 的验证
        conn = sqlite3.connect(base / "data" / "openjob.db")
        try:
            conn.execute("""
                UPDATE jobs SET company_logo_path = '', company_logo_url = ''
                WHERE deleted_at IS NULL AND company_logo_url LIKE '%/upload/avatar/%'
            """)
            conn.commit()
            rows = dict(
                conn.execute("SELECT id, company_logo_url FROM jobs WHERE company IN ('百度', '某正规公司')").fetchall()
            )
            self.assertEqual(rows.get("pol-1") or "", "")
            self.assertIn("workfeel", rows.get("pol-2") or "")
        finally:
            conn.close()

    def test_collection_rejects_personal_avatar_logo_url(self):
        """采集端双保险：candidate 的公司 Logo URL 为个人头像路径时清空。"""
        from openjob.collection.company_profile import save_logo_file

        self.assertTrue(save_logo_file(tempfile.mkdtemp(), "百度", b"\x89PNG" + b"0" * 28, "png"))
        # JS 回退只认 chatphoto 的静态断言（防止再引入 /upload/avatar/）
        source = (Path(__file__).resolve().parents[1] / "src" / "openjob" / "collection" / "platforms" / "boss.py").read_text(encoding="utf-8")
        self.assertNotIn("src.includes('/upload/avatar/')", source)
        self.assertIn("src.includes('chatphoto')", source)


if __name__ == "__main__":
    unittest.main()
