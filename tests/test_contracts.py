"""WP-S0 契约测试：迁移白名单、重启收敛、单一事实来源。"""

import tempfile
import unittest
from pathlib import Path

from openjob import contracts
from openjob.contracts import (
    IllegalJobStatusTransition,
    JOB_STATUS_LABELS,
    validate_job_status_transition,
)
from openjob.db import get_db, insert_job, transition_job_status
from openjob.scheduled_collection_store import (
    claim_scheduled_run,
    get_scheduled_run,
    mark_orphaned_scheduled_runs_interrupted,
    update_scheduled_run,
)
from openjob.tracker import status as tracker_status
from openjob.web.tasks import WorkbenchTaskRunner


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "数据分析师",
        "company": "测试公司",
        "salary": "10-15K",
        "city": "广州",
        "jd": "负责业务数据分析",
        "url": "https://example.com/job",
    }


class JobStatusTransitionWhitelistTests(unittest.TestCase):
    def test_plan_mandated_transitions_are_whitelisted(self):
        allowed = [
            ("pending", "scored"), ("pending", "filtered"), ("pending", "error"),
            ("scored", "ready"), ("scored", "filtered"),
            ("ready", "approved"), ("ready", "rejected"), ("ready", "skipped"),
            ("approved", "sent"), ("approved", "error"), ("approved", "skipped"),
            ("approved", "stale"),
            ("sent", "replied"), ("sent", "resume_sent"), ("sent", "follow_up_sent"),
        ]
        for current, new in allowed:
            validate_job_status_transition(current, new)

    def test_measured_reality_transitions_are_whitelisted(self):
        allowed = [
            ("pending", "ready"),
            ("approved", "ready"),
            ("filtered", "ready"),
            ("ready", "filtered"),
            ("ready", "sent"),
            ("pending", "rejected"),
            ("scored", "rejected"),
            ("filtered", "rejected"),
            ("sent", "rejected"),
            ("sent", "needs_resume"),
            ("error", "sent"),
            ("error", "error"),
            ("approved", "approved"),
            ("resume_sent", "replied"),
            ("needs_resume", "resume_sent"),
            ("replied", "needs_resume"),
        ]
        for current, new in allowed:
            validate_job_status_transition(current, new)

    def test_forbidden_transitions_raise(self):
        forbidden = [
            ("sent", "pending"), ("sent", "ready"), ("sent", "approved"),
            ("approved", "pending"), ("replied", "sent"), ("rejected", "ready"),
            ("skipped", "sent"), ("filtered", "approved"),
        ]
        for current, new in forbidden:
            with self.assertRaises(IllegalJobStatusTransition, msg=f"{current}->{new}"):
                validate_job_status_transition(current, new)

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(IllegalJobStatusTransition):
            validate_job_status_transition("pending", "invented_status")
        with self.assertRaises(IllegalJobStatusTransition):
            validate_job_status_transition("legacy_unknown", "ready")

    def test_manual_external_send_channel(self):
        for current in sorted(contracts.MANUAL_EXTERNAL_SEND_SOURCES):
            validate_job_status_transition(current, "sent", via="manual_external")
        with self.assertRaises(IllegalJobStatusTransition):
            validate_job_status_transition("replied", "sent", via="manual_external")
        with self.assertRaises(IllegalJobStatusTransition):
            validate_job_status_transition("pending", "approved", via="manual_external")

    def test_transition_job_status_persists_and_rejects_illegal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job(db, _job("j1"))
                transition_job_status(db, "j1", "ready")
                row = db.execute("SELECT status FROM jobs WHERE id = 'j1'").fetchone()
                self.assertEqual(row["status"], "ready")

                with self.assertRaises(IllegalJobStatusTransition):
                    transition_job_status(db, "j1", "pending")
                row = db.execute("SELECT status FROM jobs WHERE id = 'j1'").fetchone()
                self.assertEqual(row["status"], "ready")

                transition_job_status(db, "j1", "approved")
                transition_job_status(db, "j1", "sent")
                transition_job_status(db, "j1", "replied")
                with self.assertRaises(IllegalJobStatusTransition):
                    transition_job_status(db, "j1", "pending")
            finally:
                db.close()

    def test_manual_external_send_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                insert_job(db, _job("zhilian-1"))
                transition_job_status(db, "zhilian-1", "sent", via="manual_external")
                row = db.execute("SELECT status FROM jobs WHERE id = 'zhilian-1'").fetchone()
                self.assertEqual(row["status"], "sent")
            finally:
                db.close()

    def test_missing_job_is_silent_noop_like_legacy_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = get_db(Path(tmp) / "openjob.db")
            try:
                transition_job_status(db, "ghost", "sent")
            finally:
                db.close()


