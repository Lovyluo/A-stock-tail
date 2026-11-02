from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from overnight_quant.data import offline_price_observation as model

NOW = datetime.fromisoformat("2026-11-03T12:00:00+08:00")


def sample(batch="test-A", price="10.00", minute="40"):
    return {"schema": model.TEST_SCHEMA, "scope": "synthetic_test_only", "batch_id": batch,
            "captured_at": f"2026-10-19T14:{minute}:03+08:00", "rows": [
                {"batch_id": batch, "code": code, "security_identity": model.security_identity(code),
                 "price": price, "price_basis": model.PRICE_BASIS, "unit": "CNY_per_share",
                 "source_version": "synthetic_test_only", "event_time": f"2026-10-19T14:{minute}:00+08:00",
                 "observed_at": f"2026-10-19T14:{minute}:01+08:00",
                 "available_at": f"2026-10-19T14:{minute}:02+08:00", "raw_hash": "a" * 64}
                for code in model.INITIAL_POOL]}


def encoded(payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return data, hashlib.sha256(data).hexdigest()


def snapshot(payload=None):
    data, sha = encoded(payload or sample())
    return model.import_snapshot(data, expected_sha256=sha, test_only=True, now=NOW)


@pytest.mark.parametrize("price,lower,upper,low_state,high_state", [
    ("10", None, None, "UNSET", "UNSET"),
    ("10", "10", "11", "MET", "NOT_MET"),
    ("11", "10", "11", "NOT_MET", "MET"),
    ("10.5", "10", "11", "NOT_MET", "NOT_MET"),
    ("9", "10", "11", "MET", "NOT_MET"),
    ("12", None, "11", "UNSET", "MET"),
])
def test_absolute_conditions_equal_boundaries(price, lower, upper, low_state, high_state):
    result = model.evaluate(snapshot(sample(price=price)), "000001",
                            model.make_rule("000001", lower=lower, upper=upper), now=NOW)
    assert result["conditions"]["lower"]["state"] == low_state
    assert result["conditions"]["upper"]["state"] == high_state
    assert result["risk_assessment"] == "NOT_ASSESSED"
    assert not result["data_ready"] and not result["hard_gate_authorized"]
    assert all(result[key] == [] for key in ("records", "candidates", "tickets", "orders"))


@pytest.mark.parametrize("value", [True, False, 0, -1, "NaN", "Infinity", float("inf"), "-0", "abc", [], {}])
def test_invalid_thresholds(value):
    with pytest.raises(ValueError):
        model.make_rule("000001", upper=value)


@pytest.mark.parametrize("lower,upper", [(10, 10), (11, 10)])
def test_threshold_order(lower, upper):
    with pytest.raises(ValueError, match="lower_must"):
        model.make_rule("000001", lower=lower, upper=upper)


def test_no_rounding_of_user_threshold():
    upper = "10.000000000000000000000000000001"
    rule = model.make_rule("000001", upper=upper)
    assert rule["upper"] == upper
    assert model.evaluate(snapshot(), "000001", rule, now=NOW)["conditions"]["upper"]["state"] == "NOT_MET"


@pytest.mark.parametrize("field,value", [
    ("price", None), ("price", "0"), ("price", "-1"), ("price", "NaN"),
    ("event_time", None), ("available_at", None), ("observed_at", "2026-11-04T00:00:00+08:00"),
    ("event_time", "2026-10-20T00:00:00+08:00"),
    ("event_time", "2026-10-19T14:40:00"),
    ("price_basis", "QFQ"), ("unit", "UNKNOWN"), ("raw_hash", ""),
])
def test_bad_quote_never_becomes_condition(field, value):
    payload = sample()
    payload["rows"][0][field] = value
    result = model.evaluate(snapshot(payload), "000001", model.make_rule("000001", upper=10), now=NOW)
    assert result["conditions"]["upper"]["state"] == "INSUFFICIENT"
    assert result["conditions"]["upper"]["comparison_price"] is None


def test_missing_security_remains_in_pool_and_realtime_always_insufficient():
    rule = model.make_rule("000002", upper=10)
    result = model.evaluate(snapshot(), "000002", rule, now=NOW)
    assert result["reason"] == "quote_missing_in_selected_batch"
    result = model.evaluate(snapshot(), "000001", model.make_rule("000001", upper=10),
                            require_realtime=True, now=NOW)
    assert result["reason"] == "realtime_not_connected"
    assert result["conditions"]["upper"]["state"] == "INSUFFICIENT"


@pytest.mark.parametrize("change", ["mixed_batch", "wrong_identity", "duplicate", "source_version"])
def test_import_refuses_conflicts(change):
    payload = sample()
    row = payload["rows"][0]
    if change == "mixed_batch":
        row["batch_id"] = "other"
    elif change == "wrong_identity":
        row["security_identity"] = "SH:000001"
    elif change == "source_version":
        row["source_version"] = "real"
    else:
        payload["rows"].append({**row, "price": "99"})
    with pytest.raises(ValueError):
        snapshot(payload)


def test_external_anchor_scope_future_and_qfq_refused():
    data, sha = encoded(sample())
    with pytest.raises(ValueError, match="external_sha256"):
        model.import_snapshot(data, expected_sha256="b" * 64, test_only=True)
    with pytest.raises(ValueError, match="historical_session"):
        model.import_snapshot(data, expected_sha256=sha)
    with pytest.raises(ValueError):
        model.import_snapshot(data, expected_sha256=sha, test_only="true")
    payload = sample()
    payload["captured_at"] = "2027-01-01T00:00:00+08:00"
    with pytest.raises(ValueError, match="future"):
        snapshot(payload)
    data, sha = encoded({"schema": "baostock_vendor_qfq", "price": 10})
    with pytest.raises(ValueError, match="historical_session"):
        model.import_snapshot(data, expected_sha256=sha)


def test_same_batch_duplicates_order_and_unrelated_fields():
    payload = sample()
    normal = snapshot(payload)
    payload["rows"].append(deepcopy(payload["rows"][0]))
    payload["rows"].reverse()
    other = snapshot(payload)
    assert normal["rows"] == other["rows"]
    rule = model.make_rule("000001", upper=10)
    left = model.evaluate(normal, "000001", rule, now=NOW)
    payload["fund_flow"] = {"eligible": True, "main_net": "fake"}
    right = model.evaluate(snapshot(payload), "000001", rule, now=NOW)
    assert left["conditions"] == right["conditions"]


def test_rule_identity_and_comparison_changes():
    first, second = snapshot(), snapshot(sample("test-B", "11", "45"))
    rule = model.make_rule("000001", upper=11)
    different = model.make_rule("000001", upper=12)
    result = model.compare(first, second, "000001", rule, rule, now=NOW)
    assert result["comparable"] and result["price_change"] == "1.00"
    assert result["before"]["conditions"]["upper"]["state"] == "NOT_MET"
    assert result["after"]["conditions"]["upper"]["state"] == "MET"
    assert "不证明" in result["message"]
    changed = model.compare(first, second, "000001", rule, different, now=NOW)
    assert changed["rule_changed"] and "不能归因" in changed["message"]
    for left, right in [(first, None), (first, first), (second, first)]:
        assert not model.compare(left, right, "000001", rule, rule, now=NOW)["comparable"]
    with pytest.raises(ValueError, match="rule_identity"):
        model.evaluate(first, "600000", rule, now=NOW)


def test_missing_to_met_not_breakout_or_compatible_history():
    first = sample()
    first["rows"] = first["rows"][1:]
    result = model.compare(snapshot(first), snapshot(sample("test-B", "11", "45")), "000001",
                           model.make_rule("000001", upper=11), model.make_rule("000001", upper=11), now=NOW)
    assert not result["comparable"] and result["price_change"] is None
    other = snapshot(sample("test-B", "11", "45"))
    other["scope"] = "historical"
    assert not model.compare(snapshot(), other, "000001", model.make_rule("000001"),
                             model.make_rule("000001"), now=NOW)["comparable"]


def test_store_append_only_isolation_and_rules(tmp_path):
    store = model.ObservationStore(tmp_path, "synthetic_test_only")
    data, sha = encoded(sample())
    before = store.import_bytes(data, sha, now=NOW)
    assert store.import_bytes(data, sha, now=NOW) == before
    changed, changed_sha = encoded(sample(price="12"))
    with pytest.raises(ValueError, match="already_imported"):
        store.import_bytes(changed, changed_sha, now=NOW)
    identities = model.normalize_pool([*model.INITIAL_POOL, "000002"])
    rule1, rule2 = model.make_rule("000001", upper=11), model.make_rule("000001", upper=12)
    rev1 = store.save_settings(identities, {"SZ:000001": rule1}, ["000001"])
    rev2 = store.save_settings(identities, {"SZ:000001": rule2}, ["000001"])
    assert rev1 != rev2 and len(store.settings()) == 2
    assert store.settings()[rev1]["rules"]["SZ:000001"] == rule1
    assert len(store.snapshots(now=NOW)) == 1
    assert model.ObservationStore(tmp_path, "historical").snapshots(now=NOW) == []
    with pytest.raises(ValueError):
        store.save_settings(["000001"], {}, ["600000"])
    assert not (tmp_path / "records").exists()


def test_store_rejects_damaged_settings_and_write_failure(tmp_path, monkeypatch):
    store = model.ObservationStore(tmp_path, "synthetic_test_only")
    revision = store.save_settings(["000001"], {}, [])
    path = store.root / "settings" / (revision + ".json")
    body = json.loads(path.read_text(encoding="utf-8"))
    body["scope"] = "historical"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="settings_hash_or_scope"):
        store.settings()
    def denied(*args, **kwargs):
        raise PermissionError("fixture denied")
    monkeypatch.setattr(model, "write_once", denied)
    data, sha = encoded(sample())
    with pytest.raises(PermissionError):
        store.import_bytes(data, sha, now=NOW)
    assert store.snapshots(now=NOW) == []


