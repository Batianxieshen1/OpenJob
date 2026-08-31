"""Database module - SQLite storage for jobs, history and state tracking."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path("./data/openjob.db")
MAX_JOB_IDS = 1000
DELETION_PROTECTED_STATUSES = {"sent", "replied", "resume_sent", "needs_resume", "follow_up_sent"}
DELETION_PROTECTED_HISTORY_ACTIONS = {
    "sent", "manual_sent", "replied", "resume_sent", "needs_resume", "follow_up_sent", "reply_pending", "auto_replied",
}
EXTERNAL_MANUAL_SEND_PLATFORMS = {"zhilian", "51job"}


class JobDeletionConfirmationError(ValueError):
    code = "confirmation_required"


class JobDeletionConflictError(ValueError):
    code = "deletion_conflict"

    def __init__(self, message: str, *, blocked: list[dict[str, Any]] | None = None, not_found: list[str] | None = None):
        super().__init__(message)
        self.blocked = blocked or []
        self.not_found = not_found or []


class JobManualSentConflictError(ValueError):
    code = "manual_sent_conflict"

    def __init__(self, message: str, *, blocked: list[dict[str, Any]] | None = None, not_found: list[str] | None = None):
        super().__init__(message)
        self.blocked = blocked or []
        self.not_found = not_found or []


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            salary TEXT,
            city TEXT,
            experience TEXT,
            education TEXT,
            recruitment_type TEXT DEFAULT 'unknown',
            jd TEXT,
            hr_name TEXT,
            hr_title TEXT,
            hr_active TEXT,
            company_size TEXT,
            company_industry TEXT,
            url TEXT,
            score INTEGER DEFAULT 0,
            score_reason TEXT,
            greeting TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );

        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS platform_access_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'boss',
            stage TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS platform_safety_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            reason TEXT NOT NULL,
            locked_until TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
        CREATE INDEX IF NOT EXISTS idx_history_job_id ON history(job_id);
        CREATE INDEX IF NOT EXISTS idx_risk_events_type ON risk_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_platform_access_stage_action
            ON platform_access_events(stage, action, created_at);
    """)
    conn.commit()
    _migrate_v1_1(conn)
    _migrate_v1_2(conn)
    _migrate_v1_3(conn)
    _migrate_v1_4(conn)
    _migrate_platform_access_events(conn)
    _init_scoring_runs(conn)
    _init_collection_runs(conn)
    _migrate_v2_0(conn)
    _migrate_v2_1(conn)
    _migrate_v2_2(conn)
    _migrate_v2_3(conn)
    _migrate_v2_4(conn)


def job_exists(conn: sqlite3.Connection, job_id: str) -> bool:
    """Check if a job already exists in the database."""
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row is not None


def job_identity_exists(
    conn: sqlite3.Connection,
    source_platform: str,
    source_job_id: str,
    *,
    legacy_job_id: str | None = None,
) -> bool:
    """Check a platform identity while retaining old BOSS id compatibility."""
    source_platform = str(source_platform or "boss").strip() or "boss"
    source_job_id = str(source_job_id or "").strip()
    if not source_job_id:
        return bool(legacy_job_id and job_exists(conn, str(legacy_job_id)))
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE source_platform = ? AND source_job_id = ? LIMIT 1",
        (source_platform, source_job_id),
    ).fetchone()
    if row is not None:
        return True
    if source_platform == "boss":
        fallback_id = str(legacy_job_id or source_job_id)
        return job_exists(conn, fallback_id)
    return False


def _normalize_job_ids(job_ids: Any, *, required: bool = False) -> list[str]:
    if isinstance(job_ids, (str, bytes, dict)) or job_ids is None:
        values = [] if job_ids is None else None
    else:
        try:
            values = list(job_ids)
        except TypeError:
            values = None
    if values is None:
        raise ValueError("岗位 ID 必须是数组")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("岗位 ID 必须是字符串")
        job_id = value.strip()
        if job_id and job_id not in normalized:
            normalized.append(job_id)
    if len(normalized) > MAX_JOB_IDS:
        raise ValueError(f"一次最多处理 {MAX_JOB_IDS} 个岗位")
    if required and not normalized:
        raise ValueError("岗位 ID 不能为空")
    return normalized


