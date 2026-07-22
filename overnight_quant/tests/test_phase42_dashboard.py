import importlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_parse_key_value_md_reads_top_level_fields(tmp_path):
    from overnight_quant.ui.result_parser import parse_key_value_md

    report = tmp_path / "preflight_2026-06-01.md"
    report.write_text(
        "# Preflight\n\nstatus: READY_FOR_LIVE_SCAN\ntrade_date: 2026-06-01\nsession_state: PRE_MARKET\n\n## Details\nignored: true\n",
        encoding="utf-8",
    )

    parsed = parse_key_value_md(report)

    assert parsed["status"] == "READY_FOR_LIVE_SCAN"
    assert parsed["trade_date"] == "2026-06-01"
    assert parsed["session_state"] == "PRE_MARKET"
    assert "ignored" not in parsed


def test_parse_missing_report_returns_clear_status(tmp_path):
    from overnight_quant.ui.result_parser import parse_preflight_report

    parsed = parse_preflight_report(tmp_path / "missing.md")

    assert parsed["status"] == "MISSING"
    assert parsed["report_type"] == "preflight"


def test_empty_watchlist_csv_loads_with_header(tmp_path):
    from overnight_quant.ui.result_parser import parse_watchlist_csv

    csv_path = tmp_path / "next_morning_watchlist_2026-06-01.csv"
    csv_path.write_text("trade_date,code,name,category\n", encoding="utf-8")

    table = parse_watchlist_csv(csv_path)

    assert table.empty
    assert list(table.columns) == ["trade_date", "code", "name", "category"]


def test_after_close_risk_table_is_parsed_from_report(tmp_path):
    from overnight_quant.ui.result_parser import parse_after_close_risk_table

    report = tmp_path / "after_close_analysis_2026-06-03.md"
    report.write_text(
        "# After-Close Analysis Report\n\n"
        "status: WATCHLIST_READY\n\n"
        "## 5. C Class Risk Observation / Do Not Chase\n\n"
        "| code | name | score | reason | invalid_conditions |\n"
        "|---|---|---:|---|---|\n"
        "| 603311 | 金海高科 | 87.25 | limit_up_chase_risk, capital_missing | Risk observation only; do not chase. |\n"
        "| 600381 | *ST春天 | 79.75 | st_stock, limit_up_chase_risk | Risk observation only; do not chase. |\n"
        "\n## 6. Next-Morning Overall Playbook\n",
        encoding="utf-8",
    )

    table = parse_after_close_risk_table(report)

    assert not table.empty
    assert list(table.columns) == ["code", "name", "score", "reason", "invalid_conditions"]
    rows = table.to_dict("records")
    assert rows[0]["code"] == "603311"
    assert rows[0]["name"] == "金海高科"
    assert "limit_up_chase_risk" in rows[0]["reason"]


def test_after_close_risk_table_parses_chinese_report_heading(tmp_path):
    from overnight_quant.ui.result_parser import parse_after_close_risk_table

    report = tmp_path / "after_close_analysis_2026-06-03.md"
    report.write_text(
        "# 盘后观察池报告\n\n"
        "status: WATCHLIST_READY\n\n"
        "## 5. C类风险观察 / 不建议追\n\n"
        "| 代码 | 名称 | 评分 | 风险原因 | 失效条件 |\n"
        "|---|---|---:|---|---|\n"
        "| 600172 | 黄河旋风 | 55.5 | 题材信息缺失 | 只看不碰。 |\n"
        "\n## 6. 次日早盘总策略\n",
        encoding="utf-8",
    )

    table = parse_after_close_risk_table(report)

    assert not table.empty
    rows = table.to_dict("records")
    assert rows == [
        {
            "代码": "600172",
            "名称": "黄河旋风",
            "评分": "55.5",
            "风险原因": "题材信息缺失",
            "失效条件": "只看不碰。",
        }
    ]


def test_parse_dry_run_candidate_source(tmp_path):
    from overnight_quant.ui.result_parser import parse_dry_run_report

    report = tmp_path / "dry_run_scan_2026-06-01.md"
    report.write_text(
        "candidate_source: demo_fallback\nlive_candidate_count: 0\ndemo_candidate_count: 9\nvalid_for_trading_observation: NO\n",
        encoding="utf-8",
    )

    parsed = parse_dry_run_report(report)

    assert parsed["candidate_source"] == "demo_fallback"
    assert parsed["valid_for_trading_observation"] == "NO"


