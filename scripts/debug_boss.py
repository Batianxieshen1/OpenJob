"""BOSS 链路调试：验证 Browser Runtime → Chrome → BOSS 页面 → 搜索页解析 全链路。

用法：
    python scripts/debug_boss.py [搜索关键词] [城市]
默认：数据分析 广州

前置：Chrome 已通过桌面图标（或 start_openjob.ps1）以调试端口启动，且已登录 BOSS。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openjob.browser import configure, evaluate, new_tab, close_tab  # noqa: E402
from openjob.config import load_config  # noqa: E402
from openjob.cities import get_city_code  # noqa: E402


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "数据分析"
    city = sys.argv[2] if len(sys.argv) > 2 else "广州"

    config = load_config()
    configure(config)

    city_code = get_city_code(city) or ""
    query = keyword.replace(" ", "+")
    url = f"https://www.zhipin.com/web/geek/job?query={query}&city={city_code}"
    print(f"打开搜索页：{url}")

    target_id = new_tab(url, background=True)
    if not target_id:
        print("❌ 无法打开页面：请确认 Chrome 已通过调试端口启动（双击桌面 OpenJob 图标）")
        raise SystemExit(1)

    import time

    time.sleep(5)
    title = evaluate(target_id, "document.title") or ""
    print(f"页面标题：{title}")
    if "登录" in str(title) or "验证" in str(title):
        print("⚠ 检测到登录/验证页：请在打开的 Chrome 窗口中完成登录后重试")
        close_tab(target_id)
        raise SystemExit(1)

    count = evaluate(
        target_id,
        "document.querySelectorAll('ul.job-list-box li, [ka^=\"search_list_\"]').length",
    )
    print(f"解析到岗位卡片：{count or 0} 个")
    first = evaluate(
        target_id,
        "(() => { const el = document.querySelector('.job-name, [ka^=\"search_list_\"] .name');"
        " return el ? el.textContent.trim() : ''; })()",
    )
    if first:
        print(f"首个岗位：{first}")
    close_tab(target_id)
    print("✓ 链路正常")


if __name__ == "__main__":
    main()
