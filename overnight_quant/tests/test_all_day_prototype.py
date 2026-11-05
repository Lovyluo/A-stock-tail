import math

import pytest

from overnight_quant.ui.all_day_prototype import (
    intraday_status, observation_state, system_reference, theme_status,
)


@pytest.mark.parametrize("today,breadth,expected", [
    (0, .6, "走强"), (-.1, .39, "走弱"), (0, .5, "分化"),
    (None, .5, "资料不足"), (math.nan, .5, "资料不足"),
    (1, 1.1, "资料不足"), (True, .5, "资料不足"),
])
def test_intraday_is_independent_of_history(today, breadth, expected):
    assert intraday_status(today, breadth) == expected
    assert theme_status(None, breadth, today) == "资料不足"


def test_system_reference_requires_complete_matching_basis():
    basis = "unadjusted_cny_per_share"
    highs, lows = [10, 11, 12, 11, 10.5], [9, 10, 11, 10, 9.6]
    assert system_reference(highs, lows, basis, basis) == {
        "prior_high": 10.5, "prior_low": 9.6, "five_high": 12, "five_low": 9,
    }
    assert system_reference(highs[:-1], lows, basis, basis) is None
    assert system_reference(highs, lows, "qfq", basis) is None
    assert system_reference(highs, lows, basis, "unknown") is None
    assert system_reference(highs, [9, 10, 13, 10, 9], basis, basis) is None
    assert system_reference(highs, [9, 10, math.nan, 10, 9], basis, basis) is None


@pytest.mark.parametrize("relative,breadth,today,expected", [
    (1, .6, 0, "走强"), (-1, .6, 1, "走弱"), (2, .39, 1, "走弱"),
    (0, .5, 0, "分化"), (None, .5, 0, "资料不足"), (math.nan, .5, 0, "资料不足"),
    (1, 1.1, 0, "资料不足"), (True, .5, 0, "资料不足"),
])
def test_theme_requires_finite_inputs(relative, breadth, today, expected):
    assert theme_status(relative, breadth, today) == expected


@pytest.mark.parametrize("price,upper,invalid,expected", [
    (10, 11, 9, "等待确认"), (11, 11, 9, "满足观察条件"), (9, 11, 9, "条件失效"),
    (None, 11, 9, "资料不足"), (10, 9, 11, "资料不足"), (math.inf, 11, 9, "资料不足"),
])
def test_only_observation_not_breakout(price, upper, invalid, expected):
    assert observation_state(price, upper, invalid) == expected


def test_dashboard_demo_is_isolated_and_passive(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.ui import dashboard, offline_observation
    from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard
    monkeypatch.setattr(offline_observation, "DEFAULT_STORE", tmp_path)
    guard = AppTestNetworkGuard(monkeypatch)
    monkeypatch.setattr(dashboard, "run_dashboard_action", guard.deny_legacy_action)
    monkeypatch.setattr(dashboard, "run_approved_action", guard.deny_legacy_action)
    app = AppTest.from_file(str(offline_observation.Path(dashboard.__file__)), default_timeout=15).run()
    next(x for x in app.radio if x.label == "工作区").set_value("全天短线观察 · 演示").run()
    assert not app.exception
    assert any("演示数据" in x.value for x in app.warning)
    assert any("当日表现" in x.value and "近期持续性" in x.value for x in app.markdown)
    assert any("系统参考条件（合成日线）" in x.value for x in app.markdown)
    assert any("用户自定义条件" in x.value for x in app.caption)
    assert {t.label for t in app.tabs} == {"今日机会", "竞价观察", "热门题材", "核心个股", "我的跟踪"}
    next(x for x in app.button if x.label == "加入演示跟踪").click().run()
    assert any("已加入" in x.value for x in app.success)
    next(x for x in app.radio if x.label == "演示持仓状态").set_value("今日买入").run()
    assert any("不得据此" in x.value for x in app.warning)
    assert not any(guard.business_attempts.values())
    assert not list(tmp_path.rglob("*"))
