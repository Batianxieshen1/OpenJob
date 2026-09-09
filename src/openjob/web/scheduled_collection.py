"""In-workbench scheduler for collection and scoring only.

The scheduler deliberately lives inside the Web process.  It never wakes a PC,
does not make up missed work, and can only start the existing ``collect`` task.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable

from openjob.collection.orchestrator import SUPPORTED_PLATFORMS, normalize_collection_options, validate_collection_options
from openjob.scheduled_collection_store import (
    claim_scheduled_run,
    get_scheduled_run,
    list_scheduled_runs,
    update_scheduled_run,
)
from openjob.web.tasks import TaskAlreadyRunningError


def normalize_schedule_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the persisted schedule section in-place."""
    raw = config.get("collection_schedule")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("定时采集配置必须是对象")
    times_raw = raw.get("times", [])
    if not isinstance(times_raw, list):
        raise ValueError("定时采集时间必须是列表")
    times: list[str] = []
    for value in times_raw:
        time_text = str(value).strip()
        if len(time_text) != 5 or time_text[2] != ":" or not (time_text[:2].isdigit() and time_text[3:].isdigit()):
            raise ValueError("定时采集时间必须使用 HH:MM 格式，例如 09:30")
        try:
            datetime.strptime(time_text, "%H:%M")
        except ValueError as exc:
            raise ValueError("定时采集时间必须使用 HH:MM 格式，例如 09:30") from exc
        if time_text not in times:
            times.append(time_text)
    raw_platforms = raw.get("platforms", ["boss"])
    if not isinstance(raw_platforms, list):
        raise ValueError("定时采集平台必须是列表")
    platforms = [str(item).strip() for item in raw_platforms if str(item).strip()]
    if not platforms or any(platform not in SUPPORTED_PLATFORMS for platform in platforms):
        raise ValueError("定时采集平台只支持 boss、zhilian 或 51job")
    if len(platforms) != len(set(platforms)):
        raise ValueError("定时采集平台不能重复")
    try:
        max_pages = int(raw.get("max_pages", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("定时采集每平台最大页数必须是整数") from exc
    if not 1 <= max_pages <= 10:
        raise ValueError("定时采集每平台最大页数必须在 1 到 10 之间")
    pause_today_date = str(raw.get("pause_today_date") or "").strip()
    if pause_today_date:
        try:
            datetime.strptime(pause_today_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("今日暂停日期必须使用 YYYY-MM-DD 格式") from exc
    normalized = {
        "enabled": raw.get("enabled", False) is True,
        "times": sorted(times),
        "weekdays_only": raw.get("weekdays_only", True) is True,
        "max_pages": max_pages,
        "platforms": platforms,
        "pause_today_date": pause_today_date,
    }
    config["collection_schedule"] = normalized
    return normalized


class ScheduledCollectionScheduler:
    """Minute-based scheduler that only runs while this workbench process lives."""

    def __init__(
        self,
        *,
        db_path_provider: Callable[[], Path],
        config_loader: Callable[[], dict[str, Any]],
        task_runner: Any,
        task_config_builder: Callable[[dict[str, Any]], dict[str, Any]],
        preflight: Callable[[str, dict[str, Any], dict[str, Any]], list[str]],
        now_provider: Callable[[], datetime] = datetime.now,
        notify: Callable[[str, str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self._db_path_provider = db_path_provider
        self._config_loader = config_loader
        self._task_runner = task_runner
        self._task_config_builder = task_config_builder
        self._preflight = preflight
        self._now = now_provider
        self._notify = notify
        self._started_at = now_provider()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._started_at = self._now()
        self._thread = Thread(target=self._run, name="openjob-scheduled-collection", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.tick()
            self._stop_event.wait(60)

    def tick(self, *, now: datetime | None = None) -> None:
        now = now or self._now()
        config = self._config_loader()
        schedule = normalize_schedule_config(config)
        if not schedule["enabled"]:
            return
        day = now.date().isoformat()
        for scheduled_time in schedule["times"]:
            due_at = datetime.combine(now.date(), datetime.strptime(scheduled_time, "%H:%M").time())
            if due_at > now:
                continue
            db_path = self._db_path_provider()
            if get_scheduled_run(db_path, day, scheduled_time):
                continue
            if due_at < self._started_at:
                self._skip(day, scheduled_time, "工作台启动时已错过，不补跑")
                continue
            self._handle_due_slot(config, schedule, now, day, scheduled_time)

    def _handle_due_slot(self, config: dict[str, Any], schedule: dict[str, Any], now: datetime, day: str, scheduled_time: str) -> None:
        if not claim_scheduled_run(self._db_path_provider(), day, scheduled_time):
            return
        if schedule["pause_today_date"] == day:
            self._finish_skip(day, scheduled_time, "今日已暂停")
            return
        if schedule["weekdays_only"] and now.weekday() >= 5:
            self._finish_skip(day, scheduled_time, "仅工作日运行")
            return
        active = self._task_runner.status().get("active")
        if active:
            self._finish_skip(day, scheduled_time, "已有任务运行，避免抢占 Chrome")
            return
        try:
            options = self._build_options(config, schedule)
        except ValueError as exc:
            self._finish_skip(day, scheduled_time, str(exc))
            return
        messages = self._preflight("collect", config, options)
        if messages:
            self._finish_skip(day, scheduled_time, "；".join(messages))
            return
        try:
            task = self._task_runner.start("collect", self._task_config_builder({"_collection_options": options}))
        except TaskAlreadyRunningError:
            self._finish_skip(day, scheduled_time, "已有任务运行，避免抢占 Chrome")
            return
        task_id = str(task["id"])
        update_scheduled_run(self._db_path_provider(), day, scheduled_time, status="running", task_id=task_id)
        Thread(target=self._watch_task, args=(day, scheduled_time, task_id), daemon=True).start()

    def _build_options(self, config: dict[str, Any], schedule: dict[str, Any]) -> dict[str, Any]:
        base = normalize_collection_options(config)
        platforms: dict[str, Any] = {}
        for platform in schedule["platforms"]:
            if platform not in base["platforms"]:
                # Normalization includes every known platform, but retain a clear guard.
                raise ValueError(f"{platform} 未配置可用搜索条件")
            platforms[platform] = dict(base["platforms"][platform])
            platforms[platform]["max_pages"] = schedule["max_pages"]
        return validate_collection_options({
            "platform_order": list(schedule["platforms"]),
            "auto_score": True,
            "platforms": platforms,
        })

    def _skip(self, day: str, scheduled_time: str, reason: str) -> None:
        if claim_scheduled_run(self._db_path_provider(), day, scheduled_time):
            self._finish_skip(day, scheduled_time, reason)

    def _finish_skip(self, day: str, scheduled_time: str, reason: str) -> None:
        update_scheduled_run(
            self._db_path_provider(), day, scheduled_time,
            status="skipped", reason=reason, finished=True,
        )

    def _watch_task(self, day: str, scheduled_time: str, task_id: str) -> None:
        while not self._stop_event.wait(1):
            snapshot = self._task_runner.get(task_id) if hasattr(self._task_runner, "get") else None
            if not snapshot or snapshot.get("status") in {"running", "stopping"}:
                continue
            ids = snapshot.get("progress", {}).get("collected_job_ids", [])
            job_ids = [str(job_id) for job_id in ids if str(job_id)] if isinstance(ids, list) else []
            high_score_count = self._count_high_scores(job_ids)
            status = str(snapshot.get("status") or "failed")
            reason = str(snapshot.get("error") or snapshot.get("stop_reason") or "")
            update_scheduled_run(
                self._db_path_provider(), day, scheduled_time, status=status, reason=reason,
                new_jobs_count=len(job_ids), high_score_count=high_score_count, finished=True,
            )
            if self._notify:
                try:
                    self._notify("定时采集完成", f"新增 {len(job_ids)} 个岗位，高分 {high_score_count} 个", snapshot)
                except Exception:
                    pass
            return

    def _count_high_scores(self, job_ids: list[str]) -> int:
        if not job_ids:
            return 0
        config = self._config_loader()
        threshold = int(config.get("scoring", {}).get("threshold", 71))
        placeholders = ",".join("?" for _ in job_ids)
        from openjob.db import get_db
        conn = get_db(self._db_path_provider())
        try:
            return int(conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE id IN ({placeholders}) AND score >= ?",
                [*job_ids, threshold],
            ).fetchone()[0])
        finally:
            conn.close()

    def summary(self) -> dict[str, Any]:
        now = self._now()
        config = self._config_loader()
        schedule = normalize_schedule_config(config)
        runs = list_scheduled_runs(self._db_path_provider(), limit=20)
        day = now.date().isoformat()
        today = [run for run in runs if run["schedule_date"] == day]
        next_run_at = self._next_run_at(now, schedule)
        last = runs[0] if runs else None
        return {
            "enabled": schedule["enabled"],
            "next_run_at": next_run_at,
            "today_executed": len([run for run in today if run["status"] == "completed"]),
            "last_run": last,
            "last_skip_reason": last["reason"] if last and last["status"] == "skipped" else "",
            "pause_today": schedule["pause_today_date"] == day,
            "times": schedule["times"],
        }

    def _next_run_at(self, now: datetime, schedule: dict[str, Any]) -> str | None:
        """Return the next eligible slot, including a future day when today is done."""
        if not schedule["enabled"] or not schedule["times"]:
            return None

        candidate_day = now.date()
        for _ in range(8):
            is_paused_today = (
                candidate_day == now.date()
                and schedule["pause_today_date"] == candidate_day.isoformat()
            )
            if not is_paused_today and not (schedule["weekdays_only"] and candidate_day.weekday() >= 5):
                for scheduled_time in schedule["times"]:
                    candidate = datetime.combine(candidate_day, datetime.strptime(scheduled_time, "%H:%M").time())
                    if candidate > now:
                        return candidate.isoformat(timespec="minutes")
            candidate_day += timedelta(days=1)
        return None
