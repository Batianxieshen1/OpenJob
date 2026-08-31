"""简历优化引擎：JD 解析 → 匹配分析 → 分段改写 → 汇总校验（改写不编造）。"""

from openjob.ai.resume_engine.engine import (
    ResumeGenerationError,
    ResumeResult,
    export_resume_pdf,
    generate_resume,
    mark_resume_sent,
    reassemble_from_diff,
)
from openjob.ai.resume_engine.exporter import md_to_pdf, save_markdown
from openjob.ai.resume_engine.jd_parser import parse_jd
from openjob.ai.resume_engine.matcher import analyze_match
from openjob.ai.resume_engine.models import JdProfile, JdSkill, MatchReport, SectionChange
from openjob.ai.resume_engine.optimizer import apply_changes, rewrite_sections, validate_assembled

__all__ = [
    "ResumeGenerationError",
    "ResumeResult",
    "generate_resume",
    "export_resume_pdf",
    "mark_resume_sent",
    "reassemble_from_diff",
    "parse_jd",
    "analyze_match",
    "rewrite_sections",
    "apply_changes",
    "validate_assembled",
    "md_to_pdf",
    "save_markdown",
    "JdProfile",
    "JdSkill",
    "MatchReport",
    "SectionChange",
]
