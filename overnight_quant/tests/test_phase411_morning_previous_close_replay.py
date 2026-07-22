import copy
import csv
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.demo_data import demo_daily_kline, demo_market_snapshot, demo_quotes
from overnight_quant.data.market_calendar import CN_TZ, effective_after_close_trade_day, previous_likely_cn_trade_day
from overnight_quant.reports.after_close_report import MORNING_REPLAY_FIELDS
from overnight_quant.scripts.run_after_close_analysis import main, run_after_close_analysis
from overnight_quant.strategy.yang_yongxing_overnight import load_config


class ReplayStubClient:
    def __init__(self, rows: list[dict], fallback: bool = False):
        self.rows = copy.deepcopy(rows)
        self.fallback_messages = ["live data failed, fallback to demo"] if fallback else []
        self.quality_report = SimpleNamespace(
            fallback_to_demo=fallback,
            source_status=[],
            warnings=list(self.fallback_messages),
            freshness_summary={"fresh": 1, "stale": 0, "unknown": 0},
            stale_sources=[],
            expected_data_date="2026-05-27",
            freshness_basis="previous_close_expected",
            target_date_match_count=1,
            target_date_mismatch_count=0,
            timestamp_missing_count=0,
        )

    def get_market_snapshot(self) -> dict:
        return demo_market_snapshot()

    def get_candidate_quotes(self) -> list[dict]:
        return copy.deepcopy(self.rows)

    def get_daily_kline(self, code: str, lookback: int = 120) -> list[dict]:
        return demo_daily_kline(code, lookback)

    def get_kline_freshness_reasons(self, code: str) -> list[str]:
        return []


class ReplaySourceClient(AStockClient):
    def __init__(self, quote_date: str = "2026-05-27", kline_date: str = "2026-05-27"):
        super().__init__(
            mode="live",
            now=datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ),
            data_context="previous_close_replay",
            expected_data_date="2026-05-27",
        )
        self.quote_date = quote_date
        self.kline_date = kline_date
        self.hot_query_dates: list[str] = []
        self.minute_flow_calls = 0

    def _get_json(self, url: str, params=None, headers=None, timeout=10):
        if "getharden/date/" in url:
            query_date = url.split("getharden/date/", 1)[1].split("/", 1)[0]
            self.hot_query_dates.append(query_date)
            if query_date != "2026-05-27":
                return {"errocode": 0, "data": []}
            return {
                "errocode": 0,
                "data": [
                    {
                        "code": "300001",
                        "name": "Replay Robotics",
                        "close": "18.50",
                        "zhangfu": "4.80",
                        "huanshou": "8.60",
                        "chengjiaoe": "350000000",
                        "reason": "Robot+AI",
                        "ddejingliang": "1000",
                    }
                ],
            }
        raise AssertionError(f"unexpected JSON source call: {url}")

    def _tencent_quotes(self, codes: list[str]) -> dict[str, dict]:
        return {
            "300001": {
                "code": "300001",
                "name": "Replay Robotics",
                "price": 18.5,
                "change_pct": 4.8,
                "high": 18.7,
                "low": 17.7,
                "amount_wan": 35000.0,
                "volume": 100000.0,
                "turnover_pct": 8.6,
                "float_mcap_yi": 120.0,
                "limit_up": 20.15,
                "limit_down": 16.49,
                "vol_ratio": 1.5,
                "_sources": {"price": "tencent_quote.vals[3]"},
                "_freshness": {
                    "tencent_quote": self._freshness_for_date(
                        "tencent_quote", self.quote_date, "16:14:00"
                    )
                },
            }
        }

    def get_cached_list_date(self, code: str) -> dict | None:
        return {"list_date": "2020-01-01", "source": "test_cache"}

    def _eastmoney_fund_flow_minute(self, code: str) -> list[dict]:
        self.minute_flow_calls += 1
        return []

    def _eastmoney_fund_flow_daily(self, code: str) -> list[dict]:
        return []

    def _baidu_daily_kline(self, code: str, lookback: int) -> list[dict]:
        rows = demo_daily_kline(code, lookback)
        rows[-1]["date"] = self.kline_date
        return rows