class RestartConvergenceTests(unittest.TestCase):
    def test_orphaned_scheduled_runs_converge_to_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            self.assertTrue(claim_scheduled_run(db_path, "2026-09-14", "09:30"))
            update_scheduled_run(db_path, "2026-09-14", "09:30", status="running", task_id="t1")
            count = mark_orphaned_scheduled_runs_interrupted(db_path)
            self.assertEqual(count, 1)
            run = get_scheduled_run(db_path, "2026-09-14", "09:30")
            self.assertEqual(run["status"], "interrupted")
            self.assertIn("重启", run["reason"])

    def test_terminal_runs_are_not_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "openjob.db"
            claim_scheduled_run(db_path, "2026-09-14", "10:00")
            update_scheduled_run(db_path, "2026-09-14", "10:00", status="completed", finished=True)
            self.assertEqual(mark_orphaned_scheduled_runs_interrupted(db_path), 0)
            self.assertEqual(get_scheduled_run(db_path, "2026-09-14", "10:00")["status"], "completed")


class WorkbenchTaskTerminalMetadataTests(unittest.TestCase):
    def test_completed_task_carries_finished_at(self):
        runner = WorkbenchTaskRunner(executors={"collect": lambda task, config: None})
        snapshot = runner.start("collect", {})
        runner.wait(timeout=5)
        final = runner.get(snapshot["id"])
        self.assertEqual(final["status"], "completed")
        self.assertTrue(final["finished_at"])
        self.assertIn("finished_at", final)

    def test_failed_task_carries_error_count_and_finished_at(self):
        def boom(task, config):
            raise RuntimeError("模拟崩溃")

        runner = WorkbenchTaskRunner(executors={"collect": boom})
        snapshot = runner.start("collect", {})
        runner.wait(timeout=5)
        final = runner.get(snapshot["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["error"], "模拟崩溃")
        self.assertEqual(final["error_count"], 1)
        self.assertTrue(final["finished_at"])


class SingleSourceOfTruthTests(unittest.TestCase):
    def test_tracker_labels_are_contract_reexport(self):
        self.assertIs(tracker_status.STATUS_LABELS, JOB_STATUS_LABELS)

    def test_frontend_status_labels_match_contract(self):
        frontend = (
            Path(__file__).resolve().parents[1]
            / "src" / "openjob" / "web" / "frontend" / "src" / "lib" / "status.ts"
        )
        text = frontend.read_text(encoding="utf-8")
        block = text.split("STATUS_LABELS", 1)[1].split("}", 1)[0]
        parsed = {}
        for line in block.splitlines()[1:]:
            line = line.strip().rstrip(",")
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            parsed[key.strip().strip("'\"")] = value.strip().strip("'\"")
        self.assertEqual(parsed, JOB_STATUS_LABELS)

    def test_all_generators_share_one_marker_list(self):
        from openjob.ai import fact_policy, greeter
        from openjob.ai.resume_engine import bases, engine

        self.assertIs(greeter.TEMPLATE_RESUME_MARKERS, fact_policy.TEMPLATE_RESUME_MARKERS)
        self.assertIs(engine.TEMPLATE_RESUME_MARKERS, fact_policy.TEMPLATE_RESUME_MARKERS)
        self.assertIs(bases.TEMPLATE_RESUME_MARKERS, fact_policy.TEMPLATE_RESUME_MARKERS)


if __name__ == "__main__":
    unittest.main()
