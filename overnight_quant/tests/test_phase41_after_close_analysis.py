import csv
import copy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from overnight_quant.data.demo_data import demo_daily_kline, demo_market_snapshot, demo_quotes
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.reports.after_close_report import WATCHLIST_FIELDS
from overnight_quant.scripts.run_after_close_analysis import run_after_close_analysis
from overnight_quant.strategy.yang_yongxing_overnight import load_config


class StubAfterCloseClient:
    def __init__(
        self,
        mode: str,
        rows: list[dict],
        fallback: bool = False,
        record_kline_source: bool = False,
        fallback_on_kline: bool = False,
    ):
        self.mode = mode
        self.rows = copy.deepcopy(rows)
        self.record_kline_source = record_kline_source
        self.fallback_on_kline = fallback_on_kline
        self.fallback_messages = ["live data failed, fallback to demo"] if fallback else []
        self.quality_report = SimpleNamespace(
            fallback_to_demo=fallback,
            source_status=[],
            warnings=list(self.fallback_messages),
        )

    def get_market_snapshot(self) -> dict:
        return demo_market_snapshot()

    def get_candidate_quotes(self) -> list[dict]:
        return copy.deepcopy(self.rows)

    def get_daily_kline(self, code: str, lookback: int = 120) -> list[dict]:
        if self.record_kline_source:
            self.quality_report.source_status.append({"source": "baidu_daily_kline", "ok": True, "rows": 1})
        if self.fallback_on_kline and "live kline failed, using demo kline" not in self.fallback_messages:
            self.fallback_messages.append("live kline failed, using demo kline")
        return demo_daily_kline(code, lookback)

    def get_kline_freshness_reasons(self, code: str) -> list[str]:
        return []


class StubAfterCloseUniverseClient(StubAfterCloseClient):
    def __init__(self, mode: str, hot_rows: list[dict], universe_rows: list[dict]):
        super().__init__(mode, hot_rows)
        self.universe_rows = copy.deepcopy(universe_rows)
        self.hot_called = False
        self.universe_called = False

    def get_candidate_quotes(self) -> list[dict]:
        self.hot_called = True
        return super().get_candidate_quotes()

    def get_after_close_universe_quotes(self) -> list[dict]:
        self.universe_called = True
        return copy.deepcopy(self.universe_rows)


class StubSinaUniverseClient(StubAfterCloseUniverseClient):
    after_close_candidate_source = "easyquotation_sina_full_market"


class StubRecentThemeClient(StubAfterCloseClient):
    def get_recent_hot_theme_summary(self, limit: int = 10) -> list[dict]:
        return [
            {
                "theme": "Robotics",
                "active_days": 3,
                "count": 8,
                "latest_date": "2026-05-22",
                "trend": "mainline",
            }
        ]


def test_demo_mode_writes_example_outputs_and_labels_demo_only(tmp_path):
    config = _tmp_config(tmp_path)

    result = run_after_close_analysis(
        mode="demo",
        now=datetime(2026, 5, 23, 10, 0, tzinfo=CN_TZ),
        config=config,
        client=StubAfterCloseClient("demo", demo_quotes()),
    )

    assert result["status"] == "DEMO_ANALYSIS"
    assert result["valid_for_trading_observation"] == "DEMO_ONLY"
    assert str(tmp_path / "examples") in result["report_path"]
    assert str(tmp_path / "examples") in result["watchlist_csv"]
    assert _rows(result["watchlist_csv"])


def test_live_non_trading_day_writes_header_only_watchlist(tmp_path):
    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 23, 15, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("live", demo_quotes()),
    )

    assert result["status"] == "NOT_TRADING_DAY"
    assert result["valid_for_trading_observation"] == "NO"
    assert _rows(result["watchlist_csv"]) == []
    assert _headers(result["watchlist_csv"]) == WATCHLIST_FIELDS