def test_real_session_input_reuses_verifier_but_never_authorizes(monkeypatch):
    from overnight_quant.tests.test_phase92_calendar_session_contract import _confirmation, _records
    rows = _records()
    for row in rows:
        row["payload"]["field_units"] = {"price": "CNY_per_share"}
    data, sha = encoded(_confirmation(records=rows))
    result = model.import_snapshot(data, expected_sha256=sha, now=NOW)
    assert result["scope"] == "historical"
    assert model.evaluate(result, "000001", model.make_rule("000001", upper=10), now=NOW)["conditions"]["upper"]["state"] == "MET"
    bad = _confirmation(records=rows)
    bad["records"][0]["event_time"] = "invalid"
    data, sha = encoded(bad)
    with pytest.raises(ValueError, match="historical_session_rejected"):
        model.import_snapshot(data, expected_sha256=sha, now=NOW)


def test_actual_dashboard_chain_no_network_or_old_actions(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.ui import offline_observation as ui
    store = model.ObservationStore(tmp_path, "synthetic_test_only")
    for payload in [sample(), sample("test-B", "11", "45")]:
        data, sha = encoded(payload)
        store.import_bytes(data, sha, now=NOW)
    monkeypatch.setattr(ui, "DEFAULT_STORE", tmp_path)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("forbidden")
        raise AssertionError("network_or_legacy_action")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    from overnight_quant.ui import dashboard
    monkeypatch.setattr(dashboard, "run_dashboard_action", forbidden)
    path = Path(__file__).resolve().parents[1] / "ui" / "dashboard.py"
    package = path.parents[1]
    def formal_files():
        return {str(item): hashlib.sha256(item.read_bytes()).hexdigest()
                for name in ("records", "reports") for item in (package / name).rglob("*") if item.is_file()}
    original_formal_files = formal_files()
    app = AppTest.from_file(str(path), default_timeout=15).run()
    assert not app.exception
    next(x for x in app.radio if x.label == "材料空间").set_value("测试材料 · test-only").run()
    next(x for x in app.selectbox if x.label == "观察快照").select("test-A").run()
    next(x for x in app.text_input if x.label == "上沿：价格达到或高于").input("11")
    next(x for x in app.text_input if x.label == "下沿：价格达到或低于").input("9")
    next(x for x in app.button if x.label == "保存条件").click().run()
    assert not app.exception
    next(x for x in app.button if x.label == "加入跟踪").click().run()
    next(x for x in app.selectbox if x.label == "对比快照").select("test-B").run()
    assert not app.exception
    assert any("1.00" in str(value.value) for value in app.metric)
    assert any("不证明连续路径" in value.value for value in app.info)
    next(x for x in app.text_area if x.label == "证券代码（每行一个）").input("000001\n000002\n600000")
    next(x for x in app.button if x.label == "保存自选池").click().run()
    assert not app.exception and not calls
    table = app.dataframe[0].value
    assert "SZ:000002" in list(table["证券身份"])
    assert table[table["证券身份"] == "SZ:000002"].iloc[0]["比较价格"] == "缺失/不可用"
    assert not any(x.label in {"刷新实时卖出提醒", "Live Dry-run"} for x in app.button)
    revision = next(key for key, value in store.settings().items() if value["pool"] == ["SH:600000", "SZ:000001", "SZ:000002"])
    next(x for x in app.selectbox if x.label == "已保存设置版本").select(revision).run()
    next(x for x in app.button if x.label == "加载所选设置").click().run()
    assert not app.exception and not calls
    assert formal_files() == original_formal_files
