"""素材候选匹配与 AI 选择合同：确定性评分，不含任何随机或网络行为。

评分规则（固定权重）：
- 硬性技能命中 +25；JD keyword 命中 +12；岗位标题/概述命中 +10；
- 方向命中 +18；素材 title/description/achievements 命中 +8；priority 每级 +1。
同分按 priority 降序、原始行序升序。候选 prompt 不含 source/notes。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from openjob.ai.resume_engine.models import JdProfile

_TOKEN_SPLIT = re.compile(r"[,，、/\s]+")


def _tokens(value) -> list[str]:
    if isinstance(value, list):
        parts: list[str] = []
        for entry in value:
            parts.extend(_tokens(entry))
        return parts
    return [p for p in _TOKEN_SPLIT.split(str(value or "")) if p]


def _normalize(text: str) -> str:
    return str(text or "").lower().strip()


def _hits_any(source: str, targets) -> bool:
    lowered = _normalize(source)
    return any(_normalize(t) and _normalize(t) in lowered for t in targets)


@dataclass(frozen=True)
class Candidate:
    material: dict
    score: int
    reasons: list[str] = field(default_factory=list)


def rank_materials(items: list[dict], jd: JdProfile, *, limit: int = 8) -> list[Candidate]:
    """按固定权重给素材打分，返回前 limit 条候选。"""
    hard_skills = [s.skill for s in jd.hard_requirements]
    preferred = [s.skill for s in jd.preferred_skills]
    jd_keywords = list(jd.keywords)
    title_summary = " ".join([jd.title, jd.summary])
    directions = [d for d in jd_keywords]  # 方向语义并入关键词集

    candidates: list[Candidate] = []
    for order, item in enumerate(items):
        if not item.get("resume_allowed"):
            continue
        score = 0
        reasons: list[str] = []

        material_blob = " ".join([
            str(item.get("title") or ""),
            str(item.get("description") or ""),
            str(item.get("achievements") or ""),
            " ".join(_tokens(item.get("skills"))),
            " ".join(_tokens(item.get("keywords"))),
        ])

        for skill in hard_skills:
            if _hits_any(skill, [skill]) and _normalize(skill) in _normalize(material_blob):
                score += 25
                reasons.append(f"硬性技能命中：{skill}")
                break

        keyword_hit = next(
            (kw for kw in jd_keywords if _normalize(kw) and _normalize(kw) in _normalize(material_blob)),
            None,
        )
        if keyword_hit:
            score += 12
            reasons.append(f"关键词命中：{keyword_hit}")

        preferred_hit = next(
            (s for s in preferred if _normalize(s) and _normalize(s) in _normalize(material_blob)),
            None,
        )
        if preferred_hit and not keyword_hit:
            score += 8
            reasons.append(f"加分技能命中：{preferred_hit}")

        jd_blob = _normalize(title_summary)
        if _normalize(jd.title) and (
            _normalize(jd.title) in _normalize(material_blob)
            or _hits_any(jd_blob, [item.get("title")])
        ):
            score += 10
            reasons.append("与岗位标题/概述相关")

        item_directions = [d.lower() for d in _tokens(item.get("target_directions"))]
        if item_directions and any(
            d in jd_blob or any(d in _normalize(k) for k in jd_keywords) for d in item_directions
        ):
            score += 18
            reasons.append("方向命中：" + "/".join(item_directions[:3]))

        try:
            priority = int(item.get("priority") or 3)
        except (TypeError, ValueError):
            priority = 3
        score += max(1, min(5, priority))

        if score > 0:
            candidates.append(Candidate(material=item, score=score, reasons=reasons))

    candidates.sort(key=lambda c: (-c.score, -(int(c.material.get("priority") or 3)), items.index(c.material)))
    return candidates[:limit]


def validate_material_ids(ids: list[str], candidates: list[Candidate]) -> list[str]:
    """校验 AI 选择的素材 ID 必须属于本次候选集；非法 ID 直接抛错。"""
    allowed = {c.material.get("id") for c in candidates}
    cleaned: list[str] = []
    for raw in ids:
        value = str(raw or "").strip()
        if not value:
            continue
        if value not in allowed:
            raise ValueError(f"AI 引用了候选素材之外的素材：{value}")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def material_prompt(candidates: list[Candidate]) -> str:
    """构造候选素材的脱敏 prompt 文本：绝不含 source/notes。"""
    if not candidates:
        return ""
    lines = ["可用真实素材（只能引用下列 id；新增事实必须标注 material_ids）："]
    for c in candidates:
        m = c.material
        dates = "-".join(x for x in (m.get("start_date"), m.get("end_date")) if x)
        lines.append(
            f"- id={m.get('id')} | type={m.get('type')} | {m.get('title')}"
            + (f"（{m.get('organization')}）" if m.get("organization") else "")
            + (f" 角色：{m.get('role')}" if m.get("role") else "")
            + (f" 时间：{dates}" if dates else "")
            + f"\n  描述：{m.get('description')}"
            + (f"\n  成果：{m.get('achievements')}" if m.get("achievements") else "")
            + (f"\n  技能：{'、'.join(_tokens(m.get('skills')))}" if m.get("skills") else "")
            + (f"\n  方向：{'、'.join(_tokens(m.get('target_directions')))}" if m.get("target_directions") else "")
        )
    lines.append("规则：最多选择 4 条素材；新增事实必须真实来自素材原文；没有合适素材就保留底稿原文，material_ids 留空。")
    return "\n".join(lines)
