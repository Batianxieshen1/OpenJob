import tempfile
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from openjob.collection.base import CollectorHooks
from openjob.collection.models import JobCandidate, PlatformCollectionResult
from openjob.collection.orchestrator import CollectionOrchestrator, normalize_collection_options
from openjob.collection.registry import CollectorRegistry
from openjob.db import get_db, insert_job


def _candidate(platform: str, source_id: str, title: str = "正常岗位") -> JobCandidate:
    return JobCandidate(
        platform=platform,
        source_job_id=source_id,
        title=title,
        company="示例公司",
        city="北京",
        city_code="530" if platform == "zhilian" else "101010100",
        jd="负责岗位相关工作",
        url=f"https://example.test/{platform}/{source_id}",
    )


def _options(*, order=None, auto_score=False):
    order = order or ["boss"]
    values = {}
    for platform in order:
        values[platform] = {
            "keywords": ["AI"],
            "cities": ["北京"],
            "city_codes": {"北京": "530"} if platform == "zhilian" else {"北京": "101010100"},
            "max_pages": 1,
            "sort": "default",
        }
    return {"platform_order": order, "auto_score": auto_score, "platforms": values}


class _FakeCollector:
    def __init__(self, platform, events, candidates, *, stop=False):
        self.platform = platform
        self.events = events
        self.candidates = candidates
        self.stop = stop

    def collect(self, _request, hooks: CollectorHooks):
        self.events.append(f"start:{self.platform}")
        for candidate in self.candidates:
            if hooks.stop_event and hooks.stop_event.is_set():
                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
            if not hooks.on_list_candidate(candidate):
                continue
            if not hooks.on_candidate(candidate):
                self.events.append(f"target:{self.platform}")
                return PlatformCollectionResult(self.platform, "completed", "target_reached", "达到目标")
            if self.stop:
                hooks.stop_event.set()
                return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "无更多结果")


