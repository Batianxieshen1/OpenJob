import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.db import get_db, insert_job, insert_base_resume


class GreetingSourceRegressionTests(unittest.TestCase):
    def test_retry_preserves_authoritative_material_context(self):
        from openjob.ai import greeter

        calls = []
        def fake_once(*args, **kwargs):
            calls.append(kwargs.get("material_context"))
            return None if len(calls) == 1 else "可以聊聊"

        config = {"ai": {"greeting_max_attempts": 2}}
        with patch.object(greeter, "_generate_greeting_once", side_effect=fake_once):
            result = greeter._generate_with_token_retry(
                {"company": "C", "title": "实习生"},
                "真实底稿",
                config,
                material_context="真实素材：STAR",
            )
        self.assertEqual(result, "可以聊聊")
        self.assertEqual(calls, ["真实素材：STAR", "真实素材：STAR"])

    def test_generate_uses_context_resume_instead_of_undefined_fallback(self):
        from openjob.ai import greeter

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "openjob.db"
            db = get_db(db_path)
            insert_job(db, {
                "id": "job-1", "title": "数据分析实习生", "company": "C",
                "salary": "面议", "city": "广州", "jd": "要求 SQL",
                "url": "https://example.com",
            })
            db.execute("UPDATE jobs SET status='approved' WHERE id='job-1'")
            db.commit()
            db.close()

            context = greeter.GreetingContext(
                resume_summary="林庆涛｜华南师范大学｜大数据管理与应用",
                material_context="素材：用 STAR 写出的真实经历",
                trusted_text="林庆涛 华南师范大学 大数据管理与应用 用 STAR 写出的真实经历",
                source={"base_resume_id": "base-1"},
            )
            captured = {}
            def fake_generate(job, resume_summary, config, critique="", *args, **kwargs):
                captured["resume_summary"] = resume_summary
                captured["material_context"] = kwargs.get("material_context")
                return "我有相关经历，想和您聊聊。"

            config = {"ai": {"greeting_max_iterations": 0}, "_runtime_db_path": str(db_path)}
            with patch.object(greeter, "RUNTIME_DB_PATH", db_path), \
                 patch.object(greeter, "_build_greeting_context", return_value=context), \
                 patch.object(greeter, "_generate_with_token_retry", side_effect=fake_generate):
                result = greeter.generate_greetings(config)

            self.assertEqual(result, 1)
            self.assertEqual(captured["resume_summary"], context.resume_summary)
            self.assertEqual(captured["material_context"], context.material_context)

    def test_server_resume_preflight_rejects_repository_example(self):
        from openjob.web import server

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_base = server.BASE_DIR
            try:
                server.set_base_dir(root)
                (root / "resume.md").write_text("示例简历 张三 某某大学", encoding="utf-8")
                config = {"profile": {"resume_path": str(root / "resume.md")}}
                with patch.object(server, "load_config", return_value=config):
                    self.assertFalse(server._has_any_resume_source())
            finally:
                server.set_base_dir(old_base)

    def test_deliver_rejects_unverified_greeting(self):
        from openjob.web import server

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_base = server.BASE_DIR
            try:
                server.set_base_dir(root)
                db = get_db(root / "data" / "openjob.db")
                insert_job(db, {
                    "id": "job-unsafe", "title": "实习生", "company": "C",
                    "salary": "面议", "city": "广州", "jd": "要求 SQL",
                    "url": "https://example.com",
                })
                db.execute("UPDATE jobs SET status='approved', greeting='旧招呼语', greeting_fact_status='unverified' WHERE id='job-unsafe'")
                db.commit()
                db.close()

                status, body = _wsgi(server, "POST", "/api/workbench/deliver", {"job_ids": ["job-unsafe"], "direct_send": True})
                self.assertEqual(status, 409)
                self.assertIn("事实一致性校验", body)
            finally:
                server.set_base_dir(old_base)


def _wsgi(server, method, path, payload):
    import io
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status_headers = {}
    def start_response(status, headers, exc_info=None):
        status_headers["status"] = status
    environ = {
        "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "",
        "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8686", "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http", "wsgi.input": io.BytesIO(body),
        "wsgi.errors": io.StringIO(), "wsgi.multithread": False,
        "wsgi.multiprocess": False, "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/json",
    }
    result = server.app(environ, start_response)
    try:
        text = b"".join(x if isinstance(x, bytes) else x.encode() for x in result).decode("utf-8")
    finally:
        close = getattr(result, "close", None)
        if close:
            close()
    return int(status_headers["status"].split()[0]), text


if __name__ == "__main__":
    unittest.main()
