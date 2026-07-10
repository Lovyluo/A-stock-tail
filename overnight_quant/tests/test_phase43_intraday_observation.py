import csv
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.execution.order_recorder import record_position_update
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

    def get_daily_kline(self, code: str, lookback: int = 120) -> list[dict]:
        return _daily_bars()

    def _safe_fund_flow(self, code: str):
        return ([{"main_net": 3_000_000, "large_net": 1_000_000, "super_net": 500_000}], "unit_test", "")


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


def test_intraday_includes_open_positions_as_candidates(tmp_path):
    config = _tmp_config(tmp_path)
    record_position_update(
        config,
        code="300002",
        name="Held Stock",
        price=18.2,
        qty=200,
        side="BUY",
        trade_time="2026-05-22 09:40:00",
    )

    result = run_intraday_observation(
        mode="live",
        config=config,
        client=StubIntradayClient(bars=_confirmed_bars(), market_ok=True),
        now=datetime(2026, 5, 22, 10, 5, tzinfo=CN_TZ),
        trade_date="2026-05-22",
    )

    assert result["candidate_source"] == "open_positions"
    assert result["rows"][0]["code"] == "300002"
    assert result["rows"][0]["is_position"] is True
    assert result["rows"][0]["position_open_qty"] == 200
    assert result["rows"][0]["signal"] in {BUY_POINT_A, "BUY_POINT_B", BUY_WATCH}


def test_intraday_outputs_chip_volume_context_to_csv(tmp_path):
    result = run_intraday_observation(
        mode="live",
        config=_tmp_config(tmp_path),
        client=StubIntradayClient(bars=_confirmed_bars(), market_ok=True),
        now=datetime(2026, 5, 22, 10, 5, tzinfo=CN_TZ),
        trade_date="2026-05-22",
        watchlist_path=_write_watchlist(tmp_path),
    )
    row = result["rows"][0]
    csv_row = _csv_rows(result["signals_csv"])[0]

    assert row["chip_peak_type"] in {"accumulation", "washout", "markup", "distribution", "neutral"}
    assert row["volume_signal"]
    assert "chip_peak_type" in csv_row
    assert "volume_signal" in csv_row
    assert "confidence_delta" in csv_row


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


def _daily_bars() -> list[dict]:
    rows = []
    for index in range(60):
        close = 18.0 + index * 0.01
        volume = 1000
        if index == 58:
            volume = 4000
        if index == 59:
            close = 19.0
            volume = 5000
        rows.append(
            {
                "date": f"2026-04-{(index % 28) + 1:02d}",
                "open": close - 0.05,
                "close": close,
                "high": close + 0.08,
                "low": close - 0.08,
                "volume": volume,
                "amount": close * volume,
            }
        )
    return rows


def _write_watchlist(tmp_path) -> str:
    records = tmp_path / "records"
    records.mkdir(parents=True, exist_ok=True)
    path = records / "next_morning_watchlist_2026-05-22.csv"
    path.write_text(
        "trade_date,code,name,category,score,risk_flags\n"
        "2026-05-22,300001,Demo Robotics,A,88,\n",
        encoding="utf-8-sig",
    )
    return str(path)


def _csv_rows(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
