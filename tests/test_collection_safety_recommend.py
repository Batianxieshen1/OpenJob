"""推荐页安全预算测试（推荐页计划 Task 4 / §11.4 核心场景）。"""

import tempfile
import unittest
from pathlib import Path

from openjob.collection.platforms.boss import BossBrowser, BossCollector
from openjob.collection.base import CollectorHooks
from openjob.collection.models import PlatformCollectionRequest
from openjob.collection.platforms.boss_recommend import RECOMMEND_URL
from openjob.db import (
    add_platform_access,
    count_platform_access_today,
    get_db,
    get_active_platform_safety_lock,
)
from openjob.platform_safety import PlatformAccessGuard, PlatformSafetyStop


class _NoWaitThrottle:
    def wait(self, _stop_event):
        return False


def _recommend_payload(card_url="/job_detail/rec-1.html"):
    import json

    return json.dumps({
        "cards": [{
            "title": "数据分析实习生",
            "salary": "150-200元/天",
            "experience": "在校/应届",
            "education": "本科",
            "company": "示例科技公司",
            "company_meta": "",
            "hr_name": "示例女士",
            "hr_title": "HR",
            "location": "广州·天河区",
            "url": card_url,
        }],
        "page_state": {"has_expected_content": True, "is_end_of_feed": False, "end_markers": []},
    })


def _detail_payload():
    import json

    return json.dumps({
        "title": "数据分析实习生",
        "company": "示例科技公司",
        "jd": "负责业务数据看板建设。",
    })


def _hooks(collected):
    return CollectorHooks(
        stop_event=None,
        on_list_candidate=lambda _c: True,
        on_candidate=lambda c: collected.append(c) or True,
        on_parse_failed=lambda reason: None,
        on_event=lambda **_kwargs: None,
    )


class RecommendationPageBudgetTests(unittest.TestCase):
    """推荐页独立额度：到达后不再打开下一页（每次分页都 reserve）。"""

    def test_recommendation_daily_limit_stops_before_next_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            # 预置 10 次推荐页访问（默认 daily_recommendation_page_limit=10）
            for _ in range(10):
                add_platform_access(db, "collection", "recommendation_page", platform="boss")
            db.close()

            rounds = {"n": 0}

            def evaluate(_target, script):
                if "item-boss" in script:
                    rounds["n"] += 1
                    return _recommend_payload(card_url=f"/job_detail/rec-r{rounds['n']}.html")
                if ".job-sec-text" in script:
                    return _detail_payload()
                import json as _json

                return _json.dumps({"risk": None})

            browser = BossBrowser(
                new_tab=lambda _url, **_kw: "worker-tab",
                close_tab=lambda _t: True,
                evaluate=evaluate,
                navigate=lambda _t, _u: True,
                scroll=lambda *_a, **_k: True,
                wait_for_load=lambda *_a, **_k: True,
            )
            collected = []
            request = PlatformCollectionRequest(
                "boss", [], [], {}, source_channels=["recommendation"],
                recommendation_max_scrolls=5,
            )
            guard_db = get_db(db_path)
            try:
                result = BossCollector(
                    browser=browser,
                    throttle_factory=lambda **_k: _NoWaitThrottle(),
                    sleep=lambda _s: None,
                    randint=lambda _lo, _hi: 1,
                    config={"collection": {"daily_recommendation_page_limit": 10}},
                    safety_conn=guard_db,
                ).collect(request, _hooks(collected))
            finally:
                guard_db.close()

            self.assertEqual(result.status, "completed_with_shortage")
            self.assertEqual(result.reason_code, "daily_recommendation_page_limit")
            self.assertEqual(rounds["n"], 0)  # 额度已满：一次都不打开

    def test_global_budget_prioritized_over_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            # 全局额度只剩 0
            for _ in range(500):
                add_platform_access(db, "collection", "search_page", platform="boss")
            db.close()
            browser = BossBrowser(
                new_tab=lambda _url, **_kw: "worker-tab",
                close_tab=lambda _t: True,
                evaluate=lambda _t, _s: "{}",
                navigate=lambda _t, _u: True,
                scroll=lambda *_a, **_k: True,
                wait_for_load=lambda *_a, **_k: True,
            )
            collected = []
            request = PlatformCollectionRequest(
                "boss", [], [], {}, source_channels=["recommendation"],
                recommendation_max_scrolls=2,
            )
            guard_db = get_db(db_path)
            try:
                result = BossCollector(
                    browser=browser,
                    throttle_factory=lambda **_k: _NoWaitThrottle(),
                    sleep=lambda _s: None,
                    randint=lambda _lo, _hi: 1,
                    config={},
                    safety_conn=guard_db,
                ).collect(request, _hooks(collected))
            finally:
                guard_db.close()
            self.assertEqual(result.reason_code, "daily_platform_page_limit")
            self.assertEqual(collected, [])

    def test_detail_budget_shared_across_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            guard = PlatformAccessGuard(db, {"safety": {"daily_platform_page_limit": 500}}, "collection")
            # 详情额度 150 全部耗尽
            for _ in range(150):
                guard.reserve("detail_page", daily_limit=150)
            db.close()

            def evaluate(_target, script):
                if "item-boss" in script:
                    return _recommend_payload()
                if ".job-sec-text" in script:
                    return _detail_payload()
                import json as _json

                return _json.dumps({"risk": None})

            browser = BossBrowser(
                new_tab=lambda _url, **_kw: "worker-tab",
                close_tab=lambda _t: True,
                evaluate=evaluate,
                navigate=lambda _t, _u: True,
                scroll=lambda *_a, **_k: True,
                wait_for_load=lambda *_a, **_k: True,
            )
            collected = []
            request = PlatformCollectionRequest(
                "boss", [], [], {}, source_channels=["recommendation"],
                recommendation_max_scrolls=2,
            )
            guard_db = get_db(db_path)
            try:
                result = BossCollector(
                    browser=browser,
                    throttle_factory=lambda **_k: _NoWaitThrottle(),
                    sleep=lambda _s: None,
                    randint=lambda _lo, _hi: 1,
                    config={},
                    safety_conn=guard_db,
                ).collect(request, _hooks(collected))
            finally:
                guard_db.close()
            self.assertEqual(result.reason_code, "daily_detail_page_limit")
            self.assertEqual(collected, [])


