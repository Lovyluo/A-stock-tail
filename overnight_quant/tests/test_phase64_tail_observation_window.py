from datetime import datetime

from overnight_quant.data.demo_data import demo_quotes
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.strategy.after_close_analysis import AfterCloseAnalyzer, load_after_close_config


class TailClient:
    mode = "live"
    after_close_candidate_source = "stub"
    fallback_messages = []

    def get_market_snapshot(self):
        return {"indices": {"sh000001": {"change_pct": 0.3}}, "northbound_net_yi": 1, "limit_down_count": 2}

    def get_after_close_universe_quotes(self):
        return demo_quotes()

    def get_daily_kline(self, code):
        return [{"close": 10 + index * 0.1, "high": 10.2 + index * 0.1, "low": 9.8 + index * 0.1, "volume": 1000 + index * 100} for index in range(30)]


def test_before_1450_live_tail_observation_is_blocked():
    result = AfterCloseAnalyzer(TailClient(), load_after_close_config(), "live", datetime(2026, 7, 22, 14, 49, tzinfo=CN_TZ)).analyze("2026-07-22")
    assert result["status"] == "NOT_TAIL_OBSERVATION_WINDOW"


def test_after_1450_live_tail_observation_can_run():
    result = AfterCloseAnalyzer(TailClient(), load_after_close_config(), "live", datetime(2026, 7, 22, 14, 50, tzinfo=CN_TZ)).analyze("2026-07-22")
    assert result["status"] in {"WATCHLIST_READY", "NO_WATCHLIST"}
    assert result["analysis_context"] == "tail_window_live"


def test_after_close_uses_replay_context():
    result = AfterCloseAnalyzer(TailClient(), load_after_close_config(), "live", datetime(2026, 7, 22, 15, 10, tzinfo=CN_TZ)).analyze("2026-07-22")
    assert result["analysis_context"] == "after_close_replay"
