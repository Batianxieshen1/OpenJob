"""AI Resume - 定制简历域的共享校验器、PDF 渲染与生成入口。

生成主体在 resume_engine（四段流水）；本模块保留：
- 防编造/占位符/事实完整性校验器（engine 复用）
- PDF 渲染（CDP 优先，xhtml2pdf 降级）
- generate_tailored_resume 委托入口（兼容 CLI/监听/工作台旧调用点）
"""

import re
from collections import Counter
from pathlib import Path

from rich.console import Console

from openjob.browser import close_tab, new_tab, print_pdf
from openjob.db import get_db

console = Console()

RESUME_ARTIFACT_PHRASES = [
    "以下内容基于",
    "基于原始简历",
    "根据原始简历",
    "根据岗位JD",
    "岗位匹配亮点",
    "匹配该岗位",
    "结合岗位要求",
    "补充说明",
    "原始简历事实",
    "不虚构",
    "未虚构",
    "本次优化",
    "调整后的简历",
    "定制简历",
    "以下为优化后的",
    "针对该岗位",
    "针对本岗位",
    "岗位中的",
    "高度相关",
    "字节岗位",
    "可迁移到",
    "岗位要求",
    "高度匹配",
    "高度贴合",
    "高度适配",
    "JD逐条对照",
    "岗位JD覆盖",
    "逐条对照",
    "覆盖情况",
    "匹配说明",
    "无法覆盖",
]

PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{[^{}\n]{1,100}\}\}"),
    re.compile(r"\$\{[^{}\n]{1,100}\}"),
    re.compile(r"\[[^\]\n]{0,80}(?:待填写|待补充|请填写|占位符|placeholder|todo|tbd|xxx)[^\]\n]{0,80}\]", re.I),
    re.compile(r"<[^<>\n]{0,80}(?:待填写|待补充|请填写|占位符|placeholder|todo|tbd|xxx)[^<>\n]{0,80}>", re.I),
    re.compile(r"\b(?:TODO|TBD|XXX)\b", re.I),
    re.compile(r"(?:待填写|待补充|请填写|占位符)(?:[：:][^\s，。；;\n]{0,40})?"),
]

FACT_TOKEN_PATTERNS = [
    re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"),
    re.compile(r"https?://[^\s)>）】]+", re.I),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)(?:19|20)\d{2}(?:[./年-](?:0?[1-9]|1[0-2]))?(?:[./月-](?:0?[1-9]|[12]\d|3[01]))?(?:日)?(?!\d)"),
    re.compile(
        r"(?<![\w.])\d+(?:\.\d+)?(?:\s*[-~至到]\s*\d+(?:\.\d+)?)?\s*"
        r"(?:%|％|年|个月|月|天|人|次|篇|万|亿|元|K|k|W|w|倍|\+)(?!\w)"
    ),
]

_resume_failure_reasons: dict[str, str] = {}


def _find_resume_artifacts(markdown_text: str) -> list[str]:
    """Find process-disclosure phrases that should not appear in a resume."""
    return [phrase for phrase in RESUME_ARTIFACT_PHRASES if phrase in markdown_text]


def _normalize_validation_token(token: str) -> str:
    return re.sub(r"\s+", "", token).lower()


def _extract_validation_tokens(text: str, patterns: list[re.Pattern]) -> list[str]:
    tokens: list[str] = []
    for pattern in patterns:
        tokens.extend(match.group(0).strip() for match in pattern.finditer(text or ""))
    return tokens


def _extract_placeholder_tokens(text: str) -> list[str]:
    tokens = _extract_validation_tokens(text, PLACEHOLDER_PATTERNS)
    return [
        token
        for token in tokens
        if not any(token != other and token in other for other in tokens)
    ]


def _find_new_placeholders(markdown_text: str, base_resume: str) -> list[str]:
    """Return placeholders introduced or rewritten by the model.

    Placeholders already present verbatim in the source resume are an accepted
    baseline. Rewording one creates a new token and is therefore blocked.
    """
    base_counts = Counter(
        _normalize_validation_token(token)
        for token in _extract_placeholder_tokens(base_resume)
    )
    seen_counts: Counter[str] = Counter()
    introduced: list[str] = []
    for token in _extract_placeholder_tokens(markdown_text):
        normalized = _normalize_validation_token(token)
        seen_counts[normalized] += 1
        if seen_counts[normalized] > base_counts[normalized] and token not in introduced:
            introduced.append(token)
    return introduced


