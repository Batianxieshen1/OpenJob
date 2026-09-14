"""C2 可观测性测试：任务日志落盘/轮转/tail API + 错误归因聚合。"""

import io
import json
import tempfile
import unittest
from pathlib import Path

from openjob.db import add_history, get_db, insert_job, transition_job_status, update_job_score
from openjob.web import server


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


class TaskLogPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.original_base_dir = server.BASE_DIR
        server.set_base_dir(Path(self._tmp.name))

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)
        self._tmp.cleanup()

    def test_log_appended_and_tail_read(self):
        task_id = "smoke-log-task"
        for index in range(5):
            server._append_task_log_file(task_id, f"第 {index} 行日志")
        status, body = _wsgi(f"/api/logs/{task_id}/tail")
        self.assertTrue(status.startswith("200"), body)
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["source"], "file")
        self.assertEqual(body["data"]["total_lines"], 5)
        self.assertEqual(body["data"]["lines"][-1], "第 4 行日志")

        status, body = _wsgi(f"/api/logs/{task_id}/tail?lines=2")
        self.assertEqual(len(body["data"]["lines"]), 2)

    def test_log_rotation_at_size_limit(self):
        task_id = "smoke-rotate-task"
        server._append_task_log_file(task_id, "x" * 100)
        # 模拟达到 5MB 上限：直接把文件撑到接近上限再写一条大日志
        log_path = Path(self._tmp.name) / "data" / "logs" / f"{task_id}.log"
        log_path.write_text("y" * server.TASK_LOG_MAX_BYTES, encoding="utf-8")
        server._append_task_log_file(task_id, "触发轮转的新日志")
        rotated = log_path.with_suffix(".log.1")
        self.assertTrue(rotated.exists())
        self.assertEqual(log_path.read_text(encoding="utf-8").strip(), "触发轮转的新日志")

    def test_tail_rejects_path_traversal(self):
        status, body = _wsgi("/api/logs/..%2Fconfig/tail")
        self.assertTrue(status.startswith("400"), body)

    def test_tail_falls_back_to_memory(self):
        from openjob.web.tasks import WorkbenchTask

        task = WorkbenchTask(id="mem-task", mode="collect", label="单独采集")
        task.logs.append("内存中的日志")
        server.task_runner._tasks[task.id] = task
        try:
            status, body = _wsgi("/api/logs/mem-task/tail")
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(body["data"]["source"], "memory")
            self.assertIn("内存中的日志", body["data"]["lines"])
        finally:
            server.task_runner._tasks.pop(task.id, None)


class ErrorSummaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.original_base_dir = server.BASE_DIR
        server.set_base_dir(Path(self._tmp.name))
        db = get_db(Path(self._tmp.name) / "data" / "openjob.db")
        insert_job(db, {"id": "e1", "title": "数据专员", "company": "错例公司", "jd": "x", "url": "https://example.com/j"})
        update_job_score(db, "e1", 85, "种子")
        transition_job_status(db, "e1", "ready")
        add_history(db, "e1", "error", "无法找到沟通按钮：页面结构可能变化")
        add_history(db, "e1", "error", "无法找到沟通按钮：页面结构可能变化")
        add_history(db, "e1", "score_failed", "AI 未返回完整、可解析的评分 JSON")
        db.close()

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)
        self._tmp.cleanup()

    def test_errors_grouped_and_attributed(self):
        status, body = _wsgi("/api/errors/summary?days=30")
        self.assertTrue(status.startswith("200"), body)
        data = body["data"]
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["unclassified"], 0)
        by_category = {item["category"]: item["count"] for item in data["categories"]}
        self.assertEqual(by_category.get("selector_miss:页面结构变化，选择器未命中"), 2)
        self.assertEqual(by_category.get("ai_parse:AI 返回无法解析"), 1)

    def test_unknown_error_lands_in_other(self):
        db = get_db(Path(self._tmp.name) / "data" / "openjob.db")
        try:
            add_history(db, "e1", "error", "某种全新的未知失败")
        finally:
            db.close()
        status, body = _wsgi("/api/errors/summary")
        data = body["data"]
        self.assertEqual(data["total"], 4)
        self.assertEqual(data["unclassified"], 1)


if __name__ == "__main__":
    unittest.main()


class DeliveryLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.original_base_dir = server.BASE_DIR
        server.set_base_dir(Path(self._tmp.name))

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)
        self._tmp.cleanup()

    def test_delivery_log_joins_greeting_and_summary(self):
        from openjob.db import add_history, get_db, insert_job, transition_job_status, update_job_greeting, update_job_score

        db = get_db(Path(self._tmp.name) / "data" / "openjob.db")
        for job_id, final, history_action in (("d1", "sent", "sent"), ("d2", "error", "error")):
            insert_job(db, {"id": job_id, "title": "数据专员", "company": "对照公司", "jd": "x", "url": "https://www.zhipin.com/j"})
            update_job_score(db, job_id, 80, "种子")
            transition_job_status(db, job_id, "ready")
            transition_job_status(db, job_id, "approved")
            if history_action == "sent":
                transition_job_status(db, job_id, "sent")
            else:
                transition_job_status(db, job_id, "error")
            update_job_greeting(db, job_id, f"{job_id} 的招呼语全文", fact_status="verified", source_json="{}")
            add_history(db, job_id, history_action, f"{job_id} 台账")
        db.close()
        status, body = _wsgi("/api/delivery-log?days=7")
        self.assertTrue(str(status).startswith("200"), body)
        data = body["data"]
        self.assertEqual(data["summary"]["sent"], 1)
        self.assertEqual(data["summary"]["failed"], 1)
        by_job = {item["job_id"]: item for item in data["items"]}
        self.assertEqual(by_job["d1"]["greeting"], "d1 的招呼语全文")
        self.assertEqual(by_job["d1"]["greeting_fact_status"], "verified")
        self.assertEqual(by_job["d2"]["action"], "error")
