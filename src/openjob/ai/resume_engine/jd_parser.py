"""① JD 解析：岗位描述原文 → JdProfile 结构化画像。"""

from openjob.ai.resume_engine.llm import chat_json
from openjob.ai.resume_engine.models import JdProfile

JD_PARSE_SYSTEM = """你是资深 HR 与招聘信息结构化专家。把岗位 JD 解析成结构化 JSON，字段如下：
- title: 岗位名称（字符串，必填）
- company_hint: 公司/行业线索，原文没有就填空串
- summary: 一句话概括岗位核心职责
- hard_requirements: 数组，JD 中“必须具备”的能力，每项 {skill, weight, required, source_evidence}
- preferred_skills: 数组，“加分项/优先”条目，格式同上，required 固定为 false
- keywords: 数组，建议写进简历的 JD 高频专业术语，5-10 个
- experience_min_years: 数字或 null，只从原文提取
- education_min: 字符串或 null，如 “本科”，只从原文提取
- red_flags: 数组，风险信号（收费、押金、培训贷、传销话术、明显夸大），没有就空数组

规则：
1. weight 取值 1-5，代表该能力对胜任岗位的重要程度，硬性要求一般 >=3。
2. source_evidence 必须引用 JD 原文原句，证明该条目不是编造的。
3. experience_min_years / education_min 原文没提就填 null。"""


def parse_jd(jd_text: str, config: dict) -> JdProfile:
    """把 JD 原文解析成结构化画像。"""
    if not jd_text or not jd_text.strip():
        raise ValueError("JD 文本为空")
    return chat_json(
        JD_PARSE_SYSTEM,
        f"JD 原文：\n{jd_text.strip()}",
        config,
        validator=JdProfile.from_payload,
        max_tokens=int(config.get("ai", {}).get("resume_jd_parse_max_tokens", 4096)),
        purpose="resume",
    )