def test_find_latest_file_uses_newest_mtime(tmp_path):
    from overnight_quant.ui.result_parser import find_latest_file

    first = tmp_path / "dry_run_scan_2026-05-31.md"
    second = tmp_path / "dry_run_scan_2026-06-01.md"
    first.write_text("status: old\n", encoding="utf-8")
    second.write_text("status: new\n", encoding="utf-8")

    assert find_latest_file("dry_run_scan_*.md", tmp_path) == second


def test_command_whitelist_contains_only_approved_actions():
    from overnight_quant.ui.dashboard import APPROVED_ACTIONS

    assert set(APPROVED_ACTIONS) == {
        "preflight",
        "news_live",
        "auction_live",
        "intraday_live",
        "live_dry_run",
        "formal_live_scan",
        "after_close_live",
        "morning_replay_live",
        "sell_plan_live",
        "demo_intraday",
        "demo_auction",
        "demo_news",
        "demo_after_close",
        "demo_scan",
    }


def test_dashboard_defaults_to_chinese_live_reference_mode():
    from overnight_quant.ui.dashboard import DEFAULT_LANGUAGE, DEFAULT_MODE, t

    assert DEFAULT_LANGUAGE == "zh"
    assert DEFAULT_MODE == "live"
    assert t("zh", "app_title") == "A股隔夜量化观察台"
    assert t("en", "app_title") == "A-Share Overnight Quant Dashboard"


def test_action_labels_are_localized_and_demo_is_marked_as_demo():
    from overnight_quant.ui.dashboard import action_label

    assert action_label("zh", "live_dry_run") == "Live Dry-run"
    assert action_label("zh", "formal_live_scan") == "正式 Live 尾盘扫描"
    assert "演示" in action_label("zh", "demo_scan")
    assert "Demo" in action_label("en", "demo_scan")


def test_dashboard_safety_notice_is_bilingual():
    from overnight_quant.ui.dashboard import safety_notice

    assert "不会自动下单" in safety_notice("zh")
    assert "does not place orders" in safety_notice("en")


def test_status_badge_color_contract():
    from overnight_quant.ui.dashboard import status_badge

    assert status_badge("WATCHLIST_READY")["tone"] == "green"
    assert status_badge("MORNING_REPLAY_READY")["tone"] == "green"
    assert status_badge("DATA_FALLBACK_DEMO")["tone"] == "red"
    assert status_badge("MISSING")["tone"] == "gray"


def test_dashboard_css_contains_card_and_primary_button_styles():
    from overnight_quant.ui.dashboard import DASHBOARD_CSS

    assert ".oq-card" in DASHBOARD_CSS
    assert ".oq-action-grid" in DASHBOARD_CSS
    assert "border-radius" in DASHBOARD_CSS


def test_build_status_conclusion_blocks_demo_fallback_as_not_reference():
    from overnight_quant.ui.dashboard import build_status_conclusion

    state = {"dry_run": {"candidate_source": "demo_fallback"}, "preflight": {}}

    assert "不能作为实盘参考" in build_status_conclusion(state, language="zh")
    assert "not valid for live reference" in build_status_conclusion(state, language="en")


def test_live_reference_summary_distinguishes_demo_and_live():
    from overnight_quant.ui.dashboard import live_reference_summary

    assert live_reference_summary({"mode": "live", "dry_run": {"candidate_source": "live"}})["valid"] is True
    assert live_reference_summary({"mode": "demo", "dry_run": {"candidate_source": "demo"}})["valid"] is False
    assert live_reference_summary({"mode": "live", "dry_run": {"candidate_source": "demo_fallback"}})["tone"] == "red"


def test_inline_result_sections_show_values_without_paths():
    from overnight_quant.ui.dashboard import build_inline_result_sections

    state = {
        "preflight": {
            "status": "READY_FOR_LIVE_SCAN",
            "session_state": "PRE_MARKET",
            "path": "reports/preflight.md",
        },
        "dry_run": {
            "candidate_source": "live",
            "final_advice": "NO_TRADE",
            "valid_for_trading_observation": "NO",
            "path": "reports/dry_run.md",
        },
        "after_close": {"status": "WATCHLIST_READY", "path": "reports/after_close.md"},
        "morning_replay": {"status": "MORNING_REPLAY_READY", "path": "reports/replay.md"},
        "sell_plan": {"status": "NO_OPEN_POSITION", "path": "reports/sell_plan.md"},
        "lifecycle": {"status": "MISSING", "path": "reports/lifecycle.md"},
        "trade_review": {"status": "MISSING", "path": "reports/review.md"},
    }

    sections = build_inline_result_sections(state, "en")

    assert sections["preflight"][0] == ("Status", "READY_FOR_LIVE_SCAN")
    assert ("Session", "PRE_MARKET") in sections["preflight"]
    assert ("Candidate Source", "live") in sections["dry_run"]
    assert "reports/preflight.md" not in str(sections)


