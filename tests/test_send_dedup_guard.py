"""会话级去重守卫测试：会话已有我方消息时不再重复发送招呼语。"""

import json
import unittest
from unittest.mock import patch

from openjob.executor.sender import _has_outgoing_messages, _send_greeting_once


class HasOutgoingMessagesTests(unittest.TestCase):
    def test_true_when_me_sender_present(self):
        payload = json.dumps([
            {"sender": "system", "text": "系统卡片"},
            {"sender": "me", "text": "您好！"},
        ], ensure_ascii=False)
        with patch("openjob.executor.sender.evaluate", return_value=payload):
            self.assertTrue(_has_outgoing_messages("target-1"))

    def test_false_when_only_hr_and_system(self):
        payload = json.dumps([
            {"sender": "system", "text": "系统卡片"},
            {"sender": "hr", "text": "你好，看了你的简历"},
        ], ensure_ascii=False)
        with patch("openjob.executor.sender.evaluate", return_value=payload):
            self.assertFalse(_has_outgoing_messages("target-1"))

    def test_fail_open_on_invalid_payload(self):
        with patch("openjob.executor.sender.evaluate", return_value="not-json"):
            self.assertFalse(_has_outgoing_messages("target-1"))
        with patch("openjob.executor.sender.evaluate", side_effect=RuntimeError("boom")):
            self.assertFalse(_has_outgoing_messages("target-1"))


class SendGuardTests(unittest.TestCase):
    def test_send_greeting_once_skips_when_outgoing_exists(self):
        """会话已有我方消息 → 返回 already_present 成功语义，不进入发送流程。"""
        job = {"id": "job-dup", "title": "数据分析实习生", "company": "慧通数智", "url": "https://example.com/job"}
        throttle_config = {"browse_before_greet": False}

        with mock_evaluate_chain() as evaluate_mock, \
             patch("openjob.executor.sender._click_chat_button", return_value={"success": True}), \
             patch("openjob.executor.sender._handle_greet_popup", return_value={"success": True, "action": "no_popup"}), \
             patch("openjob.executor.sender._wait_for_chat_page", return_value={"success": True}), \
             patch("openjob.executor.sender.close_tab") as close_tab_mock, \
             patch("openjob.executor.sender._message_delivery_state", return_value="none"), \
             patch("openjob.executor.sender._submit_chat_message_background") as submit_mock:
            evaluate_mock.side_effect = _fake_evaluate_with_outgoing
            result, _ = _send_greeting_once(job, "招呼语文本", throttle_config)

        self.assertTrue(result.get("success"))
        self.assertTrue(result.get("already_present"))
        self.assertIn("未重复发送", result.get("history_detail", ""))
        submit_mock.assert_not_called()
        close_tab_mock.assert_called()


def _fake_evaluate_with_outgoing(target_id, js):
    """页面检查/点击 JS 放行；会话提取（MESSAGE_SELECTORS）返回含我方消息的列表。"""
    if "MESSAGE_SELECTORS" in js:
        return json.dumps([
            {"sender": "me", "text": "您好！我是王小明（此前手动发送）"},
        ], ensure_ascii=False)
    if "friend-content" in js or "listitem" in js:
        return "慧通数智"
    return json.dumps({"success": True})


def mock_evaluate_chain():
    return patch("openjob.executor.sender.evaluate", return_value="")


if __name__ == "__main__":
    unittest.main()
