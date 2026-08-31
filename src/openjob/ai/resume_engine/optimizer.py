"""③ 分段改写 + ④ 汇总校验。

③ 按“经历块”逐段改写，每块输出 before/after/reason/risk；
④ 把采纳的修改块应用到原文合成完整简历，再做完整性校验：
   - 无新增事实（联系方式/链接/日期/数字，复用内置防编造校验器）
   - 无新增公司/学历/占位符
   - 字数不膨胀超 10%
校验不过的问题分级：blocking（致命，生成失败）/ warning（进 risk_flags）。
"""

import re

from openjob.ai.resume import (
    _find_blocking_integrity_issues,
    _find_resume_artifacts,
)
from openjob.ai.resume_engine.llm import chat_json
from openjob.ai.resume_engine.models import JdProfile, MatchReport, RewriteResult, SectionChange

REWRITE_SYSTEM = """你是资深简历顾问。根据 JD 画像与匹配报告，对简历做“分段改写”。

输出 JSON：{"changes": [{"section", "before", "after", "reason", "risk"}]}

写作风格（最高优先级——读起来必须像求职者本人亲手写的）：
- 只写具体的事实、动作和结果；每条以动词或数字开头，不写任何主观自评。
- 严禁出现以下 AI 腔表述（含任何变体）：“体现”“展现了”“彰显”“凸显”“展示出了”、
  “具备较强的”、“良好的”、“出色的”、“深厚的”、“扎实的”，以及一切“体现对XX的XX”、
  “体现XX能力/思维/态度”式总结句——绝不在句尾添加能力标签。
- 改写手法只有三种：换更准确的动词、把 JD 关键词自然融进原句、调整语序突出与岗位相关的细节。
- 语气朴素直接，像在跟 HR 面对面说话；宁可平实，不要华丽。

铁律（违反任何一条都是事故）：
1. 改写不编造：绝不新增原简历没有的经历、技能、项目、数字、证书。
2. 不删减：改写后的内容量不得少于原文——只换说法、融关键词、调顺序，
   不压缩、不省略、不丢掉原有细节与数据（页面要撑满一页）。
3. before 必须是原简历中的原文片段（逐字摘录），after 是改写后的对应文本；未改动的段落不要输出。
4. reason 一句话说明对齐了 JD 的哪条要求。
5. risk 填“可能过度包装、需求职者本人核实”的点（如数据为估算、表述强于实际证据）；没有就填空串。
6. gap 类缺口不要硬编经历覆盖；可迁移项用真实证据重新表述。
7. JD 关键词只融入确实相关的段落，不堆砌。
8. section 填简历栏目名（如 “工作经历/XX公司”、“个人优势”），便于人工逐块审阅。
9. 相关经历块优先保留真实的平台案例与量化结果（阅读/观看、点赞收藏、粉丝增长、媒体发布等），
   弱相关内容可以换写法融入岗位关键词，但不能删除这些事实细节。
10. 结构零改动：changes 只允许覆盖正文文字。姓名、电话、邮箱、地址，以及栏目名称行
    （如“教育经历”“实习经历”“项目经历”“获奖情况”“技能特长”）绝不能出现在 before/after 中；
    “求职意向：…”行只允许把岗位名称换成 JD 方向，其余文字不动。
11. 行格式零改动：before 与 after 的行结构完全一致——"• | " 前缀原样保留，
    "A | B | C" 多格行的分隔段数一致；不增行、不删行、不合并行、不拆行。
12. 长度硬上限：每条 after 的长度不得超过 before 的 115%，且绝不能在行尾追加
    “体现/展现/具备/熟练掌握……”式能力标签、额外课程名或工具名——这些都会撑爆一页排版。
    某一行如果与 JD 无关、或你想不出不超长的改法，就不要输出这条 change（原文自动保留）。
13. 禁止假改写：before 与 after 完全相同的 change 一律不许输出——那等于什么都没做。
    没有值得改的行就输出空数组，而不是用复制原文凑数。"""

# AI 腔检测：改写结果出现即打回重写（chat_json 会把原因反馈给模型）。
# 不带"了"的形态也要拦（"体现执行能力"这类句尾能力标签是高频回归点）
AI_FLAVOR_PATTERNS = (
    "体现", "展现了", "彰显", "凸显", "展示出了",
    "具备较强的", "良好的沟通", "出色的", "深厚的", "扎实的",
)

# 句尾能力标签自动剥离：模型对"体现XX能力"依赖过强，打回重试不收敛时直接清洗采纳
_FLAVOR_TAIL = re.compile(r"[，,]?\s*(?:体现|展现了?|彰显|凸显|展示出了?)[^。，；\n]*")


