import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ResumeArtifactTests(unittest.TestCase):
    def test_finds_resume_artifact_phrases(self):
        from openjob.ai.resume import _find_resume_artifacts

        markdown = "以下内容基于原始简历整理。\n\n## 岗位匹配亮点\n- 补充说明：未虚构。"

        artifacts = _find_resume_artifacts(markdown)

        self.assertIn("以下内容基于", artifacts)
        self.assertIn("岗位匹配亮点", artifacts)
        self.assertIn("补充说明", artifacts)
        self.assertIn("未虚构", artifacts)

    def test_finds_expanded_resume_artifact_phrases(self):
        from openjob.ai.resume import _find_resume_artifacts

        markdown = (
            "以下为优化后的简历。\n"
            "本次优化根据岗位JD，结合岗位要求，匹配该岗位。\n"
            "这是根据原始简历生成的调整后的简历，也是一份定制简历。"
        )

        artifacts = _find_resume_artifacts(markdown)

        self.assertIn("以下为优化后的", artifacts)
        self.assertIn("本次优化", artifacts)
        self.assertIn("根据岗位JD", artifacts)
        self.assertIn("结合岗位要求", artifacts)
        self.assertIn("匹配该岗位", artifacts)
        self.assertIn("根据原始简历", artifacts)
        self.assertIn("调整后的简历", artifacts)
        self.assertIn("定制简历", artifacts)

    def test_finds_job_tailoring_leakage_phrases(self):
        from openjob.ai.resume import _find_resume_artifacts

        markdown = (
            "项目与字节岗位中的要求高度相关，可迁移到AI资讯内容质量评估场景，和岗位要求高度匹配。\n"
            "JD逐条对照：岗位JD覆盖情况如下，无法覆盖的部分已说明。"
        )

        artifacts = _find_resume_artifacts(markdown)

        self.assertIn("岗位中的", artifacts)
        self.assertIn("字节岗位", artifacts)
        self.assertIn("高度相关", artifacts)
        self.assertIn("可迁移到", artifacts)
        self.assertIn("岗位要求", artifacts)
        self.assertIn("高度匹配", artifacts)
        self.assertIn("JD逐条对照", artifacts)
        self.assertIn("岗位JD覆盖", artifacts)
        self.assertIn("无法覆盖", artifacts)

    def test_existing_source_placeholders_are_allowed_but_new_or_rewritten_ones_are_blocked(self):
        from openjob.ai.resume import _find_new_placeholders

        base_resume = "# 候选人\n\n电话：[待填写]\n作品集：{{portfolio_url}}\n"

        self.assertEqual(
            _find_new_placeholders(
                "# 候选人\n\n电话：[待填写]\n作品集：{{portfolio_url}}\n",
                base_resume,
            ),
            [],
        )
        self.assertEqual(
            _find_new_placeholders(
                "# 候选人\n\n电话：[请填写电话]\n作品集：{{portfolio_url}}\n",
                base_resume,
            ),
            ["[请填写电话]"],
        )

    def test_integrity_checks_report_new_and_missing_fact_values(self):
        from openjob.ai.resume import _find_blocking_integrity_issues

        base_resume = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "邮箱：candidate@example.com\n"
            "## 工作经历\n\n"
            "负责内容运营。\n"
        )
        generated = (
            "# 候选人\n\n"
            "## 基本信息\n\n"
            "## 工作经历\n\n"
            "负责内容运营，转化率提升 50%。\n"
        )

        issues = _find_blocking_integrity_issues(generated, base_resume)

        self.assertTrue(any("缺少基础简历中的关键信息" in issue for issue in issues))
        self.assertTrue(any("candidate@example.com" in issue for issue in issues))
        self.assertTrue(any("模型新增了原始简历中不存在的数据" in issue for issue in issues))
        self.assertTrue(any("50%" in issue for issue in issues))

class ResumePdfRuntimeTests(unittest.TestCase):
    @patch("openjob.ai.resume.close_tab")
    @patch("openjob.ai.resume.print_pdf")
    @patch("openjob.ai.resume.new_tab")
    def test_render_pdf_via_cdp_uses_browser_facade(self, new_tab, print_pdf, close_tab):
        from openjob.ai.resume import _render_pdf_via_cdp

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "resume.pdf"
            new_tab.return_value = "target-1"
            print_pdf.side_effect = lambda target, file_path: output.write_bytes(b"pdf") or True

            result = _render_pdf_via_cdp("<html><body>ok</body></html>", output)

        self.assertTrue(result)
        new_tab.assert_called_once()
        self.assertTrue(new_tab.call_args.args[0].startswith("file:///"))
        self.assertIs(new_tab.call_args.kwargs["background"], True)
        print_pdf.assert_called_once_with("target-1", output)
        close_tab.assert_called_once_with("target-1")

    @patch("openjob.ai.resume.close_tab")
    @patch("openjob.ai.resume.print_pdf")
    @patch("openjob.ai.resume.new_tab")
    def test_render_pdf_via_cdp_rejects_missing_output_file(self, new_tab, print_pdf, close_tab):
        from openjob.ai.resume import _render_pdf_via_cdp

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "missing.pdf"
            new_tab.return_value = "target-1"
            print_pdf.return_value = True

            result = _render_pdf_via_cdp("<html><body>ok</body></html>", output)

        self.assertFalse(result)
        close_tab.assert_called_once_with("target-1")


if __name__ == "__main__":
    unittest.main()