class CollectionOrchestratorTests(TestCase):
    def test_two_platforms_are_strictly_serial_and_save_only_new_rows(self):
        events = []
        boss_candidates = [
            _candidate("boss", "duplicate"),
            _candidate("boss", "filtered", "包含黑名单词岗位"),
            _candidate("boss", "save-fail"),
            _candidate("boss", "boss-new-1"),
            _candidate("boss", "boss-new-2"),
        ]
        zhilian_candidates = [_candidate("zhilian", "zl-new-1"), _candidate("zhilian", "zl-new-2")]
        registry = CollectorRegistry({
            "boss": lambda: _FakeCollector("boss", events, boss_candidates),
            "zhilian": lambda: _FakeCollector("zhilian", events, zhilian_candidates),
        })
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            db = get_db(db_path)
            try:
                insert_job(db, {**_candidate("boss", "duplicate").as_job_record()})
            finally:
                db.close()
            config = {"profile": {"deal_breakers": ["黑名单词"]}}
            real_insert = __import__("openjob.db", fromlist=["insert_job_if_new"]).insert_job_if_new

            def insert(record_conn, record):
                if record.get("source_job_id") == "save-fail":
                    raise RuntimeError("fixture save failure")
                return real_insert(record_conn, record)

            with patch("openjob.collection.orchestrator.insert_job_if_new", side_effect=insert):
                result = CollectionOrchestrator(config, db_path=db_path, registry=registry).run(
                    _options(order=["boss", "zhilian"])
                )

            db = get_db(db_path)
            try:
                rows = db.execute("SELECT id, source_platform FROM jobs ORDER BY id").fetchall()
            finally:
                db.close()

        self.assertEqual(events, ["start:boss", "start:zhilian"])
        self.assertEqual(result["platforms"]["boss"]["new"], 2)
        self.assertEqual(result["platforms"]["boss"]["duplicate"], 1)
        self.assertEqual(result["platforms"]["boss"]["filtered"], 1)
        self.assertEqual(result["platforms"]["boss"]["save_failed"], 1)
        self.assertEqual(result["platforms"]["zhilian"]["new"], 2)
        self.assertEqual(result["collected_job_ids"], ["boss-new-1", "boss-new-2", "zhilian:zl-new-1", "zhilian:zl-new-2"])
        self.assertEqual({row["source_platform"] for row in rows}, {"boss", "zhilian"})

    def test_auto_score_is_opt_in_and_receives_only_this_run_ids(self):
        candidates = [_candidate("boss", "new-1")]
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", [], candidates)})
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "collection.db"
            with patch("openjob.ai.scorer.score_jobs") as score_jobs:
                result = CollectionOrchestrator({}, db_path=db_path, registry=registry).run(
                    _options(auto_score=True)
                )
        score_jobs.assert_called_once()
        kwargs = score_jobs.call_args.kwargs
        self.assertEqual(kwargs["scope"], "selected")
        self.assertEqual(kwargs["job_ids"], ["new-1"])
        self.assertFalse(kwargs["force_rescore"])
        self.assertEqual(result["collected_job_ids"], ["new-1"])

        with tempfile.TemporaryDirectory() as tmp:
            with patch("openjob.ai.scorer.score_jobs") as score_jobs:
                CollectionOrchestrator({}, db_path=Path(tmp) / "collection.db", registry=registry).run(
                    _options(auto_score=False)
                )
        score_jobs.assert_not_called()

    def test_auto_score_forwards_workbench_progress_and_log_callbacks(self):
        candidates = [_candidate("boss", "new-1")]
        registry = CollectorRegistry({"boss": lambda: _FakeCollector("boss", [], candidates)})
        progress_events = []
        logs = []
        config = {
            "_workbench_score_progress": progress_events.append,
            "_workbench_log": logs.append,
        }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("openjob.ai.scorer.score_jobs") as score_jobs:
                CollectionOrchestrator(config, db_path=Path(tmp) / "collection.db", registry=registry).run(
                    _options(auto_score=True)
                )

        score_config = score_jobs.call_args.args[0]
        self.assertIs(score_config["_workbench_score_progress"].__self__, progress_events)
        self.assertIs(score_config["_workbench_log"].__self__, logs)

    def test_stop_event_does_not_start_the_next_platform_or_scoring(self):
        events = []
        stop_event = Event()
        registry = CollectorRegistry({
            "boss": lambda: _FakeCollector("boss", events, [_candidate("boss", "one")], stop=True),
            "zhilian": lambda: _FakeCollector("zhilian", events, [_candidate("zhilian", "two")]),
        })
        with tempfile.TemporaryDirectory() as tmp:
            config = {"_workbench_stop_event": stop_event}
            with patch("openjob.ai.scorer.score_jobs") as score_jobs:
                result = CollectionOrchestrator(config, db_path=Path(tmp) / "collection.db", registry=registry).run(
                    _options(order=["boss", "zhilian"], auto_score=True)
                )
        self.assertEqual(events, ["start:boss"])
        self.assertEqual(result["status"], "stopped")
        self.assertNotIn("start:zhilian", events)
        score_jobs.assert_not_called()

    def test_legacy_search_values_override_empty_platform_defaults(self):
        result = normalize_collection_options({
            "search": {"keywords": ["后端"], "cities": ["上海"]},
            "platforms": {"boss": {"search": {"keywords": [], "cities": []}}},
            "profile": {"target_cities": ["北京"]},
        })
        self.assertEqual(result["platforms"]["boss"]["keywords"], ["后端"])
        self.assertEqual(result["platforms"]["boss"]["cities"], ["上海"])

    def test_zhilian_city_code_is_resolved_from_city_name(self):
        result = normalize_collection_options({}, {
            "platform_order": ["zhilian"],
            "auto_score": False,
            "platforms": {
                "zhilian": {
                    "keywords": ["AI"],
                    "cities": ["北京市"],
                    "max_pages": 1,
                    "sort": "default",
                },
            },
        })
        self.assertEqual(result["platforms"]["zhilian"]["city_codes"], {"北京市": "530"})

        legacy_boss_code = normalize_collection_options({}, {
            "platform_order": ["zhilian"],
            "auto_score": False,
            "platforms": {
                "zhilian": {
                    "keywords": ["AI"],
                    "cities": ["北京"],
                    "city_codes": {"北京": "101010100"},
                    "max_pages": 1,
                    "sort": "default",
                },
            },
        })
        self.assertEqual(legacy_boss_code["platforms"]["zhilian"]["city_codes"], {"北京": "530"})

    def test_disabled_zhilian_is_not_in_implicit_default_queue(self):
        result = normalize_collection_options({
            "search": {"keywords": ["后端"], "cities": ["北京"]},
            "collection": {"default_order": ["boss", "zhilian"]},
            "platforms": {"zhilian": {"enabled": False, "search": {}}},
        })
        self.assertEqual(result["platform_order"], ["boss"])