def test_live_before_close_writes_header_only_watchlist_and_session(tmp_path):
    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 14, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("live", demo_quotes()),
    )

    assert result["status"] == "NOT_AFTER_CLOSE"
    assert result["session_state"] == "TAIL_SESSION"
    assert result["valid_for_trading_observation"] == "NO"
    assert _rows(result["watchlist_csv"]) == []


def test_live_monday_pre_market_counts_as_previous_friday_after_close(tmp_path):
    config = _tmp_config(tmp_path)
    config["after_close"]["min_a_score"] = 90
    config["after_close"]["min_b_score"] = 70

    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 25, 0, 30, tzinfo=CN_TZ),
        config=config,
        client=StubAfterCloseClient("live", demo_quotes()),
    )

    assert result["status"] == "WATCHLIST_READY"
    assert result["session_state"] == "PRE_MARKET"
    assert result["trade_date"] == "2026-05-22"
    assert result["next_trade_date"] == "2026-05-25"
    assert result["after_close_carryover"] == "YES"
    assert result["observation_date"] == "2026-05-25"
    assert Path(result["report_path"]).name == "after_close_analysis_2026-05-22.md"
    assert "after_close_carryover: YES" in Path(result["report_path"]).read_text(encoding="utf-8")


def test_live_pre_market_current_date_override_is_not_after_close(tmp_path):
    result = run_after_close_analysis(
        mode="live",
        trade_date="2026-05-25",
        now=datetime(2026, 5, 25, 0, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("live", demo_quotes()),
    )

    assert result["status"] == "NOT_AFTER_CLOSE"
    assert result["trade_date"] == "2026-05-25"
    assert _rows(result["watchlist_csv"]) == []


def test_live_fallback_marks_demo_source_and_does_not_emit_formal_rows(tmp_path):
    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("live", demo_quotes(), fallback=True),
    )

    text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert result["status"] == "DATA_FALLBACK_DEMO"
    assert result["candidate_source"] == "demo_fallback"
    assert result["valid_for_trading_observation"] == "NO"
    assert _rows(result["watchlist_csv"]) == []
    assert "candidate_source: demo_fallback" in text


def test_live_kline_demo_fallback_also_blocks_formal_rows(tmp_path):
    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("live", [demo_quotes()[0]], fallback_on_kline=True),
    )

    assert result["status"] == "DATA_FALLBACK_DEMO"
    assert result["candidate_source"] == "demo_fallback"
    assert _rows(result["watchlist_csv"]) == []


def test_live_after_close_reliable_rows_produce_a_b_and_c_categories(tmp_path):
    config = _tmp_config(tmp_path)
    config["after_close"]["min_a_score"] = 90
    config["after_close"]["min_b_score"] = 70
    rows = demo_quotes()
    moderate = copy.deepcopy(rows[0])
    moderate.update({"code": "300010", "name": "Demo Secondary", "theme_tags": ["Secondary"], "theme_rank": 10})
    rows.append(moderate)

    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=config,
        client=StubAfterCloseClient("live", rows),
    )

    assert result["status"] == "WATCHLIST_READY"
    assert result["valid_for_trading_observation"] == "YES"
    assert result["categories"]["A"]
    assert result["categories"]["B"]
    assert result["categories"]["C"]


def test_after_close_prefers_full_market_universe_over_hot_candidates(tmp_path):
    hot = copy.deepcopy(demo_quotes()[0])
    hot["code"] = "300001"
    universe = copy.deepcopy(demo_quotes()[4])
    universe["code"] = "002005"
    client = StubAfterCloseUniverseClient("live", [hot], [universe])

    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=client,
    )

    evaluated_codes = {row["code"] for row in result["evaluated_rows"]}
    assert client.universe_called is True
    assert client.hot_called is False
    assert evaluated_codes == {"002005"}
    assert result["candidate_source"] == "full_market_00_60"


