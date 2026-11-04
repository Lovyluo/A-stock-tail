from datetime import datetime, timedelta, timezone
import json

import pytest

from overnight_quant.data import observation_tracking as tracking
from overnight_quant.data import offline_price_observation as obs
from overnight_quant.tests.test_offline_price_observation import sample, encoded, snapshot, NOW


def store_with_pair(tmp_path, first="10", second="12"):
    store = obs.ObservationStore(tmp_path, "synthetic_test_only")
    for payload in (sample("A", first, "40"), sample("B", second, "41")):
        data, sha = encoded(payload)
        store.import_bytes(data, sha)
    return store


@pytest.mark.parametrize("a,b,state", [("10", "12", "BECAME_MET"), ("12", "10", "BECAME_NOT_MET"),
                                      ("12", "12", "UNCHANGED"), ("10", "10", "UNCHANGED")])
def test_transition_and_no_formal_output(a, b, state):
    rule = obs.make_rule("000001", upper=11)
    c = tracking.change(snapshot(sample("A", a, "40")), snapshot(sample("B", b, "41")), "000001", rule, rule)
    assert c["transitions"]["upper"]["state"] == state
    assert c["risk_assessment"] == c["tradability"] == "NOT_ASSESSED"
    assert all(c[k] == [] for k in ("records", "candidates", "tickets", "orders"))


def test_rule_change_and_missing_are_not_market_trigger():
    a, b = snapshot(sample("A")), snapshot(sample("B", minute="41"))
    left, right = obs.make_rule("000001", upper=11), obs.make_rule("000001", upper=9)
    c = tracking.change(a, b, "000001", left, right)
    assert c["transitions"]["upper"]["state"] == "RULE_CHANGED" and c["price_change"] == "0.00"
    del a["rows"]["SZ:000001"]
    c = tracking.change(a, b, "000001", right, right)
    assert c["transitions"]["upper"]["state"] == "INSUFFICIENT"


@pytest.mark.parametrize("a,b", [("A", "A"), ("B", "A"), ("missing", "B")])
def test_bad_pair_never_persists(tmp_path, a, b):
    store = store_with_pair(tmp_path)
    rule = obs.make_rule("000001", upper=11)
    with pytest.raises(ValueError):
        tracking.save_change(store, a, b, "000001", rule, rule)
    assert tracking.events(store) == []


def test_dedup_scope_hash_and_rule_versions(tmp_path):
    store = store_with_pair(tmp_path)
    rule = obs.make_rule("000001", upper=11)
    first = tracking.save_change(store, "A", "B", "000001", rule, rule)
    assert tracking.save_change(store, "A", "B", "SZ:000001", rule, rule) == first
    assert len(tracking.events(store)) == 1
    tracking.save_change(store, "A", "B", "000001", rule, obs.make_rule("000001", upper=13))
    assert len(tracking.events(store)) == 2
    assert not tracking.events(obs.ObservationStore(tmp_path, "historical"))
    path = next((store.root / tracking.VERSION / "changes").glob("*.json"))
    body = json.loads(path.read_text(encoding="utf-8")); body["scope"] = "historical"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError):
        tracking.events(store)


def test_note_versions_expiry_and_unknown_risk(tmp_path, monkeypatch):
    store = store_with_pair(tmp_path)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(tracking, "current_time", lambda: now)
    assert tracking.note_status(None) == "未记录"
    one = tracking.save_note(store, "000001", "公告尚未检查", "不作安全结论", "https://invalid.example/evidence",
                             due_at=(now + timedelta(hours=1)).isoformat())
    assert tracking.note_status(one, now=now) == "已记录"
    two = tracking.save_note(store, "000001", "公告尚未检查", "待人工复核", "文本", previous=one["revision"], review=True)
    assert two["previous_revision"] == one["revision"]
    assert tracking.note_status(two) == "待复核"
    assert tracking.note_status(one, now=now + timedelta(hours=1)) == "已到期"
    assert len(tracking.notes(store)) == 2
    assert one["recorded_at"] == now.isoformat()
    assert all(n["risk_assessment"] == n["tradability"] == "NOT_ASSESSED" and
               n["disclaimer"] == tracking.DISCLAIMER for n in tracking.notes(store))
    with pytest.raises(ValueError):
        tracking.save_note(store, "600000", "检查", "说明", "", previous=one["revision"])


def test_other_item_does_not_hide_expired_review(tmp_path):
    store = store_with_pair(tmp_path)
    old = tracking.save_note(store, "000001", "事项一", "未核实", "", due_at="2020-01-01T00:00:00+08:00")
    tracking.save_note(store, "000001", "事项二", "仅记录", "")
    assert tracking.note_summary(tracking.notes(store)) == "已到期"
    tracking.save_note(store, "000001", "事项一", "重新记录", "", previous=old["revision"])
    assert tracking.note_summary(tracking.notes(store)) == "已记录"


@pytest.mark.parametrize("due", ["tomorrow", "2026-11-04T12:00:00", "NaN"])
def test_invalid_note_due(tmp_path, due):
    with pytest.raises(ValueError):
        tracking.save_note(store_with_pair(tmp_path), "000001", "检查", "说明", "", due_at=due)


def test_actual_dashboard_tracking_notes_zero_network(tmp_path, monkeypatch, record_property):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.ui import offline_observation as ui
    from overnight_quant.ui import dashboard
    from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard
    store = store_with_pair(tmp_path)
    monkeypatch.setattr(ui, "DEFAULT_STORE", tmp_path)
    guard = AppTestNetworkGuard(monkeypatch)
    monkeypatch.setattr(dashboard, "run_dashboard_action", guard.deny_legacy_action)
    monkeypatch.setattr(dashboard, "run_approved_action", guard.deny_legacy_action)
    app = AppTest.from_file(str(ui.Path(ui.__file__).with_name("dashboard.py")), default_timeout=15).run()
    next(x for x in app.radio if x.label == "材料空间").set_value("测试材料 · test-only").run()
    def click(label):
        next(x for x in app.button if x.label == label).click().run()
    next(x for x in app.selectbox if x.label == "设置条件的证券").select("SZ:000001").run()
    next(x for x in app.text_input if x.label == "上沿：价格达到或高于").input("11")
    click("保存条件")
    click("加入跟踪")
    next(x for x in app.selectbox if x.label == "观察快照").select("A").run()
    next(x for x in app.selectbox if x.label == "对比快照").select("B").run()
    click("保存所选比较记录")
    click("保存所选比较记录")
    assert len(tracking.events(store)) == 1
    next(x for x in app.text_input if x.label == "检查事项").input("test-only公告核对")
    next(x for x in app.text_area if x.label == "结论说明").input("尚未核实")
    next(x for x in app.text_area if x.label == "依据文本或链接（不自动访问）").input("https://invalid.example")
    next(x for x in app.checkbox if x.label == "标记待复核").check()
    click("保存人工备注新版本")
    assert not app.exception
    assert tracking.note_status(tracking.notes(store)[-1]) == "待复核"
    assert any(tracking.DISCLAIMER in str(x.value) for x in app.success)
    assert any("待复核" in str(x.value) for x in app.dataframe)
    assert not any(guard.business_attempts.values())
    record_property("business_attempts", json.dumps(guard.business_attempts))
