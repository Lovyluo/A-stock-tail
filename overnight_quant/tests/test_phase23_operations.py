import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.demo_data import demo_quotes
from overnight_quant.scripts.run_preflight import run_preflight
from overnight_quant.scripts import run_scan as run_scan_script
from overnight_quant.strategy.yang_yongxing_overnight import YangYongxingOvernightStrategy, load_config


CN = timezone(timedelta(hours=8))


def test_preflight_report_generation(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 23, 14, 30, tzinfo=CN))

    result = run_preflight(config=config, client=client, now=client.now, trade_date="2026-05-23")

    report = Path(result["report_path"])
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "PRE_MARKET" not in text
    assert "session_state: NON_TRADING_DAY" in text
    assert result["status"] == "NON_TRADING_DAY"


def test_dry_run_does_not_generate_manual_ticket(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22", dry_run=True)

    assert result["selected"] == []
    assert result["tickets"] == []
    assert result["dry_run_selected"]
    assert not list((tmp_path / "reports").glob("manual_order_ticket_*.md"))


def test_dry_run_still_generates_signals_quality_and_report(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22", dry_run=True)

    assert Path(result["signals_csv"]).exists()
    assert Path(result["quality_report_path"]).exists()
    dry_run_report = Path(result["dry_run_report_path"])
    assert dry_run_report.exists()
    assert "DRY RUN ONLY" in dry_run_report.read_text(encoding="utf-8")


def test_signals_csv_contains_only_risk_approved_dry_run_candidates(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))
    good = _live_good_quote(datetime(2026, 5, 22, 14, 30, tzinfo=CN))
    bad = {**good, "code": "600777", "name": "*ST新潮", "is_st": True, "change_pct": 5.04}
    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [bad, good])
    monkeypatch.setattr(client, "_tencent_quotes", lambda codes: {bad["code"]: bad, good["code"]: good})

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22", dry_run=True)

    signal_rows = _csv_rows(result["signals_csv"])
    rejection_rows = _csv_rows(result["signal_rejections_csv"])
    assert [row["code"] for row in signal_rows] == [good["code"]]
    assert {row["decision"] for row in signal_rows} == {"BUY_CANDIDATE"}
    assert all("st_stock" not in row["risk_flags"] for row in signal_rows)
    assert [row["code"] for row in rejection_rows] == [bad["code"]]
    assert "st_stock" in rejection_rows[0]["risk_flags"]


def test_signal_csv_files_are_excel_friendly_utf8_sig(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22", dry_run=True)

    assert Path(result["signals_csv"]).read_bytes().startswith(b"\xef\xbb\xbf")
    assert Path(result["signal_rejections_csv"]).read_bytes().startswith(b"\xef\xbb\xbf")


def test_dry_run_report_labels_demo_fallback_candidates_as_not_valid_for_observation(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))
    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [])

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22", dry_run=True)

    text = Path(result["dry_run_report_path"]).read_text(encoding="utf-8")
    assert result["tickets"] == []
    assert "candidate_source: demo_fallback" in text
    assert "live_candidate_count: 0" in text
    assert "demo_candidate_count: 9" in text
    assert "valid_for_trading_observation: NO" in text


