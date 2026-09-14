"""B3 求职周报：上周总结 + 环比 + 待办，dry-run 预览 → 人工确认 → 写入归档。

铁律：未确认零写入。产物默认落 data/weekly_reports/，配置
profile.weekly_report_dir 可指向 Obsidian 库目录。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _week_window(now: datetime) -> tuple[str, str, str]:
    """返回 (报告期开始, 报告期结束, 环比期开始)。

    报告期 = 上周一 ~ 本周一（周一生成上周总结）；环比期 = 上上周一 ~ 上周一。
    """
    this_monday = (now - timedelta(days=now.weekday())).date()
    last_monday = this_monday - timedelta(days=7)
    prev_monday = last_monday - timedelta(days=7)
    return last_monday.isoformat(), this_monday.isoformat(), prev_monday.isoformat()


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.Error:
        return 0


def collect_weekly_stats(db_path: Path, now: datetime | None = None) -> dict[str, Any]:
    """汇总本周/上周核心漏斗数字与待办（只读）。"""
    now = now or datetime.now()
    report_start, report_end, prev_start = _week_window(now)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:

        def week(start: str, end: str) -> dict[str, int]:
            return {
                "collected": _count(conn, "SELECT COUNT(*) FROM jobs WHERE created_at >= ? AND created_at < ?", (start, end)),
                "passed": _count(conn, "SELECT COUNT(*) FROM history WHERE action='approved' AND created_at >= ? AND created_at < ?", (start, end)),
                "sent": _count(conn, "SELECT COUNT(*) FROM history WHERE action='sent' AND created_at >= ? AND created_at < ?", (start, end)),
                "replied": _count(conn, "SELECT COUNT(*) FROM jobs WHERE replied_at >= ? AND replied_at < ?", (start, end)),
                "errors": _count(conn, "SELECT COUNT(*) FROM history WHERE action IN ('error','score_failed') AND created_at >= ? AND created_at < ?", (start, end)),
            }

        stats = {
            "report_start": report_start,
            "report_end": report_end,
            "this_week": week(report_start, report_end),
            "last_week": week(prev_start, report_start),
        }
        backlog = {
            "ready": _count(conn, "SELECT COUNT(*) FROM jobs WHERE status='ready' AND deleted_at IS NULL"),
            "approved": _count(conn, "SELECT COUNT(*) FROM jobs WHERE status='approved' AND deleted_at IS NULL"),
            "approved_overdue_7d": _count(conn, "SELECT COUNT(*) FROM jobs WHERE status='approved' AND deleted_at IS NULL AND updated_at < datetime('now','-7 day')"),
            "stale": _count(conn, "SELECT COUNT(*) FROM jobs WHERE status='stale' AND deleted_at IS NULL"),
            "pending_replies": _count(conn, "SELECT COUNT(*) FROM conversations WHERE handle_status='pending'"),
        }
        top_companies = [
            dict(row)
            for row in conn.execute(
                "SELECT company, COUNT(*) AS job_count, MAX(score) AS best_score FROM jobs "
                "WHERE created_at >= ? AND created_at < ? AND deleted_at IS NULL "
                "GROUP BY company ORDER BY job_count DESC LIMIT 5",
                (report_start, report_end),
            ).fetchall()
        ]
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "week_label": f"{report_start} ~ {report_end}",
            "stats": stats,
            "backlog": backlog,
            "top_companies": top_companies,
        }
    finally:
        conn.close()


def _delta(this: int, last: int) -> str:
    diff = this - last
    if diff > 0:
        return f"{this}（↑{diff}）"
    if diff < 0:
        return f"{this}（↓{-diff}）"
    return f"{this}（持平）"


def render_weekly_markdown(report: dict[str, Any], config: dict) -> str:
    """渲染 Markdown 周报。数字全部来自 collect_weekly_stats，可直接对账。"""
    stats = report["stats"]
    this = stats["this_week"]
    last = stats["last_week"]
    backlog = report["backlog"]
    sent = this["sent"]
    replied = this["replied"]
    reply_rate = f"{replied / sent * 100:.0f}%" if sent else "—（本周无发送）"
    lines = [
        f"# OpenJob 求职周报（{report['week_label']}）",
        "",
        f"> 生成于 {report['generated_at']}｜数字来自本地数据库，可直接对账",
        "",
        "## 本周数字（环比上周）",
        "",
        f"- 新增岗位：**{_delta(this['collected'], last['collected'])}**",
        f"- 过线确认：**{_delta(this['passed'], last['passed'])}**",
        f"- 发送招呼语：**{_delta(sent, last['sent'])}**",
        f"- HR 回复：**{_delta(replied, last['replied'])}**（回复率 {reply_rate}）",
        f"- 失败记录：{this['errors']}（评分/发送，详见监测页错误归因）",
        "",
        "## 队列健康",
        "",
        f"- 待确认 {backlog['ready']}｜已确认待发送 {backlog['approved']}（超 7 天 {backlog['approved_overdue_7d']}）｜超期退出 {backlog['stale']}",
        f"- 回复工作台待处理会话：{backlog['pending_replies']}",
        "",
    ]
    if report["top_companies"]:
        lines.append("## 本周岗位最多的公司")
        lines.append("")
        for item in report["top_companies"]:
            lines.append(f"- {item['company']}：{item['job_count']} 个岗位，最高分 {item['best_score']}")
        lines.append("")
    lines.append("## 待办（每周一 20 分钟）")
    lines.append("")
    if backlog["approved"] > 0:
        lines.append(f"- [ ] 处理已确认待发送的 {backlog['approved']} 个岗位（超 7 天的用「过期退出」清理）")
    if backlog["pending_replies"] > 0:
        lines.append(f"- [ ] 回复工作台 {backlog['pending_replies']} 条会话逐条处理")
    lines.append("- [ ] 清积压：岗位池筛 error 状态，重试或归档")
    lines.append("- [ ] 定 1-2 个下周改进动作（关键词/黑名单/评分阈值）")
    lines.append("")
    return "\n".join(lines)


def write_weekly_report(markdown: str, config: dict, base_dir: Path, now: datetime | None = None) -> Path:
    """把已确认的周报写入归档目录（只有本函数会写盘）。"""
    now = now or datetime.now()
    raw_dir = str((config.get("profile") or {}).get("weekly_report_dir") or "")
    target_dir = Path(raw_dir) if raw_dir else base_dir / "data" / "weekly_reports"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")
    path = target_dir / f"openjob-weekly-{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
