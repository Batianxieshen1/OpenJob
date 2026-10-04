"""存量岗位的公司画像回填（openjob company-enrich 命令实现）。

原则（公司形象增强方案 §3.3）：
- 默认 dry-run：只列清单与预计访问量，不打开任何页面、不写库；
- --apply 后每公司至多访问 1 条样本详情页 + 1 次公司主页，复用采集器的
  护栏（detail_page / company_page 日额度）、节流延时与风险双检；
- 只回填缺简介的 BOSS 公司，已删岗位不回填；任何失败不阻塞其余公司。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjob.browser import close_tab, evaluate, navigate, new_tab, wait_for_load
from openjob.collection.company_profile import (
    JS_EXTRACT_COMPANY_INTRO,
    absolute_company_url,
    clean_company_intro,
    fetch_company_logo,
    parse_json_payload,
    save_logo_file,
)
from openjob.collection.platforms.boss import (
    BossBrowser,
    _bounded_float,
    _confirm_risk,
    _positive_int,
    JS_EXTRACT_DETAIL,
)
from openjob.db import companies_missing_intro, update_company_profile
from openjob.platform_safety import PlatformAccessGuard, PlatformSafetyStop
from openjob.throttle import PageThrottle


def run_company_enrich(
    config: dict[str, Any],
    db_path: Path,
    *,
    apply: bool = False,
    limit: int | None = None,
    daily_limit_override: int | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """回填缺简介的 BOSS 公司画像。dry-run（apply=False）零页面访问、零写入。"""
    from openjob.db import add_risk_event, get_db

    conn = get_db(Path(db_path))
    companies = companies_missing_intro(conn, limit)
    total_jobs = sum(int(entry.get("job_count") or 0) for entry in companies)
    no_sample = [entry for entry in companies if not entry.get("sample_url")]

    if not apply:
        summary: dict[str, Any] = {
            "dry_run": True,
            "companies": len(companies),
            "jobs_covered": total_jobs,
            "skipped_no_sample_url": len(no_sample),
            "estimated_pages": (len(companies) - len(no_sample)) * 2,
        }
        conn.close()
        return summary

    collection_cfg = config.get("collection", {}) if isinstance(config.get("collection"), dict) else {}
    delay_multiplier = _bounded_float(
        collection_cfg.get("collection_delay_multiplier", 1.5),
        1.5, 1.0, 5.0,
    )
    detail_limit = _positive_int(collection_cfg.get("daily_detail_page_limit", 150), 150)
    company_limit = _positive_int(
        daily_limit_override or collection_cfg.get("company_daily_page_limit", 30), 30
    )
    intro_max_chars = _positive_int(collection_cfg.get("company_intro_max_chars", 2000), 2000)
    failure_limit = _positive_int(collection_cfg.get("company_failure_limit", 3), 3)
    data_dir = Path(db_path).parent

    failure_limit = _positive_int(collection_cfg.get("max_consecutive_page_failures", 3), 3)
    risk_pause_min = _positive_int(collection_cfg.get("risk_pause_min_minutes", 5), 5)
    risk_pause_max = max(risk_pause_min, _positive_int(collection_cfg.get("risk_pause_max_minutes", 10), 10))
    pause_minutes = random.SystemRandom().randint(risk_pause_min, risk_pause_max)

    summary = {
        "dry_run": False,
        "companies": len(companies),
        "processed": 0,
        "enriched": 0,
        "skipped_no_sample_url": 0,
        "failed": 0,
        "status": "completed",
        "reason_code": "",
        "message": "",
    }
    page_failures = 0
    worker: str | None = None

    def _risk_stop(kind: str, evidence: str) -> None:
        labels = {
            "captcha": "公司回填检测到验证码",
            "blocked": "公司回填连续检测到请求拦截",
            "rate_limit": "公司回填检测到频率限制",
            "login_required": "BOSS 登录状态已失效",
        }
        label = labels.get(kind, "公司回填检测到风险")
        evidence_note = f"；证据 {evidence}" if evidence else ""
        add_risk_event(conn, f"collection_{kind}", f"{label}{evidence_note}；冷却 {pause_minutes} 分钟")
        guard.lock(kind, minutes=pause_minutes)
        summary["status"] = "blocked"
        summary["reason_code"] = kind
        summary["message"] = f"{label}{evidence_note}；本轮已停止，冷却 {pause_minutes} 分钟后可重新开始"

    try:
        browser = BossBrowser(
            new_tab=new_tab, close_tab=close_tab, evaluate=evaluate,
            navigate=navigate, scroll=lambda *args, **kwargs: True,
            wait_for_load=wait_for_load,
        )
        guard = PlatformAccessGuard(conn, config, "collection", "boss")
        throttle = PageThrottle(delay_min=2.0 * delay_multiplier, delay_max=5.0 * delay_multiplier)
        try:
            guard.ensure_unlocked()
        except PlatformSafetyStop as exc:
            summary["status"] = "completed_with_shortage"
            summary["reason_code"] = exc.reason
            summary["message"] = f"平台风险锁生效中：{exc.reason}；冷却后再试"
            return summary
        worker = new_tab("about:blank", background=True)
        if not worker:
            summary["status"] = "failed"
            summary["reason_code"] = "no_browser"
            summary["message"] = "无法打开浏览器标签页：确认 Chrome 已带调试端口启动"
            return summary
        for entry in companies:
            company = str(entry.get("company") or "")
            sample_url = str(entry.get("sample_url") or "")
            if not company or not sample_url:
                summary["skipped_no_sample_url"] += 1
                continue
            try:
                guard.reserve("detail_page", daily_limit=detail_limit)
            except PlatformSafetyStop as exc:
                summary["status"] = "completed_with_shortage"
                summary["reason_code"] = exc.reason
                summary["message"] = f"已达安全上限：{exc.reason}；下次运行可续跑"
                break
            if throttle.wait():
                summary["status"] = "stopped"
                summary["reason_code"] = "user_stopped"
                summary["message"] = "用户已停止；下次运行可续跑"
                break
            if not browser.navigate(worker, sample_url):
                page_failures += 1
                summary["failed"] += 1
                log(f"  ✗ {company}：样本详情页打开失败")
                if page_failures >= failure_limit:
                    summary["status"] = "completed_with_shortage"
                    summary["reason_code"] = "consecutive_page_failures"
                    summary["message"] = "连续页面失败，本轮回填已结束；下次运行可续跑"
                    break
                continue
            time.sleep(2 * delay_multiplier)
            wait_for_load(worker, timeout=10)
            signal = _confirm_risk(browser, worker, delay_multiplier=delay_multiplier)
            if signal and signal["kind"] == "user_stopped":
                summary["status"] = "stopped"
                summary["reason_code"] = "user_stopped"
                summary["message"] = "用户已停止；下次运行可续跑"
                break
            if signal:
                _risk_stop(signal["kind"], signal["evidence"])
                break
            card = parse_json_payload(evaluate(worker, JS_EXTRACT_DETAIL))
            logo_url = str(card.get("company_logo") or "").strip()
            company_url = absolute_company_url(str(card.get("company_page_url") or ""))

            intro = ""
            if company_url:
                try:
                    guard.reserve("company_page", daily_limit=company_limit)
                except PlatformSafetyStop as exc:
                    summary["status"] = "completed_with_shortage"
                    summary["reason_code"] = exc.reason
                    summary["message"] = f"公司主页已达日额度：{exc.reason}；下次运行可续跑"
                    break
                if throttle.wait():
                    summary["status"] = "stopped"
                    summary["reason_code"] = "user_stopped"
                    summary["message"] = "用户已停止；下次运行可续跑"
                    break
                if not browser.navigate(worker, company_url):
                    summary["failed"] += 1
                    log(f"  ✗ {company}：公司主页打开失败")
                    continue
                time.sleep(1.5 * delay_multiplier)
                wait_for_load(worker, timeout=10)
                signal = _confirm_risk(browser, worker, delay_multiplier=delay_multiplier)
                if signal and signal["kind"] == "user_stopped":
                    summary["status"] = "stopped"
                    summary["reason_code"] = "user_stopped"
                    summary["message"] = "用户已停止；下次运行可续跑"
                    break
                if signal:
                    _risk_stop(signal["kind"], signal["evidence"])
                    break
                payload = parse_json_payload(evaluate(worker, JS_EXTRACT_COMPANY_INTRO))
                intro = clean_company_intro(str(payload.get("intro") or ""), intro_max_chars)
            elif not logo_url:
                summary["failed"] += 1
                page_failures += 1
                log(f"  ✗ {company}：详情页未解析到公司主页链接与 Logo")
                if page_failures >= failure_limit:
                    summary["status"] = "completed_with_shortage"
                    summary["reason_code"] = "consecutive_page_failures"
                    summary["message"] = "连续页面失败，本轮回填已结束；下次运行可续跑"
                    break
                continue

            logo_path = ""
            if logo_url:
                fetched = fetch_company_logo(logo_url)
                if fetched is not None:
                    logo_path = save_logo_file(data_dir, company, fetched[0], fetched[1])
            rows = update_company_profile(
                conn,
                company,
                intro=intro,
                intro_url=company_url or "",
                logo_path=logo_path,
                logo_url=logo_url,
            )
            page_failures = 0
            summary["processed"] += 1
            if intro or logo_path:
                summary["enriched"] += 1
                log(f"  ✓ {company}：简介 {len(intro)} 字，Logo {'已转存' if logo_path else '未取到'}，更新 {rows} 条岗位")
            else:
                summary["failed"] += 1
                log(f"  ✗ {company}：主页可访问但未解析到简介与 Logo（可能已改版，待勘察复核）")
    except KeyboardInterrupt:
        summary["status"] = "stopped"
        summary["reason_code"] = "user_stopped"
        summary["message"] = "用户中断（Ctrl+C）；下次运行可续跑"
    finally:
        if worker:
            close_tab(worker)
        conn.close()

    if summary["status"] == "completed":
        summary["message"] = f"回填完成：{summary['enriched']}/{summary['companies']} 家公司"
    return summary
