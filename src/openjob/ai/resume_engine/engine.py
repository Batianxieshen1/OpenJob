"""简历优化引擎编排：触发守卫 → 四段流水 → 版本落库 → 导出。

设计原则（计划书 §5）：
- 改写不编造：只重排重点、改表述、对齐 JD 关键词；空白/模板简历拒绝生成；
- 幂等：同一岗位已有 review 版直接返回，重新生成需显式 confirm；
- 生成完成停在 review，导出 PDF 与发送永远人工。
"""

import json
import uuid
from pathlib import Path

from rich.console import Console

from openjob.ai.credentials import AIRequestError
from openjob.ai.resume import _pdf_page_count, _render_pdf
from openjob.cancellation import OperationCancelled, run_cancellable, stop_requested
from openjob.config import DATA_DIR, load_config
from openjob.db import (
    latest_resume_version,
    next_resume_version,
    resume_version_row,
    set_job_resume_pointer,
    update_resume_version,
)
from openjob.ai.resume_engine.jd_parser import parse_jd
from openjob.ai.resume_engine.matcher import analyze_match
from openjob.ai.resume_engine.optimizer import (
    apply_changes,
    rewrite_sections,
    validate_assembled,
)

console = Console()

# 模板/占位简历特征：出现即拒绝生成（V1 铁律）
TEMPLATE_RESUME_MARKERS = ("张三", "李四", "xxx@xx.com", "某某公司", "XX公司", "示例公司")
MIN_RESUME_CHARS = 100

LAST_RESUME_ERROR: dict[str, str] = {}

# Web 服务运行时注入（server.set_base_dir），保证引擎与面板读写同一个库；
# CLI/监听场景保持默认 ./data/openjob.db。
RUNTIME_DB_PATH: Path | None = None
RUNTIME_DATA_DIR: Path | None = None


def _db():
    from openjob.db import get_db as _get_db

    return _get_db(RUNTIME_DB_PATH) if RUNTIME_DB_PATH else _get_db()


