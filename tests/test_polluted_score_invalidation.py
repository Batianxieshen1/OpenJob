"""Polluted-score invalidation semantics (2026-09-26 incident follow-up).

规则：created_at >= 2026-09-09 且有分且 score_reason 不带 [重评] 前缀 = 污染分，
清零并标记作废；ready 中被清分的岗位降级 filtered；approved/rejected/sent 等
人工决策或历史事实状态保持状态不动。
"""

import sqlite3
from pathlib import Path

import pytest

import openjob.db as db_mod

INVALIDATE_WHERE = (
    "created_at >= '2026-09-09' AND deleted_at IS NULL AND score > 0 "
    "AND (score_reason IS NULL OR score_reason NOT LIKE '[重评]%')"
)
INVALIDATED_REASON = "旧评分已作废（评分源事故 2026-09-26 清理）"


def _insert_job(conn: sqlite3.Connection, jid: str, status: str, score: int, reason: str, created: str) -> None:
    conn.execute(
        "INSERT INTO jobs (id, title, company, status, score, score_reason, created_at) "
        "VALUES (?, 'T', 'C', ?, ?, ?, ?)",
        (jid, status, score, reason, created),
    )


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "inv.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    conn = db_mod.get_db(path)
    yield conn
    conn.close()


def _invalidate(conn: sqlite3.Connection) -> list[str]:
    """执行作废，返回被降级 ready→filtered 的 job_id 列表。"""
    rows = conn.execute(f"SELECT id, status FROM jobs WHERE {INVALIDATE_WHERE}").fetchall()
    downgraded: list[str] = []
    for row in rows:
        conn.execute(
            "UPDATE jobs SET score = 0, score_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (INVALIDATED_REASON, row["id"]),
        )
        if row["status"] == "ready":
            db_mod.transition_job_status(conn, row["id"], "filtered")
            downgraded.append(row["id"])
    conn.commit()
    return downgraded


class TestInvalidation:
    def test_polluted_scores_are_cleared(self, db):
        _insert_job(db, "a1", "approved", 30, "职责5/40", "2026-09-20")
        _insert_job(db, "a2", "rejected", 72, "职责9/40", "2026-09-21")
        downgraded = _invalidate(db)
        assert downgraded == []
        rows = {r["id"]: dict(r) for r in db.execute("SELECT id, score, score_reason FROM jobs").fetchall()}
        assert rows["a1"]["score"] == 0 and "作废" in rows["a1"]["score_reason"]
        assert rows["a2"]["score"] == 0 and "作废" in rows["a2"]["score_reason"]

    def test_rescored_and_real_scores_survive(self, db):
        _insert_job(db, "b1", "ready", 67, "[重评] 职责28/40", "2026-09-20")
        _insert_job(db, "b2", "sent", 85, "职责32/40", "2026-09-05")  # 污染期前真实评分
        downgraded = _invalidate(db)
        assert downgraded == []
        rows = {r["id"]: dict(r) for r in db.execute("SELECT id, score FROM jobs").fetchall()}
        assert rows["b1"]["score"] == 67
        assert rows["b2"]["score"] == 85

    def test_ready_with_polluted_score_downgrades(self, db):
        _insert_job(db, "c1", "ready", 30, "职责8/40", "2026-09-22")
        downgraded = _invalidate(db)
        assert downgraded == ["c1"]
        row = db.execute("SELECT status, score FROM jobs WHERE id='c1'").fetchone()
        assert row["status"] == "filtered" and row["score"] == 0

    def test_human_decisions_keep_status(self, db):
        _insert_job(db, "d1", "approved", 40, "职责6/40", "2026-09-18")
        _insert_job(db, "d2", "sent", 55, "职责10/40", "2026-09-19")
        downgraded = _invalidate(db)
        assert downgraded == []
        rows = {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs").fetchall()}
        assert rows["d1"] == "approved" and rows["d2"] == "sent"

    def test_deleted_jobs_untouched(self, db):
        db.execute(
            "INSERT INTO jobs (id, title, company, status, score, score_reason, created_at, deleted_at) "
            "VALUES ('e1', 'T', 'C', 'filtered', 20, '职责3/40', '2026-09-20', CURRENT_TIMESTAMP)"
        )
        downgraded = _invalidate(db)
        assert downgraded == []
        row = db.execute("SELECT score FROM jobs WHERE id='e1'").fetchone()
        assert row["score"] == 20  # 软删除岗位不参与作废