def _find_new_fact_tokens(markdown_text: str, base_resume: str) -> list[str]:
    """Return fact-sensitive values that do not exist in the source resume."""
    base_tokens = {
        _normalize_validation_token(token)
        for token in _extract_validation_tokens(base_resume, FACT_TOKEN_PATTERNS)
    }
    introduced: list[str] = []
    for token in _extract_validation_tokens(markdown_text, FACT_TOKEN_PATTERNS):
        normalized = _normalize_validation_token(token)
        if normalized not in base_tokens and token not in introduced:
            introduced.append(token)
    return introduced


def _markdown_section(markdown_text: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(heading)}\s*$\n?(.*?)(?=^\s*##\s+|\Z)"
    )
    match = pattern.search(markdown_text or "")
    return match.group(1) if match else ""


def _find_missing_core_facts(markdown_text: str, base_resume: str) -> list[str]:
    """Keep fact-like contact/basic-info values from the source resume."""
    source_basic_info = _markdown_section(base_resume, "## 基本信息")
    if not source_basic_info:
        return []
    source_tokens = _extract_validation_tokens(source_basic_info, FACT_TOKEN_PATTERNS)
    generated_keys = {
        _normalize_validation_token(token)
        for token in _extract_validation_tokens(markdown_text, FACT_TOKEN_PATTERNS)
    }
    return [
        token
        for token in source_tokens
        if _normalize_validation_token(token) not in generated_keys
    ]


def _find_blocking_integrity_issues(
    markdown_text: str,
    base_resume: str,
) -> list[str]:
    """Return issues that must prevent a resume from being marked ready."""
    issues: list[str] = []

    missing_core_facts = _find_missing_core_facts(markdown_text, base_resume)
    if missing_core_facts:
        issues.append(
            "事实完整性校验失败：缺少基础简历中的关键信息："
            + ", ".join(missing_core_facts[:8])
        )

    new_facts = _find_new_fact_tokens(markdown_text, base_resume)
    if new_facts:
        issues.append(
            "事实完整性校验失败：模型新增了原始简历中不存在的数据："
            + ", ".join(new_facts[:8])
        )

    new_placeholders = _find_new_placeholders(markdown_text, base_resume)
    if new_placeholders:
        issues.append(
            "占位符校验失败：模型新增或改写了占位符："
            + ", ".join(new_placeholders[:8])
        )
    return issues


def _set_resume_failure_reason(job_id: str, reason: str) -> None:
    _resume_failure_reasons[str(job_id)] = str(reason).strip() or "未知原因"


def get_last_resume_failure_reason(job_id: str) -> str:
    """Return the latest in-process failure reason for monitor history."""
    return _resume_failure_reasons.get(str(job_id), "")


def _pdf_page_count(pdf_path: Path) -> int | None:
    """Return PDF page count when it can be determined."""
    if not pdf_path.exists():
        return None

    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        pass

    try:
        data = pdf_path.read_bytes()
    except OSError:
        return None

    count = len(re.findall(rb"/Type\s*/Page\b", data))
    return count or None


