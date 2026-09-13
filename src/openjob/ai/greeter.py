"""AI Greeter - Generate personalized greeting messages with self-review."""

import json
import re
from pathlib import Path
from dataclasses import dataclass

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from openjob.ai.credentials import AIRequestError, call_anthropic_text
from openjob.cancellation import OperationCancelled, run_cancellable
from openjob.collection.text import clean_job_description
from openjob.db import (
    add_history,
    get_db,
    get_jobs_by_status,
    update_job_greeting,
    update_job_greeting_facts,
    update_job_status,
)

# Web server injects these so greeting generation uses the same runtime database/data
# directory as the workbench. CLI keeps the project-local defaults.
RUNTIME_DB_PATH: Path | None = None
RUNTIME_DATA_DIR: Path | None = None

TEMPLATE_RESUME_MARKERS = (
    "张三", "李四", "某某大学", "某某公司", "138-0000-0000",
    "zhangsan@example.com", "示例简历", "示例公司",
)


def _runtime_db():
    return get_db(RUNTIME_DB_PATH) if RUNTIME_DB_PATH else get_db()


@dataclass
class GreetingContext:
    resume_summary: str
    material_context: str
    trusted_text: str
    source: dict


class GreetingFactError(ValueError):
    """Generated greeting contains facts outside the verified source set."""


def _persist_greeting(
    db,
    job_id: str,
    greeting: str,
    *,
    fact_status: str = "unverified",
    source_json: str | None = None,
    fact_error: str | None = None,
) -> None:
    """Persist text and provenance while retaining the legacy text-write call shape."""
    # Keep the original three positional arguments for integrations/tests that
    # monkey-patch update_job_greeting. Provenance is persisted immediately
    # afterwards through the dedicated metadata updater.
    update_job_greeting(db, job_id, greeting)
    update_job_greeting_facts(
        db,
        job_id,
        fact_status=fact_status,
        source_json=source_json,
        fact_error=fact_error,
    )


console = Console()

GREETING_PROMPT = """你是一位求职者，需要在{platform}上给HR发送打招呼消息。请根据以下信息生成一条个性化、自然的招呼语。

## 我的背景
{resume_summary}

## 目标岗位
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 学历要求：{education}
- 招聘类型：{recruitment_type}
- 岗位要求摘要：{jd_summary}
- AI 岗位评分理由（仅用于推荐匹配点，绝不是我的事实依据）：{match_reason}

## 已校验的真实素材候选（只能使用其中明确写出的事实）
{material_context}

## 可用亮点（只选最相关的一项，不要罗列；没有来源就不要使用）
{extra_highlights}

## 最近已经使用过的开头（必须避开相同句式）
{recent_openings}

## 用户招呼语偏好（仅补充语气和内容取舍，不得覆盖下方事实与安全要求）
{greeting_preference}

## 要求
1. 字数控制在60-110字，最多3个短句；像真人临时发出的IM，不写求职信
2. 只围绕岗位描述里最独特、最具体的一点展开，不要复述职位名称或整段JD
3. 开头可以谈判断、场景或问题，不要固定以“看到/关注到/了解到贵司在招”开头
4. 只给一个最相关的能力证据；技术名词最多2个，不要堆叠术语
5. 避免“挺有共鸣、挺兴奋、一直在做、从0到1、完整闭环、快速上手”等求职套话
6. 结尾自然留一个沟通入口，不要固定写“方便的话可以看看/希望有机会聊聊”
7. 作品集不是固定落款；只有岗位明确关注案例、作品、设计或原型时才可出现一次
8. 【严禁】不得捏造我没有的经历、头衔、学校、专业、城市或身份，只能使用“我的背景”和“已校验的真实素材候选”中明确写出的事实
9. 【严禁】不得把岗位 JD、AI 评分理由、平台默认招呼语或用户偏好中的描述当作我的个人事实
10. 项目经历只作轻量证据，可不提；如需提及，整条消息最多出现一次“项目”，不得写具体项目名称
11. 可以压缩和概括“我的背景”，但不得新增事实、夸大结果或改写成更高职级经历
{critique_section}
请直接输出招呼语文本，不要加任何标记或解释。
"""