class RecommendationRiskBehaviorTests(unittest.TestCase):
    def _browser(self, risk_payload):
        return BossBrowser(
            new_tab=lambda _url, **_kw: "worker-tab",
            close_tab=lambda _t: True,
            evaluate=lambda _t, script: risk_payload if "risk" in script or "captcha" in script or "风险" in script or "safety" in script or len(script) < 2000 and "item-boss" not in script and "job-sec-text" not in script else (_recommend_payload() if "item-boss" in script else _detail_payload()),
            navigate=lambda _t, _u: True,
            scroll=lambda *_a, **_k: True,
            wait_for_load=lambda *_a, **_k: True,
        )

    def test_recommendation_risk_blocks_and_records_lock(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            lock_db = get_db(db_path)

            def evaluate(_target, script):
                # confirm_risk 用 JS_DETECT_COLLECTION_RISK（含 JS_DETECT 关键片段）
                if "hasExpectedContent" in script:
                    return _json.dumps({"risk": "captcha", "evidence": "captcha_element"})
                if "item-boss" in script:
                    return _recommend_payload()
                if ".job-sec-text" in script:
                    return _detail_payload()
                return _json.dumps({"risk": None})

            browser = BossBrowser(
                new_tab=lambda _url, **_kw: "worker-tab",
                close_tab=lambda _t: True,
                evaluate=evaluate,
                navigate=lambda _t, _u: True,
                scroll=lambda *_a, **_k: True,
                wait_for_load=lambda *_a, **_k: True,
            )
            collected = []
            request = PlatformCollectionRequest(
                "boss", [], [], {}, source_channels=["recommendation"],
                recommendation_max_scrolls=2,
            )
            result = BossCollector(
                browser=browser,
                throttle_factory=lambda **_k: _NoWaitThrottle(),
                sleep=lambda _s: None,
                randint=lambda _lo, _hi: 1,
                config={"safety": {"risk_lock_minutes": 5}},
                safety_conn=lock_db,
            ).collect(request, _hooks(collected))

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.reason_code, "captcha")
            lock = get_active_platform_safety_lock(lock_db)
            self.assertIsNotNone(lock)
            lock_db.close()

    def test_parser_unsupported_does_not_create_risk_lock(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            lock_db = get_db(db_path)

            def evaluate(_target, script):
                if "item-boss" in script:
                    return "<html>unexpected structure</html>"
                if "hasExpectedContent" in script:
                    return _json.dumps({"risk": None})
                return _json.dumps({"risk": None})

            browser = BossBrowser(
                new_tab=lambda _url, **_kw: "worker-tab",
                close_tab=lambda _t: True,
                evaluate=evaluate,
                navigate=lambda _t, _u: True,
                scroll=lambda *_a, **_k: True,
                wait_for_load=lambda *_a, **_k: True,
            )
            request = PlatformCollectionRequest(
                "boss", [], [], {}, source_channels=["recommendation"],
                recommendation_max_scrolls=1,
            )
            result = BossCollector(
                browser=browser,
                throttle_factory=lambda **_k: _NoWaitThrottle(),
                sleep=lambda _s: None,
                randint=lambda _lo, _hi: 1,
                config={},
                safety_conn=lock_db,
            ).collect(request, _hooks([]))

            self.assertEqual(result.status, "completed_with_shortage")
            self.assertEqual(result.reason_code, "recommendation_parser_unsupported")
            self.assertIsNone(get_active_platform_safety_lock(lock_db))
            lock_db.close()

    def test_risk_lock_survives_new_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            guard = PlatformAccessGuard(db, {"safety": {"risk_lock_minutes": 30}}, "collection")
            guard.lock("captcha", minutes=30)
            db.close()
            fresh = get_db(db_path)
            lock = get_active_platform_safety_lock(fresh)
            fresh.close()
            self.assertIsNotNone(lock)


if __name__ == "__main__":
    unittest.main()
