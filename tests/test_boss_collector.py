import json
import unittest

from openjob.collection.base import CollectorHooks
from openjob.collection.models import PlatformCollectionRequest
from openjob.collection.platforms.boss import BossBrowser, BossCollector


class _NoWaitThrottle:
    def wait(self, _stop_event):
        return False


class BossCollectorTests(unittest.TestCase):
    def test_detail_page_open_is_retried_once_before_recording_parse_failure(self):
        navigation_results = iter([False, True])
        navigation_calls = []
        collected = []
        parse_failures = []

        def evaluate(_target, script):
            if ".job-card-wrap" in script:
                return json.dumps([{
                    "title": "数据分析实习生",
                    "company": "示例公司",
                    "url": "/job_detail/example-job.html",
                }])
            if ".job-sec-text" in script:
                return json.dumps({
                    "title": "数据分析实习生",
                    "company": "示例公司",
                    "jd": "负责数据分析与报表整理",
                })
            return json.dumps({"risk": None})

        browser = BossBrowser(
            new_tab=lambda _url, **_kwargs: "worker-tab",
            close_tab=lambda _target: True,
            evaluate=evaluate,
            navigate=lambda target, url: navigation_calls.append((target, url)) or next(navigation_results),
            scroll=lambda *_args, **_kwargs: True,
            wait_for_load=lambda *_args, **_kwargs: True,
        )
        hooks = CollectorHooks(
            stop_event=None,
            on_list_candidate=lambda _candidate: True,
            on_candidate=lambda candidate: collected.append(candidate) or False,
            on_parse_failed=parse_failures.append,
            on_event=lambda **_kwargs: None,
        )

        result = BossCollector(
            browser=browser,
            throttle_factory=lambda **_kwargs: _NoWaitThrottle(),
            sleep=lambda _seconds: None,
            randint=lambda _low, _high: 1,
        ).collect(
            PlatformCollectionRequest("boss", ["数据分析实习"], ["北京"], {"北京": "101010100"}, max_pages=1),
            hooks,
        )

        self.assertEqual(result.reason_code, "callback_stopped")
        self.assertEqual(len(navigation_calls), 2)
        self.assertEqual(len(collected), 1)
        self.assertEqual(parse_failures, [])


if __name__ == "__main__":
    unittest.main()