def _strip_ai_flavor(text: str) -> str:
    cleaned = _FLAVOR_TAIL.sub("", text)
    if cleaned != text:
        cleaned = cleaned.rstrip("，,；;：:")
        if cleaned and not cleaned.endswith(("。", "！", "？")):
            cleaned += "。"
    return cleaned


def rewrite_sections(
    resume_md: str,
    jd: JdProfile,
    match: MatchReport,
    config: dict,
) -> RewriteResult:
    requirements = []
    for skill in jd.hard_requirements + jd.preferred_skills:
        tag = "硬性" if skill.required else "加分"
        requirements.append(f"- [{tag}] {skill.skill}")
    matches = []
    for entry in match.entries:
        matches.append(f"- [{entry.status}] {entry.requirement}" + (f"｜证据：{entry.evidence}" if entry.evidence else ""))
    # 逐行字数预算：after 超过该行预算就会被排版层整行回退原文
    resume_lines = [line.strip() for line in resume_md.splitlines() if line.strip()]
    budgeted = "\n\n".join(
        f"{line}〔本行字数预算≤{len(line.replace(' ', '')) + 3}字，超长整行作废〕"
        if (" | " in line or len(line) > 12) else line
        for line in resume_lines
    )
    user = f"""目标岗位：{jd.title}
建议融入关键词：{', '.join(jd.keywords) or '无'}

JD 要求：
{chr(10).join(requirements) or '-（无）'}

匹配报告：
{chr(10).join(matches) or '-（无）'}

求职者原始简历（行末〔〕内是该行改写后的字数硬上限，只做等长换写）：
{budgeted.strip()}"""
    def _validate_style(payload):
        result = RewriteResult.from_payload(payload)
        real_changes = []
        for change in result.changes:
            if _strip_ai_flavor(change.before) == _strip_ai_flavor(change.after):
                continue  # 假改写（复制原文凑数）直接丢弃
            hit = next((p for p in AI_FLAVOR_PATTERNS if p in change.after), None)
            if hit:
                cleaned = _strip_ai_flavor(change.after)
                # 剥离后仍保留主体（≥50%）且不再含腔调 → 自动清洗采纳；整条都是腔调 → 打回
                if (
                    cleaned
                    and len(cleaned) >= len(change.after) * 0.5
                    and not any(p in cleaned for p in AI_FLAVOR_PATTERNS)
                ):
                    change.after = cleaned
                else:
                    raise ValueError(
                        f"改写出现 AI 腔表述「{hit}」。简历必须像求职者本人写的："
                        "去掉一切总结性评价（体现了/展现了/彰显了…），"
                        "只保留具体动作、方法和量化结果，用动词开头重新写这条。"
                    )
            lb = len(re.sub(r"\s", "", change.before))
            la = len(re.sub(r"\s", "", change.after))
            if lb > 12 and la > lb * 1.3:
                raise ValueError(
                    f"这条改写比原文长 {round((la / lb - 1) * 100)}%（上限 30%）："
                    f"「{change.after[:44]}」。只允许等长换写——换动词、把 JD 关键词替换进原句、"
                    "调整语序，禁止在原句后面追加任何新短语；删掉你加的内容再输出。"
                )
            real_changes.append(change)
        result.changes = real_changes
        return result

    return chat_json(
        REWRITE_SYSTEM,
        user,
        config,
        validator=_validate_style,
        max_tokens=int(config.get("ai", {}).get("resume_rewrite_max_tokens", 8192)),
        purpose="resume",
    )


MAX_GROWTH_RATIO = 0.10
_COMPANY_LINE = re.compile(r"^#{1,3}\s*(.+)$", re.M)


def apply_changes(resume_md: str, changes: list[SectionChange]) -> str:
    """把人工采纳的修改块逐个应用到原文，返回合成后的完整简历。

    未在 changes 中命中的段落原样保留；before 匹配不到就跳过该块（保真优先）。
    """
    text = resume_md
    for change in changes:
        candidate = change.before.strip()
        if candidate and candidate in text:
            text = text.replace(candidate, change.after.strip(), 1)
    return text


LINE_GROWTH_RATIO = 1.3


