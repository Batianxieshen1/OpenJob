"""简历优化引擎测试：模型合同、JSON 重试、防编造校验、四段流水、API 路由。

守卫语义（计划书 §5）：改写不编造；空白/模板简历拒绝生成；
同一岗位已有 review 版幂等复用；重新生成需显式 confirm。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openjob.db import add_history, get_db, insert_job
from openjob.web import server


def _base_resume() -> str:
    return (
        "# 王小明\n\n"
        "## 基本信息\n\n"
        "邮箱：candidate@example.com\n"
        "电话：13800138000\n\n"
        "## 个人优势\n\n"
        "三年内容运营经验，熟悉数据驱动增长。\n\n"
        "## 工作经历\n\n"
        "### 某科技公司 内容运营（2021-2024）\n\n"
        "负责公众号内容策划与用户增长，阅读量提升 30%。\n\n"
        "## 教育经历\n\n"
        "某大学 本科 2017-2021\n\n"
        "## 相关技能\n\n"
        "- 内容策划\n- 数据分析\n"
    )


def _job_row(job_id: str = "job-engine-1") -> dict:
    return {
        "id": job_id,
        "title": "内容运营",
        "company": "示例科技",
        "salary": "10-20K",
        "city": "北京",
        "jd": "负责新媒体内容运营，要求：1. 三年以上内容运营经验；2. 熟悉小红书/公众号；3. 数据分析能力。",
        "url": "https://example.com/job",
    }


def _jd_profile(**overrides) -> "object":
    from openjob.ai.resume_engine.models import JdProfile

    return JdProfile.from_payload({**_jd_profile_payload(), **overrides})


def _match(**overrides) -> "object":
    from openjob.ai.resume_engine.models import MatchReport

    return MatchReport.from_payload({**_match_payload(), **overrides})


def _rewrite(rewrite_payload: dict | None = None) -> "object":
    from openjob.ai.resume_engine.models import RewriteResult

    return RewriteResult.from_payload(rewrite_payload or _rewrite_payload())


def _jd_profile_payload() -> dict:
    return {
        "title": "内容运营",
        "company_hint": "示例科技",
        "summary": "负责新媒体内容运营与增长",
        "hard_requirements": [
            {"skill": "三年以上内容运营经验", "weight": 5, "required": True, "source_evidence": "三年以上内容运营经验"},
            {"skill": "数据分析能力", "weight": 4, "required": True, "source_evidence": "数据分析能力"},
        ],
        "preferred_skills": [
            {"skill": "熟悉小红书", "weight": 3, "required": False, "source_evidence": "熟悉小红书/公众号"},
        ],
        "keywords": ["内容运营", "小红书", "数据分析"],
        "experience_min_years": 3,
        "education_min": None,
        "red_flags": [],
    }


def _match_payload() -> dict:
    return {
        "entries": [
            {"requirement": "三年以上内容运营经验", "status": "hit", "evidence": "三年内容运营经验", "note": "直接匹配"},
            {"requirement": "数据分析能力", "status": "transferable", "evidence": "阅读量提升 30%", "note": "有量化结果可迁移"},
        ],
        "overall_note": "整体匹配良好",
    }


def _rewrite_payload(*, risk: str = "阅读量提升 30% 为约数，请核实") -> dict:
    return {
        "changes": [
            {
                "section": "个人优势",
                "before": "三年内容运营经验，熟悉数据驱动增长。",
                "after": "三年新媒体运营经验，熟悉小红书增长。",
                "reason": "对齐 JD 的小红书/公众号要求",
                "risk": risk,
            }
        ]
    }


class _EngineCase(unittest.TestCase):
    """引擎/存储测试的公共基座：临时库 + 临时简历文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "data" / "openjob.db"
        self.resume_path = self.root / "resume.md"
        self.resume_path.write_text(_base_resume(), encoding="utf-8")
        self.config = {
            "profile": {
                "resume_path": str(self.resume_path),
                "resume_output_dir": str(self.root / "out"),
            },
            "ai": {"service": "deepseek", "provider": "openai_compatible", "model": "deepseek-chat"},
        }
        from openjob.ai.resume_engine import engine

        self._engine = engine
        self._original_runtime_db = engine.RUNTIME_DB_PATH
        engine.RUNTIME_DB_PATH = self.db_path

    def tearDown(self):
        self._engine.RUNTIME_DB_PATH = self._original_runtime_db
        self._tmp.cleanup()

    def _seed_job(self, job_id: str = "job-engine-1"):
        conn = get_db(self.db_path)
        try:
            insert_job(conn, _job_row(job_id))
        finally:
            conn.close()

    def _connect(self):
        return get_db(self.db_path)