def test_audit_file_rows_keep_paths_separate():
    from overnight_quant.ui.dashboard import audit_file_rows

    rows = audit_file_rows(
        {"preflight": {"path": "reports/preflight.md"}, "dry_run": {"path": "reports/dry_run.md"}},
        "en",
    )

    assert ("Preflight", "reports/preflight.md") in rows
    assert ("Live Dry-run", "reports/dry_run.md") in rows


def test_table_result_summary_reports_row_counts(tmp_path):
    from overnight_quant.ui.dashboard import table_result_summary
    from overnight_quant.ui.result_parser import parse_watchlist_csv

    csv_path = tmp_path / "watchlist.csv"
    csv_path.write_text("code,name,category\n300001,Demo,A\n300002,Demo2,B\n", encoding="utf-8")
    table = parse_watchlist_csv(csv_path)

    assert table_result_summary(table, "en") == "2 rows"
    assert table_result_summary(table, "zh") == "2 行"


def test_dashboard_localizes_after_close_table_columns_for_chinese():
    from overnight_quant.ui.dashboard import localized_table_records
    from overnight_quant.ui.result_parser import SimpleTable

    table = SimpleTable(
        [
            {
                "code": "603311",
                "name": "金海高科",
                "score": "87.25",
                "reason": "涨幅偏大，涨停追高风险",
                "invalid_conditions": "只看不碰；若继续走弱则移除观察。",
            }
        ],
        ["code", "name", "score", "reason", "invalid_conditions"],
    )

    rows = localized_table_records(table, "zh")

    assert rows == [
        {
            "代码": "603311",
            "名称": "金海高科",
            "评分": "87.25",
            "观察理由": "涨幅偏大，涨停追高风险",
            "失效条件": "只看不碰；若继续走弱则移除观察。",
        }
    ]


def test_dashboard_localizes_tail_reason_values_for_chinese():
    from overnight_quant.ui.dashboard import localized_table_records
    from overnight_quant.ui.result_parser import SimpleTable

    table = SimpleTable(
        [
            {
                "code": "600100",
                "name": "测试股份",
                "decision": "REJECT",
                "risk_flags": "tail_pullback_too_large|capital_outflow",
                "main_net_source": "sina_money_flow_current",
                "capital_score_source": "capital_score_source:eastmoney_fund_flow_minute",
                "estimated_capital_flow": "False",
                "reasons": "price_ok|change_pct_above_max|capital_score_source:eastmoney_fund_flow_minute",
            }
        ],
        ["code", "name", "decision", "risk_flags", "main_net_source", "capital_score_source", "estimated_capital_flow", "reasons"],
    )

    rows = localized_table_records(table, "zh")

    assert rows[0]["决策"] == "风险排除"
    assert rows[0]["风险标记"] == "尾盘回落过大；资金流出"
    assert rows[0]["主力净额来源"] == "新浪实时资金流"
    assert rows[0]["资金评分来源"] == "资金评分来源：东财分钟资金流"
    assert rows[0]["资金是否估算"] == "否"
    assert rows[0]["理由"] == "价格在策略范围内；涨幅高于上限；资金评分来源：东财分钟资金流"


def test_dashboard_output_directories_are_audit_only_copy():
    from overnight_quant.ui.dashboard import t

    assert t("en", "audit_artifacts") == "Audit Artifacts"
    assert "审计" in t("zh", "audit_artifacts")


def test_premium_dashboard_css_has_dark_financial_terminal_theme():
    from overnight_quant.ui.dashboard import DASHBOARD_CSS

    assert "--oq-bg" in DASHBOARD_CSS
    assert "#07111f" in DASHBOARD_CSS
    assert "--oq-lime" in DASHBOARD_CSS
    assert "backdrop-filter" in DASHBOARD_CSS
    assert ":hover" in DASHBOARD_CSS


def test_premium_tabs_include_tail_and_sell_plan():
    from overnight_quant.ui.dashboard import premium_tab_labels

    assert premium_tab_labels("en") == ["Today", "News", "Auction", "Intraday", "Tail Observation", "Positions / Sell Plan", "Audit / Maintenance"]


