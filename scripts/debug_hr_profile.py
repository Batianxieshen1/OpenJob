"""HR 画像勘察：验证 BOSS 详情页 HR 姓名/头像/唯一标识 与 聊天页 HR 侧统计 的取法。

用法：
    python scripts/debug_hr_profile.py [职位详情URL]
默认：取库内最近一条有 HR 回复的岗位（聊天里有真实会话可对照）。

前置：Chrome 已通过调试端口启动，且已登录 BOSS。
产出：详情页 HR 区块 DOM 证据 + 聊天页可见指标清单（对应 HR 画像方案 S0）。
只读勘察：不点击任何会话、不发送任何内容。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openjob.browser import close_tab, evaluate, navigate, new_tab, wait_for_load  # noqa: E402
from openjob.config import load_config  # noqa: E402

JS_PROBE_DETAIL_HR = r"""
(() => {
    const out = {};
    // 旧选择器验证（应已失效）
    out.legacy = {
        boss_info_attr: Boolean(document.querySelector('.boss-info-attr')),
        job_boss_info: Boolean(document.querySelector('.job-boss-info')),
        boss_active_time: document.querySelector('.boss-active-time')?.textContent?.trim() || '',
    };
    // 全页扫候选人区块：含"活跃"字样或 hr/boss 类名的容器
    const hrish = Array.from(document.querySelectorAll('[class*="boss"], [class*="hr"], [class*="interviewer"]'))
        .filter(el => (el.className || '').toString().length < 80 && el.textContent && el.textContent.trim().length < 400)
        .slice(0, 10)
        .map(el => ({
            cls: el.className.toString(),
            text: el.textContent.trim().slice(0, 120).replace(/\s+/g, ' '),
            imgs: Array.from(el.querySelectorAll('img')).map(i => ({
                src: (i.getAttribute('src') || '').slice(0, 100),
                cls: i.className.toString(),
            })),
            links: Array.from(el.querySelectorAll('a[href*="boss"], a[href*="user"]')).map(a => a.getAttribute('href')),
        }));
    out.hr_containers = hrish;
    // 活跃信号所在元素（文本含"活跃"或"在线"的叶子节点）
    out.active_nodes = Array.from(document.querySelectorAll('span, p, div'))
        .filter(el => el.children.length === 0 && /活跃|在线/.test(el.textContent || '') && (el.textContent || '').trim().length < 30)
        .slice(0, 6)
        .map(el => ({ cls: el.className.toString().slice(0, 60), text: el.textContent.trim() }));
    // 全页小头像（40~80px 方形图）——HR 头像候选
    out.avatar_candidates = Array.from(document.querySelectorAll('img'))
        .filter(img => {
            const w = img.clientWidth || 0;
            return w >= 30 && w <= 90 && /bosszhipin/.test(img.src || '');
        })
        .slice(0, 8)
        .map(img => ({
            src: img.src.slice(0, 120),
            w: img.clientWidth,
            cls: img.className.toString().slice(0, 50),
            alt: img.alt || '',
        }));
    // boss 唯一标识候选：data 属性 / 链接 / securityId
    out.id_candidates = {
        data_attrs: Array.from(document.querySelectorAll('[data-bossid], [data-boss-id], [data-uid], [data-security-id]'))
            .slice(0, 4)
            .map(el => ({ tag: el.tagName, attrs: Object.fromEntries(Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value.slice(0, 60)])) })),
        boss_links: Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.getAttribute('href') || '')
            .filter(h => /boss|geek\/view|user/i.test(h))
            .slice(0, 6),
    };
    out.title = document.title;
    return JSON.stringify(out);
})()
"""

JS_PROBE_CHAT = r"""
(() => {
    const out = {};
    out.path = location.pathname;
    out.title = document.title;
    // 会话列表项结构（左侧栏）
    const items = Array.from(document.querySelectorAll('[class*="chat"] [class*="item"], [class*="conversation"], [class*="friend"]'))
        .filter(el => el.textContent && el.textContent.trim().length > 4 && el.textContent.trim().length < 200)
        .slice(0, 6)
        .map(el => ({
            cls: el.className.toString().slice(0, 60),
            text: el.textContent.trim().slice(0, 160).replace(/\s+/g, ' '),
        }));
    out.list_items = items;
    // 页面上任何"沟通/消息/回复"计数类文本
    out.counter_texts = Array.from(document.querySelectorAll('span, p, div'))
        .filter(el => el.children.length === 0)
        .map(el => (el.textContent || '').trim())
        .filter(t => /\d+\s*(条|次|个|天)/.test(t) && t.length < 40)
        .slice(0, 12);
    // 选中会话的右侧信息面板（如有）
    const panel = document.querySelector('[class*="detail"], [class*="profile"], [class*="info-panel"]');
    out.right_panel = panel ? { cls: panel.className.toString().slice(0, 60), text: panel.textContent.trim().slice(0, 300).replace(/\s+/g, ' ') } : null;
    return JSON.stringify(out);
})()
"""


def _pick_default_url() -> str:
    import sqlite3

    db_path = Path(__file__).resolve().parents[1] / "data" / "openjob.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT url FROM jobs WHERE deleted_at IS NULL AND source_platform='boss' "
            "AND reply_count > 0 AND url LIKE 'https://www.zhipin.com/job_detail/%' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT url FROM jobs WHERE deleted_at IS NULL AND source_platform='boss' "
                "AND url LIKE 'https://www.zhipin.com/job_detail/%' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else ""


def main() -> None:
    load_config()
    urls = sys.argv[1:] or [_pick_default_url()]
    target_id = None
    try:
        for url in urls:
            detail_url = url if url.startswith("http") else f"https://www.zhipin.com{url}"
            print(f"\n=== 详情页 {detail_url} ===")
            target_id = new_tab(detail_url, background=True)
            if not target_id:
                print("❌ 无法打开页面：确认 Chrome 已带调试端口启动且已登录 BOSS")
                raise SystemExit(1)
            time.sleep(4)
            wait_for_load(target_id, timeout=10)
            raw = evaluate(target_id, JS_PROBE_DETAIL_HR)
            try:
                data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (json.JSONDecodeError, TypeError):
                data = {}
            print(json.dumps(data, ensure_ascii=False, indent=1)[:4000])

            print("\n=== 聊天页（只读，不点击任何会话） ===")
            if not navigate(target_id, "https://www.zhipin.com/web/chat/index"):
                print("❌ 聊天页打开失败")
                continue
            time.sleep(5)
            wait_for_load(target_id, timeout=10)
            raw = evaluate(target_id, JS_PROBE_CHAT)
            try:
                chat = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (json.JSONDecodeError, TypeError):
                chat = {}
            print(json.dumps(chat, ensure_ascii=False, indent=1)[:3000])
            close_tab(target_id)
            target_id = None
    finally:
        if target_id:
            close_tab(target_id)
    print("\n✓ HR 画像勘察完成")


if __name__ == "__main__":
    main()