class ResumeModelTests(unittest.TestCase):
    def test_jd_profile_parses_full_payload(self):
        from openjob.ai.resume_engine.models import JdProfile

        jd = JdProfile.from_payload(_jd_profile_payload())
        self.assertEqual(jd.title, "内容运营")
        self.assertEqual(len(jd.hard_requirements), 2)
        self.assertEqual(jd.hard_requirements[0].weight, 5)
        self.assertFalse(jd.preferred_skills[0].required)
        self.assertEqual(jd.experience_min_years, 3)
        self.assertIsNone(jd.education_min)

    def test_jd_profile_rejects_missing_title(self):
        from openjob.ai.resume_engine.models import JdProfile

        with self.assertRaises(ValueError):
            JdProfile.from_payload({"summary": "no title"})

    def test_jd_skill_rejects_out_of_range_weight(self):
        from openjob.ai.resume_engine.models import JdSkill

        with self.assertRaises(ValueError):
            JdSkill.from_payload({"skill": "x", "weight": 9})
        with self.assertRaises(ValueError):
            JdSkill.from_payload({"skill": "x", "weight": True})

    def test_match_entry_rejects_unknown_status(self):
        from openjob.ai.resume_engine.models import MatchEntry

        with self.assertRaises(ValueError):
            MatchEntry.from_payload({"requirement": "r", "status": "maybe"})

    def test_section_change_requires_before_after_reason(self):
        from openjob.ai.resume_engine.models import SectionChange

        with self.assertRaises(ValueError):
            SectionChange.from_payload({"section": "s", "before": "b", "after": "a"})
        with self.assertRaises(ValueError):
            SectionChange.from_payload({"section": "s", "before": "b", "after": "a", "reason": " "})
        ok = SectionChange.from_payload(
            {"section": "s", "before": "b", "after": "a", "reason": "r", "risk": "rr"}
        )
        self.assertEqual(ok.risk, "rr")

    def test_rewrite_result_requires_nonempty_changes(self):
        from openjob.ai.resume_engine.models import RewriteResult

        with self.assertRaises(ValueError):
            RewriteResult.from_payload({"changes": []})


class ResumeLlmTests(unittest.TestCase):
    def test_extract_json_strips_code_fence(self):
        from openjob.ai.resume_engine.llm import extract_json_object

        text = "```json\n{\"a\": 1}\n```"
        self.assertEqual(extract_json_object(text), {"a": 1})
        self.assertEqual(extract_json_object("前置说明 {\"b\": [1,2]} 后缀"), {"b": [1, 2]})

    def test_extract_json_raises_without_object(self):
        from openjob.ai.resume_engine.llm import extract_json_object

        with self.assertRaises(ValueError):
            extract_json_object("没有 JSON 的普通文本")

    def test_chat_json_retries_with_feedback_then_succeeds(self):
        from openjob.ai.resume_engine import llm

        responses = ["不是 JSON", json.dumps(_jd_profile_payload(), ensure_ascii=False)]
        with patch.object(llm, "_call_raw", side_effect=lambda *a, **k: responses.pop(0)):
            jd = llm.chat_json(
                "sys", "user", {}, validator=lambda d: d, max_tokens=100
            )
        self.assertEqual(jd["title"], "内容运营")

    def test_chat_json_raises_after_retry_exhausted(self):
        from openjob.ai.resume_engine import llm
        from openjob.ai.credentials import AIRequestError

        with patch.object(llm, "_call_raw", return_value="仍然不是 JSON"):
            with self.assertRaises(AIRequestError):
                llm.chat_json("sys", "user", {}, validator=lambda d: (_ for _ in ()).throw(ValueError("x")), max_tokens=10)