def _render_pdf(markdown_text: str, output_path: Path) -> bool:
    """Render markdown to PDF via Chrome CDP (Page.printToPDF).

    Falls back to xhtml2pdf if CDP is unavailable.
    """
    import markdown2

    # Convert markdown to HTML
    html_body = markdown2.markdown(markdown_text, extras=["tables", "fenced-code-blocks"])

    # Wrap with CJK-friendly CSS
    full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{
        font-family: "Microsoft YaHei", "SimSun", "WenQuanYi Micro Hei", sans-serif;
        font-size: 11pt;
        line-height: 1.6;
        margin: 40px;
        color: #333;
    }}
    h1 {{ font-size: 18pt; color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 5px; }}
    h2 {{ font-size: 14pt; color: #2c3e50; margin-top: 20px; }}
    h3 {{ font-size: 12pt; color: #34495e; }}
    ul {{ padding-left: 20px; }}
    li {{ margin-bottom: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
    th {{ background: #f5f5f5; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    # Strategy 1: Use Chrome CDP to print PDF (preferred, no extra deps)
    if _render_pdf_via_cdp(full_html, output_path):
        return True

    # Strategy 2: Fallback to xhtml2pdf (requires cairo on Windows)
    try:
        from xhtml2pdf import pisa
    except (ImportError, OSError):
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        status = pisa.CreatePDF(full_html, dest=f, encoding="utf-8")
    return not status.err


def _render_pdf_via_cdp(html_content: str, output_path: Path) -> bool:
    """Use Browser Runtime Page.printToPDF via the Python browser facade."""
    import tempfile
    import time

    temp_html = Path(tempfile.gettempdir()) / "openjob_resume.html"
    temp_html.write_text(html_content, encoding="utf-8")
    file_url = f"file:///{temp_html.as_posix()}"

    target_id = None
    try:
        target_id = new_tab(file_url, background=True)
        if not target_id:
            return False

        time.sleep(2)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if print_pdf(target_id, output_path):
            return output_path.exists() and output_path.stat().st_size > 0
        return False
    except Exception:
        return False
    finally:
        if target_id:
            close_tab(target_id)
        temp_html.unlink(missing_ok=True)


def generate_tailored_resume(job_id: str, config: dict) -> Path | None:
    """Generate a tailored resume for a specific job.

    Thin delegate to the resume_engine four-stage pipeline (parse → match →
    rewrite → assemble). Keeps the legacy contract: returns the generated file
    path (PDF preferred, Markdown fallback), None on failure, and records the
    failure reason for get_last_resume_failure_reason(). The PDF is auto-exported
    because callers (monitor card flow) hand the file to the user for manual
    sending; review/diff data stays available in the resumes table.
    """
    from openjob.ai.resume_engine import export_resume_pdf, generate_resume

    _resume_failure_reasons.pop(str(job_id), None)
    result = generate_resume(job_id, config)
    if not result.ok:
        if result.reason:
            _set_resume_failure_reason(job_id, result.reason)
        return None

    resume_id = result.resume_id
    if not resume_id:
        return None

    db = get_db()
    try:
        exported = export_resume_pdf(resume_id, config)
        if exported is None:
            record = db.execute(
                "SELECT content_md, pdf_path FROM resumes WHERE id = ?", (resume_id,)
            ).fetchone()
            if record and record["pdf_path"]:
                exported = Path(record["pdf_path"])
            elif record and record["content_md"]:
                output_dir = Path(config.get("profile", {}).get("resume_output_dir", "./data/resumes"))
                output_dir.mkdir(parents=True, exist_ok=True)
                exported = output_dir / f"resume_{job_id}.md"
                exported.write_text(record["content_md"], encoding="utf-8")
        if exported is None:
            _set_resume_failure_reason(job_id, "简历已生成但导出失败")
            return None
        db.execute(
            "UPDATE jobs SET resume_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (str(exported), job_id)
        )
        db.commit()
        _resume_failure_reasons.pop(str(job_id), None)
        return exported
    finally:
        db.close()


def generate_all_resumes(config: dict) -> int:
    """Generate tailored resumes for all scored jobs. Returns count generated."""
    db = get_db()
    threshold = config.get("scoring", {}).get("threshold", 60)

    # Get scored jobs without resume
    rows = db.execute(
        "SELECT id FROM jobs WHERE deleted_at IS NULL AND status IN ('scored', 'ready', 'approved') AND score >= ? AND resume_path IS NULL",
        (threshold,)
    ).fetchall()

    if not rows:
        console.print("[yellow]没有需要生成简历的岗位[/yellow]")
        db.close()
        return 0

    db.close()
    count = 0
    for row in rows:
        result = generate_tailored_resume(row["id"], config)
        if result:
            count += 1

    console.print(f"\n[green]✓ 共生成 {count} 份定制简历[/green]")
    return count