def query_jobs(
    conn: sqlite3.Connection,
    *,
    deleted: str = "active",
    job_ids: Any = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Query jobs with a single active/recycle-bin semantic."""
    if deleted not in {"active", "only", "all"}:
        raise ValueError("deleted 参数无效")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500):
        raise ValueError("limit 参数无效")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset 参数无效")
    ids = None if job_ids is None else _normalize_job_ids(job_ids, required=True)
    conditions: list[str] = []
    params: list[Any] = []
    if deleted == "active":
        conditions.append("deleted_at IS NULL")
    elif deleted == "only":
        conditions.append("deleted_at IS NOT NULL")
    if ids is not None:
        conditions.append(f"id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
    where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    total = int(conn.execute(f"SELECT COUNT(*) AS cnt FROM jobs{where_sql}", params).fetchone()["cnt"])
    query_params = list(params)
    pagination = ""
    if limit is not None:
        pagination = " LIMIT ? OFFSET ?"
        query_params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM jobs{where_sql} ORDER BY score DESC, created_at DESC{pagination}",
        query_params,
    ).fetchall()
    return [dict(row) for row in rows], total


def _job_rows_by_ids(conn: sqlite3.Connection, job_ids: list[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in job_ids)
    return [dict(row) for row in conn.execute(f"SELECT * FROM jobs WHERE id IN ({placeholders})", job_ids).fetchall()]


def _history_protection_reasons(conn: sqlite3.Connection, job_id: str) -> list[str]:
    actions = {
        str(row["action"] or "").strip().lower()
        for row in conn.execute("SELECT action FROM history WHERE job_id = ?", (job_id,)).fetchall()
    }
    return ["历史中存在发送或回复证据"] if actions & DELETION_PROTECTED_HISTORY_ACTIONS else []


def _scoring_run_conflicts(conn: sqlite3.Connection, job_ids: set[str]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    rows = conn.execute(
        "SELECT id, status, remaining_job_ids_json FROM scoring_runs WHERE status IN ('running', 'paused')"
    ).fetchall()
    for row in rows:
        try:
            remaining = json.loads(row["remaining_job_ids_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            remaining = []
        for job_id in sorted(job_ids & {str(value) for value in remaining if str(value)}):
            conflicts.append({
                "job_id": job_id,
                "reasons": ["独立评分任务仍在运行或等待恢复"],
                "scoring_run_id": str(row["id"]),
                "run_status": str(row["status"]),
            })
    return conflicts


def soft_delete_jobs(
    conn: sqlite3.Connection,
    job_ids: Any,
    *,
    confirmed: bool = False,
    reason: str = "用户移入回收站",
) -> dict[str, Any]:
    if confirmed is not True:
        raise JobDeletionConfirmationError("移入回收站需要 confirmed=true")
    ids = _normalize_job_ids(job_ids, required=True)
    run_conflicts = _scoring_run_conflicts(conn, set(ids))
    if run_conflicts:
        raise JobDeletionConflictError("岗位仍被评分任务引用，请先结束该评分任务", blocked=run_conflicts)
    rows = _job_rows_by_ids(conn, ids)
    found_ids = {str(row["id"]) for row in rows}
    active_rows = [row for row in rows if row.get("deleted_at") is None]
    delete_reason = str(reason or "用户移入回收站").strip()[:240]
    with conn:
        for row in active_rows:
            job_id = str(row["id"])
            conn.execute(
                "UPDATE jobs SET deleted_at = CURRENT_TIMESTAMP, deleted_reason = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND deleted_at IS NULL",
                (delete_reason, job_id),
            )
            conn.execute(
                "INSERT INTO history (job_id, action, detail) VALUES (?, 'soft_deleted', ?)",
                (job_id, delete_reason),
            )
    return {
        "requested_count": len(ids),
        "affected_count": len(active_rows),
        "not_found": [job_id for job_id in ids if job_id not in found_ids],
    }


def restore_jobs(conn: sqlite3.Connection, job_ids: Any, *, confirmed: bool = False) -> dict[str, Any]:
    if confirmed is not True:
        raise JobDeletionConfirmationError("恢复岗位需要 confirmed=true")
    ids = _normalize_job_ids(job_ids, required=True)
    rows = _job_rows_by_ids(conn, ids)
    found_ids = {str(row["id"]) for row in rows}
    deleted_rows = [row for row in rows if row.get("deleted_at") is not None]
    with conn:
        for row in deleted_rows:
            job_id = str(row["id"])
            conn.execute(
                "UPDATE jobs SET deleted_at = NULL, deleted_reason = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND deleted_at IS NOT NULL",
                (job_id,),
            )
            conn.execute(
                "INSERT INTO history (job_id, action, detail) VALUES (?, 'restored', ?)",
                (job_id, "用户从回收站恢复岗位"),
            )
    return {
        "requested_count": len(ids),
        "affected_count": len(deleted_rows),
        "not_found": [job_id for job_id in ids if job_id not in found_ids],
        "already_active": [str(row["id"]) for row in rows if row.get("deleted_at") is None],
    }


def mark_external_jobs_sent(conn: sqlite3.Connection, job_ids: Any, *, confirmed: bool = False) -> dict[str, Any]:
    """Record user-confirmed sends for collection-only platforms without automating them."""
    if confirmed is not True:
        raise JobDeletionConfirmationError("标记已发送需要 confirmed=true")
    ids = _normalize_job_ids(job_ids, required=True)
    rows = _job_rows_by_ids(conn, ids)
    found_ids = {str(row["id"]) for row in rows}
    not_found = [job_id for job_id in ids if job_id not in found_ids]
    if not_found:
        raise JobManualSentConflictError("存在不存在的岗位，未执行标记", not_found=not_found)

    completed_statuses = {"sent", "replied", "resume_sent", "needs_resume", "follow_up_sent"}
    already_sent: list[str] = []
    blocked: list[dict[str, Any]] = []
    pending_rows: list[dict[str, Any]] = []
    for row in rows:
        job_id = str(row["id"])
        reasons: list[str] = []
        platform = str(row.get("source_platform") or "boss")
        if row.get("deleted_at") is not None:
            reasons.append("岗位已进入回收站")
        if platform not in EXTERNAL_MANUAL_SEND_PLATFORMS:
            reasons.append("仅智联招聘和前程无忧支持手动标记已发送")
        if reasons:
            blocked.append({"job_id": job_id, "reasons": reasons})
        elif str(row.get("status") or "") in completed_statuses:
            already_sent.append(job_id)
        else:
            pending_rows.append(row)
    if blocked:
        raise JobManualSentConflictError("存在不允许手动标记的岗位，批量操作已整体拒绝", blocked=blocked)

    platform_labels = {"zhilian": "智联招聘", "51job": "前程无忧"}
    with conn:
        for row in pending_rows:
            job_id = str(row["id"])
            platform = str(row.get("source_platform") or "")
            detail = f"用户在{platform_labels[platform]}完成投递后手动标记"
            conn.execute(
                "UPDATE jobs SET status = 'sent', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
                (job_id,),
            )
            conn.execute(
                "INSERT INTO history (job_id, action, detail) VALUES (?, 'manual_sent', ?)",
                (job_id, detail),
            )
    return {
        "requested_count": len(ids),
        "affected_count": len(pending_rows),
        "already_sent": already_sent,
    }


def permanent_delete_jobs(
    conn: sqlite3.Connection,
    job_ids: Any,
    *,
    confirmed: bool = False,
    confirmation: str = "",
) -> dict[str, Any]:
    if confirmed is not True or confirmation != "PERMANENT_DELETE":
        raise JobDeletionConfirmationError("永久删除需要 confirmed=true 和 confirmation=PERMANENT_DELETE")
    ids = _normalize_job_ids(job_ids, required=True)
    run_conflicts = _scoring_run_conflicts(conn, set(ids))
    if run_conflicts:
        raise JobDeletionConflictError("岗位仍被评分任务引用，请先结束该评分任务", blocked=run_conflicts)
    rows = _job_rows_by_ids(conn, ids)
    found_ids = {str(row["id"]) for row in rows}
    not_found = [job_id for job_id in ids if job_id not in found_ids]
    if not_found:
        raise JobDeletionConflictError("存在不存在的岗位，未执行永久删除", not_found=not_found)
    not_deleted = [str(row["id"]) for row in rows if row.get("deleted_at") is None]
    if not_deleted:
        raise JobDeletionConflictError(
            "只能永久删除回收站中的岗位",
            blocked=[{"job_id": job_id, "reasons": ["岗位不在回收站"]} for job_id in not_deleted],
        )
    blocked: list[dict[str, Any]] = []
    for row in rows:
        reasons: list[str] = []
        if str(row.get("status") or "") in DELETION_PROTECTED_STATUSES:
            reasons.append(f"当前状态为 {row['status']}")
        reasons.extend(_history_protection_reasons(conn, str(row["id"])))
        if reasons:
            blocked.append({"job_id": str(row["id"]), "reasons": reasons})
    if blocked:
        raise JobDeletionConflictError("存在受保护岗位，批量永久删除已整体拒绝", blocked=blocked)
    with conn:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM history WHERE job_id IN ({placeholders})", ids)
        cursor = conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders}) AND deleted_at IS NOT NULL", ids)
        if cursor.rowcount != len(ids):
            raise JobDeletionConflictError("永久删除数量校验失败，事务已回滚")
    return {"requested_count": len(ids), "affected_count": len(ids)}


def insert_job_if_new(conn: sqlite3.Connection, job: dict[str, Any]) -> bool:
    """Insert a job atomically and return True only when a row was inserted.

    recruitment_type 一律在入库时按统一分类器重算：平台侧猜测（JS 关键词正则）
    不可靠，数据库为准（实习 = 标题/日薪信号；正式岗 = 月薪/社招/全职信号）。
    """
    from openjob.collection.models import classify_recruitment_type

    title = str(job.get("title") or "")
    values = {
        "id": str(job.get("id") or ""),
        "title": title,
        "company": str(job.get("company") or ""),
        "salary": job.get("salary", ""),
        "city": job.get("city", ""),
        "experience": job.get("experience", ""),
        "education": job.get("education", ""),
        "recruitment_type": classify_recruitment_type(
            title,
            str(job.get("experience") or ""),
            str(job.get("jd") or ""),
            str(job.get("salary") or ""),
        ),
        "jd": job.get("jd", ""),
        "hr_name": job.get("hr_name", ""),
        "hr_title": job.get("hr_title", ""),
        "hr_active": job.get("hr_active", ""),
        "company_size": job.get("company_size", ""),
        "company_industry": job.get("company_industry", ""),
        "url": job.get("url", ""),
        "source_platform": str(job.get("source_platform") or "boss"),
        "source_job_id": str(job.get("source_job_id") or "") or None,
        "source_keyword": job.get("source_keyword", ""),
        "source_city_code": job.get("source_city_code", ""),
    }
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO jobs (
            id, title, company, salary, city, experience, education, recruitment_type, jd,
            hr_name, hr_title, hr_active, company_size, company_industry, url,
            source_platform, source_job_id, source_keyword, source_city_code
        ) VALUES (
            :id, :title, :company, :salary, :city, :experience, :education, :recruitment_type, :jd,
            :hr_name, :hr_title, :hr_active, :company_size, :company_industry, :url,
            :source_platform, :source_job_id, :source_keyword, :source_city_code
        )
        """,
        values,
    )
    conn.commit()
    return cursor.rowcount == 1


