"""day_off 当日持久化 + --force 语义拆分（2026-09-26 审计 R3）。

规则：休息日一旦抽中写入 risk_events，当日所有后续 send_greetings 调用
（无论概率重抽结果）都冻结；--force 只跳时间窗不再隐式跳过休息日；
skip_day_off 才是显式跳过；发送额度统计与页面额度统一本地日界。
"""

import pytest

import openjob.db as db_mod
from openjob.executor.sender import send_greetings


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    path = tmp_path / "doday.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    conn = db_mod.get_db(path)
    yield conn
    conn.close()


def _config(probability: float) -> dict:
    return {"throttle": {"day_off_probability": probability}}


class TestDayOffPersistence:
    def test_first_hit_writes_marker_and_freezes(self, db_env):
        config = _config(1.0)  # 必抽中
        assert send_greetings(config) == 0
        assert config["_workbench_send_report"]["stop_reason"] == "day_off"
        n = db_env.execute("SELECT COUNT(*) FROM risk_events WHERE event_type='day_off'").fetchone()[0]
        assert n == 1

    def test_marker_freezes_even_when_not_drawn_again(self, db_env):
        send_greetings(_config(1.0))
        # 第二次概率归零，但当日标记存在 → 仍冻结
        config = _config(0.0)
        assert send_greetings(config) == 0
        assert config["_workbench_send_report"]["stop_reason"] == "day_off"
        # 不重复写标记
        n = db_env.execute("SELECT COUNT(*) FROM risk_events WHERE event_type='day_off'").fetchone()[0]
        assert n == 1

    def test_force_no_longer_skips_day_off(self, db_env):
        config = _config(1.0)
        send_greetings(config, force=True)
        assert config["_workbench_send_report"]["stop_reason"] == "day_off"

    def test_skip_day_off_explicitly_bypasses(self, db_env):
        config = _config(1.0)
        send_greetings(config, skip_day_off=True)
        assert config["_workbench_send_report"]["stop_reason"] != "day_off"

    def test_no_marker_and_not_drawn_proceeds(self, db_env):
        config = _config(0.0)
        send_greetings(config)
        assert config["_workbench_send_report"]["stop_reason"] != "day_off"
        n = db_env.execute("SELECT COUNT(*) FROM risk_events WHERE event_type='day_off'").fetchone()[0]
        assert n == 0


class TestDailyQuotaLocalBoundary:
    def test_today_sent_counted_by_local_day(self, db_env):
        """本地时区今天 08:00 前的 sent 历史（UTC 昨天）也应计入今日额度。

        恒定夹具：插入"本地今天 00:30"对应的 UTC 值（本地零点+30min 转回 UTC）。
        任何时刻运行都命中边界场景——UTC 日期比本地日期少一天且属本地今天，
        不再依赖运行时刻（-1h 版本在本地 00:00-01:00 会跨日假失败，Gemini 验收发现）。"""
        db_env.execute(
            "INSERT INTO history (job_id, action, created_at) VALUES ('j1', 'sent', datetime('now','localtime','start of day','+30 minutes','-8 hours'))"
        )
        db_env.commit()
        row = db_env.execute(
            "SELECT COUNT(*) AS cnt FROM history WHERE action='sent' AND date(created_at,'localtime')=date('now','localtime')"
        ).fetchone()
        assert row[0] == 1
