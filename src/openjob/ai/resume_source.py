"""Shared trust rules for resume sources.

The web preflight and the AI scorer must agree on what counts as a real
resume. A template file configured as ``profile.resume_path`` once silently
drove AI scoring for weeks (2026-09 incident: the bundled ``resume.md``
example shadowed the real base resume), so both layers share these rules.
"""

from pathlib import Path

# 单一定义原则：模板标记黑名单只允许存在于 fact_policy.py，
# 所有简历源信任判断从这里统一引用。
from openjob.ai.fact_policy import TEMPLATE_RESUME_MARKERS as _TEMPLATE_MARKERS

# Bundled example files; real resumes come from the config page or base_resumes.
_EXAMPLE_BASENAMES = {"resume.md", "resume.example.md"}


def is_trusted_resume_file(raw_path: object) -> bool:
	"""True when the configured file is a real, non-example resume source."""
	try:
		path = Path(str(raw_path or "").strip())
	except (TypeError, ValueError, OSError):
		return False
	if not str(path) or path.name.lower() in _EXAMPLE_BASENAMES:
		return False
	if not path.is_file():
		return False
	try:
		content = path.read_text(encoding="utf-8")
	except (OSError, UnicodeError):
		return False
	return bool(content.strip()) and not any(marker in content for marker in _TEMPLATE_MARKERS)


def load_trusted_resume_text(raw_path: object) -> str | None:
	"""Return the file text when trusted, else None (caller falls back)."""
	if not is_trusted_resume_file(raw_path):
		return None
	try:
		return Path(str(raw_path).strip()).read_text(encoding="utf-8")
	except (OSError, UnicodeError):
		return None