class OptimizerTests(unittest.TestCase):
    def test_apply_changes_replaces_adopted_block(self):
        from openjob.ai.resume_engine.models import SectionChange
        from openjob.ai.resume_engine.optimizer import apply_changes

        change = SectionChange(
            section="个人优势",
            before="三年内容运营经验，熟悉数据驱动增长。",
            after="三年新媒体内容运营经验，数据驱动。",
            reason="对齐",
        )
        result = apply_changes(_base_resume(), [change])
        self.assertIn("三年新媒体内容运营经验", result)
        self.assertNotIn("熟悉数据驱动增长。", result)

    def test_apply_changes_skips_unmatched_before(self):
        from openjob.ai.resume_engine.models import SectionChange
        from openjob.ai.resume_engine.optimizer import apply_changes

        change = SectionChange(section="s", before="原文里不存在的一句", after="新文本", reason="r")
        base = _base_resume()
        self.assertEqual(apply_changes(base, [change]), base)

    def test_validate_assembled_blocks_new_fact_tokens(self):
        from openjob.ai.resume_engine.optimizer import validate_assembled

        base = _base_resume()
        fabricated = base.replace("阅读量提升 30%", "阅读量提升 300%，新增粉丝 2万")
        blocking, _ = validate_assembled(base, fabricated)
        self.assertTrue(any("不存在的数据" in issue for issue in blocking))

    def test_validate_assembled_blocks_new_placeholders(self):
        from openjob.ai.resume_engine.optimizer import validate_assembled

        base = _base_resume()
        blocking, _ = validate_assembled(base, base + "\n\n待填写：项目名称")
        self.assertTrue(any("占位符" in issue for issue in blocking))  # 占位符校验失败

    def test_validate_assembled_blocks_length_growth(self):
        from openjob.ai.resume_engine.optimizer import validate_assembled

        base = _base_resume()
        inflated = base + "\n\n" + ("非常长的凑字数内容。" * 40)
        blocking, _ = validate_assembled(base, inflated)
        self.assertTrue(any("膨胀" in issue for issue in blocking))

    def test_validate_assembled_warns_on_artifacts(self):
        from openjob.ai.resume_engine.optimizer import validate_assembled

        base = _base_resume()
        warned = base.replace("## 个人优势", "## 岗位匹配亮点", 1)
        blocking, warnings = validate_assembled(base, warned)
        self.assertEqual(blocking, [])
        self.assertTrue(any("过程性措辞" in w for w in warnings))

    def test_reassemble_from_diff_respects_adopted_flag(self):
        from openjob.ai.resume_engine import reassemble_from_diff

        base = _base_resume()
        diff = [
            {
                "section": "个人优势",
                "before": "三年内容运营经验，熟悉数据驱动增长。",
                "after": "改写后的优势描述。",
                "reason": "r",
                "risk": "",
                "adopted": False,
            }
        ]
        self.assertEqual(reassemble_from_diff(base, diff), base)
        diff[0]["adopted"] = True
        self.assertIn("改写后的优势描述。", reassemble_from_diff(base, diff))


class EngineGuardTests(_EngineCase):
    def test_empty_resume_rejected(self):
        from openjob.ai.resume_engine import generate_resume

        self.resume_path.write_text("", encoding="utf-8")
        self._seed_job()
        result = generate_resume("job-engine-1", self.config)
        self.assertFalse(result.ok)
        self.assertIn("基础简历为空", result.reason)

    def test_template_resume_rejected(self):
        from openjob.ai.resume_engine import generate_resume

        self.resume_path.write_text("# 张三\n\n" + _base_resume(), encoding="utf-8")
        self._seed_job()
        result = generate_resume("job-engine-1", self.config)
        self.assertFalse(result.ok)
        self.assertIn("模板占位", result.reason)

    def test_short_resume_rejected(self):
        from openjob.ai.resume_engine import generate_resume

        self.resume_path.write_text("太短", encoding="utf-8")
        self._seed_job()
        result = generate_resume("job-engine-1", self.config)
        self.assertFalse(result.ok)
        self.assertIn("过短", result.reason)

    def test_missing_job_rejected(self):
        from openjob.ai.resume_engine import generate_resume

        result = generate_resume("no-such-job", self.config)
        self.assertFalse(result.ok)
        self.assertIn("未找到岗位", result.reason)

    def test_missing_jd_rejected(self):
        from openjob.ai.resume_engine import generate_resume

        self._seed_job("job-no-jd")
        conn = get_db(self.db_path)
        conn.execute("UPDATE jobs SET jd = '' WHERE id = 'job-no-jd'")
        conn.commit()
        conn.close()
        result = generate_resume("job-no-jd", self.config)
        self.assertFalse(result.ok)
        self.assertIn("JD", result.reason)