def test_risk_badge_html_uses_status_tone_classes():
    from overnight_quant.ui.dashboard import render_badge_html

    assert "oq-badge-green" in render_badge_html("PASS")
    assert "oq-badge-red" in render_badge_html("DATA_FALLBACK_DEMO")
    assert "oq-badge-yellow" in render_badge_html("NO_TRADE")


def test_hero_conclusion_contains_direct_result_copy():
    from overnight_quant.ui.dashboard import hero_conclusion

    state = {
        "conclusion": "Current output is dry-run only.",
        "dry_run": {"candidate_source": "live", "valid_for_trading_observation": "NO"},
        "reference_summary": {"reason": "dry_run_only", "tone": "yellow"},
    }

    result = hero_conclusion(state, "en")

    assert "Current output is dry-run only." in result["headline"]
    assert result["candidate_source"] == "live"
    assert result["validity"] == "NO"


def test_tail_observation_rows_exclude_limit_up_rejections(tmp_path):
    from overnight_quant.ui.dashboard import split_tail_signal_rows
    from overnight_quant.ui.result_parser import parse_signals_csv

    csv_path = tmp_path / "signals.csv"
    csv_path.write_text(
        "code,name,decision,total_score,risk_flags,reasons\n"
        "600011,LimitUp,REJECT,71.3,limit_up_unavailable,limit_up_unavailable|change_pct_above_max\n"
        "300001,Valid,BUY_CANDIDATE,82.5,,price_ok|change_pct_ok|tail_stable\n",
        encoding="utf-8",
    )

    observable, rejected = split_tail_signal_rows(parse_signals_csv(csv_path))

    assert [row["code"] for row in observable.to_dict("records")] == ["300001"]
    assert [row["code"] for row in rejected.to_dict("records")] == ["600011"]


def test_tail_observation_rows_exclude_filter_rejections_even_if_score_high(tmp_path):
    from overnight_quant.ui.dashboard import split_tail_signal_rows
    from overnight_quant.ui.result_parser import parse_signals_csv

    csv_path = tmp_path / "signals.csv"
    csv_path.write_text(
        "code,name,decision,total_score,risk_flags,reasons\n"
        "300002,TooHot,BUY_CANDIDATE,80.1,,change_pct_above_max|tail_stable\n",
        encoding="utf-8",
    )

    observable, rejected = split_tail_signal_rows(parse_signals_csv(csv_path))

    assert observable.empty
    assert [row["code"] for row in rejected.to_dict("records")] == ["300002"]


def test_tail_main_risk_rows_hide_hard_exclusions(tmp_path):
    from overnight_quant.ui.dashboard import split_tail_rejection_rows
    from overnight_quant.ui.result_parser import parse_signals_csv

    csv_path = tmp_path / "signal_rejections.csv"
    csv_path.write_text(
        "code,name,decision,total_score,risk_flags,reasons\n"
        "600777,*ST新潮,REJECT,50.0,st_stock,st_stock|amount_wan_below_min\n"
        "600011,涨停样例,REJECT,71.3,limit_up_unavailable,limit_up_unavailable|change_pct_above_max\n"
        "600100,分数不足,REJECT,66.0,score_below_min,score_below_min|theme_missing\n",
        encoding="utf-8",
    )

    main_risk, hard_excluded = split_tail_rejection_rows(parse_signals_csv(csv_path))

    assert [row["code"] for row in main_risk.to_dict("records")] == ["600100"]
    assert [row["code"] for row in hard_excluded.to_dict("records")] == ["600777", "600011"]


def test_render_table_or_empty_converts_simple_table_to_records():
    from overnight_quant.ui.dashboard import render_table_or_empty
    from overnight_quant.ui.result_parser import SimpleTable

    class FakeStreamlit:
        def __init__(self):
            self.captions = []
            self.dataframes = []

        def caption(self, text):
            self.captions.append(text)

        def dataframe(self, data, use_container_width=False):
            self.dataframes.append((data, use_container_width))

    fake = FakeStreamlit()
    table = SimpleTable([{"code": "600011", "decision": "REJECT"}], ["code", "decision"])

    render_table_or_empty(fake, table, "zh", "empty")

    assert fake.captions == ["1 行"]
    assert fake.dataframes == [([{"代码": "600011", "决策": "风险排除"}], True)]


def test_command_runner_uses_shell_false(monkeypatch):
    from overnight_quant.ui.dashboard import run_approved_action

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_approved_action("preflight", timeout=12)

    assert result["ok"] is True
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == 12
    assert captured["kwargs"]["capture_output"] is True


