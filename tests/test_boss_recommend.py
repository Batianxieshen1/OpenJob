"""推荐页解析纯函数测试（推荐页计划 Task 2 / §11.1）。"""

import unittest

from openjob.collection.platforms.boss_recommend import (
    RECOMMEND_URL,
    normalize_recommendation_card,
    parse_recommendation_payload,
    recommendation_page_url,
    recommendation_should_continue,
)


def _card(**overrides):
    card = {
        "title": "数据分析实习生",
        "salary": "150-200元/天",
        "experience": "在校/应届",
        "education": "本科",
        "company": "示例科技公司",
        "company_meta": "计算机软件 C轮 500-999人",
        "hr_name": "示例女士",
        "hr_title": "HR",
        "location": "广州·天河区·石牌",
        "url": "/job_detail/sanitized-recommend-0001.html",
    }
    card.update(overrides)
    return card


class NormalizeRecommendationCardTests(unittest.TestCase):
    def test_full_card_normalizes(self):
        result = normalize_recommendation_card(_card())
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "数据分析实习生")
        self.assertEqual(result["url"], "/job_detail/sanitized-recommend-0001.html")
        self.assertEqual(result["salary"], "150-200元/天")
        self.assertEqual(result["company"], "示例科技公司")

    def test_missing_detail_url_rejected(self):
        self.assertIsNone(normalize_recommendation_card(_card(url="")))
        self.assertIsNone(normalize_recommendation_card(_card(url="/gongsi/xxx.html")))

    def test_absolute_url_normalized_to_path(self):
        result = normalize_recommendation_card(
            _card(url="https://www.zhipin.com/job_detail/abc123.html?securityId=SECRET&ka=personal_added_job_abc123")
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["url"], "/job_detail/abc123.html")

    def test_url_without_job_detail_rejected(self):
        self.assertIsNone(normalize_recommendation_card(_card(url="https://www.zhipin.com/web/geek/recommend")))

    def test_title_required(self):
        self.assertIsNone(normalize_recommendation_card(_card(title="")))

    def test_optional_fields_may_be_empty(self):
        result = normalize_recommendation_card(_card(salary="", experience="", education="", company="", hr_name=""))
        self.assertIsNotNone(result)
        self.assertEqual(result["salary"], "")
        self.assertEqual(result["company"], "")

    def test_non_dict_rejected(self):
        self.assertIsNone(normalize_recommendation_card("not a dict"))
        self.assertIsNone(normalize_recommendation_card(None))


class ParseRecommendationPayloadTests(unittest.TestCase):
    def test_payload_with_cards(self):
        payload = {
            "cards": [_card(), _card(url="/job_detail/sanitized-recommend-0002.html", title="另一岗")],
            "page_state": {"has_expected_content": True, "is_end_of_feed": False, "end_markers": []},
        }
        batch = parse_recommendation_payload(payload)
        self.assertEqual(len(batch.cards), 2)
        self.assertTrue(batch.has_expected_content)
        self.assertFalse(batch.is_end_of_feed)

    def test_payload_json_string_accepted(self):
        import json as _json

        batch = parse_recommendation_payload(_json.dumps({"cards": [_card()], "page_state": {"has_expected_content": True}}))
        self.assertEqual(len(batch.cards), 1)

    def test_end_of_feed_with_markers_and_no_cards(self):
        batch = parse_recommendation_payload({
            "cards": [],
            "page_state": {"has_expected_content": False, "is_end_of_feed": True, "end_markers": ["没有更多"]},
        })
        self.assertTrue(batch.is_end_of_feed)

    def test_no_cards_without_expected_content_is_unsupported_not_empty(self):
        batch = parse_recommendation_payload({"cards": [], "page_state": {"has_expected_content": False}})
        self.assertFalse(batch.has_expected_content)  # 上层必须报解析异常，不能伪装为空结果

    def test_invalid_json_is_unsupported(self):
        batch = parse_recommendation_payload("<html>blocked</html>")
        self.assertFalse(batch.has_expected_content)
        self.assertEqual(batch.cards, [])

    def test_duplicate_urls_kept_for_caller_dedup(self):
        payload = {"cards": [_card(), _card()], "page_state": {"has_expected_content": True}}
        batch = parse_recommendation_payload(payload)
        self.assertEqual(len(batch.cards), 2)  # 同页去重由 JS 侧 seen 完成，normalize 不丢数据


class RecommendationShouldContinueTests(unittest.TestCase):
    def test_continues_within_limits(self):
        self.assertTrue(recommendation_should_continue(
            scroll_round=1, max_scrolls=4, same_result_rounds=0,
            same_result_limit=2, is_end_of_feed=False,
        ))

    def test_stops_at_max_scrolls(self):
        self.assertFalse(recommendation_should_continue(
            scroll_round=4, max_scrolls=4, same_result_rounds=0,
            same_result_limit=2, is_end_of_feed=False,
        ))

    def test_stops_on_end_of_feed(self):
        self.assertFalse(recommendation_should_continue(
            scroll_round=1, max_scrolls=4, same_result_rounds=0,
            same_result_limit=2, is_end_of_feed=True,
        ))

    def test_stops_after_same_result_limit(self):
        self.assertFalse(recommendation_should_continue(
            scroll_round=3, max_scrolls=4, same_result_rounds=2,
            same_result_limit=2, is_end_of_feed=False,
        ))


class RecommendationPageUrlTests(unittest.TestCase):
    def test_first_page_uses_bare_url(self):
        self.assertEqual(recommendation_page_url(1), RECOMMEND_URL)

    def test_later_pages_append_page_param(self):
        self.assertEqual(recommendation_page_url(2), f"{RECOMMEND_URL}/?page=2")
        self.assertEqual(recommendation_page_url(5), f"{RECOMMEND_URL}/?page=5")


if __name__ == "__main__":
    unittest.main()
