"""② 匹配分析：简历证据 vs JD 要求逐条对照 → 命中/可迁移/缺口。"""

from openjob.ai.resume_engine.llm import chat_json
from openjob.ai.resume_engine.models import JdProfile, MatchReport

MATCH_SYSTEM = """你是严格的简历-岗位匹配分析师。对照 JD 要求清单与求职者简历，逐条输出匹配情况。

输出 JSON：{"entries": [{"requirement", "status", "evidence", "note"}], "overall_note"}
- requirement: JD 要求条目原文（复述即可）
- status: 三选一
  * hit：简历中有直接、可引用的经历支撑
  * transferable：无直接经历，但有可迁移的相关证据
  * gap：简历中找不到支撑
- evidence: 命中/可迁移时，摘录简历中的支撑原文；gap 填空串
- note: 一句话说明判定理由（可迁移如何迁移、缺口影响多大）
- overall_note: 总体匹配度一句话

铁律：
1. 只依据简历原文判断，不得脑补简历中不存在的能力。
2. 每条 JD 硬性要求都要出现在 entries 中，不遗漏。"""


def analyze_match(resume_md: str, jd: JdProfile, config: dict) -> MatchReport:
    if not resume_md or not resume_md.strip():
        raise ValueError("简历文本为空")
    requirements = []
    for skill in jd.hard_requirements + jd.preferred_skills:
        tag = "硬性" if skill.required else "加分"
        requirements.append(f"- [{tag}][权重{skill.weight}] {skill.skill}")
    user = f"""目标岗位：{jd.title}
岗位概述：{jd.summary}

JD 要求清单：
{chr(10).join(requirements) or '-（无）'}

求职者简历：
{resume_md.strip()}"""
    return chat_json(
        MATCH_SYSTEM,
        user,
        config,
        validator=MatchReport.from_payload,
        max_tokens=int(config.get("ai", {}).get("resume_match_max_tokens", 4096)),
        purpose="resume",
    )
