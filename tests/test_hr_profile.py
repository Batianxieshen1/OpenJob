"""HR 画像（招聘者形象）测试：姓名/职位/活跃选择器修复的透传、头像落盘、库存复用、
样本页快照写回、/hr-avatars 路由、/api/hr-stats 本地聚合。"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.collection.base import CollectorHooks
from openjob.collection.company_profile import hr_avatar_file_key, save_hr_avatar_file
from openjob.collection.models import PlatformCollectionRequest
from openjob.collection.platforms.boss import BossBrowser, BossCollector
from openjob.db import get_db, insert_job_if_new, update_job_hr_snapshot

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _job_record(job_id: str, company: str, **overrides) -> dict:
    record = {
        "id": job_id,
        "title": "数据分析助理",
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


def _detail(company: str, hr_name: str = "王女士", **overrides) -> dict:
    detail = {
        "title": "数据分析助理",
        "company": company,
        "jd": "负责业务数据看板",
        "hr_name": hr_name,
        "hr_title": "招聘经理",
        "hr_active": "刚刚活跃",
        "hr_avatar": "https://img.bosszhipin.com/avatar/hr.jpg",
    }
    detail.update(overrides)
    return detail


class _NoWaitThrottle:
    def __init__(self, **_kwargs):
        pass

    def wait(self, _stop_event=None):
        return False


class _Harness:
    """最小脚本化浏览器：列表页两岗、详情按 URL 取、风险恒无。"""

    def __init__(self, details):
        self.details = details  # {"job-x.html": detail_dict}
        self.company_navs = []
        self.nav_calls = []

    def evaluate(self, _target, script):
        if ".job-card-wrap" in script:
            return json.dumps([
                {"title": f"岗位{i}", "company": d["company"], "salary": "10-15K",
                 "url": f"/job_detail/{key}"}
                for i, (key, d) in enumerate(self.details.items())
            ])
        if ".job-sec-text" in script:
            return json.dumps(self.details[self._current_key()])
        return json.dumps({"risk": None})

    def _current_key(self):
        for key in self.details:
            if key in self.nav_calls[-1]:
                return key
        return next(iter(self.details))

    def browser(self):
        def navigate(_target, url):
            self.nav_calls.append(url)
            if "/gongsi/" in url and "/gongsi/job/" not in url:
                self.company_navs.append(url)
            return True

        return BossBrowser(
            new_tab=lambda _url, **_kwargs: "worker-tab",
            close_tab=lambda _target: True,
            evaluate=self.evaluate,
            navigate=navigate,
            scroll=lambda *_a, **_k: True,
            wait_for_load=lambda *_a, **_k: True,
        )


def _collect(harness, tmp, safety_conn=None, config=None):
    collected = []
    hooks = CollectorHooks(
        stop_event=None,
        on_list_candidate=lambda _candidate: True,
        on_candidate=lambda candidate: collected.append(candidate) or True,
        on_parse_failed=lambda reason: None,
        on_event=lambda **_kwargs: None,
    )
    with patch("openjob.collection.platforms.boss.fetch_company_logo",
               return_value=(PNG_BYTES, "png")) as fetch_mock, \
         patch("openjob.collection.platforms.boss.quick_score", return_value=(1, "")):
        result = BossCollector(
            browser=harness.browser(),
            throttle_factory=lambda **_kwargs: _NoWaitThrottle(),
            sleep=lambda _seconds: None,
            randint=lambda _low, _high: 1,
            config=config or {},
            safety_conn=safety_conn,
            data_dir=tmp,
        ).collect(
            PlatformCollectionRequest("boss", ["数据分析"], ["北京"], {"北京": "101010100"}, max_pages=1),
            hooks,
        )
    return result, collected, fetch_mock


class HrProfileUnitTests(unittest.TestCase):
    def test_hr_avatar_file_key_stable_and_distinct(self):
        self.assertEqual(hr_avatar_file_key("美的", "王女士"), hr_avatar_file_key(" 美的 ", " 王女士 "))
        self.assertNotEqual(hr_avatar_file_key("美的", "王女士"), hr_avatar_file_key("美的", "李女士"))

    def test_save_hr_avatar_file_magic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = save_hr_avatar_file(tmp, "美的", "王女士", PNG_BYTES, "png")
            self.assertEqual(rel, f"assets/hr_avatars/{hr_avatar_file_key('美的', '王女士')}.png")
            self.assertTrue((Path(tmp) / rel).exists())
            self.assertEqual(save_hr_avatar_file(tmp, "美的", "王女士", b"<html>", "png"), "")

    def test_v12_insert_persists_hr_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
                self.assertTrue({"hr_avatar_path", "hr_avatar_url"} <= columns)
                self.assertTrue(insert_job_if_new(conn, _job_record(
                    "hr-1", "美的", hr_name="王女士", hr_avatar_path="assets/hr_avatars/a.png")))
                row = dict(conn.execute("SELECT hr_name, hr_avatar_path FROM jobs WHERE id='hr-1'").fetchone())
                self.assertEqual(row["hr_name"], "王女士")
                self.assertEqual(row["hr_avatar_path"], "assets/hr_avatars/a.png")
            finally:
                conn.close()

    def test_update_job_hr_snapshot_fills_empty_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job_if_new(conn, _job_record("snap-1", "美的", hr_name="旧HR"))
                self.assertTrue(update_job_hr_snapshot(
                    conn, "snap-1", hr_name="新HR", hr_title="招聘经理", hr_active="今日活跃",
                    hr_avatar_path="assets/hr_avatars/b.png",
                ))
                row = dict(conn.execute("SELECT hr_name, hr_avatar_path FROM jobs WHERE id='snap-1'").fetchone())
                self.assertEqual(row["hr_name"], "旧HR")
                self.assertEqual(row["hr_avatar_path"], "assets/hr_avatars/b.png")
            finally:
                conn.close()


class HrCollectorTests(unittest.TestCase):
    def test_fresh_hr_fields_and_avatar_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _Harness({"job-a.html": _detail("美的")})
            result, collected, _fetch = _collect(harness, tmp)
            self.assertEqual(result.status, "completed")
            self.assertEqual(collected[0].hr_name, "王女士")
            self.assertEqual(collected[0].hr_title, "招聘经理")
            self.assertEqual(collected[0].hr_active, "刚刚活跃")
            self.assertEqual(
                collected[0].hr_avatar_path,
                f"assets/hr_avatars/{hr_avatar_file_key('美的', '王女士')}.png",
            )
            self.assertTrue((Path(tmp) / collected[0].hr_avatar_path).exists())

    def test_stock_hr_avatar_reused_without_fresh_url(self):
        """库存（公司+姓名）命中 → 零图床请求；本页未暴露头像 URL 也不丢头像。"""
        with tempfile.TemporaryDirectory() as tmp:
            avatar_rel = f"assets/hr_avatars/{hr_avatar_file_key('美的', '王女士')}.png"
            (Path(tmp) / avatar_rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / avatar_rel).write_bytes(PNG_BYTES)
            harness = _Harness({"job-a.html": _detail("美的", hr_avatar="")})
            safety_conn = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job_if_new(safety_conn, _job_record(
                    "hr-stock", "美的", hr_name="王女士", hr_avatar_path=avatar_rel))
                result, collected, fetch_mock = _collect(harness, tmp, safety_conn=safety_conn)
            finally:
                safety_conn.close()
            self.assertEqual(result.status, "completed")
            self.assertEqual(collected[0].hr_avatar_path, avatar_rel)
            fetch_mock.assert_not_called()


class HrAvatarRouteTests(unittest.TestCase):
    def setUp(self):
        from openjob.web import server

        self.server = server
        self.original_base_dir = server.BASE_DIR

    def tearDown(self):
        self.server.set_base_dir(self.original_base_dir)

    def _wsgi_raw(self, path):
        captured = {"status": ""}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status

        environ = {
            "REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "",
            "SERVER_NAME": "localhost", "SERVER_PORT": "8686", "SERVER_PROTOCOL": "HTTP/1.1",
            "CONTENT_LENGTH": "0", "CONTENT_TYPE": "",
            "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(b""), "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False, "wsgi.multiprocess": False, "wsgi.run_once": False,
        }
        chunks = self.server.app(environ, start_response)
        return captured["status"], b"".join(chunks)

    def test_serves_avatar_and_rejects_bad_names(self):
        import shutil

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        avatar_dir = Path(tmp) / "data" / "assets" / "hr_avatars"
        avatar_dir.mkdir(parents=True)
        target = avatar_dir / f"{hr_avatar_file_key('美的', '王女士')}.png"
        target.write_bytes(PNG_BYTES)
        self.server.set_base_dir(tmp)
        status, body = self._wsgi_raw(f"/hr-avatars/{target.name}")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, PNG_BYTES)
        status, _ = self._wsgi_raw("/hr-avatars/evil.html")
        self.assertEqual(status, "404 Not Found")


class HrStatsApiTests(unittest.TestCase):
    def test_aggregates_local_conversations(self):
        from openjob.db import upsert_conversation
        from openjob.web import server

        original = server.BASE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                import yaml

                (base / "config.yaml").write_text("profile: {}\n", encoding="utf-8")
                db = get_db(base / "data" / "openjob.db")
                insert_job_if_new(db, _job_record("hs-1", "美的"))
                for i in range(2):
                    upsert_conversation(db, platform="boss", fingerprint=f"fp-{i}", hr_name="王女士",
                                        company="美的", snippet="你好", job_id="hs-1",
                                        match_status="matched")
                db.close()
                server.set_base_dir(base)
                captured = {}

                def start_response(status, headers, exc_info=None):
                    captured["status"] = status

                from urllib.parse import quote

                qs = f"company={quote('美的')}&hr_name={quote('王女士')}"
                environ = {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/api/hr-stats",
                    "QUERY_STRING": qs,
                    "SERVER_NAME": "localhost", "SERVER_PORT": "8686", "SERVER_PROTOCOL": "HTTP/1.1",
                    "CONTENT_LENGTH": "0", "CONTENT_TYPE": "",
                    "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
                    "wsgi.input": io.BytesIO(b""), "wsgi.errors": io.StringIO(),
                    "wsgi.multithread": False, "wsgi.multiprocess": False, "wsgi.run_once": False,
                }
                chunks = server.app(environ, start_response)
                body = json.loads(b"".join(chunks).decode("utf-8"))
                self.assertTrue(captured["status"].startswith("200"))
                self.assertEqual(body["messages"], 2)
                self.assertIn("last_at", body)
        finally:
            server.set_base_dir(original)


if __name__ == "__main__":
    unittest.main()
