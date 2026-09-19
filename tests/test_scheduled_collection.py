"""Tests for the low-interference in-workbench collection scheduler."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unittest
from typing import Any

from openjob.scheduled_collection_store import get_scheduled_run
from openjob.web.scheduled_collection import ScheduledCollectionScheduler, normalize_schedule_config


class FakeTaskRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def status(self) -> dict[str, Any]:
        return {"active": None}

    def start(self, mode: str, config: dict[str, Any]) -> dict[str, str]:
        self.calls.append((mode, config))
        return {"id": "scheduled-task-1", "status": "running"}


def _config(**schedule_overrides: Any) -> dict[str, Any]:
    schedule = {
        "enabled": True,
        "times": ["09:30"],
        "weekdays_only": True,
        "max_pages": 1,
        "platforms": ["boss"],
        "pause_today_date": "",
    }
    schedule.update(schedule_overrides)
    return {
        "collection_schedule": schedule,
        "search": {
            "keywords": ["Python"],
            "cities": ["北京"],
            "max_pages": 3,
        },
    }


def _collection_options(config: dict[str, Any]) -> dict[str, Any]:
    return config["_collection_options"]


def test_tick_starts_only_collect_with_auto_score_at_due_time(tmp_path: Path) -> None:
    runner = FakeTaskRunner()
    scheduler = ScheduledCollectionScheduler(
        db_path_provider=lambda: tmp_path / "openjob.db",
        config_loader=_config,
        task_runner=runner,
        task_config_builder=lambda extra: extra,
        preflight=lambda _mode, _config, _options: [],
        now_provider=lambda: datetime(2026, 9, 8, 9, 0),
    )

    scheduler.tick(now=datetime(2026, 9, 8, 9, 30))

    assert len(runner.calls) == 1
    mode, task_config = runner.calls[0]
    options = _collection_options(task_config)
    assert mode == "collect"
    assert options["auto_score"] is True
    assert options["platform_order"] == ["boss"]
    assert options["platforms"]["boss"]["max_pages"] == 1
    assert mode not in {"full", "deliver", "monitor"}


def _scheduler(
    tmp_path: Path,
    runner: FakeTaskRunner,
    *,
    started_at: datetime = datetime(2026, 9, 8, 9, 0),
    **schedule_overrides: Any,
) -> ScheduledCollectionScheduler:
    return ScheduledCollectionScheduler(
        db_path_provider=lambda: tmp_path / "openjob.db",
        config_loader=lambda: _config(**schedule_overrides),
        task_runner=runner,
        task_config_builder=lambda extra: extra,
        preflight=lambda _mode, _config, _options: [],
        now_provider=lambda: started_at,
    )


def test_same_slot_is_not_started_twice_after_workbench_restart(tmp_path: Path) -> None:
    runner = FakeTaskRunner()
    first = _scheduler(tmp_path, runner)
    first.tick(now=datetime(2026, 9, 8, 9, 30))

    restarted = _scheduler(tmp_path, runner)
    restarted.tick(now=datetime(2026, 9, 8, 9, 30))

    assert len(runner.calls) == 1


def test_missed_slot_is_recorded_and_never_backfilled(tmp_path: Path) -> None:
    runner = FakeTaskRunner()
    scheduler = _scheduler(tmp_path, runner, started_at=datetime(2026, 9, 8, 10, 0))

    scheduler.tick(now=datetime(2026, 9, 8, 10, 0))

    assert runner.calls == []
    run = get_scheduled_run(tmp_path / "openjob.db", "2026-09-08", "09:30")
    assert run is not None
    assert run["status"] == "skipped"
    assert "已错过" in run["reason"]


def test_pause_today_and_existing_task_skip_without_using_chrome(tmp_path: Path) -> None:
    paused_runner = FakeTaskRunner()
    paused = _scheduler(tmp_path / "paused", paused_runner, pause_today_date="2026-09-08")
    paused.tick(now=datetime(2026, 9, 8, 9, 30))
    paused_run = get_scheduled_run(tmp_path / "paused" / "openjob.db", "2026-09-08", "09:30")
    assert paused_runner.calls == []
    assert paused_run is not None and paused_run["reason"] == "今日已暂停"

    class BusyRunner(FakeTaskRunner):
        def status(self) -> dict[str, Any]:
            return {"active": {"id": "manual-task", "status": "running"}}

    busy_runner = BusyRunner()
    busy = _scheduler(tmp_path / "busy", busy_runner)
    busy.tick(now=datetime(2026, 9, 8, 9, 30))
    busy_run = get_scheduled_run(tmp_path / "busy" / "openjob.db", "2026-09-08", "09:30")
    assert busy_runner.calls == []
    assert busy_run is not None and "避免抢占 Chrome" in busy_run["reason"]


def test_weekend_schedule_is_skipped_when_workdays_only(tmp_path: Path) -> None:
    runner = FakeTaskRunner()
    scheduler = ScheduledCollectionScheduler(
        db_path_provider=lambda: tmp_path / "openjob.db",
        config_loader=_config,
        task_runner=runner,
        task_config_builder=lambda extra: extra,
        preflight=lambda _mode, _config, _options: [],
        now_provider=lambda: datetime(2026, 9, 12, 9, 0),
    )

    scheduler.tick(now=datetime(2026, 9, 12, 9, 30))

    assert runner.calls == []
    run = get_scheduled_run(tmp_path / "openjob.db", "2026-09-12", "09:30")
    assert run is not None and run["reason"] == "仅工作日运行"


def test_schedule_config_rejects_invalid_times_without_silently_disabling_it() -> None:
    config = _config(times=["9:30"])

    try:
        normalize_schedule_config(config)
    except ValueError as exc:
        assert "HH:MM" in str(exc)
    else:
        raise AssertionError("invalid schedule time must be rejected")


def test_summary_shows_tomorrows_first_slot_after_todays_slots_have_passed(tmp_path: Path) -> None:
    runner = FakeTaskRunner()
    scheduler = _scheduler(
        tmp_path,
        runner,
        started_at=datetime(2026, 9, 8, 20, 0),
        times=["09:30", "19:30"],
    )

    summary = scheduler.summary()

    assert summary["next_run_at"] == "2026-09-09T09:30"


def test_summary_skips_weekend_for_workdays_only_schedule(tmp_path: Path) -> None:
    runner = FakeTaskRunner()
    scheduler = _scheduler(
        tmp_path,
        runner,
        started_at=datetime(2026, 9, 11, 20, 0),
        times=["09:30"],
    )

    summary = scheduler.summary()

    assert summary["next_run_at"] == "2026-09-14T09:30"


def test_summary_skips_paused_today_slots(tmp_path: Path) -> None:
    runner = FakeTaskRunner()
    scheduler = _scheduler(
        tmp_path,
        runner,
        started_at=datetime(2026, 9, 8, 8, 0),
        times=["09:30", "19:30"],
        pause_today_date="2026-09-08",
    )

    summary = scheduler.summary()

    assert summary["next_run_at"] == "2026-09-09T09:30"


def scheduler_normalize(config):
    import copy
    from openjob.web.scheduled_collection import normalize_schedule_config

    return normalize_schedule_config(copy.deepcopy(config))


class RecommendationInheritanceTests(unittest.TestCase):
    """推荐页计划 Task 6：定时采集对推荐页的继承与关闭。"""

    def _config(self, schedule_overrides=None, boss_overrides=None):
        boss = {"enabled": True, "search": {"keywords": ["数据分析实习"], "cities": ["广州"]}}
        boss.update(boss_overrides or {})
        schedule = {"enabled": True, "times": ["09:30"], "max_pages": 1, "platforms": ["boss"]}
        schedule.update(schedule_overrides or {})
        return {"collection_schedule": schedule, "platforms": {"boss": boss}}

    def test_legacy_schedule_defaults_to_search_only(self):
        from openjob.web.scheduled_collection import ScheduledCollectionScheduler

        scheduler = ScheduledCollectionScheduler(
            db_path_provider=lambda: Path("x"),
            config_loader=lambda: {},
            task_runner=None,
            task_config_builder=lambda cfg: cfg,
            preflight=lambda *_a: [],
        )
        options = scheduler._build_options(self._config(), scheduler_normalize(self._config()))
        self.assertEqual(options["platforms"]["boss"]["source_channels"], ["search"])

    def test_enabled_schedule_keeps_recommendation_channel(self):
        from openjob.web.scheduled_collection import ScheduledCollectionScheduler

        scheduler = ScheduledCollectionScheduler(
            db_path_provider=lambda: Path("x"), config_loader=lambda: {},
            task_runner=None, task_config_builder=lambda cfg: cfg, preflight=lambda *_a: [],
        )
        schedule = scheduler_normalize(self._config(schedule_overrides={"include_recommendation": True}))
        self.assertTrue(schedule["include_recommendation"])
        self.assertEqual(schedule["recommendation_max_scrolls"], 2)
        options = scheduler._build_options(self._config(), schedule)
        self.assertEqual(options["platforms"]["boss"]["source_channels"], ["search", "recommendation"])
        self.assertEqual(options["platforms"]["boss"]["recommendation_max_scrolls"], 2)

    def test_recommendation_only_schedule_needs_no_keywords(self):
        from openjob.web.scheduled_collection import ScheduledCollectionScheduler

        scheduler = ScheduledCollectionScheduler(
            db_path_provider=lambda: Path("x"), config_loader=lambda: {},
            task_runner=None, task_config_builder=lambda cfg: cfg, preflight=lambda *_a: [],
        )
        cfg = self._config(
            schedule_overrides={"include_recommendation": True},
            boss_overrides={"source_channels": ["recommendation"]},
        )
        # 手工清空关键词/城市（只推荐页允许）
        cfg["platforms"]["boss"]["search"] = {}
        schedule = scheduler_normalize(cfg)
        options = scheduler._build_options(cfg, schedule)
        self.assertEqual(options["platforms"]["boss"]["source_channels"], ["recommendation"])