def test_position_update_runner_uses_shell_false(monkeypatch):
    from overnight_quant.ui.dashboard import run_position_update_action

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "[Manual Position Update]\nStatus: RECORDED\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_position_update_action(
        code="300001",
        name="Demo Robotics",
        side="BUY",
        price=18.5,
        qty=200,
        trade_time="2026-05-23 14:52:00",
        notes="seed position",
        stop_loss_price=17.95,
    )

    assert result["ok"] is True
    command = " ".join(captured["args"])
    assert "run_record_order.py" in command
    assert "--position-update" in captured["args"]
    assert "--state" in captured["args"]
    assert "real" in captured["args"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["capture_output"] is True


def test_position_update_feedback_summarizes_rejection_reason():
    from overnight_quant.ui.dashboard import position_update_feedback

    feedback = position_update_feedback(
        {"ok": False, "returncode": 2, "stdout": "Reasons:\n- sell_qty_exceeds_open_position\n", "stderr": ""},
        "zh",
    )

    assert feedback["ok"] is False
    assert "sell_qty_exceeds_open_position" in feedback["message"]


def test_formal_live_scan_blocks_outside_tail_without_running_command(monkeypatch):
    from overnight_quant.ui.dashboard import run_dashboard_action

    def fail_run(*args, **kwargs):
        raise AssertionError("formal live should not run outside tail session")

    monkeypatch.setattr(subprocess, "run", fail_run)

    result = run_dashboard_action(
        "formal_live_scan",
        "zh",
        now=datetime(2026, 6, 3, 16, 30, tzinfo=timezone(timedelta(hours=8))),
    )

    assert result["ok"] is False
    assert result["command_ran"] is False
    assert result["error"] == "FORMAL_LIVE_OUTSIDE_TAIL_SESSION"
    assert "当前不是尾盘窗口" in result["message"]


def test_formal_live_scan_runs_during_tail_with_shell_false(monkeypatch):
    from overnight_quant.ui.dashboard import run_dashboard_action

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "[Overnight Quant Signal]\nRaw terminal text\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_dashboard_action(
        "formal_live_scan",
        "zh",
        now=datetime(2026, 6, 3, 14, 30, tzinfo=timezone(timedelta(hours=8))),
    )

    assert result["ok"] is True
    assert result["command_ran"] is True
    assert "--dry-run" not in captured["args"]
    assert captured["kwargs"]["shell"] is False
    assert "[Overnight Quant Signal]" not in result["message"]


def test_action_feedback_hides_raw_stdout():
    from overnight_quant.ui.dashboard import action_feedback

    result = action_feedback(
        "live_dry_run",
        {"ok": True, "stdout": "[Overnight Quant Signal]\nMode: live\nDry Run: YES\n", "stderr": ""},
        "zh",
    )

    assert result["ok"] is True
    assert "Live Dry-run 已完成" in result["message"]
    assert "[Overnight Quant Signal]" not in str(result)


def test_after_close_feedback_warns_when_outside_after_close():
    from overnight_quant.ui.dashboard import action_feedback

    result = action_feedback(
        "after_close_live",
        {
            "ok": True,
            "stdout": "Mode: live\nStatus: NOT_AFTER_CLOSE\nSession State: PRE_MARKET\n",
            "stderr": "",
        },
        "zh",
    )

    assert result["ok"] is True
    assert result["severity"] == "warning"
    assert "没有生成正式观察池" in result["message"]
    assert "NOT_AFTER_CLOSE" in result["message"]
    assert "PRE_MARKET" in result["message"]
    assert "Mode: live" not in str(result)


def test_dashboard_exposes_formal_live_without_dry_run_flag():
    from overnight_quant.ui.dashboard import APPROVED_ACTIONS

    command = " ".join(APPROVED_ACTIONS["formal_live_scan"])
    assert "run_scan.py" in command
    assert "--mode live" in command
    assert "--dry-run" not in command


