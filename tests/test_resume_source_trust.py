"""Regression tests: a template file configured as resume_path must never
drive AI scoring (2026-09 incident — bundled resume.md shadowed the real
base resume for weeks)."""

import sqlite3

import pytest

from openjob.ai import scorer
from openjob.ai.resume_source import is_trusted_resume_file, load_trusted_resume_text

TEMPLATE_MD = """# 张三

- 电话：138-0000-0000 | 邮箱：zhangsan@example.com
- 求职意向：前端开发工程师（实习）
"""

REAL_MD = """# 林庆涛

- 电话：136-7609-7998
- 求职意向：数据分析（实习）
- 项目：电商用户增长分析框架
"""


@pytest.fixture()
def memory_db(monkeypatch):
	conn = sqlite3.connect(":memory:")
	conn.row_factory = sqlite3.Row
	conn.execute(
		"CREATE TABLE base_resumes (id TEXT PRIMARY KEY, name TEXT, direction TEXT,"
		" content_md TEXT, is_default INTEGER, created_at TEXT, updated_at TEXT)"
	)
	conn.execute(
		"INSERT INTO base_resumes VALUES ('r1', '数据分析方向', '数据分析', ?, 1, '2026-01-01', '2026-01-01')",
		("真实底稿：数据分析方向内容",),
	)
	conn.execute(
		"INSERT INTO base_resumes VALUES ('r2', '新媒体运营方向', '新媒体运营', ?, 0, '2026-01-02', '2026-01-02')",
		("新媒体底稿内容",),
	)
	monkeypatch.setattr(scorer, "get_db", lambda: conn)
	yield conn
	conn.close()


class TestIsTrustedResumeFile:
	def test_template_content_is_rejected(self, tmp_path):
		p = tmp_path / "my.md"
		p.write_text(TEMPLATE_MD, encoding="utf-8")
		assert is_trusted_resume_file(p) is False

	def test_real_content_is_trusted(self, tmp_path):
		p = tmp_path / "my.md"
		p.write_text(REAL_MD, encoding="utf-8")
		assert is_trusted_resume_file(p) is True

	def test_example_basename_rejected_even_with_real_content(self, tmp_path):
		p = tmp_path / "resume.md"
		p.write_text(REAL_MD, encoding="utf-8")
		assert is_trusted_resume_file(p) is False
		p2 = tmp_path / "resume.example.md"
		p2.write_text(REAL_MD, encoding="utf-8")
		assert is_trusted_resume_file(p2) is False

	def test_missing_and_empty_are_rejected(self, tmp_path):
		assert is_trusted_resume_file(tmp_path / "nope.md") is False
		p = tmp_path / "empty.md"
		p.write_text("", encoding="utf-8")
		assert is_trusted_resume_file(p) is False
		assert is_trusted_resume_file("") is False
		assert is_trusted_resume_file(None) is False

	def test_load_trusted_text_roundtrip(self, tmp_path):
		p = tmp_path / "real.md"
		p.write_text(REAL_MD, encoding="utf-8")
		assert load_trusted_resume_text(p) == REAL_MD
		bad = tmp_path / "bad.md"
		bad.write_text(TEMPLATE_MD, encoding="utf-8")
		assert load_trusted_resume_text(bad) is None


class TestLoadResumeFallback:
	def test_template_path_falls_back_to_default_base_resume(self, tmp_path, memory_db):
		p = tmp_path / "poison.md"
		p.write_text(TEMPLATE_MD, encoding="utf-8")
		config = {"profile": {"resume_path": str(p)}}
		assert scorer._load_resume(config) == "真实底稿：数据分析方向内容"

	def test_real_path_is_used(self, tmp_path, memory_db):
		p = tmp_path / "mine.md"
		p.write_text(REAL_MD, encoding="utf-8")
		config = {"profile": {"resume_path": str(p)}}
		assert scorer._load_resume(config) == REAL_MD

	def test_empty_path_falls_back_to_default_base_resume(self, memory_db):
		config = {"profile": {"resume_path": ""}}
		assert scorer._load_resume(config) == "真实底稿：数据分析方向内容"

	def test_missing_path_falls_back_to_default_base_resume(self, memory_db):
		config = {"profile": {"resume_path": str(_nope := __import__("pathlib").Path("Z:/no/such.md"))}}
		assert scorer._load_resume(config) == "真实底稿：数据分析方向内容"