def insert_job(conn: sqlite3.Connection, job: dict[str, Any]) -> bool:
    """Backward-compatible insert entry point; returns whether it was new."""
    return insert_job_if_new(conn, job)


def update_job_score(conn: sqlite3.Connection, job_id: str, score: int, reason: str) -> None:
    """Update job matching score."""
    conn.execute(
        "UPDATE jobs SET score = ?, score_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
        (score, reason, job_id)
    )
    conn.commit()


def update_job_greeting(conn: sqlite3.Connection, job_id: str, greeting: str) -> None:
    """Update job greeting message."""
    conn.execute(
        "UPDATE jobs SET greeting = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
        (greeting, job_id)
    )
    conn.commit()


def update_job_status(conn: sqlite3.Connection, job_id: str, status: str) -> None:
    """Update job status."""
    conn.execute(
        "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
        (status, job_id)
    )
    conn.commit()


def add_history(conn: sqlite3.Connection, job_id: str, action: str, detail: str = "") -> None:
    """Add a history record."""
    conn.execute(
        "INSERT INTO history (job_id, action, detail) VALUES (?, ?, ?)",
        (job_id, action, detail)
    )
    conn.commit()


def get_jobs_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    """Get all jobs with a given status."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = ? AND deleted_at IS NULL ORDER BY score DESC", (status,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_jobs_pending_confirmation(conn: sqlite3.Connection) -> list[dict]:
    """Get selected jobs whose greeting workflow is not complete."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status IN ('ready', 'approved')
          AND deleted_at IS NULL
          AND (greeting IS NULL OR TRIM(greeting) = '')
        ORDER BY score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def get_jobs_ready_to_send(conn: sqlite3.Connection) -> list[dict]:
    """Get jobs that have generated greetings and are ready to send."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status IN ('ready', 'approved')
          AND deleted_at IS NULL
          AND greeting IS NOT NULL
          AND TRIM(greeting) != ''
        ORDER BY score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def get_jobs_with_send_errors(conn: sqlite3.Connection) -> list[dict]:
    """Get jobs where greeting sending failed and can be retried."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE status = 'error'
          AND deleted_at IS NULL
          AND greeting IS NOT NULL
          AND TRIM(greeting) != ''
        ORDER BY updated_at DESC, score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def get_pending_scored_jobs(conn: sqlite3.Connection, threshold: int = 60) -> list[dict]:
    """Get jobs that passed scoring and are pending confirmation."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'scored' AND deleted_at IS NULL AND score >= ? ORDER BY score DESC",
        (threshold,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Get job status statistics."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM jobs WHERE deleted_at IS NULL GROUP BY status"
    ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


def _migrate_v1_1(conn: sqlite3.Connection) -> None:
    """Add v1.1 columns if they don't exist."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "quick_score" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN quick_score INTEGER DEFAULT 0")
    if "resume_path" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN resume_path TEXT DEFAULT NULL")
    conn.commit()