class ResumeGenerationError(RuntimeError):
    """带用户可读原因的生成失败。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(job_id: str, reason: str) -> "ResumeResult":
    LAST_RESUME_ERROR[job_id] = reason
    console.print(f"[red]定制简历生成失败：{reason}[/red]")
    return ResumeResult(ok=False, reason=reason)


def _validate_base_resume(resume_text: str) -> str | None:
    """基础简历体检，返回拒绝原因或 None。"""
    if not resume_text or not resume_text.strip():
        return "基础简历为空"
    compact = "".join(resume_text.split())
    if len(compact) < MIN_RESUME_CHARS:
        return f"基础简历内容过短（{len(compact)} 字符），疑似空白简历"
    for marker in TEMPLATE_RESUME_MARKERS:
        if marker in resume_text:
            return f"基础简历包含模板占位内容（“{marker}”），请先填写真实简历"
    return None


class ResumeResult:
    """generate_resume 的返回值。"""

    def __init__(self, *, ok: bool, resume_id: str | None = None, pdf_path: str | None = None,
                 content_md: str | None = None, reused: bool = False, reason: str = ""):
        self.ok = ok
        self.resume_id = resume_id
        self.pdf_path = pdf_path
        self.content_md = content_md
        self.reused = reused
        self.reason = reason


def generate_resume(
    job_id: str,
    config: dict | None = None,
    *,
    confirm_regenerate: bool = False,
) -> ResumeResult:
    """为岗位生成定制简历（四段流水），产物停 review 状态。

    幂等：已有 review/exported 版本时直接复用，除非 confirm_regenerate=True。
    """
    config = config or load_config()
    LAST_RESUME_ERROR.pop(job_id, None)
    db = _db()

    try:
        row = db.execute("SELECT * FROM jobs WHERE id = ? AND deleted_at IS NULL", (job_id,)).fetchone()
        if not row:
            return _fail(job_id, f"未找到岗位 ID：{job_id}")
        job = dict(row)

        # 幂等与重复付费护栏
        existing = latest_resume_version(db, job_id)
        if existing and existing["status"] in ("review", "exported", "sent") and not confirm_regenerate:
            return ResumeResult(
                ok=True,
                resume_id=existing["id"],
                pdf_path=existing["pdf_path"],
                content_md=existing["content_md"],
                reused=True,
            )

        # 底稿选择：多份底稿时按岗位自动挑最接近的一份（无底稿则回退 config 文件）
        jd_text = job.get("jd") or ""
        if not jd_text.strip():
            return _fail(job_id, "岗位缺少 JD 原文，无法定向优化")

        from openjob.ai.resume_engine.bases import select_base_for_job

        try:
            selection = select_base_for_job(db, f"{job.get('title') or ''}\n{jd_text}", config)
        except OSError as exc:
            return _fail(job_id, f"无法读取基础简历：{exc}")
        base_resume = selection.base["content_md"]
        base_resume_id = selection.base.get("id")
        console.print(f"[dim]底稿：{selection.reason}[/dim]")

        guard_reason = _validate_base_resume(base_resume)
        if guard_reason:
            return _fail(job_id, guard_reason)

        version = next_resume_version(db, job_id)
        resume_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO resumes (id, job_id, version, status, base_md) VALUES (?, ?, ?, 'parsing', ?)",
            (resume_id, job_id, version, base_resume),
        )
        set_job_resume_pointer(db, job_id, resume_id=resume_id, resume_status="parsing")
        update_resume_version(db, resume_id, base_resume_id=base_resume_id)
        set_job_resume_pointer(db, job_id, resume_id=resume_id, resume_status="parsing")

        try:
            # ① JD 解析
            jd = run_cancellable(lambda: parse_jd(jd_text, config), config)
            update_resume_version(db, resume_id, jd_analysis_json=json.dumps(jd.to_dict(), ensure_ascii=False))

            # ② 匹配分析
            match = run_cancellable(lambda: analyze_match(base_resume, jd, config), config)
            update_resume_version(
                db, resume_id, match_report_json=json.dumps(match.to_dict(), ensure_ascii=False)
            )

            # ②.5 素材库：启用时加载已校验索引并筛候选
            materials_enabled = bool((config.get("profile") or {}).get("resume_materials_enabled", True))
            candidates: list = []
            material_library_sha = None
            material_selection_json = None
            material_candidates_json = None
            if materials_enabled:
                from openjob.ai.resume_engine.materials import material_prompt, rank_materials
                from openjob.resume_materials import MaterialLibraryError, load_or_refresh_library

                data_dir = RUNTIME_DATA_DIR or Path("data")
                materials_path = (data_dir / "resume_materials.xlsx").resolve()
                index_path = (data_dir / "resume_materials.index.json").resolve()
                library = None
                try:
                    library = load_or_refresh_library(materials_path, index_path)
                except MaterialLibraryError as exc:
                    # 未上传素材库 = 用户尚未使用该功能：跳过素材走旧流程，不阻断生成；
                    # 文件存在但解析失败 = 用户更新出错：明确失败，不静默用旧索引。
                    if "未找到" not in str(exc):
                        update_resume_version(db, resume_id, status="failed", error=str(exc))
                        set_job_resume_pointer(db, job_id, resume_id=resume_id, resume_status="failed")
                        return _fail(job_id, str(exc))
                    console.print(f"[dim]素材库未上传，跳过素材匹配（{exc}）[/dim]")
                if library is not None:
                    material_library_sha = library.sha256
                    candidates = rank_materials(library.items, jd, limit=8)
                minimal = [
                    {
                        "id": c.material.get("id"),
                        "type": c.material.get("type"),
                        "title": c.material.get("title"),
                        "organization": c.material.get("organization"),
                        "role": c.material.get("role"),
                        "dates": "-".join(x for x in (c.material.get("start_date"), c.material.get("end_date")) if x),
                        "description": c.material.get("description"),
                        "achievements": c.material.get("achievements"),
                        "skills": c.material.get("skills"),
                        "keywords": c.material.get("keywords"),
                        "target_directions": c.material.get("target_directions"),
                        "priority": c.material.get("priority"),
                        "score": c.score,
                        "reasons": c.reasons,
                    }
                    for c in candidates
                ]
                material_candidates_json = json.dumps(minimal, ensure_ascii=False)
                console.print(f"[dim]素材候选：{len(candidates)} 条[/dim]")

            # ③ 分段改写
            rewrite = run_cancellable(
                lambda: rewrite_sections(base_resume, jd, match, config, candidates=candidates),
                config,
            )
            if stop_requested(config):
                raise OperationCancelled("用户已请求停止")

            # ③.5 素材 ID 校验：只允许引用候选集；选中素材快照用于审计与可信事实
            selected_materials: list[dict] = []
            if candidates:
                from openjob.ai.resume_engine.materials import (
                    MAX_MATERIAL_BACKED_CHANGES,
                    validate_material_ids,
                )

                for change in rewrite.changes:
                    validated = validate_material_ids(change.material_ids, candidates)
                    change.material_ids = validated
                material_backed = [c for c in rewrite.changes if c.material_ids]
                if len(material_backed) > MAX_MATERIAL_BACKED_CHANGES:
                    reason = f"本次改写最多只能让 {MAX_MATERIAL_BACKED_CHANGES} 个简历变量行使用新增素材"
                    update_resume_version(db, resume_id, status="failed", error=reason)
                    set_job_resume_pointer(db, job_id, resume_id=resume_id, resume_status="failed")
                    return _fail(job_id, reason)
                used_ids = {mid for c in rewrite.changes for mid in c.material_ids}
                selected_materials = [
                    {k: v for k, v in c.material.items() if k not in ("source", "notes")}
                    for c in candidates
                    if c.material.get("id") in used_ids
                ]
                if selected_materials:
                    material_selection_json = json.dumps(selected_materials, ensure_ascii=False)

            # ④ 汇总校验
            assembled = apply_changes(base_resume, rewrite.changes)
            trusted_material_text = " ".join(
                " ".join(str(v) for v in (m.get("title"), m.get("organization"), m.get("role"),
                                          m.get("start_date"), m.get("end_date"),
                                          m.get("description"), m.get("achievements"),
                                          " ".join(m.get("skills") or [])))
                for m in selected_materials
            )
            # 第一遍校验：只查编造/占位符/删减（零容忍，先于长度兜底防止被回退掩盖）
            blocking, warnings = validate_assembled(
                base_resume, assembled, include_layout=False, trusted_material_text=trusted_material_text
            )
            if not blocking:
                from openjob.ai.resume_engine.optimizer import sanitize_line_lengths

                sanitized = sanitize_line_lengths(base_resume, assembled)
                if sanitized != assembled:
                    warnings.append("部分改写行超长，已回退原文保住一页排版")
                    assembled = sanitized
                blocking, warnings2 = validate_assembled(
                    base_resume, assembled, trusted_material_text=trusted_material_text
                )
                warnings.extend(warnings2)
            if blocking:
                update_resume_version(db, resume_id, status="failed", error="；".join(blocking))
                set_job_resume_pointer(db, job_id, resume_id=resume_id, resume_status="failed")
                return _fail(job_id, "；".join(blocking))

            risk_flags = sorted({c.risk for c in rewrite.changes if c.risk} | set(warnings))
            diff = [
                {
                    "section": c.section,
                    "before": c.before,
                    "after": c.after,
                    "reason": c.reason,
                    "risk": c.risk,
                    "adopted": True,
                    "material_ids": c.material_ids,
                }
                for c in rewrite.changes
            ]
            update_resume_version(
                db,
                resume_id,
                status="review",
                diff_json=json.dumps(diff, ensure_ascii=False),
                content_md=assembled,
                risk_flags_json=json.dumps(risk_flags, ensure_ascii=False),
                error=None,
                material_library_sha256=material_library_sha,
                material_selection_json=material_selection_json,
                material_candidates_json=material_candidates_json,
            )
            set_job_resume_pointer(db, job_id, resume_id=resume_id, resume_status="review")
            console.print(
                f"[green]✓ 定制简历 v{version} 已生成（{len(diff)} 处修改，"
                f"{len(risk_flags)} 项待核实），等待人工审阅[/green]"
            )
            return ResumeResult(ok=True, resume_id=resume_id, content_md=assembled)
        except OperationCancelled:
            update_resume_version(db, resume_id, status="failed", error="用户取消")
            raise
        except (AIRequestError, ValueError, RuntimeError) as exc:
            reason = getattr(exc, "user_message", None) or str(exc)
            update_resume_version(db, resume_id, status="failed", error=reason)
            set_job_resume_pointer(db, job_id, resume_id=resume_id, resume_status="failed")
            return _fail(job_id, reason)
    finally:
        db.close()


def reassemble_from_diff(resume_md_base: str, diff: list[dict]) -> str:
    """按人工采纳状态重新合成简历（PATCH 后调用）。

    diff 项：{before, after, adopted, ...}。采纳→应用 after；未采纳→保持原文。
    用户手改的 after 直接生效。
    """
    from openjob.ai.resume_engine.optimizer import SectionChange

    changes = [
        SectionChange(
            section=item.get("section", ""),
            before=item.get("before", ""),
            after=item.get("after", ""),
            reason=item.get("reason", ""),
            risk=item.get("risk", ""),
            material_ids=list(item.get("material_ids") or []),
        )
        for item in diff
        if item.get("adopted", True)
    ]
    return apply_changes(resume_md_base, changes)


def export_resume_pdf(resume_id: str, config: dict | None = None, *, fmt: str = "both") -> Path | None:
    """导出定制简历（status → exported）。fmt: pdf / docx / md / both。返回主文件路径或 None。"""
    fmt = fmt if fmt in {"pdf", "docx", "md", "both"} else "both"
    config = config or load_config()
    db = _db()
    try:
        record = resume_version_row(db, resume_id)
        if not record:
            return None
        content_md = record["content_md"]
        if not content_md:
            return None
        job = db.execute(
            "SELECT company, title FROM jobs WHERE id = ?", (record["job_id"],)
        ).fetchone()
        job_label = f"{job['company']}_{job['title']}" if job else "resume"
        safe_label = "".join(c for c in job_label if c not in r'\/:*?"<>|')[:40]
        output_dir = Path(config.get("profile", {}).get("resume_output_dir", "./data/resumes"))
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = output_dir / f"{safe_label}_v{record['version']}.md"
        md_path.write_text(content_md, encoding="utf-8")
        pdf_path = output_dir / f"{safe_label}_v{record['version']}.pdf"
        # 排版优先级：底稿的原始 Word 模板（逐字替换、排版复刻）> 通用排版
        template_path = None
        if record.get("base_resume_id"):
            from openjob.db import base_resume_row

            base = base_resume_row(db, record["base_resume_id"])
            candidate = str((base or {}).get("template_path") or "")
            if candidate and Path(candidate).exists():
                template_path = Path(candidate)

        docx_out = None
        if template_path:
            try:
                from openjob.ai.resume_engine.template_docx import render_tailored_docx

                docx_out = output_dir / f"{safe_label}_v{record['version']}.docx"
                render_tailored_docx(template_path, content_md, docx_out)
                console.print("[green]✓ 已按你的原始模板排版生成 Word（只替换文字）[/green]")
            except Exception as exc:
                console.print(f"[yellow]模板复刻失败，回退通用排版: {exc}[/yellow]")
                docx_out = None

        if docx_out is None:
            if fmt in {"both", "docx"}:
                try:
                    from openjob.ai.resume_engine.exporter import md_to_docx

                    docx_out = md_to_docx(content_md, output_dir / f"{safe_label}_v{record['version']}.docx")
                except Exception as exc:
                    console.print(f"[yellow]Word 导出失败: {exc}[/yellow]")
            rendered = fmt in {"both", "pdf"} and _render_pdf(content_md, pdf_path)
            if fmt == "md":
                from openjob.ai.resume_engine.exporter import save_markdown

                md_out = save_markdown(content_md, output_dir / f"{safe_label}_v{record['version']}.md")
                final_path = md_out

        if docx_out is not None:
            final_path = docx_out
        update_resume_version(
            db,
            resume_id,
            status="exported",
            pdf_path=str(final_path) if str(final_path).endswith(".pdf") or str(final_path).endswith(".md") else None,
            docx_path=str(docx_out) if docx_out else None,
        )
        set_job_resume_pointer(db, record["job_id"], resume_id=resume_id, resume_status="exported")
        console.print(f"[green]✓ 简历已导出: {final_path}[/green]")
        return final_path
    finally:
        db.close()


def mark_resume_sent(resume_id: str, job_id: str, detail: str = "") -> bool:
    """人工已发标记（status → sent），写 history 台账。"""
    from openjob.db import add_history

    db = _db()
    try:
        record = resume_version_row(db, resume_id)
        if not record or record["job_id"] != job_id:
            return False
        update_resume_version(db, resume_id, status="sent")
        set_job_resume_pointer(db, job_id, resume_id=resume_id, resume_status="sent")
        add_history(db, job_id, "resume_sent", detail or f"定制简历 v{record['version']} 已人工发送")
        return True
    finally:
        db.close()
