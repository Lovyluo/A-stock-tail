import csv
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.scripts.run_intraday_observation import run_intraday_observation
from overnight_quant.strategy.intraday_observation import (
    BUY_POINT_A,
    BUY_WATCH,
    NO_BUY,
    IntradayObservationAnalyzer,
    load_intraday_config,
)


class StubIntradayClient:
    def __init__(self, bars: list[dict] | None = None, market_ok: bool = True):
        self.bars = bars or []
        self.market_ok = market_ok
        self.quality_report = SimpleNamespace(fallback_to_demo=False, source_status=[], warnings=[])

    def get_market_snapshot(self) -> dict:
        change = 0.4 if self.market_ok else -2.0
        return {
            "indices": {"sh000001": {"change_pct": change}, "sh000300": {"change_pct": change}},
            "northbound_net_yi": 3 if self.market_ok else -120,
            "limit_down_count": 8 if self.market_ok else 60,
        }

    def get_current_price(self, code: str) -> dict:
        return {
            "code": code,
            "name": "Demo Robotics",
            "price": 19.02,
            "open_price": 18.8,
            "open_change_pct": 1.6,
            "change_pct": 2.8,
            "last_close": 18.5,
            "high": 19.18,
            "low": 18.72,
            "limit_up": 20.35,
            "limit_up_gap_pct": 7.0,
            "vwap": 18.9,
            "is_limit_up": False,
            "is_limit_down": False,
        }

    def get_intraday_bars(self, code: str) -> list[dict]:
        return list(self.bars)


def test_demo_intraday_writes_signal_report_and_csv(tmp_path):
    result = run_intraday_observation(mode="demo", config=_tmp_config(tmp_path))

    assert result["status"] == "DEMO_INTRADAY_OBSERVATION"
    assert result["intraday_window"] == "PRIMARY_BUY"
    assert result["buy_point_a_count"] == 1
    assert Path(result["report_path"]).exists()
    rows = _csv_rows(result["signals_csv"])
    assert rows
    assert rows[0]["signal"] == BUY_POINT_A


def test_market_gate_blocks_live_intraday_buy_points(tmp_path):
    analyzer = IntradayObservationAnalyzer(
        StubIntradayClient(bars=_confirmed_bars(), market_ok=False),
        _tmp_config(tmp_path),
        "live",
        now=datetime(2026, 5, 22, 10, 5, tzinfo=CN_TZ),
        candidate_rows=[_candidate()],
    )

    result = analyzer.analyze("2026-05-22")

    assert result["status"] == "MARKET_BLOCKED"
    assert result["rows"][0]["signal"] == NO_BUY
    assert "index_weak" in result["market_gate"]["reject_reasons"]


def test_quote_only_proxy_does_not_promote_to_buy_point(tmp_path):
    analyzer = IntradayObservationAnalyzer(
        StubIntradayClient(bars=[], market_ok=True),
        _tmp_config(tmp_path),
        "live",
        now=datetime(2026, 5, 22, 10, 5, tzinfo=CN_TZ),
        candidate_rows=[_candidate()],
    )

    result = analyzer.analyze("2026-05-22")

    assert result["status"] == "INTRADAY_WATCH_ONLY"
    assert result["rows"][0]["signal"] == BUY_WATCH
    assert result["rows"][0]["intraday_source"] == "quote_vwap_proxy"


def test_live_intraday_confirmed_vwap_reclaim_can_emit_a_point(tmp_path):
    analyzer = IntradayObservationAnalyzer(
        StubIntradayClient(bars=_confirmed_bars(), market_ok=True),
        _tmp_config(tmp_path),
        "live",
        now=datetime(2026, 5, 22, 10, 5, tzinfo=CN_TZ),
        candidate_rows=[_candidate()],
    )

    result = analyzer.analyze("2026-05-22")

    assert result["status"] == "INTRADAY_SIGNAL_READY"
    assert result["rows"][0]["signal"] == BUY_POINT_A
    assert result["rows"][0]["has_intraday_series"] is True


def test_intraday_outputs_holding_defence_and_watchlist_observation_conditions(tmp_path):
    analyzer = IntradayObservationAnalyzer(
        StubIntradayClient(bars=_confirmed_bars(), market_ok=True),
        _tmp_config(tmp_path),
        "live",
        now=datetime(2026, 5, 22, 10, 5, tzinfo=CN_TZ),
        candidate_rows=[
            {**_candidate(), "source_bucket": "holding", "avg_buy_price": 19.2, "stop_loss_price": 18.4},
            {**_candidate(), "code": "600001", "source_bucket": "watchlist", "source_buckets": "watchlist|auction_new"},
        ],
    )

    result = analyzer.analyze("2026-05-22")
    holding, watch = result["rows"]
    assert holding["source_bucket"] == "holding"
    assert any("止损观察线" in item for item in holding["defence_conditions"])
    assert watch["source_bucket"] == "watchlist"
    assert any("竞价偏强" in item for item in watch["observation_conditions"])


def _tmp_config(tmp_path):
    config = load_intraday_config()
    config["paths"] = {
        "records_dir": str(tmp_path / "records"),
        "reports_dir": str(tmp_path / "reports"),
        "examples_dir": str(tmp_path / "examples"),
    }
    return config


def _candidate() -> dict:
    return {
        "code": "300001",
        "name": "Demo Robotics",
        "category": "A",
        "score": "88",
        "risk_flags": "",
    }


def _confirmed_bars() -> list[dict]:
    closes = [18.72, 18.82, 18.94, 18.88, 18.91, 18.97, 19.02]
    vols = [4200, 4600, 5000, 4300, 6500, 7200, 8200]
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "time": f"2026-05-22 10:{index + 1:02d}",
                "open": closes[index - 1] if index else close,
                "close": close,
                "high": close + 0.04,
                "low": close - 0.04,
                "volume": vols[index],
                "amount": close * vols[index] * 100,
                "vwap": 18.88 + index * 0.01,
            }
        )
    return rows


def _csv_rows(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
