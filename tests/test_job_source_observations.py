"""来源观察与跨来源去重测试（推荐页计划 Task 3 / §11.3）。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.collection.models import JobCandidate
from openjob.collection.orchestrator import CollectionOrchestrator
from openjob.db import (
    SCHEMA_VERSION,
    get_db,
    get_job_source_summaries,
    insert_job_if_new,
    record_job_source_observation,
)


def _boss_candidate(source_job_id: str, channel: str, keyword: str = "数据分析实习") -> JobCandidate:
    return JobCandidate(
        platform="boss",
        source_job_id=source_job_id,
        title="数据分析实习生",
        company="跨源公司",
        salary="150-200元/天",
        city="广州",
        city_code="101280100",
        jd="负责业务数据看板建设，需要 SQL 与 Python 能力。",
        url=f"/job_detail/{source_job_id}.html",
        source_keyword=keyword if channel == "search" else "",
        source_channel=channel,
    )




def run_collect_candidates(db_path: Path, candidates, *, auto_score=True):
    """模块级脚本化采集：走真实 CollectionOrchestrator + _SharedProcessor 管线。"""

    class _ScriptedCollector:
        platform = "boss"

        def __init__(self, cands):
            self._cands = cands

        def collect(self, request, hooks):
            for candidate in self._cands:
                if hooks.stop_event is not None and hooks.stop_event.is_set():
                    break
                if not hooks.on_list_candidate(candidate):
                    continue
                hooks.on_candidate(candidate)
            from openjob.collection.models import PlatformCollectionResult

            return PlatformCollectionResult(
                "boss", "completed", "search_exhausted", "脚本采集完成",
                new_job_ids=[c.storage_id for c in self._cands],
            )

    class _StubRegistry:
        def get(self, _name):
            return _ScriptedCollector(candidates)

    with patch("openjob.ai.scorer.score_jobs") as score_mock:
        orchestrator = CollectionOrchestrator(
            {"platforms": {"boss": {"enabled": True, "search": {
                "keywords": ["数据分析实习"], "cities": ["广州"],
            }}}},
            db_path=db_path,
            registry=_StubRegistry(),
            run_id="run-obs-1",
        )
        summary = orchestrator.run({"auto_score": auto_score})
    return summary, score_mock


class MigrationV29Tests(unittest.TestCase):
    def test_fresh_db_has_v9_and_observations_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                cols = {row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
                self.assertIn("source_channel", cols)
                tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("job_source_observations", tables)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM job_source_observations").fetchone()[0], 0)
            finally:
                db.close()

    def test_legacy_backfill_marks_search_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            db = get_db(db_path)
            insert_job_if_new(db, {
                "id": "legacy-1", "title": "数据专员", "company": "旧公司",
                "jd": "数据整理", "url": "https://www.zhipin.com/job_detail/legacy1.html",
                "source_platform": "boss", "source_job_id": "legacy1",
                "source_keyword": "旧关键词", "city": "广州", "source_city_code": "101280100",
            })
            db.close()
            db = get_db(db_path)
            try:
                row = dict(db.execute("SELECT source_channel FROM jobs WHERE id='legacy-1'").fetchone())
                self.assertEqual(row["source_channel"], "search")
                obs = db.execute(
                    "SELECT source_channel, source_keyword FROM job_source_observations WHERE job_id='legacy-1'"
                ).fetchall()
                self.assertEqual(len(obs), 1)
                self.assertEqual(obs[0]["source_channel"], "search")
                self.assertEqual(obs[0]["source_keyword"], "旧关键词")
            finally:
                db.close()
            # 幂等：重开连接不产生重复 observation
            db = get_db(db_path)
            count = db.execute(
                "SELECT COUNT(*) FROM job_source_observations WHERE job_id='legacy-1'"
            ).fetchone()[0]
            db.close()
            self.assertEqual(count, 1)


class SourceObservationTests(unittest.TestCase):
    def test_repeat_observation_increments_seen_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                for run_id in ("run-1", "run-2", "run-3"):
                    record_job_source_observation(
                        db, job_id="j1", source_platform="boss", source_channel="recommendation",
                        source_keyword="", source_city="广州", collection_run_id=run_id,
                    )
                row = dict(db.execute(
                    "SELECT seen_count, collection_run_id FROM job_source_observations WHERE job_id='j1'"
                ).fetchone())
                self.assertEqual(row["seen_count"], 3)
                self.assertEqual(row["collection_run_id"], "run-3")
            finally:
                db.close()

    def test_summaries_merge_channels_in_first_seen_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job_if_new(db, {
                    "id": "j2", "title": "数据专员", "company": "C",
                    "jd": "x", "url": "https://www.zhipin.com/job_detail/j2.html",
                    "source_platform": "boss", "source_job_id": "j2",
                    "source_keyword": "搜索词", "source_channel": "search",
                })
                record_job_source_observation(
                    db, job_id="j2", source_platform="boss", source_channel="search",
                    source_keyword="搜索词",
                )
                record_job_source_observation(
                    db, job_id="j2", source_platform="boss", source_channel="recommendation",
                )
                summaries = get_job_source_summaries(db, ["j2"])
                self.assertEqual(summaries["j2"]["source_channels"], ["search", "recommendation"])
                self.assertEqual(summaries["j2"]["source_labels"], ["搜索流", "推荐页"])
                self.assertEqual(len(summaries["j2"]["source_observations"]), 2)
            finally:
                db.close()


class CrossSourceDedupTests(unittest.TestCase):
    """同一岗位从两个来源到达：一条 jobs、两条 observation、一次评分。"""

    def _run_collect(self, db_path: Path, candidates, *, auto_score=True):
        class _ScriptedCollector:
            platform = "boss"

            def __init__(self, candidates):
                self._candidates = candidates

            def collect(self, request, hooks):
                for candidate in self._candidates:
                    if hooks.stop_event is not None and hooks.stop_event.is_set():
                        break
                    if not hooks.on_list_candidate(candidate):
                        continue
                    hooks.on_candidate(candidate)
                from openjob.collection.models import PlatformCollectionResult

                return PlatformCollectionResult(
                    "boss", "completed", "search_exhausted", "脚本采集完成",
                    new_job_ids=[c.storage_id for c in self._candidates],
                )

        class _StubRegistry:
            def get(self, _name):
                return _ScriptedCollector(candidates)

        with patch("openjob.ai.scorer.score_jobs") as score_mock:
            orchestrator = CollectionOrchestrator(
                {"platforms": {"boss": {"enabled": True, "search": {
                    "keywords": ["数据分析实习"], "cities": ["广州"],
                }}}},
                db_path=db_path,
                registry=_StubRegistry(),
                run_id="cross-run-1",
            )
            summary = orchestrator.run({"auto_score": auto_score})
        return summary, score_mock

    def test_same_job_two_sources_one_row_two_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            get_db(db_path).close()  # 初始化 schema
            candidates = [
                _boss_candidate("same-job", "search"),
                _boss_candidate("same-job", "recommendation"),
            ]
            summary, score_mock = self._run_collect(db_path, candidates)

            db = get_db(db_path)
            try:
                rows = db.execute(
                    "SELECT id, source_channel FROM jobs WHERE source_job_id = 'same-job'"
                ).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["source_channel"], "search")  # 首次来源为主来源
                summaries = get_job_source_summaries(db, [rows[0]["id"]])
                self.assertEqual(summaries[rows[0]["id"]]["source_channels"], ["search", "recommendation"])
            finally:
                db.close()
            # 唯一评分一次
            self.assertEqual(score_mock.call_count, 1)
            self.assertEqual(summary["platforms"]["boss"]["new"], 1)

    def test_recommendation_duplicate_does_not_rescore(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            get_db(db_path).close()
            # 第一轮：搜索流入库
            self._run_collect(db_path, [_boss_candidate("dup-job", "search")])
            # 第二轮：推荐流命中同一岗位
            summary, score_mock = self._run_collect(db_path, [_boss_candidate("dup-job", "recommendation")])

            self.assertEqual(summary["platforms"]["boss"]["new"], 0)
            self.assertEqual(score_mock.call_count, 0)  # 重复命中不重复评分
            db = get_db(db_path)
            try:
                summaries = get_job_source_summaries(db, ["boss:dup-job"] if False else ["dup-job"])
                channels = summaries["dup-job"]["source_channels"]
                self.assertEqual(channels, ["search", "recommendation"])
                # 重复来源不改岗位状态
                row = dict(db.execute("SELECT status, score FROM jobs WHERE id='dup-job'").fetchone())
                self.assertEqual(row["status"], "pending")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()


class FirstObservationCountTests(unittest.TestCase):
    """收尾 Batch A：首次观察 seen_count 必须为 1（走真实采集管线验证）。"""

    def test_first_new_job_observation_seen_count_is_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            get_db(db_path).close()  # 初始化 schema
            candidates = [_boss_candidate("first-job", "search")]
            run_collect_candidates(db_path, candidates, auto_score=False)

            db = get_db(db_path)
            try:
                rows = db.execute(
                    "SELECT seen_count FROM job_source_observations WHERE job_id='first-job'"
                ).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["seen_count"], 1)
            finally:
                db.close()

    def test_second_collection_same_source_increments_to_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            get_db(db_path).close()
            for _ in range(2):
                run_collect_candidates(db_path, [_boss_candidate("rep-job", "search")], auto_score=False)

            db = get_db(db_path)
            try:
                rows = db.execute(
                    "SELECT seen_count FROM job_source_observations WHERE job_id='rep-job' AND source_channel='search'"
                ).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["seen_count"], 2)
            finally:
                db.close()

    def test_full_channel_lifecycle_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            get_db(db_path).close()
            # 轮1：search 发现；轮2：search 再发现；轮3：recommendation 发现；轮4：recommendation 再发现
            run_collect_candidates(db_path, [_boss_candidate("life-job", "search")], auto_score=False)
            run_collect_candidates(db_path, [_boss_candidate("life-job", "search")], auto_score=False)
            run_collect_candidates(db_path, [_boss_candidate("life-job", "recommendation")], auto_score=False)
            run_collect_candidates(db_path, [_boss_candidate("life-job", "recommendation")], auto_score=False)

            db = get_db(db_path)
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE id='life-job'").fetchone()[0], 1)
                rows = {r["source_channel"]: r["seen_count"] for r in db.execute(
                    "SELECT source_channel, seen_count FROM job_source_observations WHERE job_id='life-job'"
                ).fetchall()}
                self.assertEqual(rows.get("search"), 2)
                self.assertEqual(rows.get("recommendation"), 2)
            finally:
                db.close()


class PermanentDeleteObservationTests(unittest.TestCase):
    """收尾 Batch B：永久删除同步清理 observation；软删除/恢复保留。"""

    def test_permanent_delete_removes_source_observations(self):
        from openjob.db import soft_delete_jobs

        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job_if_new(db, {
                    "id": "pd-1", "title": "数据专员", "company": "C",
                    "jd": "x", "url": "https://www.zhipin.com/job_detail/pd1.html",
                    "source_platform": "boss", "source_job_id": "pd-1",
                    "source_channel": "search",
                })
                record_job_source_observation(
                    db, job_id="pd-1", source_platform="boss", source_channel="search",
                    source_keyword="",
                )
                record_job_source_observation(
                    db, job_id="pd-1", source_platform="boss", source_channel="recommendation",
                )
                soft_delete_jobs(db, ["pd-1"], confirmed=True, reason="测试")
                # 软删除保留观察
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM job_source_observations WHERE job_id='pd-1'"
                ).fetchone()[0], 2)
                # 恢复后仍可查询
                from openjob.db import restore_jobs

                restore_jobs(db, ["pd-1"])
                summaries = get_job_source_summaries(db, ["pd-1"])
                self.assertEqual(summaries["pd-1"]["source_channels"], ["search", "recommendation"])
                # 再软删 + 永久删除
                soft_delete_jobs(db, ["pd-1"], confirmed=True, reason="测试")
                from openjob.db import permanent_delete_jobs

                result = permanent_delete_jobs(db, ["pd-1"], confirmed=True, confirmation="PERMANENT_DELETE")
                self.assertEqual(result["affected_count"], 1)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM job_source_observations WHERE job_id='pd-1'"
                ).fetchone()[0], 0)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE id='pd-1'"
                ).fetchone()[0], 0)
            finally:
                db.close()