def test_dry_run_report_labels_live_candidates_as_valid_for_observation(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22", dry_run=True)

    text = Path(result["dry_run_report_path"]).read_text(encoding="utf-8")
    assert result["tickets"] == []
    assert "candidate_source: live" in text
    assert "live_candidate_count: 1" in text
    assert "demo_candidate_count: 0" in text
    assert "valid_for_trading_observation: YES" in text


def test_run_scan_console_prints_candidate_source(monkeypatch, capsys):
    monkeypatch.setattr(
        run_scan_script,
        "run_scan",
        lambda **kwargs: {
            "market_gate": {"pass": False, "reasons": [], "reject_reasons": ["outside_tail_session"]},
            "candidate_count": 9,
            "candidate_source": "demo_fallback",
            "selected": [],
            "rejected": [],
            "fallback_messages": [],
            "signals_csv": "signals.csv",
            "quality_report_path": "",
            "dry_run_report_path": "",
            "scan_summary_path": "",
        },
    )
    monkeypatch.setattr("sys.argv", ["run_scan.py", "--mode", "live", "--dry-run"])

    assert run_scan_script.main() == 0
    assert "Candidate Source: demo_fallback" in capsys.readouterr().out


def test_live_dry_run_console_explains_why_buy_ticket_is_not_generated(monkeypatch, capsys):
    monkeypatch.setattr(
        run_scan_script,
        "run_scan",
        lambda **kwargs: {
            "market_gate": {"pass": True, "reasons": ["market_ok"], "reject_reasons": []},
            "candidate_count": 1,
            "candidate_source": "live",
            "valid_for_trading_observation": True,
            "selected": [],
            "dry_run_selected": [{"code": "002001", "name": "Demo Robotics", "total_score": 88}],
            "tickets": [],
            "rejected": [],
            "fallback_messages": [],
            "signals_csv": "signals.csv",
            "quality_report_path": "",
            "dry_run_report_path": "",
            "scan_summary_path": "",
        },
    )
    monkeypatch.setattr("sys.argv", ["run_scan.py", "--mode", "live", "--dry-run"])

    assert run_scan_script.main() == 0
    output = capsys.readouterr().out
    assert "Buy Ticket Not Generated:" in output
    assert "- dry_run_only:" in output


def test_live_scan_summary_generation(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")

    summary = Path(result["scan_summary_path"])
    assert summary.exists()
    text = summary.read_text(encoding="utf-8")
    assert "live_scan_summary" in summary.name
    assert "final_advice: BUY" in text
    assert "ticket_generated: YES" in text


def test_live_tail_scan_summary_explains_why_buy_ticket_is_not_generated(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    config["strategy"]["min_total_score"] = 999
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")

    summary = Path(result["scan_summary_path"])
    text = summary.read_text(encoding="utf-8")
    assert "ticket_generated: NO" in text
    assert "## Buy Ticket Not Generated Reasons" in text
    assert "score_below_threshold" in text


def test_live_tail_scan_summary_lists_limit_up_watch_only_rows(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_client(monkeypatch, tmp_path, datetime(2026, 5, 22, 14, 30, tzinfo=CN))
    limit_up = _live_good_quote(datetime(2026, 5, 22, 14, 30, tzinfo=CN))
    limit_up.update(
        {
            "code": "002777",
            "name": "Hot Limit",
            "price": 22.0,
            "limit_up": 22.0,
            "change_pct": 10.0,
            "is_limit_up": True,
        }
    )
    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [limit_up])
    monkeypatch.setattr(client, "_tencent_quotes", lambda codes: {limit_up["code"]: limit_up})

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")

    text = Path(result["scan_summary_path"]).read_text(encoding="utf-8")
    assert "## Watch Only / Do Not Chase" in text
    assert "002777 Hot Limit" in text
    assert "limit_up_unavailable" in text


def test_estimated_capital_flow_is_labeled():
    config = load_config()
    stock = {**demo_quotes()[0], "fund_flow_source": "estimated_from_big_order_net", "main_net": 1000}
    scored = __import__("overnight_quant.strategy.scoring", fromlist=["score_stock"]).score_stock(stock, [], 90, config)

    assert scored["estimated_capital_flow"] is True
    assert scored["main_net_source"] == "estimated_from_big_order_net"
    assert "capital_score_source:estimated_from_big_order_net" in scored["score_reasons"]


def test_no_automatic_trading_code_exists():
    root = Path(__file__).resolve().parents[1]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*.py")
        if "tests" not in path.parts
    )
    forbidden = ["pyautogui", "selenium", "broker api", "auto" + "_order", "place" + "_order"]
    assert not any(token in text.lower() for token in forbidden)


def _tmp_config(tmp_path):
    config = load_config()
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    return config


def _patched_client(monkeypatch, tmp_path, now):
    client = AStockClient("live", now=now, cache_dir=tmp_path / "cache")
    good = _live_good_quote(now)
    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [good])
    monkeypatch.setattr(client, "_tencent_quotes", lambda codes: {good["code"]: good})
    monkeypatch.setattr(client, "_eastmoney_quote_meta", lambda codes: {})
    monkeypatch.setattr(client, "_eastmoney_stock_info", lambda code: {"f189": "20200102", "f127": "Test"})
    monkeypatch.setattr(client, "_eastmoney_fund_flow_minute", lambda code: [{"main_net": 1000, "large_net": 500}])
    monkeypatch.setattr(client, "_eastmoney_fund_flow_daily", lambda code: [])
    monkeypatch.setattr(client, "_baidu_daily_kline", lambda code, lookback: [_daily_bar(now.date().isoformat())])
    monkeypatch.setattr(client, "_hsgt_realtime", lambda: [{"time": "14:30", "hgt_yi": 5, "sgt_yi": 4}])
    return client


def _live_good_quote(now):
    good = dict(demo_quotes()[0])
    good.update(
        {
            "code": "002001",
            "list_date": "2020-01-02",
            "is_new_stock": False,
            "_sources": {key: "unit.live" for key in good},
            "_freshness": {
                "tencent_quote": {
                    "source": "tencent_quote",
                    "data_date": now.date().isoformat(),
                    "data_time": now.strftime("%H:%M:%S"),
                    "is_stale": False,
                    "stale_reason": "",
                }
            },
        }
    )
    return good


def _daily_bar(data_date):
    return {
        "date": data_date,
        "open": 18.0,
        "close": 18.5,
        "high": 18.7,
        "low": 17.9,
        "volume": 1000,
        "amount": 10000,
        "ma5": 18.2,
        "ma10": 18.0,
        "ma20": 17.8,
    }


def _csv_rows(path: str) -> list[dict]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
