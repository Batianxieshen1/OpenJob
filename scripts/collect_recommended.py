"""抓取 BOSS「推荐页」岗位（统一管线兼容入口）。

推荐页岗位与搜索流共用同一套采集管线（去重/过滤/详情解析/入库/来源观察/
页面额度/风控/任务进度）。本脚本只是把该来源作为一次采集任务转发给
CollectionOrchestrator，自己不做任何解析或入库。

用法：
    python scripts/collect_recommended.py

前置：
    1. 专用 Chrome 已启动并登录 BOSS（建议双击桌面 OpenJob 图标）；
    2. OpenJob 配置存在（config.yaml）。

说明：
    - 旧版本的 --城市 参数已废弃：推荐页来自账号个性化推荐，与城市/关键词
      无关；如需按城市过滤请使用工作台采集窗口的搜索流。
    - 采集到的岗位可在工作台「岗位池」查看，来源徽标显示「推荐页」。
    - 本脚本不会发送招呼语、简历或回复；投递仍需人工确认。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openjob.collection.orchestrator import CollectionOrchestrator  # noqa: E402
from openjob.config import load_config  # noqa: E402


def main() -> int:
    base_dir = Path(__file__).resolve().parents[1]
    config = load_config(base_dir / "config.yaml")

    options = {
        "platform_order": ["boss"],
        "auto_score": False,
        "platforms": {
            "boss": {
                "keywords": [],
                "cities": [],
                "city_codes": {},
                "max_pages": 1,
                "sort": "default",
                "recruitment_filter": "",
                "source_channels": ["recommendation"],
                "recommendation_max_scrolls": 1,
                "recommendation_max_cards": 20,
                "recommendation_same_result_limit": 2,
            }
        },
    }

    orchestrator = CollectionOrchestrator(config, run_id=None, task_id="cli-recommend")
    summary = orchestrator.run(options)

    boss = (summary.get("platforms") or {}).get("boss") or {}
    progress = boss.get("progress") or {}
    print("")
    print("=== 推荐页采集完成 ===")
    print(f"运行 ID：{summary.get('run_id')}")
    print(f"状态：{boss.get('status')}｜原因：{boss.get('reason_code')}")
    print(f"扫描 {boss.get('seen', 0)} · 新增 {boss.get('new', 0)} · 重复 {boss.get('duplicate', 0)} · 过滤 {boss.get('filtered', 0)}")
    for channel, info in (progress.get("sources") or {}).items():
        label = info.get("label") or channel
        print(f"{label}：扫描 {info.get('seen', 0)} · 新增 {info.get('new', 0)} · 重复 {info.get('duplicate', 0)}"
              + (f"｜{info.get('reason_code')}" if info.get("reason_code") else ""))
    print("岗位已入库，请到工作台「岗位池」查看（来源徽标：推荐页）；投递仍需人工确认。")
    return 0 if boss.get("status") in {"completed", "completed_with_shortage"} else 1


if __name__ == "__main__":
    sys.exit(main())
