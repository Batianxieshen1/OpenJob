"""结构化 LLM 调用：JSON 输出 + 校验 + 失败反馈重试。

复用 credentials.call_anthropic_text（内部按 provider 自动路由到 DeepSeek 等
OpenAI 兼容接口），不直接绑定 openai SDK，保持多服务商扩展点。
校验失败时把错误原因反馈给模型重试；超限抛 AIRequestError，不静默拿脏数据。
"""

import json

from openjob.ai.credentials import AIRequestError, call_anthropic_text

_MAX_JSON_RETRIES = 1  # 首次 + 1 次纠错重试


def extract_json_object(text: str) -> dict:
    """从模型输出提取 JSON 对象：剥 code fence，截取首个 { 到最后一个 }。"""
    stripped = text.strip()
    if "```" in stripped:
        parts = stripped.split("```")
        # 取第一个成对 fence 内的内容；无闭合 fence 则取首段之后的内容
        candidate = parts[1] if len(parts) > 1 else parts[0]
        if candidate.lstrip().lower().startswith("json"):
            candidate = candidate.lstrip()[4:]
        stripped = candidate.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("输出中未找到 JSON 对象")
    try:
        data = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层必须是对象")
    return data


def chat_json(
    system: str,
    user: str,
    config: dict,
    *,
    validator,  # Callable[[object], T]，校验失败抛 ValueError
    max_tokens: int,
    timeout: float | None = None,
    purpose: str | None = None,
):
    """调用 AI 并校验输出；校验失败带错误重试一次。"""
    current_user = user
    last_error: ValueError | None = None
    for attempt in range(_MAX_JSON_RETRIES + 1):
        raw = _call_raw(system, current_user, config, max_tokens, timeout, purpose)
        if not raw:
            raise AIRequestError("empty_response", "AI 未返回内容，请检查模型配置或稍后重试")
        try:
            data = extract_json_object(raw)
            return validator(data)
        except ValueError as exc:
            last_error = exc
            if attempt >= _MAX_JSON_RETRIES:
                break
            current_user = (
                f"{user}\n\n注意：你上一次的输出无法通过校验（错误：{exc}）。"
                "请重新输出一个完整、合法的 JSON 对象，字段名和类型必须与要求一致。"
            )
    raise AIRequestError("invalid_output", f"AI 输出连续校验失败：{last_error}")


def _call_raw(
    system: str,
    user: str,
    config: dict,
    max_tokens: int,
    timeout: float | None,
    purpose: str | None,
) -> str | None:
    prompt = f"{system}\n\n{user}\n\n只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字。"
    return call_anthropic_text(prompt, config, max_tokens, timeout=timeout, purpose=purpose)
