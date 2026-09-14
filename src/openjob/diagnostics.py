"""openjob doctor：运行环境自诊断与脱敏诊断包（WP-C3）。

七项检查每项 ✅/⚠️/❌ + 一行修复建议；诊断包默认脱敏：
不含 API Key/Cookie/简历正文/联系方式/数据库内容——只含版本、结构、状态摘要。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

PASS, WARN, FAIL = "pass", "warn", "fail"


def _result(item: str, status: str, summary: str, fix: str = "") -> dict[str, str]:
    return {"item": item, "status": status, "summary": summary, "fix": fix}


def check_chrome() -> dict[str, str]:
    try:
        from urllib.request import urlopen

        with urlopen("http://127.0.0.1:9222/json/version", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        browser = str(payload.get("Browser") or "Chrome")
        return _result("Chrome 调试连接", PASS, f"9222 端口在线（{browser}）")
    except Exception:
        return _result(
            "Chrome 调试连接", FAIL, "专用 Chrome 未连接（9222 端口无响应）",
            "用 scripts/windows/start_openjob.ps1 启动，或 chrome.exe 加 --remote-debugging-port=9222 重开",
        )


def check_ai(config: dict) -> dict[str, str]:
    from openjob.web.preflight import check_ai_connection

    checks = check_ai_connection(config, required=True)
    errors = [c for c in checks if c.get("status") == "error"]
    if errors:
        first = errors[0]
        return _result("AI 服务", FAIL, str(first.get("message")), str(first.get("action") or "在配置页重新连接 AI"))
    return _result("AI 服务", PASS, "配置完整（未验证真实调用以省额度；如需验证运行 openjob ai-status）")


def check_config(base_dir: Path) -> dict[str, str]:
    config_path = base_dir / "config.yaml"
    if not config_path.exists():
        return _result("配置文件", FAIL, "config.yaml 不存在", "openjob web 打开配置面板完成初始配置")
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return _result("配置文件", FAIL, f"config.yaml 解析失败：{type(exc).__name__}", "从 config.example.yaml 重建后重新配置")
    missing = [key for key in ("search", "ai", "profile") if not isinstance(data.get(key), dict)]
    if not (data.get("search") or {}).get("keywords"):
        missing.append("search.keywords")
    if not (data.get("profile") or {}).get("resume_path"):
        missing.append("profile.resume_path")
    if missing:
        return _result("配置完整性", WARN, "缺少：" + "、".join(missing), "在配置面板补齐关键词/简历路径")
    return _result("配置完整性", PASS, "search/ai/profile 关键字段齐备")


def check_disk(base_dir: Path) -> dict[str, str]:
    try:
        usage = shutil.disk_usage(base_dir)
    except OSError:
        return _result("磁盘空间", WARN, "无法读取磁盘用量", "")
    free_gb = usage.free / (1024 ** 3)
    if free_gb < 0.5:
        return _result("磁盘空间", FAIL, f"剩余 {free_gb:.1f}GB", "清理磁盘后重试（备份/日志/简历产物都写在这里）")
    if free_gb < 2:
        return _result("磁盘空间", WARN, f"剩余 {free_gb:.1f}GB 偏低", "建议清理到 2GB 以上")
    return _result("磁盘空间", PASS, f"剩余 {free_gb:.1f}GB")


def check_backup(base_dir: Path) -> dict[str, str]:
    backups = sorted((base_dir / "data" / "backups").glob("openjob-*.db"))
    if not backups:
        return _result("数据库备份", WARN, "还没有备份文件", "工作台每日自动备份；也可手动触发（保存配置时）")
    latest = backups[-1]
    age_days = (datetime.now() - datetime.fromtimestamp(latest.stat().st_mtime)).days
    if age_days > 3:
        return _result("数据库备份", WARN, f"最新备份已 {age_days} 天前", "打开一次工作台会当日自动备份；确认面板在运行")
    return _result("数据库备份", PASS, f"最新备份 {age_days} 天前（共 {len(backups)} 份）")


def check_integrity(base_dir: Path) -> dict[str, str]:
    db_path = base_dir / "data" / "openjob.db"
    if not db_path.exists():
        return _result("数据库完整性", WARN, "数据库尚未创建（首次运行前正常）", "")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _result("数据库完整性", FAIL, f"quick_check 异常：{exc}", "openjob restore <最近的 data/backups 备份>")
    if str(row[0] if row else "") == "ok":
        return _result("数据库完整性", PASS, "PRAGMA quick_check = ok")
    return _result("数据库完整性", FAIL, f"quick_check 返回：{row}", "openjob restore <最近的 data/backups 备份>")


def check_risk_lock(base_dir: Path) -> dict[str, str]:
    db_path = base_dir / "data" / "openjob.db"
    if not db_path.exists():
        return _result("风控状态", PASS, "尚无数据库，无风控锁")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT reason, locked_until FROM platform_safety_state WHERE id = 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return _result("风控状态", WARN, "无法读取风控状态表", "")
    if not row:
        return _result("风控状态", PASS, "未锁定")
    from datetime import datetime as _dt

    try:
        until = _dt.fromisoformat(str(row["locked_until"]))
    except ValueError:
        until = None
    if until and until > _dt.now():
        return _result("风控状态", WARN, f"冷却中：{row['reason']}（至 {until:%H:%M}）", "等待冷却结束自动恢复，勿手动提前发送")
    return _result("风控状态", PASS, f"曾有记录（{row['reason']}）但已过期")


def check_schedule(base_dir: Path) -> dict[str, str]:
    config_path = base_dir / "config.yaml"
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return _result("定时采集", WARN, "无法读取配置", "")
    schedule = data.get("collection_schedule") or {}
    if not schedule.get("enabled"):
        return _result("定时采集", PASS, "未启用（工作台启动后按配置执行）")
    times = schedule.get("times") or []
    return _result("定时采集", PASS, f"已启用，时间 {', '.join(times) or '未配置'}；仅工作台存活期间执行，不补跑")


def run_doctor(base_dir: Path, config: dict, chrome_checker: Callable[[], dict] | None = None) -> list[dict[str, str]]:
    """跑全部检查。chrome_checker 注入点用于测试隔离网络。"""
    return [
        (chrome_checker or check_chrome)(),
        check_ai(config),
        check_config(base_dir),
        check_integrity(base_dir),
        check_disk(base_dir),
        check_backup(base_dir),
        check_risk_lock(base_dir),
        check_schedule(base_dir),
    ]


_STATUS_ICONS = {PASS: "✅", WARN: "⚠️", FAIL: "❌"}


def render_doctor(results: list[dict[str, str]], printer: Callable[[str], None]) -> int:
    """打印诊断结果；返回失败项数量（CLI 退出码用）。"""
    for item in results:
        icon = _STATUS_ICONS.get(item["status"], "•")
        line = f"{icon} {item['item']}：{item['summary']}"
        if item.get("fix"):
            line += f"\n   → 修复：{item['fix']}"
        printer(line)
    return sum(1 for item in results if item["status"] == FAIL)


# ── 脱敏诊断包 ────────────────────────────────────────────────────────────

def _redact(text: str) -> str:
    import re

    text = re.sub(r"1[3-9]\d{9}", "[电话已脱敏]", str(text))
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[邮箱已脱敏]", text)
    text = re.sub(r"(sk-|Bearer |api[_-]?key[\s:=\"]+)[A-Za-z0-9_\-]{6,}", r"\1[KEY已脱敏]", text, flags=re.IGNORECASE)
    return text


def build_diagnostic_package(base_dir: Path, config: dict, include_logs: bool = False) -> bytes:
    """生成 zip 诊断包字节流。默认零敏感内容：
    不含简历、素材原文、联系方式、Cookie、API Key、数据库内容、Profile 目录。
    include_logs=True 时附带 data/logs/ 的**脱敏尾部**（每文件 200 行）。
    """
    from openjob import __version__

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest: dict[str, Any] = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "openjob_version": __version__,
            "python": __import__("sys").version.split()[0],
            "platform": __import__("platform").platform(),
            "config_structure": sorted(config.keys()) if isinstance(config, dict) else [],
            "ai_provider": str((config.get("ai") or {}).get("provider") or ""),
            "search_keywords_count": len((config.get("search") or {}).get("keywords") or []),
            "doctor": run_doctor(base_dir, config),
        }
        db_path = base_dir / "data" / "openjob.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    manifest["schema_version"] = str(conn.execute("PRAGMA user_version").fetchone()[0])
                    counts = {}
                    for table in ("jobs", "history", "resumes", "conversations", "base_resumes"):
                        try:
                            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                        except sqlite3.Error:
                            counts[table] = -1
                    manifest["row_counts"] = counts
                    try:
                        recent = conn.execute(
                            "SELECT action, substr(detail, 1, 80) FROM history "
                            "WHERE action IN ('error','score_failed') ORDER BY id DESC LIMIT 20"
                        ).fetchall()
                        manifest["recent_failures"] = [
                            {"action": action, "detail": _redact(detail)} for action, detail in recent
                        ]
                    except sqlite3.Error:
                        pass
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                manifest["database_error"] = str(exc)
        backups = sorted((base_dir / "data" / "backups").glob("openjob-*.db"))
        manifest["backups"] = {
            "count": len(backups),
            "latest_age_days": (
                (datetime.now() - datetime.fromtimestamp(backups[-1].stat().st_mtime)).days if backups else None
            ),
        }
        archive.writestr("doctor.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        if include_logs:
            logs_dir = base_dir / "data" / "logs"
            if logs_dir.exists():
                for log_file in sorted(logs_dir.glob("*.log"))[-5:]:
                    try:
                        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
                        archive.writestr(f"logs/{log_file.name}", _redact("\n".join(lines)))
                    except OSError:
                        continue
    return buffer.getvalue()