def test_load_dashboard_state_reads_latest_reports(tmp_path):
    from overnight_quant.ui.dashboard import load_dashboard_state

    reports = tmp_path / "overnight_quant" / "reports"
    records = tmp_path / "overnight_quant" / "records"
    reports.mkdir(parents=True)
    records.mkdir(parents=True)
    (reports / "preflight_2026-06-01.md").write_text("status: OUTSIDE_TAIL_SESSION\n", encoding="utf-8")
    (reports / "dry_run_scan_2026-06-01.md").write_text("candidate_source: live\nFinal Advice: NO_TRADE\n", encoding="utf-8")
    (records / "signals.csv").write_text("code,name,total_score\n300001,Demo,80\n", encoding="utf-8")

    state = load_dashboard_state(root=tmp_path)

    assert state["preflight"]["status"] == "OUTSIDE_TAIL_SESSION"
    assert state["dry_run"]["candidate_source"] == "live"
    assert not state["signals"].empty


def test_load_dashboard_state_reads_signal_rejections_separately(tmp_path):
    from overnight_quant.ui.dashboard import load_dashboard_state

    reports = tmp_path / "overnight_quant" / "reports"
    records = tmp_path / "overnight_quant" / "records"
    reports.mkdir(parents=True)
    records.mkdir(parents=True)
    (records / "signals.csv").write_text(
        "code,name,decision\n300001,Valid,BUY_CANDIDATE\n",
        encoding="utf-8-sig",
    )
    (records / "signal_rejections.csv").write_text(
        "code,name,decision,risk_flags\n600777,*ST新潮,REJECT,st_stock\n",
        encoding="utf-8-sig",
    )

    state = load_dashboard_state(root=tmp_path)

    assert [row["code"] for row in state["signals"].to_dict("records")] == [300001]
    assert [row["code"] for row in state["signal_rejections"].to_dict("records")] == [600777]


def test_load_dashboard_state_reads_manual_orders_and_position_summary(tmp_path):
    from overnight_quant.ui.dashboard import load_dashboard_state

    records = tmp_path / "overnight_quant" / "records"
    records.mkdir(parents=True)
    (records / "manual_orders.csv").write_text(
        "order_id,trade_date,trade_time,code,name,side,price,qty,amount,stop_loss_price,status\n"
        "B1,2026-05-23,2026-05-23 14:52:00,300001,Demo Robotics,BUY,18.0,200,3600,17.5,FILLED\n"
        "S1,2026-05-24,2026-05-24 09:45:00,300001,Demo Robotics,SELL,19.0,100,1900,,FILLED\n",
        encoding="utf-8-sig",
    )

    state = load_dashboard_state(root=tmp_path)
    rows = state["position_summary"].to_dict("records")

    assert not state["manual_orders"].empty
    assert rows[0]["code"] == "300001"
    assert rows[0]["status"] == "PARTIALLY_CLOSED"
    assert rows[0]["open_qty"] == 100
    assert rows[0]["avg_buy_price"] == 18.0
    assert rows[0]["realized_pnl"] == 100.0


def test_load_dashboard_state_reads_sell_plan_detail_table(tmp_path):
    from overnight_quant.ui.dashboard import load_dashboard_state

    reports = tmp_path / "overnight_quant" / "reports"
    reports.mkdir(parents=True)
    (reports / "sell_plan_2026-07-07.md").write_text(
        "# Next-Day Sell Plan\n\n"
        "status: SELL_PLAN_READY\n"
        "trade_date: 2026-07-07\n\n"
        "## 持仓卖出计划明细\n\n"
        "| code | name | qty | buy_price | current_price | pnl_pct | action | level | plan |\n"
        "|---|---|---:|---:|---:|---:|---|---|---|\n"
        "| 000725 | 京东方A | 1300 | 7.77 | 7.67 | -1.29 | 卖出/减仓优先 | D | 跌破 VWAP 反抽不过则卖出 |\n",
        encoding="utf-8",
    )

    state = load_dashboard_state(root=tmp_path)
    rows = state["sell_plan_rows"].to_dict("records")

    assert rows[0]["code"] == "000725"
    assert rows[0]["action"] == "卖出/减仓优先"
    assert "VWAP" in rows[0]["plan"]


