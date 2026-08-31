"""第一批小功能测试：数据库备份、用量记录、桌面通知、市场统计、拒绝拉黑。"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.db import backup_database, get_db, insert_job


class BackupTests(unittest.TestCase):
    def test_backup_creates_snapshot_and_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = get_db(root / "data" / "openjob.db")
            insert_job(db, {"id": "b1", "title": "T", "company": "C", "jd": "j", "url": "u"})
            db.close()

            src = root / "data" / "openjob.db"
            backup_dir = root / "data" / "backups"
            import openjob.db as db_mod

            with patch.object(db_mod, "DB_PATH", src):
                for _ in range(9):
                    target = backup_database(keep=7, backup_dir=backup_dir)
                    self.assertIsNotNone(target)

            files = sorted(backup_dir.glob("openjob-*.db"))
            self.assertEqual(len(files), 7)
            check = sqlite3.connect(str(files[-1]))
            count = check.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            check.close()
            self.assertEqual(count, 1)

    def test_backup_missing_source_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            import openjob.db as db_mod

            with patch.object(db_mod, "DB_PATH", Path(tmp) / "ghost.db"):
                self.assertIsNone(backup_database(backup_dir=Path(tmp) / "b"))


class UsageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.usage_file = Path(self._tmp.name) / "usage.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_and_summarize(self):
        from openjob.ai.usage import record_usage, summarize_usage

        record_usage("score", "deepseek-chat", {"prompt_tokens": 1000, "completion_tokens": 500}, usage_file=self.usage_file)
        record_usage("greeting", "deepseek-chat", {"prompt_tokens": 200, "completion_tokens": 300}, usage_file=self.usage_file)
        record_usage("skip", "deepseek-chat", {}, usage_file=self.usage_file)  # 无用量不记录

        summary = summarize_usage(7, usage_file=self.usage_file)
        self.assertEqual(summary["total"]["calls"], 2)
        self.assertEqual(summary["total"]["prompt_tokens"], 1200)
        self.assertEqual(summary["total"]["completion_tokens"], 800)
        # 预估费用：(1200/1e6*1) + (800/1e6*2) = 0.0028
        self.assertAlmostEqual(summary["total"]["estimated_cost"], 0.0028, places=4)
        self.assertEqual(len(summary["days"]), 1)

    def test_summarize_empty_file(self):
        from openjob.ai.usage import summarize_usage

        summary = summarize_usage(7, usage_file=self.usage_file)
        self.assertEqual(summary["total"]["calls"], 0)
        self.assertEqual(summary["days"], [])


class NotifyTests(unittest.TestCase):
    def test_disabled_by_config(self):
        from openjob.notify import notify_desktop

        with patch("openjob.notify.subprocess.run") as run_mock:
            ok = notify_desktop("t", "m", {"monitor": {"desktop_notify": False}})
        self.assertFalse(ok)
        run_mock.assert_not_called()

    def test_invokes_powershell_with_encoded_command(self):
        from openjob.notify import notify_desktop

        with patch("openjob.notify.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            ok = notify_desktop("标题", "内容", {"monitor": {"desktop_notify": True}})

        self.assertTrue(ok)
        args = run_mock.call_args.args[0]
        self.assertEqual(args[0], "powershell")
        self.assertIn("-EncodedCommand", args)
        import base64

        script = base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16-le")
        self.assertIn("标题", script)
        self.assertIn("内容", script)

    def test_failure_returns_false(self):
        from openjob.notify import notify_desktop

        with patch("openjob.notify.subprocess.run", side_effect=OSError("no powershell")):
            self.assertFalse(notify_desktop("t", "m"))


class MarketStatsTests(unittest.TestCase):
    def _rows(self):
        return [
            {"company": "A公司", "salary": "10-15K", "city": "广州", "education": "本科",
             "experience": "1-3年", "recruitment_type": "experienced", "source_platform": "boss",
             "jd": "要求 Python SQL 数据分析，五险一金 双休"},
            {"company": "B公司", "salary": "150-200元/天", "city": "佛山", "education": "本科",
             "experience": "经验不限", "recruitment_type": "campus", "source_platform": "boss",
             "jd": "熟悉 Excel 可视化，转正机会，包吃"},
            {"company": "A公司", "salary": "", "city": "广州", "education": "大专",
             "experience": "", "recruitment_type": "campus", "source_platform": "boss",
             "jd": "内容运营 小红书"},
        ]

    def test_compute_market_stats_dimensions(self):
        from openjob.web.market_stats import compute_market_stats

        stats = compute_market_stats(self._rows())
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["platform"][0]["name"], "boss")
        self.assertEqual(stats["city"][0]["name"], "广州")
        # 薪资分桶：10-15K 一桶、日薪实习一桶、未标注一桶
        salary_names = [item["name"] for item in stats["salary"]]
        self.assertIn("8-12K", salary_names)  # 10-15K 按下限 10 归入 8-12K 桶
        self.assertIn("日薪实习", salary_names)
        self.assertIn("未标注", salary_names)
        self.assertEqual(stats["recruitment"][0]["name"], "campus")
        self.assertEqual(stats["top_companies"][0]["name"], "A公司")
        skill_names = [item["name"] for item in stats["skill_freq"]]
        self.assertIn("Python", skill_names)
        self.assertIn("SQL", skill_names)
        welfare_names = [item["name"] for item in stats["welfare_freq"]]
        self.assertIn("五险一金", welfare_names)
        self.assertIn("包吃", welfare_names)

    def test_compute_empty(self):
        from openjob.web.market_stats import compute_market_stats

        self.assertEqual(compute_market_stats([]), {"total": 0})


class RejectBlocklistTests(unittest.TestCase):
    def setUp(self):
        from openjob.web import server

        self.server = server
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._original = server.BASE_DIR
        server.set_base_dir(self.root)

    def tearDown(self):
        self.server.set_base_dir(self._original)
        self._tmp.cleanup()

    def _request(self, body: dict):
        import io

        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status

        raw = json.dumps(body).encode("utf-8")
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/workbench/reject",
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(raw)),
            "CONTENT_TYPE": "application/json",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(raw),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        resp = self.server.app(environ, start_response)
        payload = b"".join(c if isinstance(c, bytes) else c.encode() for c in resp).decode("utf-8")
        return int(status_headers["status"].split()[0]), json.loads(payload)

    def test_reject_with_blocklist_updates_config(self):
        import yaml

        db = get_db(self.server.DATA_DIR / "openjob.db")
        insert_job(db, {"id": "rej-1", "title": "运营实习生", "company": "黑名单公司", "jd": "j", "url": "u"})
        db.close()
        (self.root / "config.yaml").write_text(yaml.safe_dump({"profile": {}}), encoding="utf-8")

        status, body = self._request({"job_ids": ["rej-1"], "block_companies": ["黑名单公司"]})

        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["blocked_companies"], ["黑名单公司"])
        saved = yaml.safe_load((self.root / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(saved["profile"]["blocked_companies"], ["黑名单公司"])

        # 再拒一次同公司：不重复追加
        db = get_db(self.server.DATA_DIR / "openjob.db")
        insert_job(db, {"id": "rej-2", "title": "运营实习生2", "company": "黑名单公司", "jd": "j", "url": "u2"})
        db.close()
        status, body = self._request({"job_ids": ["rej-2"], "block_companies": ["黑名单公司"]})
        self.assertEqual(body["blocked_new"], 0)


class ApproveTests(unittest.TestCase):
    """人工放行：filtered → ready，用户判断优先于 AI 评分。"""

    def setUp(self):
        from openjob.web import server

        self.server = server
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._original = server.BASE_DIR
        server.set_base_dir(self.root)

    def tearDown(self):
        self.server.set_base_dir(self._original)
        self._tmp.cleanup()

    def _request(self, path: str):
        import io

        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status

        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(b""),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        resp = self.server.app(environ, start_response)
        payload = b"".join(c if isinstance(c, bytes) else c.encode() for c in resp).decode("utf-8")
        return int(status_headers["status"].split()[0]), json.loads(payload)

    def test_approve_moves_filtered_to_ready(self):
        from openjob.web import server

        db = get_db(self.server.DATA_DIR / "openjob.db")
        insert_job(db, {"id": "ap-1", "title": "数据实习生", "company": "小公司", "jd": "j", "url": "u"})
        db.execute("UPDATE jobs SET status = 'filtered', score = 55 WHERE id = 'ap-1'")
        db.commit()
        db.close()

        status, body = self._request("/api/jobs/ap-1/approve")
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])

        check = get_db(self.server.DATA_DIR / "openjob.db")
        row = dict(check.execute("SELECT status FROM jobs WHERE id = 'ap-1'").fetchone())
        history = check.execute("SELECT detail FROM history WHERE job_id = 'ap-1' AND action = 'approved'").fetchone()
        check.close()
        self.assertEqual(row["status"], "ready")
        self.assertIn("人工放行", history["detail"])

    def test_approve_rejects_non_filtered_job(self):
        from openjob.web import server

        db = get_db(self.server.DATA_DIR / "openjob.db")
        insert_job(db, {"id": "ap-2", "title": "T", "company": "C", "jd": "j", "url": "u2"})
        db.close()
        status, body = self._request("/api/jobs/ap-2/approve")
        self.assertEqual(status, 409)
        self.assertIn("只有被 AI 过滤", body["error"])


class RecruitmentFilterValidationTests(unittest.TestCase):
    def test_invalid_recruitment_filter_rejected(self):
        from openjob.collection.orchestrator import normalize_collection_options

        config = {"search": {"keywords": ["实习"], "cities": ["广州"]}}
        with self.assertRaises(ValueError):
            normalize_collection_options(config, {
                "platforms": {"boss": {"search": {"keywords": ["实习"], "cities": ["广州"], "recruitment_filter": "only-internship"}}}
            })

    def test_valid_recruitment_filter_passes_through(self):
        from openjob.collection.orchestrator import normalize_collection_options

        options = normalize_collection_options({"search": {"keywords": ["实习"], "cities": ["广州"]}}, {
            "platforms": {"boss": {"search": {"keywords": ["实习"], "cities": ["广州"], "recruitment_filter": "campus"}}}
        })
        self.assertEqual(options["platforms"]["boss"]["recruitment_filter"], "campus")


class DocxExportTests(unittest.TestCase):
    def test_md_to_docx_round_trip(self):
        """Word 导出后，用项目自带的 docx 解析器回读，结构与关键内容保留。"""
        from openjob.ai.resume_engine.exporter import md_to_docx
        from openjob.web.resume_upload import docx_to_markdown

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "resume.docx"
            nl = chr(10)
            md = nl.join([
                "# 王小明",
                "",
                "## 教育背景",
                "",
                "- 华南师范大学 大数据管理与应用",
                "",
                "## 相关经历",
                "",
                "- **用户调研**：完成 **400+** 份问卷分析",
                "",
            ])
            result = md_to_docx(md, out)

            self.assertTrue(result.exists())
            self.assertGreater(result.stat().st_size, 5000)
            round_trip = docx_to_markdown(result.read_bytes())
            self.assertIn("王小明", round_trip)
            self.assertIn("教育背景", round_trip)
            self.assertIn("400+", round_trip)
            self.assertIn("华南师范大学", round_trip)


class RewriteStyleGuardTests(unittest.TestCase):
    def test_rewrite_rejects_ai_flavor_output(self):
        """改写出现「体现了」式 AI 腔 → 校验器打回（chat_json 会带反馈重试）。"""
        import json as _json

        from openjob.ai.resume_engine import optimizer
        from openjob.ai.resume_engine.models import JdProfile, MatchReport

        captured = {}

        def fake_chat_json(system, user, config, *, validator, max_tokens, purpose=None):
            captured["validator"] = validator
            captured["system"] = system
            bad = {"changes": [{"section": "相关经历", "before": "b",
                                "after": "体现了对数据的敏感性", "reason": "r", "risk": ""}]}
            validator(_json.loads(_json.dumps(bad)))  # 模拟模型输出 AI 腔 → 校验应抛错
            return {}

        jd = JdProfile.from_payload({"title": "运营实习"})
        match = MatchReport.from_payload({"entries": []})

        with patch.object(optimizer, "chat_json", side_effect=fake_chat_json):
            with self.assertRaises(ValueError) as ctx:
                optimizer.rewrite_sections("简历正文", jd, match, {})

        self.assertIn("AI 腔", str(ctx.exception))
        self.assertIn("“体现”", captured["system"])

    def test_rewrite_passes_human_style(self):
        from openjob.ai.resume_engine import optimizer
        from openjob.ai.resume_engine.models import JdProfile, MatchReport, RewriteResult

        captured = {}

        import json as _json

        def fake_chat_json(system, user, config, *, validator, max_tokens, purpose=None):
            captured["validator"] = validator
            good_dict = {"changes": [
                {"section": "相关经历", "before": "负责数据整理。",
                 "after": "用 Python 完成 400 份问卷的清洗与分析。", "reason": "对齐数据分析要求", "risk": ""}]}
            return validator(_json.loads(_json.dumps(good_dict)))

        jd = JdProfile.from_payload({"title": "数据分析实习"})
        match = MatchReport.from_payload({"entries": []})

        with patch.object(optimizer, "chat_json", side_effect=fake_chat_json):
            result = optimizer.rewrite_sections("简历正文", jd, match, {})

        self.assertEqual(len(result.changes), 1)


class MinLengthBlockingTests(unittest.TestCase):
    def test_shrunk_content_is_blocked(self):
        from openjob.ai.resume_engine.optimizer import validate_assembled

        base = "• " + "内容细节。" * 40
        shrunk = "• " + "内容细节。" * 10
        blocking, _ = validate_assembled(base, shrunk)
        self.assertTrue(any("少了" in issue for issue in blocking))

    def test_equal_length_passes(self):
        from openjob.ai.resume_engine.optimizer import validate_assembled

        base = "• " + "内容细节。" * 40
        blocking, _ = validate_assembled(base, base)
        self.assertEqual(blocking, [])


if __name__ == "__main__":
    unittest.main()
