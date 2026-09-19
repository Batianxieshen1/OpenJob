"""BOSS 推荐页只读勘察（Batch A / Task 0.2）。

通过 OpenJob Browser Runtime 打开登录态推荐页，记录：
最终 URL、页面状态（登录/验证码/预期内容）、卡片结构与字段、滚动新增行为、结束信号。
只读操作：不点击任何沟通/投递类按钮，不读取 Cookie，不调用私有接口。
输出：data/recon/recommendation-recon-raw.json（含真实数据，不入 Git）。
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openjob.browser import (
    close_tab,
    evaluate,
    get_page_info,
    navigate,
    new_tab,
    wait_for_load,
)

RECOMMEND_URL = "https://www.zhipin.com/web/geek/recommend"

# 只读 DOM 结构勘察：卡片容器/链接/字段探针，不读任何用户隐私字段
JS_RECON = r"""
(() => {
  const result = {
    href: location.href,
    readyState: document.readyState,
    title: document.title,
    bodyTextSample: (document.body.innerText || '').slice(0, 400),
    counts: {},
    signals: {},
    cardSamples: [],
    candidateSelectors: {}
  };

  // 页面状态信号（与现有 JS_DETECT_COLLECTION_RISK 的判据对齐）
  const bodyText = document.body ? document.body.innerText : '';
  result.signals.has_captcha = !!document.querySelector('.nc_iconfont, .geetest_panel, [class*="captcha"], #wrap.nc-multiline, .icon-official-code');
  result.signals.captcha_text = /验证码|安全验证|操作过于频繁/.test(bodyText.slice(0, 600)) || /验证码|安全验证/.test(bodyText.slice(0, 600));
  result.signals.login_wall = /登录|登陆/.test((document.querySelector('.header-login-btn, [ka="header-login"], .inner-right') || {}).innerText || '') || /请登录/.test(bodyText.slice(0, 300));
  result.signals.is_403 = document.title.includes('403') || /403|拒绝访问/.test(bodyText.slice(0, 200));

  // 候选卡片容器探针：以 /job_detail/ 链接为强身份信号，closest() 向上找容器
  const detailLinks = Array.from(document.querySelectorAll('a[href*="/job_detail/"]'));
  result.counts.detail_links = detailLinks.length;

  const cardClassCounter = {};
  detailLinks.slice(0, 40).forEach(a => {
    let node = a;
    for (let depth = 0; depth < 6 && node && node !== document.body; depth++) {
      node = node.parentElement;
      if (!node) break;
      const cls = String(node.className || '').trim();
      if (!cls) continue;
      const key = cls.split(/\s+/).slice(0, 3).join('.');
      if (/^(job|card|recommend|li|ul|ul-|sojob|job-primary|job-card)/i.test(key.replace(/\./g, '-')) || /job|recommend|card/i.test(key)) {
        cardClassCounter[key] = (cardClassCounter[key] || 0) + 1;
      }
    }
  });
  result.candidateSelectors.cardContainers = Object.entries(cardClassCounter)
    .sort((a, b) => b[1] - a[1]).slice(0, 8);

  // 按 job-primary / job-card-box 类结构的样本采集（与搜索页同源的常见结构）
  const containerCandidates = [
    '.job-card-box', '.job-card-left', '.job-primary', '.job-card-wrapper',
    'li[class*="job"]', '[class*="recommend"] li', '[ka^="recommend"]'
  ];
  let containerSelectorUsed = '';
  let cards = [];
  for (const sel of containerCandidates) {
    const nodes = document.querySelectorAll(sel);
    const withLink = Array.from(nodes).filter(n => n.querySelector('a[href*="/job_detail/"]'));
    if (withLink.length >= Math.max(2, Math.floor(detailLinks.length / 3))) {
      containerSelectorUsed = sel;
      cards = withLink;
      break;
    }
  }
  result.candidateSelectors.used = containerSelectorUsed;
  result.counts.cards_by_container = cards.length;

  // 字段探针：取前 3 个卡片的内部结构（类名与文本长度，不取真实内容）
  cards.slice(0, 3).forEach((card, index) => {
    const sample = { index, cardClass: String(card.className || '').slice(0, 80), fields: {} };
    const link = card.querySelector('a[href*="/job_detail/"]');
    if (link) {
      sample.fields.link_href_path = (() => { try { return new URL(link.href, location.origin).pathname; } catch (e) { return 'PARSE_FAIL'; } })();
      sample.fields.link_ka = link.getAttribute('ka') || '';
    }
    card.querySelectorAll('[class*="name"], [class*="title"], [class*="salary"], [class*="info"], [class*="company"], [class*="area"], [class*="desc"], h3, span').forEach(el => {
      const cls = String(el.className || '').trim();
      if (!cls) return;
      const key = cls.split(/\s+/)[0];
      if (!sample.fields[key]) {
        sample.fields[key] = { tag: el.tagName, textLen: (el.innerText || '').length, textPreview: (el.innerText || '').slice(0, 24) };
      }
    });
    result.cardSamples.push(sample);
  });

  // 结束/分页信号
  result.signals.end_markers = ['没有更多', '暂无更多', '到底了', '暂无推荐', '加载更多', '换一批']
    .filter(marker => bodyText.includes(marker));
  result.signals.has_pagination = !!document.querySelector('.page-pagination, [class*="pagination"], [ka*="page"]');
  result.signals.has_load_more = !!Array.from(document.querySelectorAll('button, a, div')).find(el => /加载更多|换一批|查看更多/.test(el.innerText || ''));

  // 页面主要区块类名（辅助确认整体结构）
  result.candidateSelectors.topLevelClasses = Array.from(document.querySelectorAll('body > div > div, main, [class*="page"], [class*="recommend"], [class*="feed"]'))
    .slice(0, 20)
    .map(el => String(el.className || '').trim().split(/\s+/).slice(0, 2).join('.'))
    .filter(Boolean)
    .slice(0, 12);

  return JSON.stringify(result);
})();
"""

JS_SCROLL_INFO = r"""
(() => {
  const before = document.querySelectorAll('a[href*="/job_detail/"]').length;
  window.scrollBy(0, Math.floor(window.innerHeight * 0.9));
  return JSON.stringify({ detailLinksBefore: before, scrollY: window.scrollY, innerHeight: window.innerHeight });
})();
"""

JS_AFTER_SCROLL = r"""
(() => JSON.stringify({
  detailLinksAfter: document.querySelectorAll('a[href*="/job_detail/"]').length,
  bodyTextTail: (document.body.innerText || '').slice(-200),
}))();
"""


def main() -> None:
    out_dir = Path("data/recon")
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"recon_at": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": []}

    target_id = new_tab(RECOMMEND_URL, background=True)
    if not target_id:
        print("FAIL: 无法打开推荐页 tab")
        return
    record["target_id"] = target_id
    try:
        wait_for_load(target_id, timeout=20)
        time.sleep(4)  # 等待首屏懒加载

        info = get_page_info(target_id)
        record["page_info"] = info

        recon_raw = evaluate(target_id, JS_RECON)
        try:
            recon = json.loads(recon_raw) if isinstance(recon_raw, str) else recon_raw
        except (json.JSONDecodeError, TypeError):
            recon = {"parse_error": str(recon_raw)[:400]}
        record["steps"].append({"step": "initial_recon", "result": recon})

        # 有限滚动勘察：3 轮，观察新增
        scroll_records = []
        for round_no in range(1, 4):
            scroll_raw = evaluate(target_id, JS_SCROLL_INFO)
            time.sleep(3)  # 等懒加载
            after_raw = evaluate(target_id, JS_AFTER_SCROLL)
            try:
                after = json.loads(after_raw) if isinstance(after_raw, str) else {}
            except (json.JSONDecodeError, TypeError):
                after = {}
            scroll_records.append({"round": round_no, **after})
        record["steps"].append({"step": "scroll_probe", "rounds": scroll_records})

        # 滚动结束后再做一次结构勘察（确认加载后的 DOM 一致性）
        recon2_raw = evaluate(target_id, JS_RECON)
        try:
            recon2 = json.loads(recon2_raw) if isinstance(recon2_raw, str) else {}
        except (json.JSONDecodeError, TypeError):
            recon2 = {}
        record["steps"].append({"step": "post_scroll_recon", "detail_links": recon2.get("counts", {}).get("detail_links"),
                                 "container_used": recon2.get("candidateSelectors", {}).get("used")})

    finally:
        close_tab(target_id)
        record["closed"] = True

    out_path = out_dir / "recommendation-recon-raw.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: 勘察完成，原始记录已写 {out_path}")
    # 控制台输出关键结论
    for step in record["steps"]:
        if step["step"] == "initial_recon":
            r = step["result"]
            print("\n=== 关键发现 ===")
            print("最终 URL:", r.get("href"))
            print("signals:", json.dumps(r.get("signals", {}), ensure_ascii=False))
            print("detail_links:", r.get("counts", {}).get("detail_links"))
            print("container used:", r.get("candidateSelectors", {}).get("used"))
            print("card containers 候选:", json.dumps(r.get("candidateSelectors", {}).get("cardContainers", []), ensure_ascii=False))
            print("end_markers:", r.get("signals", {}).get("end_markers"))
            print("topLevelClasses:", json.dumps(r.get("candidateSelectors", {}).get("topLevelClasses", []), ensure_ascii=False))
            for sample in r.get("cardSamples", [])[:2]:
                print(f"卡片样本 #{sample.get('index')}: class={sample.get('cardClass')}")
                for key, val in list(sample.get("fields", {}).items())[:14]:
                    print(f"   {key}: {val}")


if __name__ == "__main__":
    main()
