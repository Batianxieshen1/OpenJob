#!/usr/bin/env python3
"""OpenJob 配置向导：交互式生成 config.yaml，完成首次安装引导。

用法：
    python scripts/setup.py              # 交互式向导
    python scripts/setup.py --defaults   # 全部使用默认值（API Key 留空，稍后在面板填写）
    python scripts/setup.py --output other.yaml

安全说明：API Key 只写入本地 config.yaml（已在 .gitignore 中），永远不会进入 git。
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.yaml"

SERVICE_DEFAULTS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "doubao": ("https://ark.cn-beijing.volces.com/api/v3", "", "ARK_API_KEY"),
    "anthropic": ("https://api.anthropic.com", "", "ANTHROPIC_API_KEY"),
}


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_int(prompt: str, default: int) -> int:
    raw = ask(prompt, str(default))
    try:
        return int(raw)
    except ValueError:
        print(f"  ! 输入无效，使用默认值 {default}")
        return default


def ask_list(prompt: str, default: list[str]) -> list[str]:
    raw = ask(prompt, ", ".join(default))
    items = [x.strip() for x in re.split(r"[,，、\s]+", raw) if x.strip()]
    return items or default


def ask_secret(prompt: str, env_hint: str) -> str:
    print(f"{prompt}")
    print(f"  （输入时不回显；直接回车则使用环境变量 {env_hint}，或稍后在配置面板填写）")
    key = getpass.getpass("  API Key: ").strip()
    if key and not key.startswith("sk-"):
        print("  ! 注意：该 Key 不是 sk- 开头，如服务方格式不同可忽略此提示")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenJob 配置向导")
    parser.add_argument("--defaults", action="store_true", help="全部使用默认值，不交互")
    parser.add_argument("--output", default=str(ROOT / "config.yaml"), help="输出路径")
    args = parser.parse_args()

    if not EXAMPLE.exists():
        print("未找到 config.example.yaml，请在项目根目录运行。")
        return 1

    out_path = Path(args.output)
    if out_path.exists() and not args.defaults:
        confirm = input(f"{out_path.name} 已存在，覆盖？(y/N): ").strip().lower()
        if confirm != "y":
            print("已取消。")
            return 0

    print("=" * 56)
    print("OpenJob 配置向导")
    print("=" * 56)
    print("回车即接受 [方括号] 中的默认值。所有内容只写入本地文件，")
    print("config.yaml 已被 .gitignore 排除，不会进入 git 仓库。\n")

    # 1) AI 服务
    service = "deepseek"
    if not args.defaults:
        service = ask("AI 服务 (deepseek / doubao / anthropic)", "deepseek").lower()
        if service not in SERVICE_DEFAULTS:
            print(f"  ! 未知服务 {service}，回退 deepseek")
            service = "deepseek"
    base_url_default, model_default, env_hint = SERVICE_DEFAULTS.get(service, SERVICE_DEFAULTS["deepseek"])
    api_key = "" if args.defaults else ask_secret("1) AI 服务 API Key", env_hint)
    base_url = base_url_default
    model = model_default

    # 2) 求职画像
    if args.defaults:
        keywords, cities, recruitment, threshold = ["数据分析"], ["广州"], "campus", 70
        greeting_pref = ""
    else:
        print("\n2) 求职画像（用于岗位采集与 AI 评分）")
        keywords = ask_list("目标岗位关键词（逗号分隔）", ["数据分析"])
        cities = ask_list("目标城市（逗号分隔）", ["广州"])
        recruitment = ask("岗位性质 (campus=实习 / experienced=正式 / both)", "campus").lower()
        if recruitment not in {"campus", "experienced", "both"}:
            print("  ! 无效值，回退 campus")
            recruitment = "campus"
        greeting_pref = ask("招呼语偏好（可选，一句话描述你的语气/亮点）", "")
        threshold = ask_int("评分阈值（低于此分的岗位不生成简历）", 70)

    # 3) 写配置
    import yaml

    config = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    profile = config.setdefault("profile", {})
    profile["recruitment_type"] = recruitment
    profile["target_cities"] = cities
    profile["allow_internship"] = recruitment in {"campus", "both"}
    if greeting_pref:
        profile["greeting_preference"] = greeting_pref

    search = config.setdefault("search", {})
    search["keywords"] = keywords
    search["cities"] = cities
    platforms = config.setdefault("platforms", {})
    boss = platforms.setdefault("boss", {})
    boss["search"] = {**boss.get("search", {}), "keywords": keywords, "cities": cities}

    scoring = config.setdefault("scoring", {})
    scoring["threshold"] = threshold

    ai = config.setdefault("ai", {})
    ai["service"] = service
    ai["base_url"] = base_url
    if model:
        ai["model"] = model
    if api_key:
        ai["api_key"] = api_key

    # 4) 数据目录
    data_dir = ROOT / "data"
    (data_dir / "resumes" / "templates").mkdir(parents=True, exist_ok=True)
    (data_dir / "backups").mkdir(parents=True, exist_ok=True)

    out_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print("\n" + "=" * 56)
    print("✅ 配置已写入", out_path)
    if not api_key:
        print(f"   ⚠️ 尚未配置 API Key：可通过环境变量 {env_hint} 提供，或打开配置面板填写")
    print("\n下一步：")
    print("  1. 启动工作台（首次会自动初始化数据库）：")
    print("      python -m openjob.web.server    # 或双击 启动工作台.bat")
    print("  2. 打开 http://127.0.0.1:8686 完成剩余配置：")
    print("      - 上传简历底稿（.docx，建议一页式，支持多方向多份）")
    print("      - 核对采集关键词与城市")
    print("  3. 准备浏览器自动化：关闭全部 Chrome 后，用调试模式启动：")
    print("      chrome --remote-debugging-port=9222")
    print("  4. 在工作台「运行全流程」开始采集 → 审核评分 → 确认投递")
    print("\n安全提醒：所有投递/发送动作都需要人工确认；API Key 只存本地。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
