"""公司画像（Logo/简介）采集、回填与展示链路测试（公司形象增强方案 S5）。"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openjob.collection.base import CollectorHooks
from openjob.collection.company_profile import (
    absolute_company_url,
    clean_company_intro,
    logo_file_key,
    normalize_company_name,
    save_logo_file,
)
from openjob.collection.models import PlatformCollectionRequest
from openjob.collection.platforms.boss import BossBrowser, BossCollector
from openjob.company_enrich import run_company_enrich
from openjob.db import companies_missing_intro, get_db, insert_job_if_new, update_company_profile

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


class _NoWaitThrottle:
    def __init__(self, **_kwargs):
        pass

    def wait(self, _stop_event=None):
        return False


class CompanyProfileUnitTests(unittest.TestCase):
    def test_normalize_company_name_strips_whitespace(self):
        self.assertEqual(normalize_company_name("  美的\t集团 "), "美的集团")

    def test_logo_file_key_is_stable_hex(self):
        key = logo_file_key("美的")
        self.assertEqual(key, logo_file_key(" 美的 "))
        self.assertEqual(len(key), 16)
        self.assertNotEqual(key, logo_file_key("美的集团"))

    def test_clean_company_intro_collapses_and_strips_fold_suffix(self):
        cleaned = clean_company_intro(" 美的是一家\n 科技集团  展开 ")
        self.assertEqual(cleaned, "美的是一家 科技集团")
        # textContent 直取会混入区块标题词与折叠残留，一并剥离
        self.assertEqual(clean_company_intro(" 公司简介\n 美的是一家 科技集团 收起 "), "美的是一家 科技集团")
        self.assertEqual(clean_company_intro("很长的简介内容", max_chars=5), "很长的简介…")

    def test_absolute_company_url_rules(self):
        self.assertEqual(absolute_company_url("/gongsi/abc.html"), "https://www.zhipin.com/gongsi/abc.html")
        self.assertIsNone(absolute_company_url("/gongsi/job/abc.html"))
        self.assertIsNone(absolute_company_url("/i101304/"))
        self.assertIsNone(absolute_company_url(""))
        self.assertEqual(
            absolute_company_url("https://www.zhipin.com/gongsi/abc.html"),
            "https://www.zhipin.com/gongsi/abc.html",
        )

    def test_save_logo_file_sniffs_magic_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            relative = save_logo_file(tmp, "美的", PNG_BYTES, "png")
            self.assertEqual(relative, f"assets/logos/{logo_file_key('美的')}.png")
            target = Path(tmp) / relative
            self.assertTrue(target.exists())
            first_mtime = target.stat().st_mtime_ns
            save_logo_file(tmp, "美的", PNG_BYTES + b"changed", "png")
            self.assertEqual(target.stat().st_mtime_ns, first_mtime)
            # 魔数识别失败不落盘
            self.assertEqual(save_logo_file(tmp, "坏文件", b"<html>", "png"), "")


class CompanyProfileDbTests(unittest.TestCase):
    def test_migration_adds_company_profile_columns_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            conn = get_db(db_path)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            self.assertTrue({"company_logo_path", "company_logo_url", "company_intro", "company_intro_url"} <= columns)
            self.assertEqual(int(conn.execute("PRAGMA user_version").fetchone()[0]), 11)
            conn.close()
            conn = get_db(db_path)  # 二次打开幂等
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            self.assertIn("company_intro", columns)
            conn.close()

    def test_insert_job_if_new_persists_company_profile_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            record = _job_record("job-1", "美的", company_logo_path="assets/logos/a.png", company_intro="简介")
            self.assertTrue(insert_job_if_new(conn, record))
            row = dict(conn.execute("SELECT * FROM jobs WHERE id='job-1'").fetchone())
            self.assertEqual(row["company_logo_path"], "assets/logos/a.png")
            self.assertEqual(row["company_intro"], "简介")
            conn.close()

    def test_update_company_profile_coalesce_keeps_existing_logo_and_skips_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            insert_job_if_new(conn, _job_record("job-1", "美的", company_logo_path="assets/logos/keep.png"))
            insert_job_if_new(conn, _job_record("job-2", "美的"))
            from openjob.db import soft_delete_jobs

            soft_delete_jobs(conn, ["job-2"], confirmed=True, reason="test")
            rows = update_company_profile(
                conn, "美的", intro="简介", intro_url="https://www.zhipin.com/gongsi/a.html",
                logo_path="assets/logos/new.png", logo_url="https://img.bosszhipin.com/a.png",
            )
            self.assertEqual(rows, 1)  # 已删岗位不回填
            row = dict(conn.execute("SELECT * FROM jobs WHERE id='job-1'").fetchone())
            self.assertEqual(row["company_intro"], "简介")
            self.assertEqual(row["company_logo_path"], "assets/logos/keep.png")
            self.assertEqual(row["company_logo_url"], "https://img.bosszhipin.com/a.png")
            conn.close()

    def test_companies_missing_intro_lists_boss_rows_with_sample_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = get_db(Path(tmp) / "openjob.db")
            insert_job_if_new(conn, _job_record("job-a", "美的"))
            insert_job_if_new(conn, _job_record("job-b", "美的", company_intro="已有"))
            insert_job_if_new(conn, _job_record("job-c", "智联公司", source_platform="zhilian"))
            entries = companies_missing_intro(conn)
            self.assertEqual([entry["company"] for entry in entries], ["美的"])
            self.assertEqual(entries[0]["job_count"], 1)
            self.assertIn("job-a", entries[0]["sample_url"])
            conn.close()


class _EnrichBrowserHarness:
    """采集器公司画像测试的共用脚本化浏览器。"""

    def __init__(self, detail_by_url, intro_text=" 美的是一家 科技集团 展开 ", risk_kind=None):
        self.detail_by_url = detail_by_url
        self.intro_text = intro_text
        self.risk_kind = risk_kind
        self.nav_calls = []
        self.company_navs = []
        self.state = {"on_company": False}

    def evaluate(self, _target, script):
        # 顺序敏感：风险探测脚本同样包含 .job-card-wrap 选择器，必须最先判定
        if "geetest_panel" in script:
            if self.risk_kind and self.state["on_company"]:
                return json.dumps({"risk": self.risk_kind, "evidence": "e"})
            return json.dumps({"risk": None})
        if ".job-card-wrap" in script:
            return json.dumps([
                {"title": f"岗位{i}", "company": detail["company"], "salary": "10-15K",
                 "url": f"/job_detail/{key}"}
                for i, (key, detail) in enumerate(self.detail_by_url.items())
            ])
        if "公司简介|公司介绍|企业介绍" in script:
            return json.dumps({"intro": self.intro_text})
        if ".job-sec-text" in script:
            return json.dumps(self.detail_by_url[self._current_detail_key()])
        return json.dumps({"risk": None})

    def _current_detail_key(self):
        for url in self.detail_by_url:
            if url in self.nav_calls[-1]:
                return url
        return next(iter(self.detail_by_url))

    def browser(self):
        def navigate(_target, url):
            self.nav_calls.append(url)
            if "/gongsi/" in url:
                self.company_navs.append(url)
                self.state["on_company"] = True
            else:
                self.state["on_company"] = False
            return True

        return BossBrowser(
            new_tab=lambda _url, **_kwargs: "worker-tab",
            close_tab=lambda _target: True,
            evaluate=self.evaluate,
            navigate=navigate,
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )


class CollectorEnrichTests(unittest.TestCase):
    def _detail(self, company: str) -> dict:
        return {
            "title": "数据分析助理",
            "company": company,
            "jd": "负责业务数据看板",
            "company_logo": "https://img.bosszhipin.com/logo.png",
            "company_page_url": "/gongsi/test.html",
        }

    def _collect(self, harness, tmp, *, config=None, companies=None, safety_conn=None):
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

    def test_collector_captures_logo_and_intro_with_company_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EnrichBrowserHarness({
                "job-a.html": self._detail("美的"),
                "job-b.html": self._detail("美的"),
            })
            result, collected, fetch_mock = self._collect(harness, tmp)
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(collected), 2)
            for candidate in collected:
                self.assertEqual(candidate.company_intro, "美的是一家 科技集团")
                self.assertEqual(candidate.company_intro_url, "https://www.zhipin.com/gongsi/test.html")
                self.assertEqual(candidate.company_logo_path, f"assets/logos/{logo_file_key('美的')}.png")
                self.assertTrue((Path(tmp) / candidate.company_logo_path).exists())
            # 公司主页与 Logo 每公司只处理一次
            self.assertEqual(len(harness.company_navs), 1)
            self.assertEqual(fetch_mock.call_count, 1)

    def test_collector_reuses_stocked_company_profile(self):
        """库里已有同公司画像（旧岗位采过）→ 新岗位零额外访问：不进公司页、不重拉图。"""
        with tempfile.TemporaryDirectory() as tmp:
            logo_rel = f"assets/logos/{logo_file_key('美的')}.png"
            harness = _EnrichBrowserHarness({
                "job-a.html": self._detail("美的"),
                "job-b.html": self._detail("美的"),
            })
            db_path = Path(tmp) / "openjob.db"
            (Path(tmp) / logo_rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / logo_rel).write_bytes(PNG_BYTES)  # 库存 Logo 文件真实存在才复用
            safety_conn = get_db(db_path)
            try:
                insert_job_if_new(safety_conn, _job_record(
                    "stock-1", "美的",
                    company_intro="库内已有简介", company_intro_url="https://www.zhipin.com/gongsi/stock.html",
                    company_logo_path=logo_rel,
                ))
                result, collected, fetch_mock = self._collect(
                    harness, tmp, safety_conn=safety_conn,
                )
            finally:
                safety_conn.close()
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(collected), 2)
            for candidate in collected:
                self.assertEqual(candidate.company_intro, "库内已有简介")
                self.assertEqual(candidate.company_logo_path, logo_rel)
            # 零额外访问：不进公司主页、不重拉图床
            self.assertEqual(len(harness.company_navs), 0)
            fetch_mock.assert_not_called()
            # 库存 Logo 文件缺失时应回退重新拉取（_logo_file_exists 把关），正例已由上方覆盖。

    def test_collector_stock_logo_attached_without_fresh_url(self):
        """回归：库存 Logo 应独立挂取——详情页未暴露 Logo URL 时不得丢库存。"""
        with tempfile.TemporaryDirectory() as tmp:
            logo_rel = f"assets/logos/{logo_file_key('小鹏汽车')}.png"
            (Path(tmp) / logo_rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / logo_rel).write_bytes(PNG_BYTES)
            harness = _EnrichBrowserHarness({"job-a.html": {
                "title": "数据分析实习生", "company": "小鹏汽车", "jd": "负责数据看板",
                "company_logo": "",  # 本页恰好没暴露 Logo URL
                "company_page_url": "/gongsi/xpeng.html",
            }})
            safety_conn = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job_if_new(safety_conn, _job_record(
                    "old-xpeng", "小鹏汽车",
                    company_intro="库存简介", company_intro_url="https://www.zhipin.com/gongsi/xpeng.html",
                    company_logo_path=logo_rel,
                ))
                result, collected, fetch_mock = self._collect(harness, tmp, safety_conn=safety_conn)
            finally:
                safety_conn.close()
            self.assertEqual(result.status, "completed")
            self.assertEqual(collected[0].company_logo_path, logo_rel)
            self.assertEqual(collected[0].company_intro, "库存简介")
            fetch_mock.assert_not_called()
            self.assertEqual(len(harness.company_navs), 0)

    def test_collector_skips_company_page_when_intro_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EnrichBrowserHarness({"job-a.html": self._detail("美的")})
            result, collected, _fetch = self._collect(
                harness, tmp,
                config={"collection": {"enrich_intro": False}},
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(harness.company_navs), 0)
            self.assertEqual(collected[0].company_intro, "")
            self.assertEqual(collected[0].company_intro_url, "https://www.zhipin.com/gongsi/test.html")

    def test_collector_company_page_risk_blocks_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EnrichBrowserHarness({"job-a.html": self._detail("美的")}, risk_kind="captcha")
            result, collected, _fetch = self._collect(harness, tmp)
            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.reason_code, "captcha")
            self.assertEqual(collected, [])

    def test_collector_company_page_daily_limit_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EnrichBrowserHarness({
                "job-a.html": self._detail("美的"),
                "job-b.html": self._detail("格力"),
            })
            db_path = Path(tmp) / "openjob.db"
            safety_conn = get_db(db_path)
            try:
                config = {"collection": {"company_daily_page_limit": 1}}
                result, collected, _fetch = self._collect(
                    harness, tmp, config=config, safety_conn=safety_conn,
                )
            finally:
                safety_conn.close()
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(collected), 2)
            with_intro = [c for c in collected if c.company_intro]
            without_intro = [c for c in collected if not c.company_intro]
            self.assertEqual(len(with_intro), 1)
            self.assertEqual(len(without_intro), 1)
            self.assertTrue(db_path.exists())


class CompanyEnrichTests(unittest.TestCase):
    def _seed(self, tmp):
        conn = get_db(Path(tmp) / "openjob.db")
        insert_job_if_new(conn, _job_record("job-a", "美的"))
        insert_job_if_new(conn, _job_record("job-a2", "美的"))
        insert_job_if_new(conn, _job_record("job-b", "格力"))
        conn.close()

    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)
            new_tab_mock = Mock(side_effect=AssertionError("dry-run 不得打开页面"))
            with patch("openjob.company_enrich.new_tab", new_tab_mock):
                summary = run_company_enrich({}, Path(tmp) / "openjob.db", apply=False)
            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["companies"], 2)
            self.assertEqual(summary["jobs_covered"], 3)
            self.assertEqual(summary["estimated_pages"], 4)
            new_tab_mock.assert_not_called()
            conn = get_db(Path(tmp) / "openjob.db")
            row = dict(conn.execute("SELECT company_intro FROM jobs WHERE id='job-a'").fetchone())
            self.assertEqual(row["company_intro"] or "", "")
            conn.close()

    def test_apply_backfills_profiles_with_mocked_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)

            def evaluate(_target, script):
                if "公司简介|公司介绍|企业介绍" in script:
                    return json.dumps({"intro": " 美的是一家 科技集团 展开 "})
                if ".job-sec-text" in script:
                    return json.dumps({
                        "company_logo": "https://img.bosszhipin.com/logo.png",
                        "company_page_url": "/gongsi/test.html",
                    })
                return json.dumps({"risk": None})

            with patch("openjob.company_enrich.new_tab", return_value="worker"), \
                 patch("openjob.company_enrich.close_tab", return_value=True), \
                 patch("openjob.company_enrich.navigate", return_value=True), \
                 patch("openjob.company_enrich.wait_for_load", return_value=True), \
                 patch("openjob.company_enrich.evaluate", evaluate), \
                 patch("openjob.company_enrich.PageThrottle", _NoWaitThrottle), \
                 patch("openjob.company_enrich.time.sleep", lambda _s: None), \
                 patch("openjob.company_enrich.fetch_company_logo", return_value=(PNG_BYTES, "png")):
                summary = run_company_enrich({}, Path(tmp) / "openjob.db", apply=True)
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["enriched"], 2)
            conn = get_db(Path(tmp) / "openjob.db")
            rows = [dict(r) for r in conn.execute(
                "SELECT company_intro, company_logo_path FROM jobs WHERE company='美的' ORDER BY id"
            ).fetchall()]
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row["company_intro"], "美的是一家 科技集团")
                self.assertTrue(row["company_logo_path"].startswith("assets/logos/"))
                self.assertTrue((Path(tmp) / row["company_logo_path"]).exists())
            conn.close()

    def test_apply_survives_sample_page_failure(self):
        """样本详情页打开失败 → 记 failed 继续下一家（回归：failure_limit 曾因重构丢失而 NameError）。"""
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)
            calls = {"n": 0}

            def navigate(_target, url):
                calls["n"] += 1
                return calls["n"] > 1  # 第一家样本页失败，后续恢复

            def evaluate(_target, script):
                if "公司简介|公司介绍|企业介绍" in script:
                    return json.dumps({"intro": " 格力是一家制造集团 "})
                if ".job-sec-text" in script:
                    return json.dumps({"company_page_url": "/gongsi/gree.html"})
                return json.dumps({"risk": None})

            with patch("openjob.company_enrich.new_tab", return_value="worker"),                  patch("openjob.company_enrich.close_tab", return_value=True),                  patch("openjob.company_enrich.navigate", navigate),                  patch("openjob.company_enrich.wait_for_load", return_value=True),                  patch("openjob.company_enrich.evaluate", evaluate),                  patch("openjob.company_enrich.PageThrottle", _NoWaitThrottle),                  patch("openjob.company_enrich.time.sleep", lambda _s: None):
                summary = run_company_enrich({}, Path(tmp) / "openjob.db", apply=True)
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["enriched"], 1)
            conn = get_db(Path(tmp) / "openjob.db")
            row = dict(conn.execute("SELECT company_intro FROM jobs WHERE company='格力' LIMIT 1").fetchone())
            self.assertEqual(row["company_intro"], "格力是一家制造集团")
            conn.close()

    def test_apply_blocked_on_captcha(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)
            state = {"on_company": False}

            def navigate(_target, url):
                state["on_company"] = "/gongsi/" in url
                return True

            def evaluate(_target, script):
                if "geetest_panel" in script and state["on_company"]:
                    return json.dumps({"risk": "captcha", "evidence": "e"})
                if ".job-sec-text" in script:
                    return json.dumps({"company_page_url": "/gongsi/test.html"})
                return json.dumps({"risk": None})

            with patch("openjob.company_enrich.new_tab", return_value="worker"), \
                 patch("openjob.company_enrich.close_tab", return_value=True), \
                 patch("openjob.company_enrich.navigate", navigate), \
                 patch("openjob.company_enrich.wait_for_load", return_value=True), \
                 patch("openjob.company_enrich.evaluate", evaluate), \
                 patch("openjob.company_enrich.PageThrottle", _NoWaitThrottle), \
                 patch("openjob.company_enrich.time.sleep", lambda _s: None):
                summary = run_company_enrich({}, Path(tmp) / "openjob.db", apply=True)
            self.assertEqual(summary["status"], "blocked")
            self.assertEqual(summary["reason_code"], "captcha")
            conn = get_db(Path(tmp) / "openjob.db")
            events = conn.execute("SELECT event_type FROM risk_events").fetchall()
            self.assertTrue(any("captcha" in row[0] for row in events))
            conn.close()


class CompanyLogoRouteTests(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = None
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
            "REQUEST_METHOD": "GET",
            "PATH_INFO": path,
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
        chunks = self.server.app(environ, start_response)
        return captured["status"], b"".join(chunks)

    def test_serves_saved_logo_file(self):
        import shutil

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        logo_dir = Path(tmp) / "data" / "assets" / "logos"
        logo_dir.mkdir(parents=True)
        target = logo_dir / f"{logo_file_key('美的')}.png"
        target.write_bytes(PNG_BYTES)
        self.server.set_base_dir(tmp)
        status, body = self._wsgi_raw(f"/company-logos/{target.name}")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, PNG_BYTES)

    def test_rejects_non_logo_filenames(self):
        import shutil

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.server.set_base_dir(tmp)
        status, body = self._wsgi_raw("/company-logos/evil.html")
        self.assertEqual(status, "404 Not Found")
        self.assertIn(b"Not found", body)


if __name__ == "__main__":
    unittest.main()
