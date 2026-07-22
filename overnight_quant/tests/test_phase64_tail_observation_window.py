from datetime import datetime

from overnight_quant.data.demo_data import demo_quotes
from overnight_quant.data.market_calendar import AFTERNOON_SESSION, CN_TZ, TAIL_SESSION, get_session_state
from overnight_quant.strategy.after_close_analysis import AfterCloseAnalyzer, load_after_close_config


class AfterCloseClient:
    mode = "live"
    after_close_candidate_source = "stub"
    fallback_messages = []

    def get_market_snapshot(self):
        return {"indices": {"sh000001": {"change_pct": 0.3}}, "northbound_net_yi": 1, "limit_down_count": 2}

    def get_after_close_universe_quotes(self):
        return demo_quotes()

    def get_daily_kline(self, code):
        return [{"close": 10 + index * 0.1, "high": 10.2 + index * 0.1, "low": 9.8 + index * 0.1, "volume": 1000 + index * 100} for index in range(30)]


def test_before_1450_live_after_close_observation_is_blocked():
    result = AfterCloseAnalyzer(AfterCloseClient(), load_after_close_config(), "live", datetime(2026, 7, 22, 14, 49, tzinfo=CN_TZ)).analyze("2026-07-22")
    assert result["status"] == "NOT_AFTER_CLOSE"


def test_after_1450_live_after_close_observation_can_run():
    result = AfterCloseAnalyzer(AfterCloseClient(), load_after_close_config(), "live", datetime(2026, 7, 22, 14, 50, tzinfo=CN_TZ)).analyze("2026-07-22")
    assert result["status"] in {"WATCHLIST_READY", "NO_WATCHLIST"}
    assert result["analysis_context"] == "after_close_early_window"


def test_after_close_uses_replay_context():
    result = AfterCloseAnalyzer(AfterCloseClient(), load_after_close_config(), "live", datetime(2026, 7, 22, 15, 10, tzinfo=CN_TZ)).analyze("2026-07-22")
    assert result["analysis_context"] == "after_close_replay"


def test_existing_tail_strategy_window_remains_1425_to_1455():
    assert get_session_state(datetime(2026, 7, 22, 14, 25, tzinfo=CN_TZ)) == TAIL_SESSION
    assert get_session_state(datetime(2026, 7, 22, 14, 55, tzinfo=CN_TZ)) == TAIL_SESSION
    assert get_session_state(datetime(2026, 7, 22, 14, 56, tzinfo=CN_TZ)) == AFTERNOON_SESSION