def sanitize_line_lengths(base_md: str, assembled_md: str, max_ratio: float = LINE_GROWTH_RATIO) -> str:
    """行级兜底：单行明显超长的改写整行回退原文，其余改写保留。

    模型倾向在行尾扩写（能力标签/课程名/工具名），重试也不一定收敛；
    超标行直接回退原文即可保住一页排版，代价只是该行不做定向优化。
    """
    base_lines = [line.strip() for line in base_md.splitlines() if line.strip()]
    lines = [line.strip() for line in assembled_md.splitlines() if line.strip()]
    if len(base_lines) != len(lines):
        return assembled_md  # 结构异常交由 validate_assembled 处理
    out: list[str] = []
    for b, n in zip(base_lines, lines):
        lb = len(re.sub(r"\s", "", b))
        ln = len(re.sub(r"\s", "", n))
        out.append(b if (lb > 12 and ln > lb * max_ratio) else n)
    return "\n\n".join(out)


def validate_assembled(
    base_resume: str,
    assembled_md: str,
    *,
    include_layout: bool = True,
) -> tuple[list[str], list[str]]:
    """汇总校验：返回 (blocking_issues, warnings)。

    include_layout=False 时只做事实完整性检查（编造/占位符/删减），
    跳过结构与长度检查——供“先查编造、再做长度兜底”的两段式流程使用。
    """
    blocking: list[str] = []
    warnings: list[str] = []

    if not assembled_md.strip():
        blocking.append("合成结果为空")
        return blocking, warnings

    # 事实完整性：新增事实（编造）/新增占位符/丢失核心事实（联系方式等）
    blocking.extend(_find_blocking_integrity_issues(assembled_md, base_resume))

    # 过程性措辞
    artifacts = _find_resume_artifacts(assembled_md)
    if artifacts:
        warnings.append(f"包含定制过程性措辞：{', '.join(artifacts[:5])}")

    if not include_layout:
        return blocking, warnings

    # 字数膨胀
    base_len = len(re.sub(r"\s", "", base_resume))
    new_len = len(re.sub(r"\s", "", assembled_md))
    if base_len > 0 and new_len > base_len * (1 + MAX_GROWTH_RATIO):
        blocking.append(
            f"简历字数膨胀 {round((new_len / base_len - 1) * 100)}%（上限 {round(MAX_GROWTH_RATIO * 100)}%）"
        )
    # 内容删减：改写不许压缩（页面要撑满一页）
    if base_len > 0 and new_len < base_len * 0.92:
        blocking.append(
            f"简历内容比原稿少了 {round((1 - new_len / base_len) * 100)}%——"
            "改写只换说法不删细节，页面需要撑满一页"
        )

    # 新增标题行（新公司/新学校/新栏目的粗信号）
    base_headings = {h.strip().lower() for h in _COMPANY_LINE.findall(base_resume)}
    for heading in _COMPANY_LINE.findall(assembled_md):
        if heading.strip().lower() not in base_headings:
            warnings.append(f"新增了原简历没有的标题行：{heading.strip()[:40]}（请核实非编造）")
            break

    # 结构一致：渲染器按行位置写回模板，行数/格子数变了会整篇错位
    base_lines = [line.strip() for line in base_resume.splitlines() if line.strip()]
    new_lines = [line.strip() for line in assembled_md.splitlines() if line.strip()]
    if len(base_lines) != len(new_lines):
        blocking.append(
            f"结构改变：原稿 {len(base_lines)} 行、改写后 {len(new_lines)} 行——"
            "只允许逐行替换文字，不许增删行"
        )
    else:
        for idx, (b, n) in enumerate(zip(base_lines, new_lines)):
            if len(b.split(" | ")) != len(n.split(" | ")):
                blocking.append(f"第 {idx + 1} 行格子数改变（{b[:30]}）：{n[:40]}")
                break
        # 行级膨胀：单行明显变长会导致渲染折行、溢出一页
        for idx, (b, n) in enumerate(zip(base_lines, new_lines)):
            lb = len(re.sub(r"\s", "", b))
            ln_ = len(re.sub(r"\s", "", n))
            if lb > 12 and ln_ > lb * LINE_GROWTH_RATIO:
                blocking.append(
                    f"第 {idx + 1} 行比原稿长 {round((ln_ / lb - 1) * 100)}%"
                    f"（上限 {round((LINE_GROWTH_RATIO - 1) * 100)}%），"
                    f"疑似添加了总结性尾巴：{n[:40]}"
                )
                break

    # 固定区保护：短纯文本行（030 结构的栏目名/姓名等）必须逐字保留；
    # 带 # 的 markdown 标题行归上方“新增标题行”warning 通道管辖
    new_set = set(new_lines)
    for line in base_lines:
        if len(line) <= 10 and " | " not in line and not line.startswith("#") and line not in new_set:
            blocking.append(f"固定区行被改动：「{line}」必须逐字保留")
            break

    return blocking, warnings