def _migrate_v1_2(conn: sqlite3.Connection) -> None:
    """Add recycle-bin metadata without rebuilding or rewriting job rows."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "deleted_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN deleted_at TIMESTAMP NULL")
    if "deleted_reason" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN deleted_reason TEXT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_deleted_at ON jobs(deleted_at)")
    conn.commit()


def _migrate_v1_3(conn: sqlite3.Connection) -> None:
    """Add source identity columns without rewriting existing BOSS ids."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    additions = {
        "source_platform": "TEXT NOT NULL DEFAULT 'boss'",
        "source_job_id": "TEXT NULL",
        "source_keyword": "TEXT NULL",
        "source_city_code": "TEXT NULL",
    }
    for name, definition in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_source_identity
        ON jobs(source_platform, source_job_id)
        WHERE source_job_id IS NOT NULL AND TRIM(source_job_id) <> ''
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source_platform ON jobs(source_platform)")
    conn.commit()


def _migrate_v1_4(conn: sqlite3.Connection) -> None:
    """Add education and recruitment-type fields without aggressive inference."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "education" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN education TEXT")
    if "recruitment_type" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN recruitment_type TEXT DEFAULT 'unknown'")
    searchable = "COALESCE(title, '') || ' ' || COALESCE(jd, '') || ' ' || COALESCE(experience, '')"
    conn.execute(f"""
        UPDATE jobs SET education = CASE
            WHEN {searchable} LIKE '%博士%' THEN '博士'
            WHEN {searchable} LIKE '%硕士%' THEN '硕士'
            WHEN {searchable} LIKE '%本科%' THEN '本科'
            WHEN {searchable} LIKE '%大专%' OR {searchable} LIKE '%专科%' THEN '大专'
            WHEN {searchable} LIKE '%学历不限%' OR {searchable} LIKE '%不限学历%' THEN '不限'
            ELSE education
        END
        WHERE education IS NULL OR TRIM(education) = ''
    """)
    conn.execute(f"""
        UPDATE jobs SET recruitment_type = CASE
            WHEN {searchable} LIKE '%校招%' OR {searchable} LIKE '%校园招聘%'
              OR {searchable} LIKE '%应届%' OR {searchable} LIKE '%毕业生%'
              OR {searchable} LIKE '%管培生%' OR {searchable} LIKE '%实习生%' THEN 'campus'
            WHEN {searchable} LIKE '%社招%' OR {searchable} LIKE '%社会招聘%' THEN 'experienced'
            ELSE 'unknown'
        END
        WHERE recruitment_type IS NULL OR TRIM(recruitment_type) = '' OR recruitment_type = 'unknown'
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_recruitment_type ON jobs(recruitment_type)")
    conn.commit()


