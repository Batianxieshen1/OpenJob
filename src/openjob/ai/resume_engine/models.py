"""简历引擎数据模型：LLM 输出的“合同”。

每个模型提供 from_payload(data) -> 实例，校验失败抛 ValueError（带可读原因），
由 llm.chat_json 把错误反馈给模型重试。风格与 scorer._structured_score_result 一致，
不引入 pydantic 依赖。
"""

from dataclasses import dataclass, field


def _require_str(payload: dict, key: str, *, default: str = "") -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"字段 {key} 必须是字符串，实际为 {type(value).__name__}")
    return value


def _str_list(payload: dict, key: str) -> list[str]:
    value = payload.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"字段 {key} 必须是字符串数组")
    return [item for item in value if item.strip()]


@dataclass
class JdSkill:
    """JD 中的一条技能/要求条目。"""

    skill: str
    weight: int = 3  # 1-5 重要程度
    required: bool = True
    source_evidence: str = ""  # JD 原文依据，防编造

    @staticmethod
    def from_payload(data: object) -> "JdSkill":
        if not isinstance(data, dict):
            raise ValueError("技能条目必须是对象")
        skill = _require_str(data, "skill")
        if not skill.strip():
            raise ValueError("技能条目缺少 skill 文本")
        weight = data.get("weight", 3)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 1 <= weight <= 5:
            raise ValueError("weight 必须是 1-5 的数字")
        required = data.get("required", True)
        if not isinstance(required, bool):
            raise ValueError("required 必须是布尔值")
        return JdSkill(
            skill=skill.strip(),
            weight=int(weight),
            required=required,
            source_evidence=_require_str(data, "source_evidence"),
        )


@dataclass
class JdProfile:
    """① JD 结构化画像：解析一次，匹配/改写/审阅复用。"""

    title: str
    company_hint: str = ""
    summary: str = ""
    hard_requirements: list[JdSkill] = field(default_factory=list)
    preferred_skills: list[JdSkill] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    experience_min_years: int | None = None
    education_min: str | None = None
    red_flags: list[str] = field(default_factory=list)

    @staticmethod
    def from_payload(payload: object) -> "JdProfile":
        if not isinstance(payload, dict):
            raise ValueError("JD 解析输出必须是 JSON 对象")
        title = _require_str(payload, "title")
        if not title.strip():
            raise ValueError("缺少 title（岗位名称）")
        years = payload.get("experience_min_years")
        if years is not None and (isinstance(years, bool) or not isinstance(years, (int, float))):
            raise ValueError("experience_min_years 必须是数字或 null")
        education = payload.get("education_min")
        if education is not None and not isinstance(education, str):
            raise ValueError("education_min 必须是字符串或 null")
        skills_payload = payload.get("hard_requirements", [])
        preferred_payload = payload.get("preferred_skills", [])
        if not isinstance(skills_payload, list) or not isinstance(preferred_payload, list):
            raise ValueError("hard_requirements / preferred_skills 必须是数组")
        return JdProfile(
            title=title.strip(),
            company_hint=_require_str(payload, "company_hint"),
            summary=_require_str(payload, "summary"),
            hard_requirements=[JdSkill.from_payload(item) for item in skills_payload],
            preferred_skills=[JdSkill.from_payload(item) for item in preferred_payload],
            keywords=_str_list(payload, "keywords"),
            experience_min_years=int(years) if years is not None else None,
            education_min=education,
            red_flags=_str_list(payload, "red_flags"),
        )

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "company_hint": self.company_hint,
            "summary": self.summary,
            "hard_requirements": [vars(s) for s in self.hard_requirements],
            "preferred_skills": [vars(s) for s in self.preferred_skills],
            "keywords": self.keywords,
            "experience_min_years": self.experience_min_years,
            "education_min": self.education_min,
            "red_flags": self.red_flags,
        }


@dataclass
class MatchEntry:
    """② 匹配分析中的一条对照：简历证据 vs JD 要求。"""

    requirement: str
    status: str  # hit / transferable / gap
    evidence: str = ""  # 简历中的支撑原文；gap 时为空
    note: str = ""

    VALID_STATUSES = {"hit", "transferable", "gap"}

    @staticmethod
    def from_payload(data: object) -> "MatchEntry":
        if not isinstance(data, dict):
            raise ValueError("匹配条目必须是对象")
        requirement = _require_str(data, "requirement")
        status = _require_str(data, "status")
        if not requirement.strip():
            raise ValueError("匹配条目缺少 requirement")
        if status not in MatchEntry.VALID_STATUSES:
            raise ValueError(f"status 必须是 {'/'.join(sorted(MatchEntry.VALID_STATUSES))}，实际为 {status}")
        return MatchEntry(
            requirement=requirement.strip(),
            status=status,
            evidence=_require_str(data, "evidence"),
            note=_require_str(data, "note"),
        )


@dataclass
class MatchReport:
    """② 匹配报告：命中/可迁移/缺口 三类清单。"""

    entries: list[MatchEntry] = field(default_factory=list)
    overall_note: str = ""

    @staticmethod
    def from_payload(payload: object) -> "MatchReport":
        if not isinstance(payload, dict):
            raise ValueError("匹配分析输出必须是 JSON 对象")
        entries_payload = payload.get("entries", [])
        if not isinstance(entries_payload, list):
            raise ValueError("entries 必须是数组")
        return MatchReport(
            entries=[MatchEntry.from_payload(item) for item in entries_payload],
            overall_note=_require_str(payload, "overall_note"),
        )

    def to_dict(self) -> dict:
        return {
            "entries": [vars(e) for e in self.entries],
            "overall_note": self.overall_note,
        }

    def summary_counts(self) -> dict[str, int]:
        return {
            "hit": sum(1 for e in self.entries if e.status == "hit"),
            "transferable": sum(1 for e in self.entries if e.status == "transferable"),
            "gap": sum(1 for e in self.entries if e.status == "gap"),
        }


@dataclass
class SectionChange:
    """③ 分段改写的一个修改块：供人工逐块采纳/撤销。"""

    section: str  # 简历栏目名，如 “工作经历 / 字节跳动”
    before: str
    after: str
    reason: str
    risk: str = ""  # 非空 = 风险标记，需人工核实

    @staticmethod
    def from_payload(data: object) -> "SectionChange":
        if not isinstance(data, dict):
            raise ValueError("修改块必须是对象")
        section = _require_str(data, "section")
        before = _require_str(data, "before")
        after = _require_str(data, "after")
        reason = _require_str(data, "reason")
        if not section.strip():
            raise ValueError("修改块缺少 section")
        if not before.strip():
            raise ValueError("修改块缺少 before（原文）")
        if not after.strip():
            raise ValueError("修改块缺少 after（改写）")
        if not reason.strip():
            raise ValueError("修改块缺少 reason（修改理由）")
        return SectionChange(
            section=section.strip(),
            before=before,
            after=after,
            reason=reason.strip(),
            risk=_require_str(data, "risk"),
        )


@dataclass
class RewriteResult:
    """③ 分段改写输出。"""

    changes: list[SectionChange] = field(default_factory=list)

    @staticmethod
    def from_payload(payload: object) -> "RewriteResult":
        if not isinstance(payload, dict):
            raise ValueError("分段改写输出必须是 JSON 对象")
        changes_payload = payload.get("changes", [])
        if not isinstance(changes_payload, list) or not changes_payload:
            raise ValueError("changes 必须是非空数组")
        return RewriteResult(changes=[SectionChange.from_payload(item) for item in changes_payload])
