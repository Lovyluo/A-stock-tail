from datetime import datetime

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.strategy.auction_observation import AuctionObservationAnalyzer, load_auction_config, load_trading_day_candidates


class AuctionClient:
    def __init__(self, market_change=0.6):
        self.market_change = market_change

    def get_market_snapshot(self):
        return {"indices": {"sh000001": {"change_pct": self.market_change}, "sh000300": {"change_pct": self.market_change}, "sz399006": {"change_pct": self.market_change}}}

    def get_current_price(self, code):
        return {"code": code, "name": code, "price": 10.15, "last_close": 10.0, "amount_wan": 1800, "vol_ratio": 1.4}


class DemoFallbackAuctionClient(AuctionClient):
    def __init__(self):
        super().__init__()
        self.fallback_messages = []

    def get_current_price(self, code):
        self.fallback_messages.append("live current price failed, fallback to demo")
        return super().get_current_price(code)


def test_live_outside_auction_window_is_blocked():
    result = AuctionObservationAnalyzer(AuctionClient(), load_auction_config(), "live", datetime(2026, 7, 22, 9, 20, tzinfo=CN_TZ), [{"code": "000001"}]).analyze()
    assert result["status"] == "NOT_AUCTION_WINDOW"


def test_demo_auction_runs_outside_window():
    result = AuctionObservationAnalyzer(AuctionClient(), load_auction_config(), "demo", datetime(2026, 7, 22, 15, 30, tzinfo=CN_TZ), [{"code": "000001"}]).analyze()
    assert result["status"] == "DEMO_AUCTION_OBSERVATION"


def test_candidate_universe_contains_holding_tail_pick_and_watchlist(tmp_path):
    (tmp_path / "manual_orders.csv").write_text("order_id,trade_date,code,name,side,price,qty,amount,status\n1,2026-07-21,000001,持仓股,BUY,10,100,1000,FILLED\n", encoding="utf-8")
    (tmp_path / "signals.csv").write_text("code,name\n000002,尾盘股\n", encoding="utf-8-sig")
    (tmp_path / "next_morning_watchlist_2026-07-21.csv").write_text("code,name,category\n600001,观察股,A\n", encoding="utf-8-sig")
    rows = load_trading_day_candidates(tmp_path)
    by_code = {row["code"]: row for row in rows}
    assert by_code["000001"]["source_bucket"] == "holding"
    assert by_code["000002"]["source_bucket"] == "tail_pick"
    assert by_code["600001"]["source_bucket"] == "watchlist"


def test_market_direction_changes_action_bias():
    config = load_auction_config()
    now = datetime(2026, 7, 22, 9, 27, tzinfo=CN_TZ)
    strong = AuctionObservationAnalyzer(AuctionClient(0.8), config, "live", now, [{"code": "000001"}]).analyze()["rows"][0]
    weak = AuctionObservationAnalyzer(AuctionClient(-0.8), config, "live", now, [{"code": "000001"}]).analyze()["rows"][0]
    assert strong["action_bias"] == "attack"
    assert weak["action_bias"] != "attack"


def test_live_demo_quote_fallback_is_not_treated_as_auction_data():
    result = AuctionObservationAnalyzer(
        DemoFallbackAuctionClient(),
        load_auction_config(),
        "live",
        datetime(2026, 7, 22, 9, 27, tzinfo=CN_TZ),
        [{"code": "000001"}],
    ).analyze()

    assert result["status"] == "AUCTION_DATA_UNAVAILABLE"
    assert result["valid_for_trading_observation"] == "NO"
    assert result["rows"][0]["action_bias"] == "avoid"
    assert "auction_quote_missing" in result["rows"][0]["risk_flags"]
    assert any("demo_fallback" in error for error in result["source_errors"])