class EnginePipelineTests(_EngineCase):
    def _patched_stages(self, rewrite_payload=None):
        from openjob.ai.resume_engine import engine

        return (
            patch.object(engine, "parse_jd", return_value=_jd_profile()),
            patch.object(engine, "analyze_match", return_value=_match()),
            patch.object(engine, "rewrite_sections", return_value=_rewrite(rewrite_payload)),
        )

    def test_full_pipeline_creates_review_version(self):
        from openjob.ai.resume_engine import engine, generate_resume

        self._seed_job()
        p1, p2, p3 = self._patched_stages()
        with p1, p2, p3:
            result = generate_resume("job-engine-1", self.config)

        self.assertTrue(result.ok)
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM resumes WHERE job_id = ?", ("job-engine-1",)).fetchall()
            self.assertEqual(len(rows), 1)
            record = dict(rows[0])
            self.assertEqual(record["status"], "review")
            self.assertEqual(record["version"], 1)
            self.assertIsNotNone(record["base_md"])
            diff = json.loads(record["diff_json"])
            self.assertEqual(len(diff), 1)
            self.assertTrue(diff[0]["adopted"])
            self.assertIn("小红书", record["content_md"])
            flags = json.loads(record["risk_flags_json"])
            self.assertTrue(any("核实" in flag for flag in flags))
            job = dict(conn.execute("SELECT resume_id, resume_status FROM jobs WHERE id = ?", ("job-engine-1",)).fetchone())
            self.assertEqual(job["resume_status"], "review")
            self.assertEqual(job["resume_id"], record["id"])
        finally:
            conn.close()

    def test_idempotent_reuse_of_review_version(self):
        from openjob.ai.resume_engine import engine, generate_resume

        self._seed_job()
        p1, p2, p3 = self._patched_stages()
        with p1, p2, p3:
            first = generate_resume("job-engine-1", self.config)
            second = generate_resume("job-engine-1", self.config)

        self.assertTrue(first.ok and second.ok)
        self.assertTrue(second.reused)
        self.assertEqual(first.resume_id, second.resume_id)

    def test_confirm_regenerate_creates_version_2(self):
        from openjob.ai.resume_engine import engine, generate_resume

        self._seed_job()
        p1, p2, p3 = self._patched_stages()
        with p1, p2, p3:
            first = generate_resume("job-engine-1", self.config)
            second = generate_resume("job-engine-1", self.config, confirm_regenerate=True)

        self.assertTrue(first.ok and second.ok)
        self.assertNotEqual(first.resume_id, second.resume_id)
        conn = self._connect()
        try:
            versions = [r["version"] for r in conn.execute(
                "SELECT version FROM resumes WHERE job_id = ? ORDER BY version", ("job-engine-1",)
            ).fetchall()]
            self.assertEqual(versions, [1, 2])
        finally:
            conn.close()

    def test_fabricated_content_marks_version_failed(self):
        from openjob.ai.resume_engine import engine, generate_resume

        self._seed_job()
        bad_rewrite = _rewrite_payload(risk="")
        bad_rewrite["changes"][0]["after"] = "负责内容运营，转化率提升 50%，新增粉丝 2万。"
        bad_rewrite["changes"][0]["before"] = "三年内容运营经验，熟悉数据驱动增长。"
        p1, p2, p3 = self._patched_stages(rewrite_payload=bad_rewrite)  # dict OK: engine 只读 changes
        with p1, p2, p3:
            result = generate_resume("job-engine-1", self.config)

        self.assertFalse(result.ok)
        conn = self._connect()
        try:
            record = dict(conn.execute("SELECT * FROM resumes WHERE job_id = ?", ("job-engine-1",)).fetchone())
            self.assertEqual(record["status"], "failed")
            self.assertIn("不存在的数据", record["error"] or "")
            job = dict(conn.execute("SELECT resume_status FROM jobs WHERE id = ?", ("job-engine-1",)).fetchone())
            self.assertEqual(job["resume_status"], "failed")
        finally:
            conn.close()

    def test_ai_error_marks_version_failed_with_readable_reason(self):
        from openjob.ai.credentials import AIRequestError
        from openjob.ai.resume_engine import engine, generate_resume

        self._seed_job()
        p1, _, _ = self._patched_stages()
        with patch.object(engine, "RUNTIME_DB_PATH", self.db_path), p1, \
             patch.object(engine, "analyze_match", side_effect=AIRequestError("rate_limit", "AI 服务触发请求或 Token 频率限制")):
            result = generate_resume("job-engine-1", self.config)

        self.assertFalse(result.ok)
        self.assertIn("频率限制", result.reason)
        conn = self._connect()
        try:
            record = dict(conn.execute("SELECT status FROM resumes WHERE job_id = ?", ("job-engine-1",)).fetchone())
            self.assertEqual(record["status"], "failed")
        finally:
            conn.close()