def _migrate_v2_2(conn: sqlite3.Connection) -> None:
    """Reclassify recruitment_type with the 实习/正式岗 rules (salary-aware)."""
    from openjob.collection.models import classify_recruitment_type

    rows = conn.execute(
        "SELECT id, title, experience, jd, salary, recruitment_type FROM jobs"
    ).fetchall()
    changed = 0
    for row in rows:
        new_type = classify_recruitment_type(
            row["title"], row["experience"] or "", row["jd"] or "", row["salary"] or ""
        )
        if new_type != row["recruitment_type"]:
            conn.execute(
                "UPDATE jobs SET recruitment_type = ? WHERE id = ?", (new_type, row["id"])
            )
            changed += 1
    if changed:
        conn.commit()


def _migrate_v2_3(conn: sqlite3.Connection) -> None:
    """Add docx_path to resumes (Word 导出 alongside PDF)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(resumes)").fetchall()}
    if "docx_path" not in cols:
        conn.execute("ALTER TABLE resumes ADD COLUMN docx_path TEXT")
    conn.commit()


def _migrate_v2_4(conn: sqlite3.Connection) -> None:
    """base_resumes.template_path：保存上传的原始 Word 模板（排版复刻用）。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(base_resumes)").fetchall()}
    if "template_path" not in cols:
        conn.execute("ALTER TABLE base_resumes ADD COLUMN template_path TEXT")
    conn.commit()


def _migrate_platform_access_events(conn: sqlite3.Connection) -> None:
    """Scope PR #66 access counters to a platform without losing old BOSS events."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(platform_access_events)").fetchall()}
    if "platform" not in cols:
        conn.execute("ALTER TABLE platform_access_events ADD COLUMN platform TEXT NOT NULL DEFAULT 'boss'")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_platform_access_platform_stage_action "
        "ON platform_access_events(platform, stage, action, created_at)"
    )
    conn.commit()


def _init_scoring_runs(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scoring_runs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            status TEXT NOT NULL,
            options_json TEXT NOT NULL,
            remaining_job_ids_json TEXT NOT NULL,
            progress_json TEXT NOT NULL,
            pause_reason TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scoring_runs_status ON scoring_runs(status);
        """
    )
    conn.commit()


