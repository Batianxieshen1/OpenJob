"""SQLite persistence for low-interference scheduled collection runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjob.db import get_db


def _row_as_dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def claim_scheduled_run(db_path: Path, schedule_date: str, schedule_time: str) -> bool:
    """Atomically reserve one schedule slot; a restart cannot run it twice."""
    conn = get_db(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO scheduled_collection_runs (schedule_date, schedule_time, status)
            VALUES (?, ?, 'claimed')
            ON CONFLICT(schedule_date, schedule_time) DO NOTHING
            """,
            (schedule_date, schedule_time),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def update_scheduled_run(
    db_path: Path,
    schedule_date: str,
    schedule_time: str,
    *,
    status: str,
    reason: str = "",
    task_id: str | None = None,
    new_jobs_count: int | None = None,
    high_score_count: int | None = None,
    finished: bool = False,
) -> None:
    assignments = ["status = ?", "reason = ?", "updated_at = CURRENT_TIMESTAMP"]
    params: list[Any] = [status, reason]
    if task_id is not None:
        assignments.append("task_id = ?")
        params.append(task_id)
    if new_jobs_count is not None:
        assignments.append("new_jobs_count = ?")
        params.append(new_jobs_count)
    if high_score_count is not None:
        assignments.append("high_score_count = ?")
        params.append(high_score_count)
    if finished:
        assignments.append("finished_at = CURRENT_TIMESTAMP")
    params.extend([schedule_date, schedule_time])
    conn = get_db(db_path)
    try:
        conn.execute(
            f"UPDATE scheduled_collection_runs SET {', '.join(assignments)} "
            "WHERE schedule_date = ? AND schedule_time = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def get_scheduled_run(db_path: Path, schedule_date: str, schedule_time: str) -> dict[str, Any] | None:
    conn = get_db(db_path)
    try:
        return _row_as_dict(conn.execute(
            "SELECT * FROM scheduled_collection_runs WHERE schedule_date = ? AND schedule_time = ?",
            (schedule_date, schedule_time),
        ).fetchone())
    finally:
        conn.close()


def list_scheduled_runs(db_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    conn = get_db(db_path)
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM scheduled_collection_runs ORDER BY schedule_date DESC, schedule_time DESC LIMIT ?",
            (limit,),
        ).fetchall()]
    finally:
        conn.close()


def mark_orphaned_scheduled_runs_stopped(db_path: Path) -> int:
    conn = get_db(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE scheduled_collection_runs
            SET status = 'stopped', reason = '工作台重启，上一轮计划任务未完成',
                finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
            """
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