class DelegationTests(_EngineCase):
    def test_generate_tailored_resume_exports_and_updates_pointer(self):
        from openjob.ai import resume as resume_module
        from openjob.ai.resume_engine import engine

        self._seed_job()
        p1, p2, p3 = (
            patch.object(engine, "parse_jd", return_value=_jd_profile()),
            patch.object(engine, "analyze_match", return_value=_match()),
            patch.object(engine, "rewrite_sections", return_value=_rewrite(_rewrite_payload(risk=""))),
        )
        with p1, p2, p3, \
             patch.object(engine, "_render_pdf", side_effect=lambda md, out: out.write_text("pdf", encoding="utf-8") or True) as render_pdf:
            path = resume_module.generate_tailored_resume("job-engine-1", self.config)

        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertTrue(str(path).endswith(".docx"))  # Word 优先
        conn = self._connect()
        try:
            record = dict(conn.execute("SELECT status, docx_path FROM resumes WHERE job_id = ?", ("job-engine-1",)).fetchone())
            self.assertEqual(record["status"], "exported")
            self.assertEqual(record["docx_path"], str(path))
        finally:
            conn.close()

    def test_generate_tailored_resume_failure_records_reason(self):
        from openjob.ai import resume as resume_module

        self.resume_path.write_text("# 张三\n\n" + _base_resume(), encoding="utf-8")
        self._seed_job()
        path = resume_module.generate_tailored_resume("job-engine-1", self.config)
        self.assertIsNone(path)
        self.assertIn("模板占位", resume_module.get_last_resume_failure_reason("job-engine-1"))