def test_sell_plan_refresh_updates_panel_state_without_rerun(monkeypatch):
    from overnight_quant.ui import dashboard
    from overnight_quant.ui.result_parser import SimpleTable

    calls = []

    def fake_run_dashboard_action(action, language):
        calls.append((action, language))
        return {"ok": True, "message": "refreshed"}

    def fake_load_sell_plan_state(mode="live", root=None):
        return {
            "sell_plan": {"status": "SELL_PLAN_READY", "trade_date": "2026-07-08", "path": "sell_plan.md"},
            "sell_plan_rows": SimpleTable(
                [
                    {
                        "code": "603823",
                        "name": "Demo",
                        "qty": "300",
                        "action": "先观察",
                        "level": "C",
                        "plan": "刷新后的计划",
                        "realtime_alert": "刷新后的实时提醒",
                    }
                ],
                ["code", "name", "qty", "action", "level", "plan", "realtime_alert"],
            ),
            "lifecycle": {},
            "trade_review": {},
        }

    class FakeExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeColumn:
        def __init__(self, parent):
            self.parent = parent

        def button(self, label, use_container_width=False, key=None):
            return key == "refresh_sell_plan_realtime"

        def toggle(self, label, value=False, key=None):
            return False

        def caption(self, text):
            self.parent.captions.append(text)

        def markdown(self, text, unsafe_allow_html=False):
            self.parent.markdowns.append((text, unsafe_allow_html))

    class FakeStreamlit:
        def __init__(self):
            self.session_state = {}
            self.markdowns = []
            self.captions = []
            self.infos = []

        def markdown(self, text, unsafe_allow_html=False):
            self.markdowns.append((text, unsafe_allow_html))

        def columns(self, spec):
            count = len(spec) if isinstance(spec, list) else int(spec)
            return [FakeColumn(self) for _ in range(count)]

        def caption(self, text):
            self.captions.append(text)

        def info(self, text):
            self.infos.append(text)

        def expander(self, label):
            return FakeExpander()

        def json(self, data):
            pass

        def dataframe(self, data, use_container_width=False):
            pass

        def rerun(self):
            raise AssertionError("sell-plan refresh should not rerun the whole app")

    monkeypatch.setattr(dashboard, "run_dashboard_action", fake_run_dashboard_action)
    monkeypatch.setattr(dashboard, "load_sell_plan_state", fake_load_sell_plan_state)

    fake = FakeStreamlit()
    dashboard._render_sell_plan_page(
        fake,
        {
            "sell_plan": {"status": "OLD"},
            "sell_plan_rows": SimpleTable([], []),
            "lifecycle": {},
            "trade_review": {},
        },
        "zh",
        mode="live",
    )

    assert calls == [("sell_plan_live", "zh")]
    assert fake.session_state["last_action_feedback"]["message"] == "refreshed"
    assert fake.session_state["sell_plan_state_override"]["sell_plan"]["status"] == "SELL_PLAN_READY"
    assert any("刷新后的实时提醒" in text for text, _ in fake.markdowns)


def test_key_value_rows_render_as_summary_cards_not_table():
    from overnight_quant.ui.dashboard import render_key_value_rows

    class FakeStreamlit:
        def __init__(self):
            self.markdowns = []
            self.tables = []
            self.captions = []

        def markdown(self, text, unsafe_allow_html=False):
            self.markdowns.append((text, unsafe_allow_html))

        def table(self, data):
            self.tables.append(data)

        def caption(self, text):
            self.captions.append(text)

    fake = FakeStreamlit()

    render_key_value_rows(fake, [("状态", "WATCHLIST_READY"), ("候选来源", "实时数据")], "zh")

    assert not fake.tables
    assert fake.markdowns
    assert "oq-kv-grid" in fake.markdowns[0][0]
    assert fake.markdowns[0][1] is True


def test_load_dashboard_state_reads_after_close_risk_rows_when_watchlist_is_empty(tmp_path):
    from overnight_quant.ui.dashboard import load_dashboard_state

    reports = tmp_path / "overnight_quant" / "reports"
    records = tmp_path / "overnight_quant" / "records"
    reports.mkdir(parents=True)
    records.mkdir(parents=True)
    (records / "next_morning_watchlist_2026-06-03.csv").write_text(
        "trade_date,next_trade_date,code,name,category,score\n",
        encoding="utf-8-sig",
    )
    (reports / "after_close_analysis_2026-06-03.md").write_text(
        "# After-Close Analysis Report\n\n"
        "status: WATCHLIST_READY\n"
        "valid_for_trading_observation: YES\n\n"
        "## 5. C Class Risk Observation / Do Not Chase\n\n"
        "| code | name | score | reason | invalid_conditions |\n"
        "|---|---|---:|---|---|\n"
        "| 603311 | 金海高科 | 87.25 | limit_up_chase_risk | Risk observation only; do not chase. |\n",
        encoding="utf-8",
    )

    state = load_dashboard_state(root=tmp_path)

    assert state["watchlist"].empty
    risk_rows = state["after_close_risk_rows"].to_dict("records")
    assert risk_rows == [
        {
            "code": "603311",
            "name": "金海高科",
            "score": "87.25",
            "reason": "limit_up_chase_risk",
            "invalid_conditions": "Risk observation only; do not chase.",
        }
    ]


