"""Scoring provenance: every scoring run records which resume it used.

2026-09 incident follow-up: template resume shadowed real scoring for weeks
because runs carried no resume fingerprint.
"""

from pathlib import Path

import pytest

import openjob.db as db_mod
from openjob.ai import scorer
from openjob.scoring_run_store import (
    create_scoring_run,
    get_scoring_run,
    update_scoring_run,
)


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    path = tmp_path / "prov.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    conn = db_mod.get_db(path)
    yield conn, path
    conn.close()


class TestResumeProvenance:
    def test_labels_trusted_file_source(self, tmp_path):
        p = tmp_path / "mine.md"
        p.write_text("林庆涛 真实简历内容", encoding="utf-8")
        config = {"profile": {"resume_path": str(p)}}
        source, digest = scorer.resume_provenance(config, "林庆涛 真实简历内容")
        assert source == f"file:{p}"
        assert len(digest) == 16

    def test_labels_base_resume_fallback(self):
        source, digest = scorer.resume_provenance({"profile": {"resume_path": ""}}, "某底稿内容")
        assert source == "base_resume:default"
        assert len(digest) == 16

    def test_labels_template_as_base_resume_fallback(self, tmp_path):
        """模板路径不可信 → 指纹必须落在实际使用的底稿上，而非模板。"""
        p = tmp_path / "poison.md"
        p.write_text("张三 模板", encoding="utf-8")
        config = {"profile": {"resume_path": str(p)}}
        source, digest = scorer.resume_provenance(config, "真实底稿内容")
        assert source == "base_resume:default"

    def test_empty_resume(self):
        source, digest = scorer.resume_provenance({"profile": {}}, "")
        assert source == "none"
        assert digest == ""

    def test_digest_changes_with_content(self):
        _, d1 = scorer.resume_provenance({"profile": {}}, "内容A")
        _, d2 = scorer.resume_provenance({"profile": {}}, "内容B")
        assert d1 != d2


class TestRunProvenanceRecorded:
    def test_update_scoring_run_persists_provenance(self, db_env):
        conn, path = db_env
        create_scoring_run(path, run_id="r1", options={}, job_ids=["j1"])
        source, digest = scorer.resume_provenance({"profile": {}}, "底稿X")
        update_scoring_run(path, "r1", resume_source=source, resume_sha256=digest)
        got = get_scoring_run(path, "r1")
        assert got["resume_source"] == "base_resume:default"
        assert got["resume_sha256"] == digest

    def test_scored_at_written_by_update_job_score(self, db_env):
        conn, _ = db_env
        conn.execute(
            "INSERT INTO jobs (id, title, company, status, created_at) VALUES ('j1', 'T', 'C', 'pending', CURRENT_TIMESTAMP)"
        )
        conn.commit()
        db_mod.update_job_score(conn, "j1", 50, "r")
        row = conn.execute("SELECT score, scored_at FROM jobs WHERE id='j1'").fetchone()
        assert row["score"] == 50
        assert row["scored_at"] is not None

    def test_migration_adds_columns_idempotent(self, db_env):
        conn, path = db_env
        cols_runs = {r[1] for r in conn.execute("PRAGMA table_info(scoring_runs)").fetchall()}
        cols_jobs = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert {"resume_source", "resume_sha256"} <= cols_runs
        assert "scored_at" in cols_jobs
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == db_mod.SCHEMA_VERSION
        # 幂等：重复跑迁移不报错
        db_mod._migrate_v2_10(conn)