def _init_collection_runs(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS collection_runs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            status TEXT NOT NULL,
            options_json TEXT NOT NULL,
            platform_states_json TEXT NOT NULL,
            collected_job_ids_json TEXT NOT NULL,
            current_platform TEXT,
            stop_reason TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP NULL
        );
        CREATE INDEX IF NOT EXISTS idx_collection_runs_status ON collection_runs(status);
        """
    )
    conn.commit()


def update_job_quick_score(conn: sqlite3.Connection, job_id: str, quick_score: int) -> None:
    """Update job quick (pre-filter) score."""
    conn.execute(
        "UPDATE jobs SET quick_score = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
        (quick_score, job_id)
    )
    conn.commit()


def reset_ai_filtered_jobs(conn: sqlite3.Connection) -> int:
    """Move only AI-scored low-match jobs back to the pending queue."""
    cursor = conn.execute("""
        UPDATE jobs
        SET status = 'pending', score = 0, score_reason = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE status = 'filtered'
          AND deleted_at IS NULL
          AND COALESCE(score_reason, '') != ''
          AND score_reason NOT LIKE '预筛不通过:%'
          AND score_reason NOT LIKE 'AI评分失败:%'
          AND score_reason NOT LIKE 'AI 评分失败:%'
          AND score_reason NOT LIKE '评分失败:%'
    """)
    conn.commit()
    return cursor.rowcount


def add_risk_event(conn: sqlite3.Connection, event_type: str, detail: str = "") -> None:
    """Record a risk/anti-ban event."""
    conn.execute(
        "INSERT INTO risk_events (event_type, detail) VALUES (?, ?)",
        (event_type, detail)
    )
    conn.commit()


def count_platform_access_today(
    conn: sqlite3.Connection,
    *,
    platform: str = "boss",
    stage: str | None = None,
    action: str | None = None,
) -> int:
    """Count recorded platform page opens during the current local day."""
    clauses = [
        "datetime(created_at, 'localtime') >= datetime('now', 'localtime', 'start of day')",
        "platform = ?",
    ]
    params: list[str] = [str(platform or "boss")]
    if stage:
        clauses.append("stage = ?")
        params.append(stage)
    if action:
        clauses.append("action = ?")
        params.append(action)
    row = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM platform_access_events WHERE {' AND '.join(clauses)}",
        params,
    ).fetchone()
    return int(row["cnt"] if row else 0)


def add_platform_access(
    conn: sqlite3.Connection,
    stage: str,
    action: str,
    *,
    platform: str = "boss",
) -> None:
    """Record one platform page-open attempt without URLs or account data."""
    conn.execute(
        "INSERT INTO platform_access_events (platform, stage, action) VALUES (?, ?, ?)",
        (str(platform or "boss"), stage, action),
    )
    conn.commit()


def set_platform_safety_lock(
    conn: sqlite3.Connection,
    reason: str,
    *,
    minutes: int = 10,
) -> None:
    """Persist a temporary account-safety lock across task and process restarts."""
    locked_until = datetime.now(timezone.utc) + timedelta(minutes=max(int(minutes), 1))
    conn.execute(
        """
        INSERT INTO platform_safety_state (id, reason, locked_until, updated_at)
        VALUES (1, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            reason = excluded.reason,
            locked_until = excluded.locked_until,
            updated_at = CURRENT_TIMESTAMP
        """,
        (reason, locked_until.isoformat()),
    )
    conn.commit()


def get_active_platform_safety_lock(conn: sqlite3.Connection) -> dict[str, str] | None:
    """Return the active safety lock, clearing it after its cooldown expires."""
    row = conn.execute(
        "SELECT reason, locked_until FROM platform_safety_state WHERE id = 1"
    ).fetchone()
    if not row:
        return None
    try:
        locked_until = datetime.fromisoformat(str(row["locked_until"]))
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        locked_until = datetime.now(timezone.utc)
    if locked_until <= datetime.now(timezone.utc):
        conn.execute("DELETE FROM platform_safety_state WHERE id = 1")
        conn.commit()
        return None
    return {"reason": str(row["reason"]), "locked_until": locked_until.isoformat()}


def get_funnel_stats(conn: sqlite3.Connection, *, today: bool = False) -> dict[str, int]:
    """Get cumulative or local-calendar-day funnel counts for dashboard."""
    time_scope = (
        "datetime(created_at, 'localtime') >= datetime('now', 'localtime', 'start of day')"
        if today else "1 = 1"
    )
    scope = f"deleted_at IS NULL AND ({time_scope})"
    total = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope}").fetchone()["cnt"]
    prefilter_passed = conn.execute(f"""
        SELECT COUNT(*) as cnt FROM jobs
        WHERE {scope}
          AND (status != 'filtered' OR (status = 'filtered' AND score > 0))
    """).fetchone()["cnt"]
    ai_scored = conn.execute(f"""
        SELECT COUNT(*) as cnt FROM jobs
        WHERE {scope}
          AND (
            status IN ('scored', 'ready', 'approved', 'rejected', 'sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')
            OR (
                status = 'filtered'
                AND COALESCE(score_reason, '') != ''
                AND score_reason NOT LIKE '预筛不通过:%'
                AND score_reason NOT LIKE 'AI评分失败:%'
                AND score_reason NOT LIKE 'AI 评分失败:%'
                AND score_reason NOT LIKE '评分失败:%'
            )
          )
    """).fetchone()["cnt"]
    approved = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope} AND status IN ('approved', 'sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')").fetchone()["cnt"]
    sent = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope} AND status IN ('sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent')").fetchone()["cnt"]
    replied = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope} AND status IN ('replied', 'resume_sent', 'needs_resume')").fetchone()["cnt"]
    resume_sent = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope} AND status = 'resume_sent'").fetchone()["cnt"]
    needs_resume = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope} AND status = 'needs_resume'").fetchone()["cnt"]
    resume_generated = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope} AND resume_path IS NOT NULL AND TRIM(resume_path) != ''").fetchone()["cnt"]
    follow_up = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope} AND status = 'follow_up_sent'").fetchone()["cnt"]
    rejected = conn.execute(f"SELECT COUNT(*) as cnt FROM jobs WHERE {scope} AND status = 'rejected'").fetchone()["cnt"]
    return {"采集总数": total, "初筛通过": prefilter_passed, "AI评分": ai_scored, "人工确认": approved, "发送": sent, "回复": replied, "简历已发": resume_sent, "待手动发简历": needs_resume, "简历生成": resume_generated, "跟进": follow_up, "拒绝": rejected}


def get_daily_activity(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """Get daily activity for last N days."""
    rows = conn.execute("""
        SELECT date(h.created_at) as day, h.action, COUNT(*) as cnt
        FROM history h
        JOIN jobs j ON h.job_id = j.id
        WHERE h.created_at >= date('now', ?)
          AND j.deleted_at IS NULL
        GROUP BY date(h.created_at), h.action
        ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()
    return [dict(row) for row in rows]


def get_top_companies(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """Get top companies by average score."""
    rows = conn.execute("""
        SELECT company, ROUND(AVG(score), 0) as avg_score, COUNT(*) as job_count
        FROM jobs WHERE deleted_at IS NULL AND score > 0
        GROUP BY company
        ORDER BY avg_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_recent_history(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Get recent history entries with job info."""
    rows = conn.execute("""
        SELECT h.id, h.job_id, h.action, h.detail, h.created_at, j.company, j.title,
               j.resume_path,
               CASE
                 WHEN h.action = 'resume_failed'
                  AND (
                    (j.resume_path IS NOT NULL AND TRIM(j.resume_path) != '')
                    OR EXISTS (
                      SELECT 1
                      FROM history r
                      WHERE r.job_id = h.job_id
                        AND r.action IN ('needs_resume', 'resume_sent')
                        AND r.id > h.id
                    )
                  )
                 THEN 1
                 ELSE 0
               END AS resolved
        FROM history h
        JOIN jobs j ON h.job_id = j.id
        WHERE j.deleted_at IS NULL
        ORDER BY h.created_at DESC, h.id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_unresolved_resume_failures(conn: sqlite3.Connection) -> list[dict]:
    """Get the latest resume generation failure for jobs not resolved by a later success."""
    rows = conn.execute("""
        SELECT h.id, h.job_id, h.action, h.detail, h.created_at, j.company, j.title,
               j.resume_path, 0 AS resolved
        FROM history h
        JOIN jobs j ON h.job_id = j.id
        WHERE h.action = 'resume_failed'
          AND j.deleted_at IS NULL
          AND h.id = (
            SELECT MAX(f.id)
            FROM history f
            WHERE f.job_id = h.job_id
              AND f.action = 'resume_failed'
          )
          AND (j.resume_path IS NULL OR TRIM(j.resume_path) = '')
          AND NOT EXISTS (
            SELECT 1
            FROM history r
            WHERE r.job_id = h.job_id
              AND r.action IN ('needs_resume', 'resume_sent')
              AND r.id > h.id
          )
        ORDER BY h.created_at DESC, h.id DESC
    """).fetchall()
    return [dict(row) for row in rows]


def count_unresolved_reply_pending(conn: sqlite3.Connection) -> int:
    """Count latest reply_pending rows that have not been resolved for each job."""
    row = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM history h
        WHERE h.action = 'reply_pending'
          AND EXISTS (
            SELECT 1 FROM jobs j WHERE j.id = h.job_id AND j.deleted_at IS NULL
          )
          AND h.id = (
            SELECT MAX(p.id)
            FROM history p
            WHERE p.job_id = h.job_id
              AND p.action = 'reply_pending'
          )
          AND NOT EXISTS (
            SELECT 1
            FROM history r
            WHERE r.job_id = h.job_id
              AND r.action IN ('reply_dismissed', 'replied', 'auto_replied')
              AND r.id > h.id
          )
    """).fetchone()
    return int(row["cnt"] or 0)


def count_unresolved_monitor_items(conn: sqlite3.Connection) -> int:
    """Count unresolved reply suggestions and resume generation failures."""
    return count_unresolved_reply_pending(conn) + len(get_unresolved_resume_failures(conn))


def backup_database(keep: int = 7, backup_dir: Path | None = None) -> Path | None:
    """用 SQLite 在线备份 API 落一份快照到 data/backups/，保留最近 keep 份。

    在线备份允许在面板运行中执行；失败返回 None，绝不阻塞采集主流程。
    """
    import shutil
    from datetime import datetime as _dt

    src = DB_PATH
    if not src.exists():
        return None
    target_dir = backup_dir or (src.parent / "backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
    import uuid as _uuid

    target = target_dir / f"openjob-{stamp}-{_uuid.uuid4().hex[:6]}.db"
    try:
        conn = sqlite3.connect(str(src))
        dest = sqlite3.connect(str(target))
        conn.backup(dest)
        dest.close()
        conn.close()
    except sqlite3.Error:
        target.unlink(missing_ok=True)
        return None
    backups = sorted(target_dir.glob("openjob-*.db"))
    for old in backups[:-keep] if len(backups) > keep else []:
        old.unlink(missing_ok=True)
    return target


RESUME_VERSION_STATUSES = {"parsing", "drafting", "review", "exported", "sent", "failed"}


def _migrate_v2_0(conn: sqlite3.Connection) -> None:
    """Add per-job tailored resume versions (review/diff workflow)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS resumes (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'parsing',
            jd_analysis_json TEXT,
            match_report_json TEXT,
            diff_json TEXT,
            base_md TEXT,
            content_md TEXT,
            pdf_path TEXT,
            risk_flags_json TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(job_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_resumes_job_id ON resumes(job_id);
    """)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "resume_status" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN resume_status TEXT")
    if "resume_id" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN resume_id TEXT")
    conn.commit()


def _migrate_v2_1(conn: sqlite3.Connection) -> None:
    """Add named base resumes (multi-direction resumes) and engine base pointer."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS base_resumes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            direction TEXT DEFAULT '',
            content_md TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(resumes)").fetchall()}
    if "base_resume_id" not in cols:
        conn.execute("ALTER TABLE resumes ADD COLUMN base_resume_id TEXT")
    conn.commit()


def base_resume_row(conn: sqlite3.Connection, base_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM base_resumes WHERE id = ?", (base_id,)).fetchone()
    return dict(row) if row else None


def list_base_resumes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, direction, is_default, LENGTH(content_md) AS chars, created_at, updated_at "
        "FROM base_resumes ORDER BY is_default DESC, created_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def insert_base_resume(
    conn: sqlite3.Connection,
    *,
    base_id: str,
    name: str,
    direction: str,
    content_md: str,
    is_default: bool,
) -> None:
    if is_default:
        conn.execute("UPDATE base_resumes SET is_default = 0 WHERE is_default = 1")
    conn.execute(
        "INSERT INTO base_resumes (id, name, direction, content_md, is_default) VALUES (?, ?, ?, ?, ?)",
        (base_id, name, direction, content_md, 1 if is_default else 0),
    )
    conn.commit()


def delete_base_resume(conn: sqlite3.Connection, base_id: str) -> bool:
    cursor = conn.execute("DELETE FROM base_resumes WHERE id = ?", (base_id,))
    conn.commit()
    return cursor.rowcount > 0


def update_base_resume(
    conn: sqlite3.Connection,
    base_id: str,
    *,
    name: str | None = None,
    direction: str | None = None,
    is_default: bool | None = None,
    content_md: str | None = None,
    template_path: str | None = None,
) -> bool:
    assignments = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[object] = []
    if name is not None:
        assignments.append("name = ?")
        params.append(name)
    if direction is not None:
        assignments.append("direction = ?")
        params.append(direction)
    if content_md is not None:
        assignments.append("content_md = ?")
        params.append(content_md)
    if template_path is not None:
        assignments.append("template_path = ?")
        params.append(template_path)
    if is_default is True:
        conn.execute("UPDATE base_resumes SET is_default = 0 WHERE is_default = 1")
        assignments.append("is_default = 1")
    elif is_default is False:
        assignments.append("is_default = 0")
    params.append(base_id)
    cursor = conn.execute(f"UPDATE base_resumes SET {', '.join(assignments)} WHERE id = ?", params)
    conn.commit()
    return cursor.rowcount > 0


def resume_version_row(conn: sqlite3.Connection, resume_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
    return dict(row) if row else None


def resume_versions_for_job(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM resumes WHERE job_id = ? ORDER BY version DESC", (job_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def latest_resume_version(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM resumes WHERE job_id = ? ORDER BY version DESC LIMIT 1", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def next_resume_version(conn: sqlite3.Connection, job_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM resumes WHERE job_id = ?", (job_id,)
    ).fetchone()
    return int(row["v"] or 0) + 1


def update_resume_version(
    conn: sqlite3.Connection,
    resume_id: str,
    *,
    status: str | None = None,
    jd_analysis_json: str | None = None,
    match_report_json: str | None = None,
    diff_json: str | None = None,
    base_md: str | None = None,
    base_resume_id: str | None = None,
    content_md: str | None = None,
    pdf_path: str | None = None,
    docx_path: str | None = None,
    risk_flags_json: str | None = None,
    error: str | None = None,
) -> None:
    """Update mutable resume fields; None leaves the column untouched."""
    assignments = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[object] = []
    for column, value in (
        ("status", status),
        ("jd_analysis_json", jd_analysis_json),
        ("match_report_json", match_report_json),
        ("diff_json", diff_json),
        ("base_md", base_md),
        ("base_resume_id", base_resume_id),
        ("content_md", content_md),
        ("pdf_path", pdf_path),
        ("docx_path", docx_path),
        ("risk_flags_json", risk_flags_json),
        ("error", error),
    ):
        if value is not None:
            assignments.append(f"{column} = ?")
            params.append(value)
    params.append(resume_id)
    conn.execute(f"UPDATE resumes SET {', '.join(assignments)} WHERE id = ?", params)
    conn.commit()


def set_job_resume_pointer(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    resume_id: str | None,
    resume_status: str | None,
) -> None:
    conn.execute(
        "UPDATE jobs SET resume_id = ?, resume_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (resume_id, resume_status, job_id),
    )
    conn.commit()


def get_jobs_needing_resume(conn: sqlite3.Connection) -> list[dict]:
    """Get jobs waiting for manual resume send (tailored PDF generated, not yet sent)."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'needs_resume' AND deleted_at IS NULL ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]