class SourceChannelContractTests(TestCase):
    """推荐页来源通道契约（推荐计划 Task 1.6 的 8 个场景）。"""

    def _cfg(self, **overrides):
        cfg = {
            "search": {"keywords": ["数据分析实习"], "cities": ["广州"]},
            "platforms": {"boss": {"enabled": True, "search": {}}},
        }
        cfg["platforms"]["boss"].update(overrides)
        return cfg

    def test_legacy_config_normalizes_to_search_only(self):
        result = normalize_collection_options(self._cfg())
        boss = result["platforms"]["boss"]
        self.assertEqual(boss["source_channels"], ["search"])
        self.assertEqual(boss["recommendation_max_scrolls"], 4)
        self.assertEqual(boss["recommendation_max_cards"], 50)
        self.assertEqual(boss["recommendation_same_result_limit"], 2)

    def test_boss_search_and_recommendation_accepted(self):
        cfg = self._cfg(source_channels=["search", "recommendation"])
        cfg["platforms"]["boss"]["recommendation_max_scrolls"] = 2
        result = normalize_collection_options(cfg)
        self.assertEqual(result["platforms"]["boss"]["source_channels"], ["search", "recommendation"])
        self.assertEqual(result["platforms"]["boss"]["recommendation_max_scrolls"], 2)

    def test_boss_recommendation_only_allows_empty_keywords(self):
        cfg = {"search": {}, "platforms": {"boss": {"enabled": True, "source_channels": ["recommendation"], "search": {}}}}
        result = normalize_collection_options(cfg)
        self.assertEqual(result["platforms"]["boss"]["source_channels"], ["recommendation"])
        self.assertEqual(result["platforms"]["boss"]["keywords"], [])

    def test_non_boss_recommendation_rejected(self):
        with self.assertRaises(ValueError):
            normalize_collection_options({
                "platforms": {
                    "zhilian": {
                        "source_channels": ["search", "recommendation"],
                        "keywords": ["AI"], "cities": ["北京"],
                        "city_codes": {"北京": "101010100"},
                    }
                },
                "platform_order": ["zhilian"],
            })

    def test_duplicate_channels_deduped_in_order(self):
        cfg = self._cfg(source_channels=["recommendation", "search", "recommendation"])
        result = normalize_collection_options(cfg)
        self.assertEqual(result["platforms"]["boss"]["source_channels"], ["recommendation", "search"])

    def test_search_channel_requires_keywords_and_city(self):
        cfg = self._cfg()
        cfg["search"] = {"keywords": [], "cities": []}
        with self.assertRaises(ValueError):
            normalize_collection_options(cfg)

    def test_recommendation_params_out_of_range_rejected(self):
        cfg = self._cfg(source_channels=["search", "recommendation"], recommendation_max_scrolls=99)
        with self.assertRaises(ValueError):
            normalize_collection_options(cfg)
        cfg2 = self._cfg(source_channels=["search", "recommendation"], recommendation_max_cards=0)
        with self.assertRaises(ValueError):
            normalize_collection_options(cfg2)

    def test_unknown_channel_rejected(self):
        cfg = self._cfg(source_channels=["search", "weibo"])
        with self.assertRaises(ValueError):
            normalize_collection_options(cfg)

    def test_auto_score_false_still_collects_recommendation(self):
        result = normalize_collection_options(
            self._cfg(source_channels=["search", "recommendation"]),
            {"auto_score": False},
        )
        self.assertIn("recommendation", result["platforms"]["boss"]["source_channels"])
        self.assertFalse(result["auto_score"])