def test_load_dashboard_state_reads_morning_replay_risk_rows_when_watchlist_is_empty(tmp_path):
    from overnight_quant.ui.dashboard import load_dashboard_state

    reports = tmp_path / "overnight_quant" / "reports"
    records = tmp_path / "overnight_quant" / "records"
    reports.mkdir(parents=True)
    records.mkdir(parents=True)
    (records / "morning_replay_watchlist_2026-06-04.csv").write_text(
        "analysis_mode,observation_date,replay_as_of_date,code,name,category,score\n",
        encoding="utf-8-sig",
    )
    (reports / "morning_replay_analysis_2026-06-04.md").write_text(
        "# Morning Replay Report\n\n"
        "status: MORNING_REPLAY_READY\n"
        "valid_for_trading_observation: YES\n\n"
        "## 5. C Class Risk Observation / Do Not Chase\n\n"
        "| code | name | score | reason | invalid_conditions |\n"
        "|---|---|---:|---|---|\n"
        "| 002600 | 领益智造 | 71.0 | safety_field_unknown | Risk observation only; do not chase. |\n",
        encoding="utf-8",
    )

    state = load_dashboard_state(root=tmp_path)

    assert state["morning_replay_watchlist"].empty
    risk_rows = state["morning_replay_risk_rows"].to_dict("records")
    assert risk_rows == [
        {
            "code": "002600",
            "name": "领益智造",
            "score": "71.0",
            "reason": "safety_field_unknown",
            "invalid_conditions": "Risk observation only; do not chase.",
        }
    ]


def test_formal_live_buy_plan_rows_parse_latest_ticket(tmp_path):
    from overnight_quant.ui.dashboard import formal_live_buy_plan_rows, load_dashboard_state

    reports = tmp_path / "overnight_quant" / "reports"
    records = tmp_path / "overnight_quant" / "records"
    reports.mkdir(parents=True)
    records.mkdir(parents=True)
    (reports / "manual_order_ticket_2026-06-03_300001.md").write_text(
        "Strategy: yang_yongxing_overnight_v1\n"
        "Date: 2026-06-03\n"
        "Code: 300001\n"
        "Name: 测试股份\n"
        "Suggested Price: 18.50\n"
        "Max Acceptable Price: 18.78\n"
        "Suggested Amount: 3700.0\n"
        "Suggested Quantity: 200\n"
        "Stop Loss: 17.95\n"
        "Next-Day Plan: 次日 09:25 后按卖出计划处理\n",
        encoding="utf-8",
    )
    (records / "signals.csv").write_text(
        "code,name,decision,total_score\n300001,测试股份,BUY_CANDIDATE,82.5\n",
        encoding="utf-8-sig",
    )

    rows = formal_live_buy_plan_rows(load_dashboard_state(root=tmp_path), "zh")

    assert ("股票代码", "300001") in rows
    assert ("价格区间", "18.50 - 18.78") in rows
    assert ("仓位金额", "3700.0") in rows
    assert ("建议数量", "200") in rows
    assert ("明早卖出计划", "次日 09:25 后按卖出计划处理") in rows


def test_dashboard_modules_do_not_import_execution_modules():
    files = [
        Path("overnight_quant/ui/dashboard.py"),
        Path("overnight_quant/ui/result_parser.py"),
        Path("overnight_quant/scripts/run_dashboard.py"),
    ]
    forbidden = [
        "manual_ticket",
        "order_recorder",
        "position_tracker",
        "pyautogui",
        "selenium",
        "auto_order",
        "place_order",
    ]

    for path in files:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        lowered = text.lower()
        assert not any(token in lowered for token in forbidden), path


def test_run_dashboard_reports_missing_streamlit(monkeypatch, capsys):
    module = importlib.import_module("overnight_quant.scripts.run_dashboard")

    monkeypatch.setattr(module, "streamlit_available", lambda: False)

    assert module.main() == 2
    assert "UI_DEPENDENCY_MISSING" in capsys.readouterr().out


def test_run_dashboard_launches_streamlit_with_shell_false(monkeypatch):
    module = importlib.import_module("overnight_quant.scripts.run_dashboard")
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(module, "streamlit_available", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert module.main() == 0
    assert captured["args"][:3] == [sys.executable, "-m", "streamlit"]
    assert "overnight_quant" in str(captured["args"][-1])
    assert captured["kwargs"]["shell"] is False