def test_after_close_candidate_source_uses_specific_full_market_adapter(tmp_path):
    universe = copy.deepcopy(demo_quotes()[4])
    universe["code"] = "002005"
    client = StubSinaUniverseClient("live", [], [universe])

    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=client,
    )

    assert result["candidate_source"] == "easyquotation_sina_full_market"


def test_after_close_result_includes_recent_hot_theme_summary(tmp_path):
    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubRecentThemeClient("demo", [demo_quotes()[0]]),
    )

    assert result["recent_hot_themes"][0]["theme"] == "Robotics"
    assert result["recent_hot_themes"][0]["trend"] == "mainline"


def test_hard_risk_never_enters_a_or_b_and_c_is_not_a_trigger_plan(tmp_path):
    rows = copy.deepcopy(demo_quotes())
    for row in rows:
        if row["code"] == "600003":
            row["tail_pullback_pct"] = 3.6
    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", rows),
    )

    ab_codes = {row["code"] for group in ("A", "B") for row in result["categories"][group]}
    c_rows = result["categories"]["C"]
    assert "002002" not in ab_codes
    assert "600003" not in ab_codes
    assert "600006" not in ab_codes
    assert c_rows
    assert all(not row["tomorrow_watch_plan"] for row in c_rows)
    assert all(("只看不碰" in row["invalid_conditions"] or "不建议追" in row["invalid_conditions"]) for row in c_rows)


def test_watchlist_csv_excludes_c_class_risk_rows(tmp_path):
    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", demo_quotes()),
    )

    csv_rows = _rows(result["watchlist_csv"])
    assert result["categories"]["C"]
    assert csv_rows
    assert {row["category"] for row in csv_rows}.issubset({"A", "B"})
    assert not any("limit_up_chase_risk" in row["risk_flags"] for row in csv_rows)


def test_watchlist_csv_is_excel_friendly_utf8_sig(tmp_path):
    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", demo_quotes()),
    )

    assert Path(result["watchlist_csv"]).read_bytes().startswith(b"\xef\xbb\xbf")


def test_a_and_b_have_watch_plan_and_invalid_conditions(tmp_path):
    config = _tmp_config(tmp_path)
    config["after_close"]["min_a_score"] = 100
    config["after_close"]["min_b_score"] = 70
    result = run_after_close_analysis(
        mode="demo",
        config=config,
        client=StubAfterCloseClient("demo", [demo_quotes()[0]]),
    )

    rows = result["categories"]["B"]
    assert rows
    assert rows[0]["tomorrow_watch_plan"]
    assert rows[0]["invalid_conditions"]
    assert "buy" not in rows[0]["tomorrow_watch_plan"].lower()
    assert "观察" in rows[0]["tomorrow_watch_plan"]
    assert "失效" in rows[0]["invalid_conditions"] or "低开" in rows[0]["invalid_conditions"]


def test_after_close_reasons_and_c_risk_copy_are_user_facing_chinese(tmp_path):
    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", demo_quotes()),
    )

    c_rows = result["categories"]["C"]
    assert c_rows
    assert "涨幅" in c_rows[0]["reason"] or "题材" in c_rows[0]["reason"] or "量比" in c_rows[0]["reason"]
    assert "do not chase" not in c_rows[0]["invalid_conditions"].lower()
    assert "只看不碰" in c_rows[0]["invalid_conditions"] or "不建议追" in c_rows[0]["invalid_conditions"]


def test_estimated_and_missing_data_are_labelled(tmp_path):
    estimated = copy.deepcopy(demo_quotes()[0])
    estimated.update(
        {
            "fund_flow_source": "estimated_from_big_order_net",
            "main_net": 1000,
            "theme_tags": [],
            "theme_rank": None,
            "big_order_net": None,
        }
    )
    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", [estimated]),
    )

    row = _all_category_rows(result)[0]
    assert row["estimated_capital_flow"] is True
    assert row["main_net_source"] == "estimated_from_big_order_net"
    assert "estimated_capital_flow" in row["data_quality_flags"]
    assert "theme_missing" in row["data_quality_flags"]
    assert "capital_missing" in row["data_quality_flags"]