class MixedSourceProgressTests(TestCase):
    """收尾 Batch A：混合来源 run 中 sources 状态必须按来源独立，不被互相覆盖。"""

    def _scripted_mixed_run(self, *, rec_status, rec_reason):
        from openjob.collection.models import PlatformCollectionResult

        class _ScriptedCollector:
            platform = "boss"

            def collect(self, request, hooks):
                for candidate in ():
                    pass
                return PlatformCollectionResult(
                    "boss", "completed_with_shortage", "recommendation_scroll_limit",
                    "已达到推荐页最大分页轮次 1",
                    source_results={
                        "search": {"status": "completed", "reason_code": "search_exhausted", "message": "搜索流完成"},
                        "recommendation": {"status": rec_status, "reason_code": rec_reason, "message": "推荐页阶段"},
                    },
                )

        class _StubRegistry:
            def get(self, _name):
                return _ScriptedCollector()

        with patch("openjob.ai.scorer.score_jobs") as score_mock:
            orchestrator = CollectionOrchestrator(
                {"platforms": {"boss": {"enabled": True, "search": {
                    "keywords": ["数据分析实习"], "cities": ["广州"],
                }}}},
                db_path=Path(tempfile.mkdtemp()) / "openjob.db",
                registry=_StubRegistry(),
                run_id="mixed-run-1",
            )
            summary = orchestrator.run({
                "auto_score": False,
                "platforms": {"boss": {
                    "keywords": ["数据分析实习"], "cities": ["广州"],
                    "source_channels": ["search", "recommendation"],
                }},
            })
        return summary, score_mock

    def test_search_success_recommendation_scroll_limit(self):
        summary, _ = self._scripted_mixed_run(
            rec_status="completed_with_shortage", rec_reason="recommendation_scroll_limit",
        )
        sources = summary["platforms"]["boss"]["sources"]
        self.assertEqual(sources["search"]["status"], "completed")
        self.assertEqual(sources["search"]["reason_code"], "search_exhausted")
        self.assertEqual(sources["recommendation"]["status"], "completed_with_shortage")
        self.assertEqual(sources["recommendation"]["reason_code"], "recommendation_scroll_limit")
        # source_results 保持真实结果不被改写
        results = summary["platforms"]["boss"]["source_results"]
        self.assertEqual(results["search"]["reason_code"], "search_exhausted")
        self.assertEqual(results["recommendation"]["reason_code"], "recommendation_scroll_limit")

    def test_search_success_recommendation_parser_unsupported(self):
        summary, _ = self._scripted_mixed_run(
            rec_status="completed_with_shortage", rec_reason="recommendation_parser_unsupported",
        )
        sources = summary["platforms"]["boss"]["sources"]
        self.assertEqual(sources["search"]["status"], "completed")
        self.assertEqual(sources["recommendation"]["reason_code"], "recommendation_parser_unsupported")

    def test_search_success_recommendation_blocked(self):
        summary, _ = self._scripted_mixed_run(
            rec_status="blocked", rec_reason="captcha",
        )
        sources = summary["platforms"]["boss"]["sources"]
        self.assertEqual(sources["search"]["status"], "completed")
        self.assertEqual(sources["recommendation"]["status"], "blocked")
        self.assertEqual(sources["recommendation"]["reason_code"], "captcha")

    def test_search_success_recommendation_user_stopped(self):
        summary, _ = self._scripted_mixed_run(
            rec_status="stopped", rec_reason="user_stopped",
        )
        sources = summary["platforms"]["boss"]["sources"]
        self.assertEqual(sources["search"]["status"], "completed")
        self.assertEqual(sources["recommendation"]["status"], "stopped")
        self.assertEqual(sources["recommendation"]["reason_code"], "user_stopped")