def test_previous_likely_trade_day_uses_weekday_proxy():
    assert previous_likely_cn_trade_day(date(2026, 5, 28)).isoformat() == "2026-05-27"
    assert previous_likely_cn_trade_day(date(2026, 5, 25)).isoformat() == "2026-05-22"


def test_pre_market_effective_after_close_trade_day_uses_previous_trade_day():
    assert effective_after_close_trade_day(datetime(2026, 5, 25, 0, 30, tzinfo=CN_TZ)).isoformat() == "2026-05-22"
    assert effective_after_close_trade_day(datetime(2026, 5, 25, 15, 30, tzinfo=CN_TZ)).isoformat() == "2026-05-25"
    assert effective_after_close_trade_day(datetime(2026, 5, 23, 15, 30, tzinfo=CN_TZ)).isoformat() == "2026-05-22"
    assert effective_after_close_trade_day(datetime(2026, 5, 24, 15, 30, tzinfo=CN_TZ)).isoformat() == "2026-05-22"
    assert effective_after_close_trade_day(datetime(2026, 5, 25, 10, 0, tzinfo=CN_TZ)) is None


def test_morning_replay_writes_distinct_lineage_output_paths(tmp_path):
    result = _run_replay(tmp_path, datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ))

    assert result["analysis_mode"] == "previous_close_replay"
    assert result["observation_date"] == "2026-05-28"
    assert result["trade_date"] == "2026-05-27"
    assert result["next_trade_date"] == "2026-05-28"
    assert result["replay_as_of_date"] == "2026-05-27"
    assert Path(result["report_path"]).name == "morning_replay_analysis_2026-05-28.md"
    assert Path(result["watchlist_csv"]).name == "morning_replay_watchlist_2026-05-28.csv"
    assert _headers(result["watchlist_csv"]) == MORNING_REPLAY_FIELDS


def test_replay_pre_market_can_produce_formal_rows(tmp_path):
    result = _run_replay(tmp_path, datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ))

    assert result["status"] == "MORNING_REPLAY_READY"
    assert result["candidate_source"] == "live_previous_close_replay"
    assert result["freshness_basis"] == "previous_close_expected"
    assert result["valid_for_trading_observation"] == "YES"
    assert _rows(result["watchlist_csv"])


def test_replay_call_auction_can_produce_formal_rows(tmp_path):
    result = _run_replay(tmp_path, datetime(2026, 5, 28, 9, 20, tzinfo=CN_TZ))

    assert result["status"] == "MORNING_REPLAY_READY"
    assert _rows(result["watchlist_csv"])


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 5, 23, 8, 45, tzinfo=CN_TZ),
        datetime(2026, 5, 28, 9, 31, tzinfo=CN_TZ),
        datetime(2026, 5, 28, 15, 30, tzinfo=CN_TZ),
    ],
)
def test_replay_outside_window_writes_header_only(tmp_path, now):
    result = _run_replay(tmp_path, now)

    assert result["status"] == "NOT_REPLAY_WINDOW"
    assert result["valid_for_trading_observation"] == "NO"
    assert _rows(result["watchlist_csv"]) == []


def test_replay_fallback_writes_no_formal_rows(tmp_path):
    result = run_after_close_analysis(
        mode="live",
        replay_previous_close=True,
        now=datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=ReplayStubClient([demo_quotes()[0]], fallback=True),
    )

    assert result["status"] == "REPLAY_DATA_FALLBACK_DEMO"
    assert result["candidate_source"] == "demo_fallback"
    assert _rows(result["watchlist_csv"]) == []


def test_replay_report_discloses_reconstruction_warning(tmp_path):
    result = _run_replay(tmp_path, datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ))
    text = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "analysis_mode: previous_close_replay" in text
    assert "observation_date: 2026-05-28" in text
    assert "replay_as_of_date: 2026-05-27" in text
    assert "freshness_basis: previous_close_expected" in text
    assert "本观察池基于前一交易日收盘数据在早盘前重建" in text


def test_replay_client_accepts_exact_previous_close_quote_date():
    client = ReplaySourceClient()

    rows = client.get_candidate_quotes()

    assert client.hot_query_dates == ["2026-05-27"]
    assert client.minute_flow_calls == 0
    assert "quote_stale" not in rows[0]["_freshness_reasons"]
    assert "freshness_unknown" not in rows[0]["_freshness_reasons"]
    assert rows[0]["_freshness"]["tencent_quote"]["freshness_basis"] == "previous_close_expected"
    assert client.quality_report.target_date_match_count >= 1


