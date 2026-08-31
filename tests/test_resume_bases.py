"""多简历底稿测试：选稿策略 + CRUD API + 引擎集成。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.db import get_db, insert_base_resume

RESUME_DATA = "数据分析底稿" * 60
RESUME_DATA_OP = "新媒体运营底稿" * 60


class BaseSelectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "data" / "openjob.db"
        self.config = {"profile": {"resume_path": str(self.root / "resume.md")}}
        (self.root / "resume.md").write_text("默认简历文件内容" * 30, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _connect(self):
        return get_db(self.db_path)

    def test_no_bases_falls_back_to_config_file(self):
        from openjob.ai.resume_engine.bases import select_base_for_job

        conn = self._connect()
        try:
            sel = select_base_for_job(conn, "数据分析岗 JD", self.config)
        finally:
            conn.close()
        self.assertIsNone(sel.base.get("id"))
        self.assertIn("默认简历", sel.reason)

    def test_single_base_used_directly(self):
        from openjob.ai.resume_engine.bases import select_base_for_job

        conn = self._connect()
        try:
            insert_base_resume(conn, base_id="b1", name="数据分析", direction="数据分析", content_md=RESUME_DATA, is_default=False)
            sel = select_base_for_job(conn, "数据分析岗 JD", self.config)
        finally:
            conn.close()
        self.assertEqual(sel.base["id"], "b1")
        self.assertIn("唯一底稿", sel.reason)

    def test_direction_label_beats_overlap(self):
        from openjob.ai.resume_engine.bases import select_base_for_job

        conn = self._connect()
        try:
            insert_base_resume(conn, base_id="b-data", name="数据分析方向", direction="数据分析", content_md=RESUME_DATA, is_default=True)
            insert_base_resume(conn, base_id="b-op", name="运营方向", direction="运营", content_md=RESUME_DATA_OP, is_default=False)
            sel = select_base_for_job(conn, "招聘数据分析实习生，要求 SQL 与看板能力", self.config)
        finally:
            conn.close()
        self.assertEqual(sel.base["id"], "b-data")

    def test_low_overlap_uses_default(self):
        from openjob.ai.resume_engine.bases import select_base_for_job

        conn = self._connect()
        try:
            insert_base_resume(conn, base_id="b-a", name="甲方向", direction="甲乙", content_md="完全不同的内容一" * 50, is_default=False)
            insert_base_resume(conn, base_id="b-b", name="乙方向", direction="丙丁", content_md="完全不同的内容二" * 50, is_default=True)
            sel = select_base_for_job(conn, "毫不相关的岗位描述量子物理实验", self.config)
        finally:
            conn.close()
        self.assertEqual(sel.base["id"], "b-b")
        self.assertIn("默认", sel.reason)


class BaseCrudApiTests(unittest.TestCase):
    def setUp(self):
        from openjob.web import server

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._original = server.BASE_DIR
        server.set_base_dir(self.root)

    def tearDown(self):
        from openjob.web import server

        server.set_base_dir(self._original)
        self._tmp.cleanup()

    def _wsgi(self, method, path, body=b"", content_type=""):
        import io

        from openjob.web import server

        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        if body:
            environ["CONTENT_LENGTH"] = str(len(body))
        if content_type:
            environ["CONTENT_TYPE"] = content_type
        resp = server.app(environ, start_response)
        try:
            payload = b"".join(c if isinstance(c, bytes) else c.encode() for c in resp).decode("utf-8")
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()
        return int(status_headers["status"].split()[0]), payload

    def _multipart(self, filename="data.md", content="数据分析简历内容" * 30):
        boundary = "----OpenJobBaseResume"
        body = (
            (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: text/markdown\r\n\r\n"
            ).encode("utf-8")
            + content.encode("utf-8")
            + (
                f"\r\n--{boundary}\r\n"
                f'Content-Disposition: form-data; name="name"\r\n\r\n数据分析方向\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="direction"\r\n\r\n数据分析\r\n'
                f"--{boundary}--\r\n"
            ).encode("utf-8")
        )
        return self._wsgi("POST", "/api/resume/bases", body, f"multipart/form-data; boundary={boundary}")

    def test_upload_list_patch_delete(self):
        from openjob.web import server

        status, payload = self._multipart()
        self.assertEqual(status, 200)
        base_id = json.loads(payload)["id"]

        status, payload = self._wsgi("GET", "/api/resume/bases")
        bases = json.loads(payload)["bases"]
        self.assertEqual(len(bases), 1)
        self.assertEqual(bases[0]["name"], "数据分析方向")

        status, _ = self._wsgi("PATCH", f"/api/resume/bases/{base_id}", json.dumps({"is_default": True, "name": "数据方向"}).encode(), "application/json")
        self.assertEqual(status, 200)
        conn = get_db(server.DATA_DIR / "openjob.db")
        row = conn.execute("SELECT name, is_default FROM base_resumes WHERE id = ?", (base_id,)).fetchone()
        conn.close()
        self.assertEqual(row["name"], "数据方向")
        self.assertEqual(row["is_default"], 1)

        status, _ = self._wsgi("DELETE", f"/api/resume/bases/{base_id}")
        self.assertEqual(status, 200)
        status, payload = self._wsgi("GET", "/api/resume/bases")
        self.assertEqual(json.loads(payload)["bases"], [])

    def test_upload_rejects_empty_file(self):
        status, _ = self._multipart(filename="empty.md", content="")
        self.assertEqual(status, 400)

    def test_delete_missing_base_404(self):
        status, _ = self._wsgi("DELETE", "/api/resume/bases/nope")
        self.assertEqual(status, 404)

    def test_upload_stores_text_not_bytes(self):
        """回归：中文内容入库必须是 TEXT，字节串会炸掉选稿的文本处理。"""
        from openjob.web import server

        status, payload = self._multipart(content="数据分析简历中文内容" * 20)
        self.assertEqual(status, 200)
        base_id = json.loads(payload)["id"]
        conn = get_db(server.DATA_DIR / "openjob.db")
        row = conn.execute(
            "SELECT typeof(content_md) t, content_md FROM base_resumes WHERE id = ?", (base_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["t"], "text")
        self.assertIn("数据分析简历中文内容", row["content_md"])


class EngineBaseIntegrationTests(unittest.TestCase):
    def test_generate_uses_selected_base_and_records_pointer(self):
        from openjob.ai.resume_engine import engine
        from openjob.ai.resume_engine.models import RewriteResult
        from openjob.db import insert_job, get_db as gdb

        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        db_path = root / "data" / "openjob.db"
        config = {
            "profile": {"resume_path": str(root / "resume.md"), "resume_output_dir": str(root / "out")},
            "ai": {"service": "deepseek"},
        }
        (root / "resume.md").write_text("通用底稿" * 60, encoding="utf-8")

        conn = gdb(db_path)
        insert_job(conn, {"id": "job-base", "title": "数据分析实习生", "company": "C", "salary": "100/天", "city": "广州", "jd": "要求 SQL、Python 与数据看板能力，负责业务分析。", "url": "u"})
        insert_base_resume(conn, base_id="b-gen", name="数据分析", direction="数据分析", content_md="# 林晓\n\n## 相关技能\n- SQL 熟练，Python 数据分析" * 8, is_default=True)
        insert_base_resume(conn, base_id="b-gen2", name="运营", direction="运营", content_md="运营底稿内容" * 60, is_default=False)
        conn.close()

        original = engine.RUNTIME_DB_PATH
        engine.RUNTIME_DB_PATH = db_path
        try:
            from openjob.ai.resume_engine.models import JdProfile, MatchReport

            with patch.object(engine, "parse_jd", return_value=JdProfile.from_payload({"title": "数据分析实习生"})), \
                 patch.object(engine, "analyze_match", return_value=MatchReport.from_payload({"entries": []})), \
                 patch.object(engine, "rewrite_sections") as rewrite_mock:
                rewrite_mock.return_value = RewriteResult.from_payload({"changes": [
                    {"section": "相关技能", "before": "SQL 熟练，Python 数据分析", "after": "SQL 熟练，Python 数据分析与看板", "reason": "对齐", "risk": ""}
                ]})
                result = engine.generate_resume("job-base", config)
            self.assertTrue(result.ok)
            conn = gdb(db_path)
            record = dict(conn.execute("SELECT base_resume_id, base_md FROM resumes WHERE job_id = 'job-base'").fetchone())
            conn.close()
            self.assertEqual(record["base_resume_id"], "b-gen")
            self.assertIn("林晓", record["base_md"])
        finally:
            engine.RUNTIME_DB_PATH = original
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
