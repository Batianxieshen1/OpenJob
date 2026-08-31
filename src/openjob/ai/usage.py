"""DeepSeek 用量记录：每次 AI 调用追加一行到 data/usage.jsonl。

记录失败绝不影响主流程（成本可见性是锦上添花）。
"""

import json
import os
from datetime import datetime
from pathlib import Path

USAGE_FILE = Path("./data/usage.jsonl")

# 预估单价（元/百万 token）。DeepSeek 价格会调整，仅作粗略参考，以官方账单为准。
COST_PER_M_INPUT = 1.0
COST_PER_M_OUTPUT = 2.0


def record_usage(purpose: str, model: str, usage: dict, *, usage_file: Path | None = None) -> None:
    """追加一条用量记录。usage 形如 {"prompt_tokens": int, "completion_tokens": int}。"""
    target = usage_file or USAGE_FILE
    if not isinstance(usage, dict) or not (usage.get("prompt_tokens") or usage.get("completion_tokens")):
        return
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "purpose": str(purpose or "unknown"),
        "model": str(model or ""),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def summarize_usage(days: int = 7, *, usage_file: Path | None = None) -> dict:
    """按本地日期汇总最近 N 天用量。"""
    target = usage_file or USAGE_FILE
    entries = []
    if target.exists():
        try:
            with open(target, encoding="utf-8") as f:
                for line in f:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
    daily: dict[str, dict] = {}
    for entry in entries:
        try:
            day = datetime.fromisoformat(entry["ts"]).strftime("%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        bucket = daily.setdefault(day, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
        bucket["prompt_tokens"] += entry.get("prompt_tokens", 0)
        bucket["completion_tokens"] += entry.get("completion_tokens", 0)
        bucket["calls"] += 1
    recent = sorted(daily.items(), reverse=True)[:max(1, days)]
    days_list = [
        {
            "date": day,
            **bucket,
            "estimated_cost": round(
                bucket["prompt_tokens"] / 1e6 * COST_PER_M_INPUT
                + bucket["completion_tokens"] / 1e6 * COST_PER_M_OUTPUT,
                4,
            ),
        }
        for day, bucket in recent
    ]
    total_calls = sum(d["calls"] for d in days_list)
    total_prompt = sum(d["prompt_tokens"] for d in days_list)
    total_completion = sum(d["completion_tokens"] for d in days_list)
    return {
        "days": days_list,
        "total": {
            "calls": total_calls,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "estimated_cost": round(
                total_prompt / 1e6 * COST_PER_M_INPUT + total_completion / 1e6 * COST_PER_M_OUTPUT, 4
            ),
        },
    }