class ResumeApiTests(_EngineCase):
    def setUp(self):
        super().setUp()
        import yaml

        (self.root / "config.yaml").write_text(
            yaml.safe_dump({
                "profile": {
                    "resume_path": str(self.resume_path),
                    "resume_output_dir": str(self.root / "out"),
                },
                "ai": {"service": "deepseek", "provider": "openai_compatible", "model": "deepseek-chat"},
            }),
            encoding="utf-8",
        )
        self._original_base_dir = server.BASE_DIR
        server.set_base_dir(self.root)

    def tearDown(self):
        server.set_base_dir(self._original_base_dir)
        super().tearDown()

    def _request(self, path: str, method: str = "GET", json_body: dict | None = None):
        import io

        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status

        request_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(request_body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        if json_body is not None:
            environ["CONTENT_LENGTH"] = str(len(request_body))
            environ["CONTENT_TYPE"] = "application/json"

        response_iter = server.app(environ, start_response)
        try:
            body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in response_iter
            ).decode("utf-8")
        finally:
            close = getattr(response_iter, "close", None)
            if close:
                close()
        return int(status_headers["status"].split()[0]), body

    def _generate(self, job_id: str = "job-engine-1", **kwargs):
        from openjob.ai.resume_engine import engine

        patches = (
            patch.object(engine, "parse_jd", return_value=_jd_profile()),
            patch.object(engine, "analyze_match", return_value=_match()),
            patch.object(engine, "rewrite_sections", return_value=_rewrite(_rewrite_payload(risk=""))),
        )
        with patches[0], patches[1], patches[2]:
            status, body = self._request(
                "/api/resume/generate",
                method="POST",
                json_body={"job_id": job_id, **kwargs},
            )
        return status, json.loads(body)

    def test_generate_requires_job_id(self):
        status, body = self._request("/api/resume/generate", method="POST", json_body={})
        self.assertEqual(status, 400)
        self.assertIn("job_id", body)

    def test_generate_unknown_job_returns_400(self):
        status, body = self._request(
            "/api/resume/generate", method="POST", json_body={"job_id": "ghost"}
        )
        self.assertEqual(status, 400)
        self.assertIn("未找到岗位", body)

    def test_generate_creates_review_version_and_reuses(self):
        self._seed_job()
        status, body = self._generate()
        self.assertEqual(status, 200)
        self.assertFalse(body["reused"])

        status, body = self._generate()
        self.assertEqual(status, 200)
        self.assertTrue(body["reused"])

    def test_generate_with_confirm_creates_new_version(self):
        self._seed_job()
        _, first = self._generate()
        _, second = self._generate(confirm_regenerate=True)
        self.assertNotEqual(first["resume_id"], second["resume_id"])

    def test_versions_list_returns_desc(self):
        self._seed_job()
        self._generate()
        self._generate(confirm_regenerate=True)
        status, body = self._request("/api/resume/job-engine-1/versions")
        self.assertEqual(status, 200)
        versions = json.loads(body)["versions"]
        self.assertEqual([v["version"] for v in versions], [2, 1])

    def test_version_detail_returns_diff_and_flags(self):
        self._seed_job()
        _, body = self._generate()
        status, raw = self._request(f"/api/resume/version/{body['resume_id']}")
        detail = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(detail["status"], "review")
        self.assertTrue(detail["diff_json"])
        self.assertIsNotNone(detail["base_md"])

    def test_patch_adopt_reject_reassembles(self):
        self._seed_job()
        _, body = self._generate()
        resume_id = body["resume_id"]

        status, raw = self._request(f"/api/resume/version/{resume_id}")
        detail = json.loads(raw)
        diff = json.loads(detail["diff_json"])

        diff[0]["adopted"] = False
        status, raw = self._request(
            f"/api/resume/version/{resume_id}", method="PATCH", json_body={"diff": diff}
        )
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertNotIn("三年新媒体内容运营经验", payload["content_md"])

        diff[0]["adopted"] = True
        diff[0]["after"] = "手改后的优势描述。"
        status, raw = self._request(
            f"/api/resume/version/{resume_id}", method="PATCH", json_body={"diff": diff}
        )
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertIn("手改后的优势描述。", payload["content_md"])

    def test_patch_rejects_fabricated_content(self):
        self._seed_job()
        _, body = self._generate()
        resume_id = body["resume_id"]

        status, raw = self._request(f"/api/resume/version/{resume_id}")
        diff = json.loads(json.loads(raw)["diff_json"])
        diff[0]["after"] = diff[0]["after"] + "，转化率提升 50%，粉丝 2万。"

        status, raw = self._request(
            f"/api/resume/version/{resume_id}", method="PATCH", json_body={"diff": diff}
        )
        self.assertEqual(status, 400)
        self.assertIn("不存在的数据", raw)

    def test_export_marks_version_exported(self):
        from openjob.ai.resume_engine import engine

        self._seed_job()
        _, body = self._generate()
        with patch.object(engine, "_render_pdf", return_value=False) as render_pdf:
            status, raw = self._request(f"/api/resume/version/{body['resume_id']}/export", method="POST")
        self.assertEqual(status, 200)
        render_pdf.assert_called_once()
        conn = self._connect()
        try:
            record = dict(conn.execute("SELECT status FROM resumes WHERE id = ?", (body["resume_id"],)).fetchone())
            self.assertEqual(record["status"], "exported")
        finally:
            conn.close()

    def test_mark_sent_writes_history(self):
        self._seed_job()
        _, body = self._generate()
        status, raw = self._request(
            f"/api/resume/version/{body['resume_id']}/mark-sent",
            method="POST",
            json_body={"job_id": "job-engine-1"},
        )
        self.assertEqual(status, 200)
        conn = self._connect()
        try:
            record = dict(conn.execute("SELECT status FROM resumes WHERE id = ?", (body["resume_id"],)).fetchone())
            self.assertEqual(record["status"], "sent")
            history = conn.execute(
                "SELECT action FROM history WHERE job_id = ? AND action = 'resume_sent'", ("job-engine-1",)
            ).fetchall()
            self.assertEqual(len(history), 1)
        finally:
            conn.close()

    def test_mark_sent_mismatched_job_404(self):
        self._seed_job()
        _, body = self._generate()
        status, _ = self._request(
            f"/api/resume/version/{body['resume_id']}/mark-sent",
            method="POST",
            json_body={"job_id": "other-job"},
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
