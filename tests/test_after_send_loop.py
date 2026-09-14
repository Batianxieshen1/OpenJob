"""A 阶段投后闭环测试：A1 回流 / A2 终态与时间线 / A3 回复工作台 / B9 积压治理。"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.ai import greeter
from openjob.contracts import JOB_STATUS_TRANSITIONS, IllegalJobStatusTransition, validate_job_status_transition
from openjob.db import (
    add_history,
    get_conversation,
    get_db,
    insert_job,
    link_conversation,
    list_conversations,
    record_job_reply,
    set_conversation_handle_status,
    transition_job_status,
    upsert_conversation,
)
from openjob.executor import monitor
from openjob.web import server

BASE_MD = """李明明
数据分析方向 | 广州
教育经历
华南师范大学 | 大数据管理与应用 | 本科
项目经历
• | 用户调研 | 独立设计问卷并回收有效样本 400 份 | 制作推文及海报通过朋友圈与小红书投放
技能特长
SQL | Python | Pandas
"""


def _job(job_id: str, **overrides) -> dict:
    job = {
        "id": job_id,
        "title": "数据分析助理",
        "company": "云启科技",
        "salary": "10-15K",
        "city": "广州",
        "jd": "负责业务数据看板建设",
        "hr_name": "王HR",
        "url": "https://example.com/job",
    }
    job.update(overrides)
    return job


class ReplyFactStoreTests(unittest.TestCase):
    def test_v2_7_v2_8_migrations_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            db.close()
            db = get_db(db_path)  # 第二次初始化不报错、不重复加列
            try:
                cols = {row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
                self.assertIn("replied_at", cols)
                self.assertIn("reply_count", cols)
                self.assertIn("last_reply_snippet", cols)
                self.assertIn("interview_at", cols)
                self.assertIn("closed_at", cols)
                self.assertIn("closed_reason", cols)
                tables = {
                    row["name"]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertIn("conversations", tables)
            finally:
                db.close()

    def test_record_job_reply_first_and_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job(db, _job("r1"))
                record_job_reply(db, "r1", "你好，方便聊聊这个岗位吗？")
                row = dict(db.execute("SELECT * FROM jobs WHERE id = 'r1'").fetchone())
                self.assertEqual(row["reply_count"], 1)
                self.assertTrue(row["replied_at"])
                self.assertIn("方便聊聊", row["last_reply_snippet"])
                first_replied_at = row["replied_at"]

                record_job_reply(db, "r1", "明天下午方便面试吗")
                row = dict(db.execute("SELECT * FROM jobs WHERE id = 'r1'").fetchone())
                self.assertEqual(row["reply_count"], 2)
                self.assertEqual(row["replied_at"], first_replied_at)
                self.assertIn("面试", row["last_reply_snippet"])
            finally:
                db.close()

    def test_upsert_conversation_idempotent_and_resolved_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                fingerprint = "fp-1"
                first_id = upsert_conversation(
                    db, platform="boss", fingerprint=fingerprint,
                    hr_name="王HR", company="云启科技", snippet="第一条",
                    job_id=None, match_status="unmatched",
                )
                second_id = upsert_conversation(
                    db, platform="boss", fingerprint=fingerprint,
                    hr_name="王HR", company="云启科技", snippet="第二条",
                    job_id=None, match_status="unmatched",
                )
                self.assertEqual(first_id, second_id)
                rows = list_conversations(db, handle_status="pending")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["last_message_snippet"], "第二条")

                set_conversation_handle_status(db, first_id, "resolved")
                upsert_conversation(
                    db, platform="boss", fingerprint=fingerprint,
                    hr_name="王HR", company="云启科技", snippet="第三条",
                    job_id=None, match_status="unmatched",
                )
                row = get_conversation(db, first_id)
                self.assertEqual(row["handle_status"], "resolved")
                self.assertEqual(row["last_message_snippet"], "第二条")
            finally:
                db.close()

    def test_link_conversation_only_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job(db, _job("j1"))
                conv_id = upsert_conversation(
                    db, platform="boss", fingerprint="fp-9",
                    hr_name="王HR", company="云启科技", snippet="消息",
                    job_id=None, match_status="unmatched",
                )
                self.assertTrue(link_conversation(db, conv_id, "j1"))
                self.assertEqual(get_conversation(db, conv_id)["match_status"], "matched")
                set_conversation_handle_status(db, conv_id, "resolved")
                insert_job(db, _job("j2"))
                self.assertFalse(link_conversation(db, conv_id, "j2"))
            finally:
                db.close()


class MonitorMatchTests(unittest.TestCase):
    def _jobs(self):
        return [
            _job("j1", hr_name="王HR", company="云启科技"),
            _job("j2", hr_name="李HR", company="星河数据"),
            _job("j3", hr_name="", company="无名公司"),
        ]

    def test_exact_match(self):
        job, status = monitor._match_conversation_to_job(
            {"hr_name": "王HR", "company": "云启科技"}, self._jobs()
        )
        self.assertEqual((job["id"], status), ("j1", "matched"))

    def test_fuzzy_company_match(self):
        job, status = monitor._match_conversation_to_job(
            {"hr_name": "王HR", "company": "云启"}, self._jobs()
        )
        self.assertEqual((job["id"], status), ("j1", "matched"))

    def test_company_only_fallback(self):
        # 岗位没有 hr_name 时按公司名兜底匹配（沿用既有语义）
        job, status = monitor._match_conversation_to_job(
            {"hr_name": "张助理", "company": "无名公司"}, self._jobs()
        )
        self.assertEqual((job["id"], status), ("j3", "matched"))

    def test_unmatched_when_nothing_matches(self):
        job, status = monitor._match_conversation_to_job(
            {"hr_name": "陌生HR", "company": "陌生公司"}, self._jobs()
        )
        self.assertIsNone(job)
        self.assertEqual(status, "unmatched")

    def test_ambiguous_when_two_jobs_match(self):
        jobs = [
            _job("a1", hr_name="王HR", company="云启科技"),
            _job("a2", hr_name="王HR", company="云启科技"),
        ]
        job, status = monitor._match_conversation_to_job(
            {"hr_name": "王HR", "company": "云启科技"}, jobs
        )
        self.assertIsNone(job)
        self.assertEqual(status, "ambiguous")

    def test_unmatched_without_hr_name(self):
        job, status = monitor._match_conversation_to_job(
            {"hr_name": "", "company": "云启科技"}, self._jobs()
        )
        self.assertIsNone(job)
        self.assertEqual(status, "unmatched")

    def test_fingerprint_stable(self):
        conv = {"hr_name": "王HR", "company": "云启科技", "last_message": "你好"}
        self.assertEqual(monitor._conversation_fingerprint(conv), monitor._conversation_fingerprint(dict(conv)))

    def test_post_send_state_machine_extensions(self):
        validate_job_status_transition("replied", "interview")
        validate_job_status_transition("interview", "offer")
        validate_job_status_transition("interview", "hr_rejected")
        validate_job_status_transition("offer", "closed")
        validate_job_status_transition("hr_rejected", "closed")
        with self.assertRaises(IllegalJobStatusTransition):
            validate_job_status_transition("closed", "ready")
        self.assertNotIn("sent", JOB_STATUS_TRANSITIONS["interview"])


def _run_scan(db_path, conversations, tracked_jobs=None):
    """运行一次监测扫描；每次调用让 monitor 自己打开并关闭连接（模拟真实行为）。"""
    with patch.object(monitor, "get_db", side_effect=lambda *a, **k: get_db(db_path)), \
         patch.object(monitor, "stop_requested", return_value=False), \
         patch.object(monitor, "_open_monitor_tab", return_value="tab-1"), \
         patch.object(monitor, "_wait_or_stop", return_value=False), \
         patch.object(monitor, "_wait_for_page_or_stop", return_value=True), \
         patch.object(monitor, "_inspect_monitor_page", return_value=None), \
         patch.object(monitor, "evaluate", return_value=json.dumps(conversations)), \
         patch.object(monitor, "close_tab", return_value=None):
        return monitor._check_boss_replies({"monitor": {}}, tracked_jobs)


class MonitorScanWritebackTests(unittest.TestCase):
    def _db_with_sent_job(self, tmp):
        db_path = Path(tmp) / "openjob.db"
        db = get_db(db_path)
        insert_job(db, _job("s1", hr_name="王HR", company="云启科技"))
        transition_job_status(db, "s1", "ready")
        transition_job_status(db, "s1", "approved")
        transition_job_status(db, "s1", "sent")
        db.close()
        return db_path

    def test_matched_reply_writes_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._db_with_sent_job(tmp)
            conversations = [{
                "hr_name": "王HR", "company": "云启科技",
                "last_message": "你好，看了你的简历想聊聊", "has_reply": True,
            }]
            results = _run_scan(db_path, conversations)
            self.assertEqual(len(results), 1)
            db = get_db(db_path)
            try:
                job = dict(db.execute("SELECT * FROM jobs WHERE id = 's1'").fetchone())
                self.assertEqual(job["status"], "replied")
                self.assertEqual(job["reply_count"], 1)
                self.assertIn("想聊聊", job["last_reply_snippet"])
                rows = list_conversations(db, handle_status="pending")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["match_status"], "matched")
                self.assertEqual(rows[0]["job_id"], "s1")
                history = db.execute(
                    "SELECT detail FROM history WHERE job_id = 's1' AND action = 'replied'"
                ).fetchall()
                self.assertEqual(len(history), 1)
                self.assertIn("reply_fingerprint", history[0]["detail"])
            finally:
                db.close()

    def test_repeated_scan_does_not_double_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._db_with_sent_job(tmp)
            conversations = [{
                "hr_name": "王HR", "company": "云启科技",
                "last_message": "你好，看了你的简历想聊聊", "has_reply": True,
            }]
            _run_scan(db_path, conversations)
            _run_scan(db_path, conversations)
            db = get_db(db_path)
            try:
                job = dict(db.execute("SELECT * FROM jobs WHERE id = 's1'").fetchone())
                self.assertEqual(job["reply_count"], 1)
                self.assertEqual(len(list_conversations(db, handle_status="pending")), 1)
                history_count = db.execute(
                    "SELECT COUNT(*) AS cnt FROM history WHERE job_id = 's1' AND action = 'replied'"
                ).fetchone()["cnt"]
                self.assertEqual(history_count, 1)
            finally:
                db.close()

    def test_unmatched_conversation_registered_not_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._db_with_sent_job(tmp)
            conversations = [{
                "hr_name": "陌生HR", "company": "陌生公司",
                "last_message": "在吗", "has_reply": True,
            }]
            results = _run_scan(db_path, conversations)
            self.assertEqual(results, [])
            db = get_db(db_path)
            try:
                rows = list_conversations(db, handle_status="pending")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["match_status"], "unmatched")
                self.assertIsNone(rows[0]["job_id"])
            finally:
                db.close()

    def test_ambiguous_conversation_not_auto_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            try:
                insert_job(db, _job("a1", hr_name="王HR", company="云启科技"))
                insert_job(db, _job("a2", hr_name="王HR", company="云启科技"))
                for job_id in ("a1", "a2"):
                    transition_job_status(db, job_id, "ready")
                    transition_job_status(db, job_id, "approved")
                    transition_job_status(db, job_id, "sent")
            finally:
                db.close()
            conversations = [{
                "hr_name": "王HR", "company": "云启科技",
                "last_message": "聊聊？", "has_reply": True,
            }]
            results = _run_scan(db_path, conversations)
            self.assertEqual(results, [])
            db = get_db(db_path)
            try:
                rows = list_conversations(db, handle_status="pending")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["match_status"], "ambiguous")
                for job_id in ("a1", "a2"):
                    job = dict(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
                    self.assertEqual(job["status"], "sent")
                    self.assertEqual(job["reply_count"], 0)
            finally:
                db.close()


def _wsgi_request(path, method="GET", json_body=None):
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
    body = b"".join(chunks).decode("utf-8")
    return captured["status"], body


class AfterSendApiTests(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = server.BASE_DIR

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)

    def _setup(self, tmp, *, job_status="sent"):
        import yaml

        base_dir = Path(tmp)
        (base_dir / "config.yaml").write_text(
            yaml.dump({"profile": {}}, allow_unicode=True), encoding="utf-8"
        )
        db = get_db(base_dir / "data" / "openjob.db")
        insert_job(db, _job("api-1"))
        # 按白名单合法链推进到目标状态
        if job_status in {"ready", "approved", "sent"}:
            transition_job_status(db, "api-1", "ready")
        if job_status in {"approved", "sent"}:
            transition_job_status(db, "api-1", "approved")
        if job_status == "sent":
            transition_job_status(db, "api-1", "sent")
        add_history(db, "api-1", "sent", "测试发送台账")
        db.close()
        server.set_base_dir(base_dir)

    def test_mark_replied_manual_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            status, body = _wsgi_request(
                "/api/jobs/api-1/mark-replied", method="POST",
                json_body={"snippet": "HR 约明天面试"},
            )
            payload = json.loads(body)
            self.assertTrue(status.startswith("200"), body)
            self.assertTrue(payload["success"])
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                job = dict(db.execute("SELECT * FROM jobs WHERE id = 'api-1'").fetchone())
                self.assertEqual(job["status"], "replied")
                self.assertEqual(job["reply_count"], 1)
            finally:
                db.close()

    def test_mark_replied_rejects_illegal_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp, job_status="pending")
            status, body = _wsgi_request(
                "/api/jobs/api-1/mark-replied", method="POST", json_body={},
            )
            payload = json.loads(body)
            self.assertTrue(status.startswith("409"), body)
            self.assertEqual(payload["error"]["code"], "ILLEGAL_STATUS_TRANSITION")

    def test_transition_to_interview_stamps_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                transition_job_status(db, "api-1", "replied")
            finally:
                db.close()
            status, body = _wsgi_request(
                "/api/jobs/api-1/transition", method="POST",
                json_body={"status": "interview"},
            )
            payload = json.loads(body)
            self.assertTrue(status.startswith("200"), body)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                job = dict(db.execute("SELECT * FROM jobs WHERE id = 'api-1'").fetchone())
                self.assertEqual(job["status"], "interview")
                self.assertTrue(job["interview_at"])
            finally:
                db.close()

    def test_transition_closed_carries_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                transition_job_status(db, "api-1", "replied")
                transition_job_status(db, "api-1", "interview")
            finally:
                db.close()
            status, body = _wsgi_request(
                "/api/jobs/api-1/transition", method="POST",
                json_body={"status": "closed", "reason": "接受其他 Offer"},
            )
            self.assertTrue(status.startswith("200"), body)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                job = dict(db.execute("SELECT * FROM jobs WHERE id = 'api-1'").fetchone())
                self.assertEqual(job["status"], "closed")
                self.assertEqual(job["closed_reason"], "接受其他 Offer")
                self.assertTrue(job["closed_at"])
            finally:
                db.close()

    def test_stale_exit_then_reactivate(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp, job_status="approved")
            status, body = _wsgi_request(
                "/api/jobs/stale-exit", method="POST", json_body={"job_ids": ["api-1"]},
            )
            payload = json.loads(body)
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(payload["data"]["stale_count"], 1)
            status, body = _wsgi_request(
                "/api/jobs/api-1/transition", method="POST", json_body={"status": "ready"},
            )
            self.assertTrue(status.startswith("200"), body)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                job = dict(db.execute("SELECT status FROM jobs WHERE id = 'api-1'").fetchone())
                self.assertEqual(job["status"], "ready")
            finally:
                db.close()

    def test_job_detail_includes_history_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            status, body = _wsgi_request("/api/jobs/api-1")
            payload = json.loads(body)
            self.assertTrue(status.startswith("200"), body)
            self.assertIn("history", payload)
            self.assertTrue(any(item["action"] == "sent" for item in payload["history"]))


class InboxApiTests(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = server.BASE_DIR

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)

    def _setup(self, tmp):
        import yaml

        base_dir = Path(tmp)
        (base_dir / "config.yaml").write_text(
            yaml.dump({"profile": {}}, allow_unicode=True), encoding="utf-8"
        )
        db = get_db(base_dir / "data" / "openjob.db")
        from openjob.db import insert_base_resume

        insert_base_resume(
            db, base_id="base-1", name="数据分析方向",
            direction="数据分析", content_md=BASE_MD, is_default=True,
        )
        insert_job(db, _job("linked"))
        transition_job_status(db, "linked", "ready")
        transition_job_status(db, "linked", "approved")
        transition_job_status(db, "linked", "sent")
        upsert_conversation(
            db, platform="boss", fingerprint="fp-linked",
            hr_name="王HR", company="云启科技", snippet="方便发一份完整简历吗",
            job_id="linked", match_status="matched",
        )
        upsert_conversation(
            db, platform="boss", fingerprint="fp-orphan",
            hr_name="陌生HR", company="陌生公司", snippet="在吗",
            job_id=None, match_status="unmatched",
        )
        db.close()
        server.set_base_dir(base_dir)

    def test_inbox_lists_pending_conversations(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            status, body = _wsgi_request("/api/inbox")
            payload = json.loads(body)
            self.assertTrue(status.startswith("200"), body)
            self.assertTrue(payload["success"])
            self.assertEqual(payload["data"]["pending_count"], 2)
            self.assertEqual(len(payload["data"]["conversations"]), 2)

    def test_draft_generation_is_clipboard_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                conv = [c for c in list_conversations(db, handle_status="pending") if c["job_id"]]
                conversation_id = conv[0]["id"]
            finally:
                db.close()
            with patch.object(
                greeter, "_call_claude",
                return_value="我做过 400 份问卷的用户调研，方便发一份我的完整简历给您。",
            ), patch.object(monitor, "_send_message_in_chat") as send_mock, \
               patch.object(monitor, "_send_message_in_chat", send_mock):
                status, body = _wsgi_request(
                    f"/api/conversations/{conversation_id}/draft", method="POST", json_body={},
                )
            payload = json.loads(body)
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(payload["data"]["fact_status"], "verified")
            self.assertIn("400 份", payload["data"]["draft"])
            send_mock.assert_not_called()

    def test_draft_fact_issues_surface_to_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                conv = [c for c in list_conversations(db, handle_status="pending") if c["job_id"]]
                conversation_id = conv[0]["id"]
            finally:
                db.close()
            with patch.object(
                greeter, "_call_claude",
                return_value="我毕业于清华大学计算机专业，做过完整的推荐系统。",
            ):
                status, body = _wsgi_request(
                    f"/api/conversations/{conversation_id}/draft", method="POST", json_body={},
                )
            payload = json.loads(body)
            self.assertTrue(status.startswith("200"), body)
            self.assertEqual(payload["data"]["fact_status"], "unverified")
            self.assertTrue(payload["data"]["issues"])

    def test_draft_requires_linked_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                conv = [c for c in list_conversations(db, handle_status="pending") if not c["job_id"]]
                conversation_id = conv[0]["id"]
            finally:
                db.close()
            status, body = _wsgi_request(
                f"/api/conversations/{conversation_id}/draft", method="POST", json_body={},
            )
            payload = json.loads(body)
            self.assertTrue(status.startswith("409"), body)
            self.assertEqual(payload["error"]["code"], "CONVERSATION_UNLINKED")

    def test_link_then_resolve_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                conv = [c for c in list_conversations(db, handle_status="pending") if not c["job_id"]]
                conversation_id = conv[0]["id"]
            finally:
                db.close()
            status, body = _wsgi_request(
                f"/api/conversations/{conversation_id}/link", method="POST",
                json_body={"job_id": "linked"},
            )
            self.assertTrue(status.startswith("200"), body)
            status, body = _wsgi_request(
                f"/api/conversations/{conversation_id}/resolve", method="POST", json_body={},
            )
            self.assertTrue(status.startswith("200"), body)
            db = get_db(Path(tmp) / "data" / "openjob.db")
            try:
                row = get_conversation(db, conversation_id)
                self.assertEqual(row["handle_status"], "resolved")
                self.assertEqual(row["job_id"], "linked")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
