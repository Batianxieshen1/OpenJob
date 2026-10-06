"""公司画像采集的共享构件：Logo 转存、简介清洗、公司主页提取 JS。

选择器依据 2026-10-02 S0 勘察（scripts/debug_company_profile.py）：
- 详情页公司卡 .sider-company .company-info：Logo 图片 + /gongsi/ 公司主页链接；
- 公司主页（/gongsi/<hash>.html）：h3"公司简介"所在 .job-sec 区块为简介文本；
- Logo 图床（img.bosszhipin.com）允许带 Referer 的服务端直取（页面内 fetch 被 CORS 拦截）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx

LOGO_MAX_BYTES = 512 * 1024
_LOGO_FETCH_HEADERS = {
    "Referer": "https://www.zhipin.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
    ),
}

# 公司主页简介提取：定位"公司简介"标题所在区块，直接取整块 textContent。
# 不做 DOM 克隆删减——简介正文就位于 fold/expand 类容器内，按类名删元素会把正文一并删掉
# （2026-10-02 真机教训：删减版只取到"展开"二字）；界面词由 clean_company_intro 剥离。
JS_EXTRACT_COMPANY_INTRO = """
(() => {
    const out = { intro: '', company_page_url: location.pathname };
    const heading = Array.from(document.querySelectorAll('h2, h3'))
        .find(el => /公司简介|公司介绍|企业介绍/.test(el.textContent || ''));
    if (!heading) return JSON.stringify(out);
    const section = heading.closest('.job-sec') || heading.parentElement;
    if (!section) return JSON.stringify(out);
    out.intro = section.textContent.trim();
    return JSON.stringify(out);
})()
"""


def normalize_company_name(company: str) -> str:
    """公司名归一（仅去空白）：作为 Logo 落盘与去重的键，不做模糊归并。"""
    return re.sub(r"\s+", "", str(company or ""))


def logo_file_key(company: str) -> str:
    return hashlib.sha1(normalize_company_name(company).encode("utf-8")).hexdigest()[:16]


def clean_company_intro(text: str, max_chars: int = 2000) -> str:
    """简介清洗：折叠空白、剥掉区块标题词与折叠按钮残留、超长截断。"""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"^(?:公司简介|公司介绍|企业介绍)[:：]?", "", value).strip()
    value = re.sub(r"(?:展开|收起)$", "", value).strip()
    if len(value) > max_chars:
        value = value[:max_chars].rstrip() + "…"
    return value


def absolute_company_url(href: str) -> str | None:
    """公司主页链接规范化：排除"查看全部职位"（/gongsi/job/），返回绝对 URL。"""
    value = str(href or "").strip()
    if value.startswith("//"):
        value = f"https:{value}"
    if value.startswith("/"):
        if "/gongsi/job/" in value:
            return None
        if value.startswith("/gongsi/") or value.startswith("/company/"):
            return f"https://www.zhipin.com{value}"
        return None
    if value.startswith("http") and ("/gongsi/" in value or "/company/" in value) and "/gongsi/job/" not in value:
        return value
    return None


def _sniff_image_ext(content: bytes) -> str:
    """按魔数识别图片类型，识别失败返回空串（宁可不落盘，不留坏文件）。"""
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"\x89PNG"):
        return "png"
    if content.startswith(b"GIF8"):
        return "gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return ""


def fetch_company_logo(url: str, timeout: float = 15.0) -> tuple[bytes, str] | None:
    """服务端直取图床 Logo。

    页面内 fetch 会被 CORS 拦截（S0 实测），而该图是页面本来就加载的公开静态资源，
    带 Referer 的服务端 GET 与浏览器行为等价，风险增量可忽略。
    """
    value = str(url or "").strip()
    if value.startswith("//"):
        value = f"https:{value}"
    if not value.startswith("http"):
        return None
    # 黑名单：BOSS 默认公司图标（公司未上传 Logo 时的占位图）与横幅 banner
    # （部分详情页 .sider-company 内混入的工作环境横幅）。转存它们只会把
    # "灰色占位图/宽横幅"当成 Logo 存进库（2026-10-06 用户反馈 + 存量清洗）。
    if "/beijin/mcs/banner/" in value or "894ce6fa7e58d64d57e7f22d2f3a9d18afa7fcceaa24b8ea28f56f1bb14732c0" in value:
        return None
    try:
        response = httpx.get(
            value,
            headers=_LOGO_FETCH_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or not response.content or len(response.content) > LOGO_MAX_BYTES:
        return None
    ext = _sniff_image_ext(response.content)
    if not ext:
        return None
    return response.content, ext


def save_logo_file(data_dir: Path | str, company: str, content: bytes, ext: str) -> str:
    """Logo 落盘（每公司一份，幂等）：返回相对 data/ 的路径，失败返回空串。

    落盘前复验魔数与扩展名一致（宁可不落盘，不留坏文件）。
    """
    if not content or not ext or _sniff_image_ext(content) != ext:
        return ""
    try:
        logo_dir = Path(data_dir) / "assets" / "logos"
        logo_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{logo_file_key(company)}.{ext}"
        target = logo_dir / filename
        if not target.exists():
            target.write_bytes(content)
        return f"assets/logos/{filename}"
    except OSError:
        return ""


def parse_json_payload(raw: Any) -> dict[str, Any]:
    """浏览器 evaluate 返回的 JSON 字符串 → dict（失败返回空 dict）。"""
    import json

    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}
