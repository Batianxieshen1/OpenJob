"""公司画像字段勘察：验证 BOSS 详情页 Logo/公司主页链接 与 公司页简介 的取法。

用法：
    python scripts/debug_company_profile.py [职位详情URL...]
默认：取库内最近一条"美的"岗位。

前置：Chrome 已通过桌面图标（或 start_openjob.ps1）以调试端口启动，且已登录 BOSS。
产出：每页的关键选择器证据，供采集器 JS 提取逻辑定稿（对应公司画像方案 S0）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openjob.browser import close_tab, evaluate, navigate, new_tab, wait_for_load  # noqa: E402
from openjob.config import load_config  # noqa: E402

DEFAULT_URL = "https://www.zhipin.com/job_detail/095ab3b2608405640nNy3NW6F1FR.html"

# 详情页：公司卡结构 + 候选 Logo + 公司主页链接 + 初始状态字段探测
JS_PROBE_DETAIL = r"""
(() => {
    const out = {};
    const sider = document.querySelector('.sider-company');
    out.has_sider_company = Boolean(sider);
    if (sider) {
        out.sider_html_head = sider.innerHTML.slice(0, 1200);
        out.imgs = Array.from(sider.querySelectorAll('img')).map(img => ({
            src: img.getAttribute('src') || '',
            cls: img.className,
            alt: img.getAttribute('alt') || '',
        }));
        out.company_links = Array.from(sider.querySelectorAll('a'))
            .map(a => ({ href: a.getAttribute('href') || '', text: a.textContent.trim().slice(0, 40) }))
            .filter(l => l.href.includes('/gongsi/') || l.href.includes('/company/'));
    }
    out.page_links = Array.from(document.querySelectorAll('a[href*="/gongsi/"], a[href*="/company/"]')).slice(0, 5)
        .map(a => ({ href: a.getAttribute('href') || '', text: a.textContent.trim().slice(0, 40) }));
    // 初始状态探测：Boss 页面常把公司信息挂在 __INITIAL_STATE__
    const state = window.__INITIAL_STATE__ || {};
    const hits = [];
    const scan = (obj, path, depth) => {
        if (!obj || depth > 3 || hits.length > 12) return;
        if (typeof obj === 'string') {
            if (/img\.bosszhipin\.com.*(logo|brand)/i.test(obj)) hits.push(path + ' = ' + obj.slice(0, 120));
            return;
        }
        if (typeof obj !== 'object') return;
        for (const key of Object.keys(obj)) {
            if (/logo|brand|companyIntro|intro/i.test(key)) {
                const value = obj[key];
                hits.push(path + '.' + key + ' = ' + (typeof value === 'string' ? value.slice(0, 160) : JSON.stringify(value).slice(0, 160)));
            }
            try { scan(obj[key], path + '.' + key, depth + 1); } catch (e) { /* 循环引用跳过 */ }
        }
    };
    try { scan(state, '$', 0); } catch (e) { /* 同上 */ }
    out.state_hits = hits;
    // 详情页是否自带公司简介文案
    const introLike = document.querySelector('[class*="company-intro"], [class*="intro-content"], .job-sec.company-sec');
    out.detail_intro_node = introLike ? { cls: introLike.className, text_head: introLike.textContent.trim().slice(0, 200) } : null;
    out.title = document.title;
    return JSON.stringify(out);
})()
"""

# 公司主页：简介文案的候选容器
JS_PROBE_COMPANY = r"""
(() => {
    const out = {};
    out.title = document.title;
    out.is_company_page = /\/gongsi\/|\/company\//.test(location.pathname);
    // 按类名找简介容器
    const candidates = Array.from(document.querySelectorAll(
        '[class*="company-intro"], [class*="intro-content"], [class*="company-content"], .about-pos, [class*="company-text"]'
    ));
    out.containers = candidates.slice(0, 8).map(el => ({
        cls: el.className,
        text_len: el.textContent.trim().length,
        text_head: el.textContent.trim().slice(0, 240),
    }));
    // 兜底：含"公司简介/公司介绍"标题的区块
    const heading = Array.from(document.querySelectorAll('h2, h3, .section-title, [class*="title"]'))
        .find(el => /公司简介|公司介绍|企业介绍/.test(el.textContent || ''));
    if (heading) {
        out.heading = { tag: heading.tagName, cls: heading.className, text: heading.textContent.trim().slice(0, 40) };
        const section = heading.closest('section, div');
        if (section) {
            out.heading_section = {
                cls: section.className,
                text_len: section.textContent.trim().length,
                text_head: section.textContent.trim().slice(0, 240),
            };
        }
    }
    out.logo_imgs = Array.from(document.querySelectorAll('img')).slice(0, 30)
        .map(img => img.getAttribute('src') || '').filter(src => /img\.bosszhipin\.com.*(logo|brand)/i.test(src));
    return JSON.stringify(out);
})()
"""

# Logo 页面内转存验证：fetch 图床 → base64（走浏览器会话，无跨域泄露）
JS_FETCH_LOGO = r"""
(async (url) => {
    try {
        const resp = await fetch(url, { credentials: 'include' });
        if (!resp.ok) return JSON.stringify({ ok: false, status: resp.status });
        const blob = await resp.blob();
        const reader = new FileReader();
        const dataUrl = await new Promise((resolve, reject) => {
            reader.onload = () => resolve(reader.result);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
        return JSON.stringify({ ok: true, type: blob.type, size: blob.size, head: String(dataUrl).slice(0, 60) });
    } catch (err) {
        return JSON.stringify({ ok: false, error: String(err) });
    }
})("__LOGO_URL__")
"""


def _pick_default_url() -> str:
    import sqlite3

    db_path = Path(__file__).resolve().parents[1] / "data" / "openjob.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT url FROM jobs WHERE deleted_at IS NULL AND source_platform='boss' "
            "AND company='美的' AND url LIKE 'https://www.zhipin.com/job_detail/%' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else DEFAULT_URL


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
            import time

            time.sleep(4)
            wait_for_load(target_id, timeout=10)
            raw = evaluate(target_id, JS_PROBE_DETAIL)
            try:
                detail = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (json.JSONDecodeError, TypeError):
                detail = {}
            print(json.dumps(detail, ensure_ascii=False, indent=1)[:3500])

            logo_url = ""
            for img in detail.get("imgs") or []:
                src = str(img.get("src") or "")
                if src.startswith("http") or src.startswith("//"):
                    logo_url = src if src.startswith("http") else f"https:{src}"
                    break
            company_path = ""
            for link in detail.get("company_links") or detail.get("page_links") or []:
                href = str(link.get("href") or "")
                if "/gongsi/" in href or "/company/" in href:
                    company_path = "/" + href.split("/", 1)[-1] if href.startswith("/") else href
                    break

            if logo_url:
                print(f"\n--- 页面内 fetch 转存验证：{logo_url[:80]} ---")
                raw = evaluate(target_id, JS_FETCH_LOGO.replace("__LOGO_URL__", logo_url))
                print(raw)

            if company_path:
                company_url = f"https://www.zhipin.com{company_path}"
                print(f"\n=== 公司主页 {company_url} ===")
                if not navigate(target_id, company_url):
                    print("❌ 公司主页打开失败")
                    continue
                time.sleep(4)
                wait_for_load(target_id, timeout=10)
                raw = evaluate(target_id, JS_PROBE_COMPANY)
                try:
                    company = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except (json.JSONDecodeError, TypeError):
                    company = {}
                print(json.dumps(company, ensure_ascii=False, indent=1)[:3500])
            else:
                print("\n⚠ 详情页未发现 /company/ 链接")
            close_tab(target_id)
            target_id = None
    finally:
        if target_id:
            close_tab(target_id)
    print("\n✓ 勘察完成")


if __name__ == "__main__":
    main()