def test_after_close_reason_groups_separate_positive_missing_and_risk(tmp_path):
    stock = copy.deepcopy(demo_quotes()[0])
    stock.update(
        {
            "theme_tags": [],
            "theme_rank": None,
            "main_net": None,
            "big_order_net": None,
            "is_limit_up": True,
        }
    )

    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", [stock]),
    )

    row = _all_category_rows(result)[0]
    assert "change_ideal" in row["positive_reason_keys"]
    assert "close_near_high" in row["positive_reason_keys"]
    assert "theme_missing" in row["info_gap_reason_keys"]
    assert "theme_missing" not in row["missing_reason_keys"]
    assert "main_net_missing" in row["missing_reason_keys"]
    assert "big_order_missing" in row["missing_reason_keys"]
    assert "limit_up_chase_risk" in row["risk_reason_keys"]
    assert "change_ideal" not in row["missing_reason_keys"]
    assert "change_ideal" not in row["risk_reason_keys"]
    assert len(row["positive_reason_keys"]) == len(set(row["positive_reason_keys"]))


def test_theme_missing_is_info_gap_not_c_risk_reason(tmp_path):
    stock = copy.deepcopy(demo_quotes()[0])
    stock.update({"theme_tags": [], "theme_rank": None})

    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", [stock]),
    )

    row = _all_category_rows(result)[0]
    assert "theme_missing" in row["info_gap_reason_keys"]
    assert "theme_missing" not in row["missing_reason_keys"]
    assert "theme_missing" not in row["risk_reason_keys"]
    assert "题材信息缺失" not in row["risk_reasons"]


def test_one_day_hot_theme_is_risk_observation_not_formal_watchlist(tmp_path):
    stock = copy.deepcopy(demo_quotes()[0])
    stock.update(
        {
            "theme_tags": ["New One Day Theme"],
            "theme_rank": 1,
            "same_theme_strong_count": 1,
            "theme_active_days": 1,
            "theme_rotation_state": "new_or_one_day",
        }
    )
    config = _tmp_config(tmp_path)
    config["after_close"]["min_a_score"] = 70
    config["after_close"]["min_b_score"] = 60

    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=config,
        client=StubAfterCloseClient("live", [stock]),
    )

    assert result["categories"]["A"] == []
    assert result["categories"]["B"] == []
    assert result["categories"]["C"]
    row = result["categories"]["C"][0]
    assert "theme_one_day_risk" in row["risk_flags"]
    assert "theme_one_day_risk" in row["risk_reason_keys"]


def test_mainline_weak_board_relative_strength_can_remain_formal_watchlist(tmp_path):
    stock = copy.deepcopy(demo_quotes()[0])
    stock.update(
        {
            "theme_tags": ["Robotics"],
            "theme_rank": 1,
            "same_theme_strong_count": 4,
            "theme_active_days": 4,
            "theme_rotation_state": "mainline",
            "theme_block_change_pct": -2.4,
            "change_pct": 5.2,
        }
    )
    config = _tmp_config(tmp_path)
    config["after_close"]["min_a_score"] = 70
    config["after_close"]["min_b_score"] = 60

    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=config,
        client=StubAfterCloseClient("live", [stock]),
    )

    formal = result["categories"]["A"] + result["categories"]["B"]
    assert formal
    row = formal[0]
    assert row["theme_market_state"] == "mainline_pullback_relative_strength"
    assert "theme_rotation_risk" not in row["risk_flags"]
    assert "theme_relative_strength_in_pullback" in row["positive_reason_keys"]