@pytest.mark.parametrize(
    ("quote_date", "reason"),
    [
        ("2026-05-26", "replay_data_too_old"),
        ("2026-05-28", "replay_data_from_observation_day"),
        ("", "timestamp_missing"),
    ],
)
def test_replay_client_rejects_non_target_quote_dates(quote_date, reason):
    client = ReplaySourceClient(quote_date=quote_date)

    row = client.get_candidate_quotes()[0]

    assert "freshness_unknown" in row["_freshness_reasons"]
    assert reason in row["_freshness_reasons"]
    assert client.quality_report.target_date_mismatch_count + client.quality_report.timestamp_missing_count >= 1


def test_replay_client_requires_target_dated_baidu_kline():
    accepted = ReplaySourceClient(kline_date="2026-05-27")
    rejected = ReplaySourceClient(kline_date="2026-05-26")

    accepted.get_daily_kline("300001")
    rejected.get_daily_kline("300001")

    assert accepted.get_kline_freshness_reasons("300001") == []
    assert "freshness_unknown" in rejected.get_kline_freshness_reasons("300001")
    assert "replay_data_too_old" in rejected.get_kline_freshness_reasons("300001")


def test_replay_kline_date_uncertainty_blocks_formal_rows(tmp_path):
    client = ReplayStubClient([demo_quotes()[0]])

    def stale_kline_reasons(code: str) -> list[str]:
        return ["freshness_unknown", "replay_data_too_old"]

    client.get_kline_freshness_reasons = stale_kline_reasons
    result = run_after_close_analysis(
        mode="live",
        replay_previous_close=True,
        now=datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=client,
    )

    assert result["status"] == "REPLAY_DATA_QUALITY_BLOCKED"
    assert _rows(result["watchlist_csv"]) == []


def test_replay_requires_live_mode(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_after_close_analysis.py", "--mode", "demo", "--replay-previous-close"],
    )

    assert main() == 2
    assert "REPLAY_REQUIRES_LIVE_MODE" in capsys.readouterr().out


def test_replay_rejects_date_override(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_after_close_analysis.py",
            "--mode",
            "live",
            "--replay-previous-close",
            "--date",
            "2026-05-27",
        ],
    )

    assert main() == 2
    assert "REPLAY_DATE_OVERRIDE_UNSUPPORTED" in capsys.readouterr().out


def test_replay_does_not_generate_manual_ticket(tmp_path):
    result = _run_replay(tmp_path, datetime(2026, 5, 28, 8, 45, tzinfo=CN_TZ))

    output_root = Path(result["report_path"]).parents[1]
    assert list(output_root.rglob("manual_order_ticket_*.md")) == []


def test_replay_production_modules_do_not_import_trading_execution_code():
    production_files = [
        Path("overnight_quant/data/astock_client.py"),
        Path("overnight_quant/data/live_data_quality.py"),
        Path("overnight_quant/data/market_calendar.py"),
        Path("overnight_quant/strategy/after_close_analysis.py"),
        Path("overnight_quant/reports/after_close_report.py"),
        Path("overnight_quant/scripts/run_after_close_analysis.py"),
    ]
    forbidden_tokens = [
        "pyautogui",
        "selenium",
        "broker api",
        "auto_order",
        "place_order",
        "manual_ticket",
        "order_recorder",
        "position_tracker",
        "YangYongxingOvernightStrategy",
    ]

    for path in production_files:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert not any(token.lower() in lowered for token in forbidden_tokens), path


def _run_replay(tmp_path: Path, now: datetime) -> dict:
    return run_after_close_analysis(
        mode="live",
        replay_previous_close=True,
        now=now,
        config=_tmp_config(tmp_path),
        client=ReplayStubClient([demo_quotes()[0]]),
    )


def _tmp_config(tmp_path: Path) -> dict:
    config = load_config()
    config["paths"]["records_dir"] = str(tmp_path / "real" / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "real" / "reports")
    config["paths"]["examples_dir"] = str(tmp_path / "examples")
    return config


def _rows(path: str) -> list[dict]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _headers(path: str) -> list[str]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle))