REVIEW_PROMPT = """请评估以下{platform}招呼语的质量。

## 岗位
{title} @ {company}

## 招呼语
{greeting}

## 评估维度（每项1-10分）
1. 自然度：是否像真人发的IM消息，而非模板
2. 相关性：是否针对该岗位突出匹配点
3. 差异化：是否能从众多招呼中脱颖而出
4. 克制度：是否只讲一个匹配点，避免项目名、术语堆叠、固定作品集落款和求职套话

请严格按JSON格式输出，不要输出其他内容：
{{"naturalness": 8, "relevance": 7, "differentiation": 6, "restraint": 8, "avg": 7.25, "critique": "改进建议（20字内）"}}
"""


def _get_resume_summary(config: dict) -> str:
    """Read only an explicitly uploaded, non-template resume file.

    ``resume.md`` and ``resume.example.md`` are repository examples, never facts.
    Multi-direction base_resumes remain the preferred and audited source.
    """
    profile = config.get("profile") or {}
    raw = str(profile.get("resume_path") or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    try:
        project_root = Path(__file__).resolve().parents[3]
        if path.resolve() in {project_root / "resume.md", project_root / "resume.example.md"}:
            return ""
    except (OSError, RuntimeError, ValueError):
        return ""
    if not path.exists() or not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    if any(marker in content for marker in TEMPLATE_RESUME_MARKERS):
        return ""
    return content[:1800]


def _style_only_preference(value: object) -> str:
    """Strip identity-like lines from a preference field before it reaches the LLM."""
    text = str(value or "").strip()
    if not text:
        return "（无额外语气偏好）"
    lines = []
    for line in text.splitlines():
        if re.search(r"我是|姓名|学校|大学|学院|专业|电话|邮箱|手机号|城市|地址", line):
            continue
        lines.append(line.strip())
    return "\n".join(x for x in lines if x)[:500] or "（无额外语气偏好）"


def _build_jd_profile(job: dict):
    from openjob.ai.resume_engine.models import JdProfile

    jd = clean_job_description(job.get("jd", ""))
    # Deterministic keyword extraction is only for ranking existing materials;
    # it is not presented as a user fact and does not replace JD parsing.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}|[\u4e00-\u9fff]{2,12}", f"{job.get('title', '')} {jd}")
    keywords = list(dict.fromkeys(tokens))[:80]
    return JdProfile(title=str(job.get("title") or ""), summary=jd, keywords=keywords)


def _load_greeting_materials(config: dict, job: dict):
    from openjob.ai.resume_engine.materials import material_prompt, rank_materials
    from openjob.resume_materials import MaterialLibraryError, load_or_refresh_library, resolve_library_paths

    data_dir = RUNTIME_DATA_DIR or Path("data")
    materials_path, index_path = resolve_library_paths(config, data_dir)
    try:
        library = load_or_refresh_library(materials_path, index_path)
    except MaterialLibraryError as exc:
        if "未找到" in str(exc):
            return None, "", None
        raise
    candidates = rank_materials(library.items, _build_jd_profile(job), limit=8)
    return candidates, material_prompt(candidates), library


def _build_greeting_context(db, job: dict, config: dict) -> GreetingContext:
    from openjob.ai.resume_engine.bases import select_base_for_job

    jd_text = str(job.get("jd") or "").strip()
    if not jd_text:
        raise GreetingFactError("岗位缺少 JD 原文，无法进行事实一致性生成")
    selection = select_base_for_job(db, f"{job.get('title') or ''}\n{jd_text}", config)
    base = selection.base
    if not base:
        # Compatibility path for a user's explicitly uploaded real resume file;
        # select_base_for_job itself still rejects repository examples.
        resume_summary = _get_resume_summary(config)
        if not resume_summary:
            raise GreetingFactError(selection.reason)
        base = {"id": None, "name": "用户上传简历文件", "content_md": resume_summary}
    base_text = str(base.get("content_md") or "").strip()
    if not base_text:
        raise GreetingFactError("真实简历底稿为空")
    if any(marker in base_text for marker in TEMPLATE_RESUME_MARKERS):
        raise GreetingFactError("底稿包含示例/占位信息，已阻止生成")

    candidates, material_context, library = _load_greeting_materials(config, job)
    candidate_items = [c.material for c in (candidates or [])]
    trusted_parts = [base_text]
    for item in candidate_items:
        trusted_parts.append(" ".join(str(item.get(k) or "") for k in (
            "title", "organization", "city", "role", "start_date", "end_date", "description", "achievements", "resume_bullets", "award_level", "skills", "keywords", "target_directions"
        )))
    source = {
        "base_resume_id": base.get("id"),
        "base_resume_name": base.get("name"),
        "base_selection_reason": selection.reason,
        "material_library_sha256": getattr(library, "sha256", None),
        "candidate_material_ids": [str(item.get("id")) for item in candidate_items if item.get("id")],
        "fact_policy": "only_base_resume_and_resume_allowed_materials",
    }
    return GreetingContext(
        resume_summary=base_text,
        material_context=material_context or "（当前素材库没有与该 JD 明确匹配的候选，不得自行补充事实）",
        trusted_text=" ".join(trusted_parts),
        source=source,
    )


def _greeting_fact_issues(greeting: str, trusted_text: str) -> list[str]:
    """Conservative deterministic guard against hallucinated identity/metrics."""
    text = str(greeting or "")
    trusted = str(trusted_text or "")
    issues: list[str] = []
    for marker in TEMPLATE_RESUME_MARKERS:
        if marker in text:
            issues.append(f"包含示例/占位信息：{marker}")
    for pattern, label in (
        (r"[\u4e00-\u9fffA-Za-z]{2,30}(?:大学|学院)", "学校"),
        (r"[\u4e00-\u9fffA-Za-z]{2,30}(?:专业)", "专业"),
    ):
        for value in re.findall(pattern, text):
            if value not in trusted:
                issues.append(f"{label}事实未在真实底稿/素材库中找到：{value}")
    for value in re.findall(r"\b(?:1[3-9]\d{9}|\d{6,18}@[A-Za-z0-9.-]+|\d{2,}(?:\.\d+)?%?)\b", text):
        if value not in trusted:
            issues.append(f"联系方式或量化数字未在真实来源中找到：{value}")
    return list(dict.fromkeys(issues))


def _call_claude(
    prompt: str,
    config: dict,
    max_tokens: int | None = None,
    *,
    purpose: str = "greeting",
) -> str | None:
    """Call Claude API and return response text."""
    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    default_key = "greeting_review_max_tokens" if purpose == "greeting_review" else "greeting_max_tokens"
    default_tokens = 4096 if purpose == "greeting_review" else 8192
    token_limit = max_tokens if max_tokens is not None else ai_cfg.get(default_key, default_tokens)
    try:
        token_limit = max(128, min(int(token_limit or default_tokens), 65536))
    except (TypeError, ValueError):
        token_limit = default_tokens
    return run_cancellable(
        lambda: call_anthropic_text(
            prompt,
            config,
            token_limit,
            timeout=ai_cfg.get(
                f"{purpose}_timeout_seconds",
                ai_cfg.get("greeting_timeout_seconds", ai_cfg.get("timeout_seconds", 180)),
            ),
            purpose=purpose,
        ),
        config,
    )


def _truncate_prompt_text(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    marker = "\n...[为适配模型上下文已裁剪]...\n"
    available = max(limit - len(marker), 2)
    head = max(int(available * 0.7), 1)
    return f"{text[:head]}{marker}{text[-(available - head):]}"


def _notify(config: dict, message: str, *, error: bool = False) -> None:
    console.print(f"[{'red' if error else 'yellow'}]{message}[/{'red' if error else 'yellow'}]")
    callback = config.get("_workbench_log")
    if callable(callback):
        callback(message)


def _json_greeting_text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            nested = _json_greeting_text(item)
            if nested:
                return nested
        return None
    if not isinstance(value, dict):
        return None
    for key in ("greeting", "message", "text", "content"):
        nested = _json_greeting_text(value.get(key))
        if nested:
            return nested
    for key in ("data", "result", "output"):
        nested = _json_greeting_text(value.get(key))
        if nested:
            return nested
    return None


def _normalize_greeting_response(response: str | None) -> str | None:
    """Accept common provider wrappers while rejecting non-answer payloads."""
    if not isinstance(response, str):
        return None
    greeting = response.strip()
    if not greeting:
        return None

    fenced = re.fullmatch(
        r"```(?:json|text|markdown|md)?\s*(.*?)\s*```",
        greeting,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        greeting = fenced.group(1).strip()

    parsed_greeting = None
    structured_response = greeting.startswith("{") or greeting.startswith("[")
    if structured_response:
        try:
            parsed = json.loads(greeting)
        except (json.JSONDecodeError, TypeError):
            return None
        parsed_greeting = _json_greeting_text(parsed)
    else:
        decoder = json.JSONDecoder()
        for index, char in enumerate(greeting):
            if char not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(greeting[index:])
            except (json.JSONDecodeError, TypeError):
                continue
            parsed_greeting = _json_greeting_text(parsed)
            if parsed_greeting:
                break
    if structured_response and parsed_greeting is None:
        return None
    if parsed_greeting is not None:
        greeting = parsed_greeting

    greeting = re.sub(
        r"^\s*(?:最终)?(?:打招呼语|招呼语|消息内容|回复)\s*[:：]\s*",
        "",
        greeting,
        count=1,
        flags=re.IGNORECASE,
    )
    greeting = greeting.strip().strip('"\'“”‘’').strip()
    if not greeting:
        return None
    if re.fullmatch(r"(?is)(?:抱歉|无法|不能).{0,80}", greeting):
        return None

    if len(greeting) > 150:
        cut = greeting[:150]
        for sep in ("。", "！", "？", "～", "\n"):
            idx = cut.rfind(sep)
            if idx > 50:
                greeting = cut[:idx + 1]
                break
        else:
            greeting = cut
    return greeting.strip() or None


def _opening_signature(greeting: str, limit: int = 24) -> str:
    """Return a compact first-clause signature for batch-level diversity."""
    text = " ".join(str(greeting or "").split())
    for separator in ("，", "。", "！", "？", "——", "—", "-"):
        text = text.split(separator, 1)[0]
    return text[:limit]


def _greeting_style_issues(
    greeting: str,
    recent_openings: list[str] | None = None,
) -> list[str]:
    """Return deterministic style issues that should trigger a rewrite."""
    issues = []
    if greeting.count("项目") > 1:
        issues.append("不要反复强调项目，整条消息最多出现一次“项目”")
    if len(greeting) > 110:
        issues.append("压缩到110字以内，只保留一个匹配点")

    templated_openings = (
        "看到这个岗位",
        "看到贵司在招",
        "关注到贵司在招",
        "了解到贵司在招",
    )
    if greeting.startswith(templated_openings):
        issues.append("换掉模板化开头，直接从岗位中的具体问题或判断切入")

    clichés = (
        "挺有共鸣", "挺兴奋", "一直在做", "正好是我", "从0到1",
        "完整闭环", "完整落地", "快速上手", "期待进一步沟通",
        "方便的话可以看看",
    )
    used_clichés = [phrase for phrase in clichés if phrase in greeting]
    if used_clichés:
        issues.append(f"去掉求职套话：{'、'.join(used_clichés[:3])}")

    lower_greeting = greeting.lower()
    technical_concepts = [
        any(term in lower_greeting for term in ("agent", "tool calling", "memory")),
        any(term in lower_greeting for term in ("rag", "知识库")),
        "prompt" in lower_greeting,
        "工作流" in greeting and "agent" not in lower_greeting,
        any(term in lower_greeting for term in ("大模型", "llm")),
        "mcp" in lower_greeting,
    ]
    if sum(technical_concepts) > 2:
        issues.append("技术名词最多保留2个，只留下与岗位最相关的能力证据")

    opening = _opening_signature(greeting)
    if opening and opening in set(recent_openings or []):
        issues.append("本批次已使用相同开头，请换一种自然切入方式")
    return issues


def _parse_review_response(response: str | None) -> dict | None:
    if not isinstance(response, str) or not response.strip():
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(response):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(response[index:])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        try:
            avg = float(parsed.get("avg"))
        except (TypeError, ValueError):
            continue
        if not 1 <= avg <= 10:
            continue
        parsed["avg"] = avg
        parsed["critique"] = str(parsed.get("critique") or "")
        return parsed
    return None


def _platform_label(job: dict) -> str:
    return {
        "boss": "BOSS直聘",
        "zhilian": "智联招聘",
        "51job": "前程无忧",
    }.get(str(job.get("source_platform") or "boss"), "招聘平台")


def _review_greeting(
    greeting: str,
    job: dict,
    config: dict,
    max_tokens: int | None = None,
) -> dict | None:
    """Self-evaluate a greeting. Returns scores dict or None on failure."""
    prompt = REVIEW_PROMPT.format(
        platform=_platform_label(job),
        title=job["title"],
        company=job["company"],
        greeting=greeting,
    )
    response = _call_claude(prompt, config, max_tokens, purpose="greeting_review")
    return _parse_review_response(response)


def _generate_greeting_once(
    job: dict,
    resume_summary: str,
    config: dict,
    critique: str = "",
    *,
    compact: bool = False,
    max_tokens: int | None = None,
    recent_openings: list[str] | None = None,
    material_context: str = "",
) -> str | None:
    """Generate a single greeting attempt."""
    jd_limit = 250 if compact else 500
    resume_limit = 800 if compact else 1500
    jd_summary = _truncate_prompt_text(clean_job_description(job.get("jd", "")), jd_limit) or "无详细描述"
    critique_section = f"\n## 补充改进要求\n- {critique}\n" if critique else ""

    # Build extra highlights from config (portfolio URL, personal strengths, etc.)
    profile_cfg = config.get("profile", {})
    highlights = profile_cfg.get("extra_highlights", [])
    portfolio_url = profile_cfg.get("portfolio_url", "")
    highlight_lines = [f"- {h}" for h in highlights]
    portfolio_context = f"{job.get('title', '')} {job.get('jd', '')}".lower()
    portfolio_requested = any(
        keyword in portfolio_context
        for keyword in ("作品集", "案例", "case", "原型", "交互设计", "视觉设计")
    )
    if portfolio_url and portfolio_requested:
        # An explicitly configured portfolio URL is a user-provided fact. Keep
        # it available only when the JD actually asks for portfolio/case work;
        # arbitrary extra_highlights remain excluded until they are audited.
        highlight_lines.append(f"- 个人作品集网址：{portfolio_url}")
    extra_highlights = (
        "\n".join(line for line in highlight_lines if "个人作品集网址" in line)
        or "（已禁用未审计的配置亮点；只使用真实底稿/素材库）"
    )

    prompt = GREETING_PROMPT.format(
        platform=_platform_label(job),
        resume_summary=_truncate_prompt_text(resume_summary, resume_limit),
        title=job["title"],
        company=job["company"],
        salary=job["salary"] or "面议",
        education=job.get("education", "") or "未识别",
        recruitment_type={"campus": "校招", "experienced": "社招"}.get(
            job.get("recruitment_type", ""), "未识别"
        ),
        jd_summary=jd_summary,
        match_reason=_truncate_prompt_text(job.get("score_reason", "") or "（无，仅根据 JD 与真实来源匹配）", 240),
        material_context=_truncate_prompt_text(material_context or "（无）", 1800),
        critique_section=critique_section,
        extra_highlights=extra_highlights,
        recent_openings=(
            "\n".join(f"- {opening}" for opening in (recent_openings or [])[-8:])
            or "（暂无）"
        ),
        greeting_preference=_truncate_prompt_text(
            _style_only_preference(profile_cfg.get("greeting_preference", "")),
            500,
        ),
    )

    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    token_limit = max_tokens if max_tokens is not None else ai_cfg.get("greeting_max_tokens", 8192)
    response = _call_claude(prompt, config, token_limit)
    return _normalize_greeting_response(response)


def _generate_with_token_retry(
    job: dict,
    resume_summary: str,
    config: dict,
    critique: str = "",
    recent_openings: list[str] | None = None,
    material_context: str = "",
) -> str | None:
    """Retry only request-size/output-limit failures without changing batch size."""
    try:
        result = _generate_greeting_once(
            job,
            resume_summary,
            config,
            critique,
            recent_openings=recent_openings,
            material_context=material_context,
        )
        if result:
            return result
        ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
        try:
            max_attempts = max(1, min(int(ai_cfg.get("greeting_max_attempts", 2) or 2), 3))
        except (TypeError, ValueError):
            max_attempts = 2
        for attempt in range(2, max_attempts + 1):
            _notify(
                config,
                f"{job['company']}｜{job['title']} 未返回完整招呼语，正在重试（{attempt}/{max_attempts}）。",
            )
            result = _generate_greeting_once(
                job,
                resume_summary,
                config,
                critique,
                recent_openings=recent_openings,
                material_context=material_context,
            )
            if result:
                return result
        return None
    except AIRequestError as exc:
        if exc.kind == "output_truncated":
            _notify(config, f"{job['company']}｜{job['title']} 的招呼语回答被截断，正在增大输出 Token 上限后重试。")
            compact = False
            ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
            try:
                configured_tokens = int(ai_cfg.get("greeting_max_tokens", 8192) or 8192)
            except (TypeError, ValueError):
                configured_tokens = 8192
            retry_max_tokens = min(
                max(configured_tokens * 2, 600),
                65536,
            )
        elif exc.kind == "output_limit":
            _notify(config, f"{job['company']}｜{job['title']} 正在降低单次输出 Token 上限后重试招呼语。")
            compact = False
            retry_max_tokens = 160
        elif exc.kind == "context_limit":
            _notify(config, f"{job['company']}｜{job['title']} 内容较长，正在压缩后重试招呼语。")
            compact = True
            retry_max_tokens = 160
        else:
            raise

    try:
        return _generate_greeting_once(
            job,
            resume_summary,
            config,
            critique,
            compact=compact,
            max_tokens=retry_max_tokens,
            recent_openings=recent_openings,
            material_context=material_context,
        )
    except AIRequestError as retry_exc:
        if retry_exc.kind in {"output_truncated", "output_limit", "context_limit"}:
            _notify(
                config,
                f"已跳过 {job['company']}｜{job['title']}：调整单次 Token 请求后仍失败。",
            )
            return None
        raise


def _review_with_token_retry(greeting: str, job: dict, config: dict) -> dict | None:
    """Keep greeting review from interrupting the usable generated draft."""
    try:
        return _review_greeting(greeting, job, config)
    except AIRequestError as exc:
        if exc.kind == "output_truncated":
            _notify(config, f"{job['company']}｜{job['title']} 的质量检查回答被截断，正在增大输出 Token 上限后重试。")
            ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
            try:
                configured_tokens = int(ai_cfg.get("greeting_review_max_tokens", 4096) or 4096)
            except (TypeError, ValueError):
                configured_tokens = 4096
            retry_max_tokens = min(
                max(configured_tokens * 2, 600),
                65536,
            )
        elif exc.kind == "output_limit":
            _notify(config, f"{job['company']}｜{job['title']} 正在降低单次输出 Token 上限后重试质量检查。")
            retry_max_tokens = 128
        elif exc.kind == "context_limit":
            return None
        else:
            raise

    try:
        return _review_greeting(greeting, job, config, retry_max_tokens)
    except AIRequestError as retry_exc:
        if retry_exc.kind in {"output_truncated", "output_limit", "context_limit"}:
            return None
        raise


def generate_greetings(config: dict) -> int:
    """Generate greetings for approved jobs with optional self-review. Returns count generated."""
    db = _runtime_db()
    jobs = get_jobs_by_status(db, "approved")
    _workbench_job_ids = {str(job_id) for job_id in config.get("_workbench_job_ids", [])}
    if _workbench_job_ids:
        jobs = [job for job in jobs if str(job["id"]) in _workbench_job_ids]

    if not jobs:
        console.print("[yellow]没有已确认的岗位可生成招呼语。请先运行 `openjob confirm`，或使用 `openjob run` 执行完整流程。[/yellow]")
        db.close()
        return 0

    # Context is built per job because each JD may select a different real base
    # resume and a different set of resume_allowed material candidates.

    ai_cfg = config.get("ai", {})
    review_threshold = ai_cfg.get("greeting_review_threshold", 7.0)
    try:
        max_iterations = max(0, int(ai_cfg.get("greeting_max_iterations", 2) or 0))
    except (TypeError, ValueError):
        max_iterations = 2

    recent_rows = db.execute(
        """
        SELECT greeting
        FROM jobs
        WHERE greeting IS NOT NULL AND trim(greeting) != ''
        ORDER BY updated_at DESC
        LIMIT 20
        """
    ).fetchall()
    recent_openings = [
        opening
        for row in recent_rows
        if (opening := _opening_signature(str(row["greeting"] or "")))
    ]

    count = 0
    failed = 0
    pause_reason = ""
    stop_event = config.get("_workbench_stop_event")
    cancelled = False

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task(f"生成招呼语 (0/{len(jobs)})", total=len(jobs))

        for index, job in enumerate(jobs, start=1):
            if stop_event is not None and stop_event.is_set():
                break
            try:
                greeting_context = _build_greeting_context(db, job, config)
            except (GreetingFactError, OSError, ValueError, RuntimeError) as exc:
                reason = str(exc)
                failed += 1
                add_history(db, job["id"], "greeting_fact_failed", reason)
                _persist_greeting(db, job["id"], str(job.get("greeting") or ""), fact_status="failed", fact_error=reason)
                _notify(config, f"已阻止 {job['company']}｜{job['title']}：{reason}", error=True)
                progress.update(task, advance=1, description=f"生成招呼语 ({index}/{len(jobs)})")
                continue
            best_greeting = None
            pause_after_current = ""

            for iteration in range(max_iterations + 1):
                if stop_event is not None and stop_event.is_set():
                    break
                critique = ""
                if iteration > 0 and best_greeting:
                    try:
                        review = _review_with_token_retry(best_greeting, job, config)
                    except OperationCancelled:
                        cancelled = True
                        break
                    except AIRequestError as exc:
                        pause_after_current = exc.user_message
                        break
                    style_issues = _greeting_style_issues(best_greeting, recent_openings)
                    if review is None and not style_issues:
                        _notify(
                            config,
                            f"{job['company']}｜{job['title']} 的质量检查返回格式无法识别，已保留可用招呼语并继续。",
                        )
                        break
                    if review and review.get("avg", 10) >= review_threshold and not style_issues:
                        break
                    critique_parts = style_issues[:]
                    if review and review.get("critique"):
                        critique_parts.append(str(review["critique"]))
                    critique = "；".join(critique_parts)

                try:
                    greeting = _generate_with_token_retry(
                        job,
                        greeting_context.resume_summary,
                        config,
                        critique,
                        recent_openings,
                        material_context=greeting_context.material_context,
                    )
                except OperationCancelled:
                    cancelled = True
                    break
                except AIRequestError as exc:
                    if best_greeting:
                        pause_after_current = exc.user_message
                    else:
                        pause_reason = exc.user_message
                    break

                if not greeting:
                    if not best_greeting:
                        failed += 1
                    break

                best_greeting = greeting
                if max_iterations == 0:
                    break

            if cancelled or (stop_event is not None and stop_event.is_set()):
                break
            if not best_greeting:
                if not pause_reason and not (stop_event is not None and stop_event.is_set()):
                    add_history(db, job["id"], "greeting_failed", "AI 未返回完整招呼语，岗位保留为待生成")
                progress.update(task, advance=1, description=f"生成招呼语 ({index}/{len(jobs)})")
                if pause_reason:
                    break
                continue

            fact_issues = _greeting_fact_issues(best_greeting, greeting_context.trusted_text)
            if fact_issues:
                reason = "；".join(fact_issues[:5])
                failed += 1
                _persist_greeting(db, job["id"], best_greeting, fact_status="failed", source_json=json.dumps(greeting_context.source, ensure_ascii=False), fact_error=reason)
                add_history(db, job["id"], "greeting_fact_failed", reason)
                _notify(config, f"已阻止 {job['company']}｜{job['title']}：{reason}", error=True)
                progress.update(task, advance=1, description=f"生成招呼语 ({index}/{len(jobs)})")
                continue

            _persist_greeting(
                db,
                job["id"],
                best_greeting,
                fact_status="verified",
                source_json=json.dumps(greeting_context.source, ensure_ascii=False),
                fact_error=None,
            )
            update_job_status(db, job["id"], "ready")
            opening = _opening_signature(best_greeting)
            if opening:
                recent_openings.append(opening)
            count += 1
            progress.update(task, advance=1, description=f"生成招呼语 ({index}/{len(jobs)})")

            if pause_after_current:
                pause_reason = pause_after_current
                break

    db.close()
    if pause_reason:
        remaining = max(len(jobs) - count, 0)
        _notify(
            config,
            f"招呼语生成已安全暂停：{pause_reason}。已生成内容已保存，剩余 {remaining} 个岗位下次运行会继续处理。",
            error=True,
        )
    if failed:
        _notify(config, f"本轮有 {failed} 个岗位未生成招呼语并保留为待处理，可稍后重试。")
    return count
