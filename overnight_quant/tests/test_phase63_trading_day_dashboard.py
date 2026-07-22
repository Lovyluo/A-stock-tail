from pathlib import Path

from overnight_quant.ui.dashboard import APPROVED_ACTIONS, maintenance_action_keys, premium_tab_labels, primary_action_keys


def test_live_dry_run_and_health_check_are_not_primary_actions():
    assert "live_dry_run" in APPROVED_ACTIONS
    assert "preflight" in APPROVED_ACTIONS
    assert "live_dry_run" not in primary_action_keys()
    assert "preflight" not in primary_action_keys()
    assert "formal_live_scan" in primary_action_keys()
    assert "after_close_live" in primary_action_keys()
    assert {"live_dry_run", "preflight"}.issubset(set(maintenance_action_keys()))


def test_dashboard_tabs_match_trading_day_workflow():
    labels = premium_tab_labels("zh")
    assert labels == ["今日总览", "消息面", "集合竞价", "盘中攻防", "尾盘策略", "盘后观察池", "持仓/卖出计划", "审计与维护"]


def test_workbench_does_not_contain_forbidden_execution_capabilities():
    files = [
        Path("overnight_quant/strategy/auction_observation.py"),
        Path("overnight_quant/strategy/news_briefing.py"),
        Path("overnight_quant/strategy/intraday_observation.py"),
        Path("overnight_quant/ui/dashboard.py"),
    ]
    forbidden = ["pyautogui", "selenium", "broker api", "auto_order", "place_order", "自动点击"]
    for path in files:
        lowered = path.read_text(encoding="utf-8").lower()
        assert not any(token in lowered for token in forbidden), path
