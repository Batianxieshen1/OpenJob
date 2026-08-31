"""抓取 BOSS「推荐页」岗位（登录后的个性化推荐流）并入流水线。

用途：推荐页是 BOSS 按你的画像（在校/应届/期望方向）主动推流的岗位，
质量高且含搜索覆盖不到的岗位。本脚本抓取当前渲染的推荐卡片，
按城市过滤后入库（与搜索采集同库去重），随后可用 `openjob score` 评分。

用法：
    python scripts/collect_recommended.py [--城市 广州,佛山]

前置：Chrome 已通过桌面图标启动且已登录 BOSS。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openjob.browser import close_tab, configure, evaluate, new_tab  # noqa: E402
from openjob.cities import get_city_code  # noqa: E402
from openjob.config import load_config  # noqa: E402
from openjob.db import get_db, insert_job  # noqa: E402

EXTRACT_JS = r"""
(() => {
    const cards = [];
    const seen = new Set();
    for (const a of document.querySelectorAll('a[href*="/job_detail/"]')) {
        const href = a.getAttribute('href') || '';
        const idMatch = href.match(/job_detail\/([^.?]+)/);
        if (!idMatch || seen.has(idMatch[1])) continue;
        seen.add(idMatch[1]);
        let root = a;
        for (let k = 0; k < 8 && root; k++) {
            const t = root.innerText || '';
            if (t.includes('元/天') || t.includes('元/月') || t.includes('K')) break;
            root = root.parentElement;
        }
        const lines = (root.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
        const title = (a.querySelector('.job-name-text') || a).textContent.trim();
        const locationMatch = text => { const m = (text || '').match(/([\u4e00-\u9fa5]{2,4})·/); return m ? m[1] : ''; };
        const city = locationMatch((a.querySelector('.location') || {}).textContent || '')
            || locationMatch(lines.join(' '));
        const salary = (lines.find(l => /元\/(天|时|月)|K/.test(l)) || '').slice(0, 30);
        const company = (lines.find(l => /公司|科技|集团|有限/.test(l) && !/元\/|HR|hr|招聘|专员/.test(l)) || '').slice(0, 30);
        cards.push({
            id: idMatch[1],
            title,
            href,
            city,
            salary,
            company,
            lines: lines.slice(0, 8),
        });
    }
    return JSON.stringify(cards);
})()
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--城市", default="广州,佛山", help="只保留这些城市的岗位（逗号分隔，空=不过滤）")
    args = parser.parse_args()
    keep_cities = [c.strip() for c in args.城市.split(",") if c.strip()] if args.城市 else []

    config = load_config()
    configure(config)

    target_id = new_tab("https://www.zhipin.com/web/geek/recommend", background=True)
    if not target_id:
        print("❌ 无法打开推荐页：请先双击桌面 OpenJob 图标启动专用 Chrome 并登录 BOSS")
        raise SystemExit(1)
    time.sleep(8)

    raw = evaluate(target_id, EXTRACT_JS)
    close_tab(target_id)
    cards = json.loads(raw) if isinstance(raw, str) else raw
    if not cards:
        print("❌ 未解析到推荐卡片（页面未加载完或未登录），请稍后重试")
        raise SystemExit(1)

    db = get_db()
    added, skipped_city, skipped_dup = 0, 0, 0
    for card in cards:
        city = card.get("city") or ""
        if keep_cities and city and not any(c in city for c in keep_cities):
            skipped_city += 1
            continue
        job_id = f"rec-{card['id']}"
        jd_text = " | ".join(card.get("lines") or [])
        inserted = insert_job(db, {
            "id": job_id,
            "title": card.get("title") or "",
            "company": card.get("company") or "",
            "salary": card.get("salary") or "",
            "city": city,
            "experience": "在校/应届",
            "education": "",
            "jd": f"[BOSS推荐流] {jd_text}",
            "url": f"https://www.zhipin.com{card.get('href') or ''}",
            "source_platform": "boss",
            "source_job_id": card["id"],
            "source_keyword": "推荐页",
        })
        if inserted:
            added += 1
            print(f"  + {card.get('title', '')[:26]:<28} {card.get('salary', ''):<14} {city}")
        else:
            skipped_dup += 1
    db.close()
    print(f"\n推荐页抓取完成：新入库 {added}，城市过滤 {skipped_city}，重复 {skipped_dup}")
    print("下一步：openjob score 或面板「重新评分」为这批岗位打分")


if __name__ == "__main__":
    main()