def test_weak_non_mainline_board_is_rotation_risk_even_if_stock_rises(tmp_path):
    stock = copy.deepcopy(demo_quotes()[0])
    stock.update(
        {
            "theme_tags": ["Short Theme"],
            "theme_rank": 4,
            "same_theme_strong_count": 1,
            "theme_active_days": 1,
            "theme_rotation_state": "unconfirmed",
            "theme_block_change_pct": -2.2,
            "change_pct": 5.2,
        }
    )
    config = _tmp_config(tmp_path)
    config["after_close"]["min_a_score"] = 70
    config["after_close"]["min_b_score"] = 60

    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=config,
        client=StubAfterCloseClient("live", [stock]),
    )

    assert result["categories"]["A"] == []
    assert result["categories"]["B"] == []
    assert result["categories"]["C"]
    row = result["categories"]["C"][0]
    assert row["theme_market_state"] == "rotation_risk"
    assert "theme_rotation_risk" in row["risk_flags"]
    assert "theme_rotation_risk" in row["risk_reason_keys"]


def test_hard_risk_flags_are_exposed_as_risk_reasons(tmp_path):
    stock = copy.deepcopy(demo_quotes()[0])
    stock.update({"name": "ST Demo", "is_st": True})

    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", [stock]),
    )

    row = result["categories"]["C"][0]
    assert "st_stock" in row["risk_flags"]
    assert "st_stock" in row["risk_reason_keys"]
    assert "change_ideal" not in row["risk_reason_keys"]


def test_live_critical_quality_on_observable_candidate_blocks_formal_watchlist(tmp_path):
    stale = copy.deepcopy(demo_quotes()[0])
    stale["_freshness_reasons"] = ["quote_stale"]
    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("live", [stale]),
    )

    assert result["status"] == "DATA_QUALITY_BLOCKED"
    assert result["valid_for_trading_observation"] == "NO"
    assert _rows(result["watchlist_csv"]) == []


def test_live_safety_unknown_on_observable_candidate_blocks_formal_watchlist(tmp_path):
    unknown = copy.deepcopy(demo_quotes()[0])
    unknown["_risk_unknown_reasons"] = ["st_status_unknown"]
    result = run_after_close_analysis(
        mode="live",
        now=datetime(2026, 5, 22, 15, 30, tzinfo=CN_TZ),
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("live", [unknown]),
    )

    assert result["status"] == "DATA_QUALITY_BLOCKED"
    assert _rows(result["watchlist_csv"]) == []


def test_reports_disclose_proxy_calendar_risk_warning_and_never_create_ticket(tmp_path):
    config = _tmp_config(tmp_path)
    result = run_after_close_analysis(
        mode="demo",
        config=config,
        client=StubAfterCloseClient("demo", demo_quotes()),
    )
    text = Path(result["report_path"]).read_text(encoding="utf-8")

    assert "next_trade_date_calendar: weekday_proxy" in text
    assert "本报告仅用于观察计划" in text
    assert not list(tmp_path.rglob("manual_order_ticket_*.md"))


def test_report_quality_includes_sources_recorded_during_kline_scoring(tmp_path):
    result = run_after_close_analysis(
        mode="demo",
        config=_tmp_config(tmp_path),
        client=StubAfterCloseClient("demo", [demo_quotes()[0]], record_kline_source=True),
    )

    text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "baidu_daily_kline: OK" in text


def test_new_production_modules_contain_no_execution_or_click_implementation():
    root = Path(__file__).resolve().parents[1]
    modules = [
        root / "strategy" / "after_close_analysis.py",
        root / "reports" / "after_close_report.py",
        root / "scripts" / "run_after_close_analysis.py",
    ]
    content = "\n".join(path.read_text(encoding="utf-8", errors="ignore").lower() for path in modules)
    forbidden = ["pyautogui", "selenium", "broker api", "auto" + "_order", "place" + "_order"]
    assert not any(term in content for term in forbidden)
    forbidden_coupling = ["yangyongxingovernightstrategy", "manual_ticket", "order_recorder", "position_tracker"]
    assert not any(term in content for term in forbidden_coupling)


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


def _all_category_rows(result: dict) -> list[dict]:
    return [row for key in ("A", "B", "C") for row in result["categories"][key]]
