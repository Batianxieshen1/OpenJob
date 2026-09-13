"""Confirmation UI - Rich terminal display for job review."""

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

from openjob.db import get_db, get_jobs_pending_confirmation, transition_job_status, add_history

console = Console()


def _save_user_edited_greeting(db, job: dict, new_greeting: str, config: dict) -> None:
    """用户手改招呼语：重新走完整事实校验，防编辑绕过（WP-S1）。

    校验通过 → verified（来源标记 user_confirmed_fact）；
    校验失败 → 按未验证保存并显示具体缺口，发送边界会拦截，让用户补证据。
    """
    import json as _json
    from datetime import datetime

    from openjob.ai.fact_policy import rebuild_trusted_baseline, validate_generated_text
    from openjob.db import update_job_greeting, update_job_greeting_facts

    job_id = str(job["id"])
    raw_source = str(job.get("greeting_source_json") or "").strip()
    try:
        source = _json.loads(raw_source) if raw_source else {}
    except _json.JSONDecodeError:
        source = {}
    if not isinstance(source, dict) or not (
        source.get("base_resume_id")
        or source.get("base_resume_name")
        or source.get("candidate_material_ids")
    ):
        update_job_greeting(
            db, job_id, new_greeting,
            fact_status="unverified",
            fact_error="用户编辑的招呼语缺少事实溯源，已按未验证保存",
        )
        console.print("[yellow]  该岗位招呼语没有事实溯源记录，手改文本已按未验证保存（发送前会被拦截）[/yellow]")
        return
    source = {
        **source,
        "source_type": "user_confirmed_fact",
        "edited_by": "cli_confirm",
        "edited_at": datetime.now().isoformat(timespec="seconds"),
    }
    trusted, missing = rebuild_trusted_baseline(db, source, config)
    issues = [f"事实来源已不存在：{item}" for item in missing]
    if not missing:
        issues = validate_generated_text(new_greeting, trusted)
    update_job_greeting(db, job_id, new_greeting)
    if issues:
        update_job_greeting_facts(
            db, job_id,
            fact_status="unverified",
            source_json=_json.dumps(source, ensure_ascii=False),
            fact_error="；".join(issues[:5]),
        )
        console.print(f"[red]  ⚠ 编辑内容包含真实底稿/素材库之外的事实，已按未验证保存：{'；'.join(issues[:3])}[/red]")
        console.print("[yellow]  请把这些经历补充进素材库后重新生成，发送边界会拦截未验证文本[/yellow]")
    else:
        update_job_greeting_facts(
            db, job_id,
            fact_status="verified",
            source_json=_json.dumps(source, ensure_ascii=False),
            fact_error=None,
        )
        add_history(db, job_id, "greeting_edited", "用户编辑招呼语并通过事实校验")


def show_confirmation(config: dict) -> bool:
    """Display jobs for confirmation. Returns True if any jobs were approved."""
    db = get_db()
    jobs = get_jobs_pending_confirmation(db)

    if not jobs:
        console.print("[yellow]没有待确认的岗位[/yellow]")
        db.close()
        return False

    # Display summary table
    console.print(f"\n[bold cyan]═══ 投递清单 ({len(jobs)} 个岗位) ═══[/bold cyan]\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=3)
    table.add_column("公司", width=16)
    table.add_column("职位", width=24)
    table.add_column("薪资", width=10)
    table.add_column("匹配分", width=6, justify="center")
    table.add_column("评分理由", width=30)

    for i, job in enumerate(jobs, 1):
        reason_preview = (job.get("score_reason", "") or "")[:28]
        if len(job.get("score_reason", "") or "") > 28:
            reason_preview += "..."

        score_style = "green" if job["score"] >= 80 else "yellow" if job["score"] >= 60 else "red"

        table.add_row(
            str(i),
            (job["company"] or "")[:14],
            (job["title"] or "")[:22],
            job.get("salary", "") or "面议",
            f"[{score_style}]{job['score']}[/{score_style}]",
            reason_preview
        )

    console.print(table)
    console.print()

    # Confirmation options
    choice = Prompt.ask(
        "[bold]操作[/bold]",
        choices=["a", "s", "q"],
        default="s"
    )

    if choice == "q":
        console.print("[yellow]已取消[/yellow]")
        db.close()
        return False

    if choice == "a":
        # Approve all
        for job in jobs:
            transition_job_status(db, job["id"], "approved")
            add_history(db, job["id"], "approved", "批量确认")
        console.print(f"[green]✓ 已确认 {len(jobs)} 个岗位[/green]")
        db.close()
        return True

    # Individual selection mode
    approved_count = 0
    for i, job in enumerate(jobs, 1):
        console.print(f"\n[bold]#{i}[/bold] {job['company']} - {job['title']} ({job.get('salary', '面议')})")
        console.print(f"  匹配分: {job['score']} | {job.get('score_reason', '')}")
        console.print(f"  招呼语: {job.get('greeting', '')}")

        action = Prompt.ask("  ", choices=["y", "n", "e", "q"], default="y")

        if action == "q":
            break
        elif action == "y":
            transition_job_status(db, job["id"], "approved")
            add_history(db, job["id"], "approved", "逐个确认")
            approved_count += 1
        elif action == "e":
            # Edit greeting
            new_greeting = Prompt.ask("  新招呼语")
            if new_greeting:
                _save_user_edited_greeting(db, job, new_greeting, config)
            transition_job_status(db, job["id"], "approved")
            add_history(db, job["id"], "approved", "编辑后确认")
            approved_count += 1
        else:
            transition_job_status(db, job["id"], "skipped")
            add_history(db, job["id"], "skipped", "用户跳过")

    console.print(f"\n[green]✓ 已确认 {approved_count} 个岗位[/green]")
    db.close()
    return approved_count > 0
