"""推荐页 collector 集成测试（推荐页计划 Task 2 / §11.2 核心场景）。"""

import json
import unittest
from threading import Event

from openjob.collection.base import CollectorHooks
from openjob.collection.models import PlatformCollectionRequest
from openjob.collection.platforms.boss import BossBrowser, BossCollector
from openjob.collection.platforms.boss_recommend import RECOMMEND_URL


class _NoWaitThrottle:
    def wait(self, _stop_event):
        return False


def _recommend_payload(card_url="/job_detail/rec-1.html", title="数据分析实习生"):
    return json.dumps({
        "cards": [{
            "title": title,
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


def _search_payload():
    return json.dumps([{
        "title": "数据分析实习生",
        "company": "示例公司",
        "url": "/job_detail/search-1.html",
    }])


def _detail_payload():
    return json.dumps({
        "title": "数据分析实习生",
        "company": "示例科技公司",
        "jd": "负责业务数据看板建设，需要 SQL 与 Python 能力。",
    })


def _browser_factory(*, evaluate_map, navigation_results=None, new_tab_urls=None):
    navigation_calls = []
    new_tab_calls = []
    close_calls = []
    nav_iter = iter(navigation_results) if navigation_results else None

    def evaluate(_target, script):
        for marker, payload in evaluate_map.items():
            if marker in script:
                return payload
        return json.dumps({"risk": None})

    def navigate(target, url):
        navigation_calls.append(url)
        if nav_iter is not None:
            return next(nav_iter)
        return True

    def new_tab(url, **_kwargs):
        new_tab_calls.append(url)
        return "worker-tab"

    browser = BossBrowser(
        new_tab=new_tab,
        close_tab=lambda _target: close_calls.append(_target) or True,
        evaluate=evaluate,
        navigate=navigate,
        scroll=lambda *_args, **_kwargs: True,
        wait_for_load=lambda *_args, **_kwargs: True,
    )
    return browser, new_tab_calls, navigation_calls, close_calls


def _hooks(collected, parse_failures=None, stop_event=None):
    return CollectorHooks(
        stop_event=stop_event,
        on_list_candidate=lambda _candidate: True,
        on_candidate=lambda candidate: collected.append(candidate) or True,
        on_parse_failed=(parse_failures or []).append,
        on_event=lambda **_kwargs: None,
    )


class RecommendationOnlyTests(unittest.TestCase):
    def test_recommendation_only_never_opens_search_url(self):
        browser, new_tab_calls, navigation_calls, close_calls = _browser_factory(
            evaluate_map={"item-boss": _recommend_payload(), ".job-sec-text": _detail_payload()},
        )
        collected = []
        request = PlatformCollectionRequest(
            "boss", [], [], {}, max_pages=3,
            source_channels=["recommendation"],
            recommendation_max_scrolls=1,
        )
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kwargs: _NoWaitThrottle(),
            sleep=lambda _seconds: None,
            randint=lambda _low, _high: 1,
        ).collect(request, _hooks(collected))

        self.assertTrue(all(RECOMMEND_URL in url for url in new_tab_calls), new_tab_calls)
        self.assertEqual(len(collected), 1)
        candidate = collected[0]
        self.assertEqual(candidate.source_channel, "recommendation")
        self.assertEqual(candidate.source_keyword, "")
        self.assertEqual(candidate.title, "数据分析实习生")
        self.assertIn("SQL", candidate.jd)
        # 计划书 §6.5：轮次跑满（max_scrolls=1）→ completed_with_shortage，非伪装成功
        self.assertEqual(result.status, "completed_with_shortage")
        self.assertEqual(result.reason_code, "recommendation_scroll_limit")
        self.assertEqual(close_calls, ["worker-tab"])
        self.assertIn("recommendation", result.source_results)

    def test_search_and_recommendation_share_worker_and_close_once(self):
        browser, new_tab_calls, navigation_calls, close_calls = _browser_factory(
            evaluate_map={
                "item-boss": _recommend_payload(card_url="/job_detail/rec-2.html"),
                ".job-card-wrap": _search_payload(),
                ".job-sec-text": _detail_payload(),
            },
        )
        collected = []
        request = PlatformCollectionRequest(
            "boss", ["数据分析实习"], ["北京"], {"北京": "101010100"}, max_pages=1,
            source_channels=["search", "recommendation"],
            recommendation_max_scrolls=1,
        )
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kwargs: _NoWaitThrottle(),
            sleep=lambda _seconds: None,
            randint=lambda _low, _high: 1,
        ).collect(request, _hooks(collected))

        self.assertEqual(len(new_tab_calls), 1)  # 搜索开 tab，推荐复用 navigate
        self.assertTrue(any("/web/geek/job?" in url for url in new_tab_calls))
        self.assertTrue(any("/web/geek/recommend" in url for url in navigation_calls))
        self.assertEqual(len(collected), 2)
        self.assertEqual({c.source_channel for c in collected}, {"search", "recommendation"})
        self.assertEqual(len(close_calls), 1)  # close_tab 全程一次
        # 推荐轮次跑满 → 总体 completed_with_shortage（计划书 §6.5），但两来源均已采集
        self.assertEqual(result.status, "completed_with_shortage")
        self.assertEqual(result.reason_code, "recommendation_scroll_limit")
        self.assertEqual(
            set(result.source_results.keys()), {"search", "recommendation"},
        )

    def test_parser_unsupported_reports_shortage_without_fake_success(self):
        browser, _new, _nav, _close = _browser_factory(
            evaluate_map={"item-boss": "<html>unexpected</html>", ".job-sec-text": _detail_payload()},
        )
        collected = []
        parse_failures = []
        request = PlatformCollectionRequest(
            "boss", [], [], {}, source_channels=["recommendation"],
            recommendation_max_scrolls=1,
        )
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kwargs: _NoWaitThrottle(),
            sleep=lambda _seconds: None,
            randint=lambda _low, _high: 1,
        ).collect(request, _hooks(collected, parse_failures))

        self.assertEqual(result.status, "completed_with_shortage")
        self.assertEqual(result.reason_code, "recommendation_parser_unsupported")
        self.assertEqual(collected, [])
        self.assertNotIn("recommendation_feed_exhausted", result.reason_code)

    def test_two_rounds_without_new_cards_stop_safely(self):
        evaluate_map = {"item-boss": _recommend_payload(), ".job-sec-text": _detail_payload()}
        browser, _new, navigation_calls, _close = _browser_factory(evaluate_map=evaluate_map)
        collected = []
        request = PlatformCollectionRequest(
            "boss", [], [], {}, source_channels=["recommendation"],
            recommendation_max_scrolls=5,
            recommendation_same_result_limit=2,
        )
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kwargs: _NoWaitThrottle(),
            sleep=lambda _seconds: None,
            randint=lambda _low, _high: 1,
        ).collect(request, _hooks(collected))

        self.assertEqual(result.status, "completed_with_shortage")
        self.assertEqual(result.reason_code, "recommendation_no_new_cards")
        # 每轮同一 URL（fixture 固定卡片）→ 连续两轮无新增即停，未跑满 5 轮
        recommend_navigations = [url for url in navigation_calls if "/web/geek/recommend" in url]
        self.assertLessEqual(len(recommend_navigations), 3)

    def test_card_limit_stops_pagination(self):
        payloads = iter([
            _recommend_payload(card_url="/job_detail/rec-p1.html"),
            _recommend_payload(card_url="/job_detail/rec-p2.html"),
            _recommend_payload(card_url="/job_detail/rec-p3.html"),
        ])
        browser, _new, navigation_calls, _close = _browser_factory(
            evaluate_map={"item-boss": lambda: next(payloads), ".job-sec-text": _detail_payload()},
        )
        # evaluate_map 的 lambda 不支持——改为闭包计数
        state = {"round": 0}

        def evaluate(_target, script):
            if "item-boss" in script:
                state["round"] += 1
                return _recommend_payload(card_url=f"/job_detail/rec-p{state['round']}.html")
            if ".job-sec-text" in script:
                return _detail_payload()
            return json.dumps({"risk": None})

        browser2 = BossBrowser(
            new_tab=lambda url, **_kw: "worker-tab",
            close_tab=lambda _t: True,
            evaluate=evaluate,
            navigate=lambda _t, _u: True,
            scroll=lambda *_a, **_k: True,
            wait_for_load=lambda *_a, **_k: True,
        )
        collected = []
        request = PlatformCollectionRequest(
            "boss", [], [], {}, source_channels=["recommendation"],
            recommendation_max_scrolls=5, recommendation_max_cards=2,
        )
        result = BossCollector(
            browser=browser2,
            throttle_factory=lambda **_kwargs: _NoWaitThrottle(),
            sleep=lambda _seconds: None,
            randint=lambda _low, _high: 1,
        ).collect(request, _hooks(collected))

        self.assertEqual(result.status, "completed_with_shortage")
        self.assertEqual(result.reason_code, "recommendation_card_limit")
        self.assertLessEqual(len(collected), 3)

    def test_stop_event_ends_recommendation_immediately(self):
        stop_event = Event()
        stop_event.set()
        browser, _new, _nav, _close = _browser_factory(
            evaluate_map={"item-boss": _recommend_payload(), ".job-sec-text": _detail_payload()},
        )
        collected = []
        request = PlatformCollectionRequest(
            "boss", [], [], {}, source_channels=["recommendation"],
            recommendation_max_scrolls=2,
        )
        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kwargs: _NoWaitThrottle(),
            sleep=lambda _seconds: None,
            randint=lambda _low, _high: 1,
        ).collect(request, _hooks(collected, stop_event=stop_event))

        self.assertEqual(result.status, "stopped")
        self.assertEqual(collected, [])


if __name__ == "__main__":
    unittest.main()
