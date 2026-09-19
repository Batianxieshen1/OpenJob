"""BOSS 推荐页解析与有限分页驱动（推荐页计划 Task 2 / Batch B）。

真实 DOM 勘察依据：docs/architecture/boss-recommendation-discovery.md。
关键事实：推荐页是传统分页（?page=N，每页约 20 卡，li.item-boss），
窗口滚动不加载新内容——因此 `max_scrolls` 语义为最大分页轮次。

安全边界：解析器只读卡片内 a[href*="/job_detail/"] 链接，绝不查询或点击
卡片上的「继续沟通」按钮（a.btn.btn-startchat），不读取 Cookie/localStorage。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

RECOMMEND_URL = "https://www.zhipin.com/web/geek/recommend"
RECOMMENDATION_CHANNEL = "recommendation"

# 与搜索页 JS_EXTRACT_LIST 返回 JSON 字符串的约定一致；本脚本返回对象：
# {cards: [...], page_state: {has_expected_content, is_end_of_feed, end_markers}}
# 卡片字段以 discovery 确认的 selector 为准：
# - 职位链接 a[href*="/job_detail/"]（强身份信号；归一化为 pathname）
# - 职位名 .job-name-text；地点 .location；公司 .company-info .text b a
# - 薪资/经验/学历：.job-info > p.gray（直接文本=薪资，span=经验、学历）
# - HR：.info-header h3.name
JS_EXTRACT_RECOMMEND_LIST = r"""
(() => {
    const cards = [];
    const seen = new Set();
    const nodes = document.querySelectorAll('li.item-boss');
    nodes.forEach((card) => {
        const link = card.querySelector('a[href*="/job_detail/"]');
        if (!link) return;
        let path = '';
        try { path = new URL(link.href, location.origin).pathname; } catch (err) { return; }
        if (!path || !path.includes('/job_detail/') || seen.has(path)) return;
        seen.add(path);
        const title = (card.querySelector('.job-name-text') || {}).innerText || '';
        const locationText = (card.querySelector('.location') || {}).innerText || '';
        const companyEl = card.querySelector('.company-info .text b a');
        const companyMeta = Array.from(card.querySelectorAll('.company-info p.gray span'))
            .map((el) => (el.innerText || '').trim()).filter(Boolean);
        const grayP = card.querySelector('.job-info p.gray');
        let salary = '';
        let experience = '';
        let education = '';
        if (grayP) {
            const spans = Array.from(grayP.querySelectorAll('span')).map((el) => (el.innerText || '').trim()).filter(Boolean);
            const fullText = (grayP.innerText || '').trim();
            salary = spans.length ? fullText.split(spans[0])[0].trim() : fullText;
            if (spans.length > 0) experience = spans[0];
            if (spans.length > 1) education = spans[1];
        }
        const hrNameEl = card.querySelector('.info-header h3.name');
        let hrName = '';
        let hrTitle = '';
        if (hrNameEl) {
            const hrSpans = Array.from(hrNameEl.querySelectorAll('span'));
            hrName = hrSpans.length ? (hrSpans[0].innerText || '').trim() : (hrNameEl.innerText || '').trim();
            const graySpan = card.querySelector('.info-header h3.name span.gray');
            hrTitle = (graySpan || {}).innerText || '';
        }
        cards.push({
            title: title.trim(),
            salary: salary.trim(),
            experience: experience,
            education: education,
            company: companyEl ? (companyEl.innerText || '').trim() : '',
            company_meta: companyMeta.join(' '),
            hr_name: hrName,
            hr_title: hrTitle.trim(),
            location: locationText.replace(/[[\]]/g, '').trim(),
            url: path,
        });
    });
    const bodyText = (document.body ? document.body.innerText : '');
    const endMarkers = ['没有更多', '暂无更多', '到底了', '暂无推荐', '没有更多推荐']
        .filter((marker) => bodyText.includes(marker));
    return JSON.stringify({
        cards: cards,
        page_state: {
            has_expected_content: cards.length > 0 || Boolean(document.querySelector('li.item-boss')),
            is_end_of_feed: cards.length === 0 && endMarkers.length > 0,
            end_markers: endMarkers,
        },
    });
})();
"""

_END_MARKERS = ("没有更多", "暂无更多", "到底了", "暂无推荐", "没有更多推荐")


@dataclass(frozen=True)
class RecommendationBatch:
    """一次推荐页分页轮次的解析结果。"""

    cards: list[dict[str, str]] = field(default_factory=list)
    has_expected_content: bool = True
    is_end_of_feed: bool = False
    page_marker: str = ""
    risk: str | None = None
    risk_evidence: str = ""


def normalize_recommendation_card(raw: object) -> dict[str, str] | None:
    """归一化一张推荐卡片；不可用卡片返回 None（计入 parse_failed）。"""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    if not url or "/job_detail/" not in url:
        return None
    if url.startswith("http"):
        try:
            from urllib.parse import urlparse

            url = urlparse(url).path or url
        except ValueError:
            return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    return {
        "title": title,
        "salary": str(raw.get("salary") or "").strip(),
        "experience": str(raw.get("experience") or "").strip(),
        "education": str(raw.get("education") or "").strip(),
        "company": str(raw.get("company") or "").strip(),
        "company_meta": str(raw.get("company_meta") or "").strip(),
        "hr_name": str(raw.get("hr_name") or "").strip(),
        "hr_title": str(raw.get("hr_title") or "").strip(),
        "location": str(raw.get("location") or "").strip(),
        "url": url,
    }


def parse_recommendation_payload(raw: Any) -> RecommendationBatch:
    """解析浏览器返回的 JSON 字符串；非法结构返回 has_expected_content=False。"""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return RecommendationBatch(has_expected_content=False)
    if not isinstance(payload, dict):
        return RecommendationBatch(has_expected_content=False)
    page_state = payload.get("page_state") if isinstance(payload.get("page_state"), dict) else {}
    cards: list[dict[str, str]] = []
    for item in payload.get("cards") or []:
        normalized = normalize_recommendation_card(item)
        if normalized is not None:
            cards.append(normalized)
    end_markers = page_state.get("end_markers") if isinstance(page_state.get("end_markers"), list) else []
    return RecommendationBatch(
        cards=cards,
        has_expected_content=bool(page_state.get("has_expected_content")) or bool(cards),
        is_end_of_feed=bool(page_state.get("is_end_of_feed")) or (not cards and bool(end_markers)),
        page_marker=str(page_state.get("page_marker") or ""),
    )


def recommendation_should_continue(
    *,
    scroll_round: int,
    max_scrolls: int,
    same_result_rounds: int,
    same_result_limit: int,
    is_end_of_feed: bool,
) -> bool:
    """是否允许下一轮分页：上限、结束信号、连续无新增三重终止。"""
    if is_end_of_feed:
        return False
    if scroll_round >= max_scrolls:
        return False
    if same_result_rounds >= same_result_limit:
        return False
    return True


def recommendation_page_url(page_no: int) -> str:
    """第 N 页的推荐页 URL（N>=2 追加 ?page=N；第 1 页用裸 URL）。"""
    if page_no <= 1:
        return RECOMMEND_URL
    return f"{RECOMMEND_URL}/?page={page_no}"
