from copy import deepcopy
from pathlib import Path

import pytest

from overnight_quant.data.concept_batch_observation import CONTRACT, sha
from overnight_quant.data.offline_price_observation import ObservationStore, make_rule
from overnight_quant.ui import concept_observation_handoff as handoff


def batch():
    return {"synthetic_test_only": False, "contract_hash": CONTRACT, "selected_name": "Source name",
            "batch_id": "fixture", "member_coverage": {"complete": True, "received": 2, "total": 2},
            "member_raw": [{"f12": "000001", "f13": 0, "f14": "Name A", "f3": "UNKNOWN"},
                           {"f12": "600000", "f13": 1, "f14": "Name B", "f6": "UNKNOWN"}]}


def test_navigation_does_not_require_or_promote_numeric_fields():
    assert handoff.member_choices(batch()) == {"SZ:000001": "Name A", "SH:600000": "Name B"}


@pytest.mark.parametrize("market", [True, "0", None, 2, 1])
def test_market_mismatch_or_unknown_cannot_be_routed(market):
    value = batch()
    value["member_raw"][0]["f13"] = market
    with pytest.raises(ValueError):
        handoff.member_choices(value)


@pytest.mark.parametrize("change", ["incomplete", "duplicate", "no_membership"])
def test_invalid_membership_cannot_be_used_for_navigation(change):
    value = batch()
    if change == "incomplete":
        value["member_coverage"]["total"] = 3
    elif change == "duplicate":
        value["member_raw"][1] = deepcopy(value["member_raw"][0])
    else:
        value["contract_hash"] = "unknown"
    with pytest.raises(ValueError):
        handoff.member_choices(value)


def test_reverification_checks_selection_batch_and_scope(monkeypatch):
    monkeypatch.setattr(handoff, "load_batch", lambda *args: batch())
    result, name = handoff.verified_selection((), "SZ:000001", expected_id="fixture", synthetic=False)
    assert name == "Name A" and "price" not in result
    for identity, expected_id, synthetic in [("SZ:000002", "fixture", False),
                                              ("SZ:000001", "other", False), ("SZ:000001", "fixture", True)]:
        with pytest.raises(ValueError):
            handoff.verified_selection((), identity, expected_id=expected_id, synthetic=synthetic)


def test_integer_code_is_not_converted_to_an_identity():
    value = batch()
    value["member_raw"][1]["f12"] = 600000
    with pytest.raises(ValueError, match="member_code_invalid"):
        handoff.member_choices(value)


@pytest.fixture
def captured(tmp_path):
    from overnight_quant.tests.test_concept_batch_observation import run, page, lists, stocks
    process, receipt, target, _ = run(tmp_path, [page(1, lists(1)), page(2, stocks(2))])
    assert process.returncode == 0, receipt
    report = target / "returned-report.json"
    return (str(target), str(report), sha(report), sha(target / "final-confirmation.json"))


def open_page(monkeypatch, tmp_path, inputs):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.ui import dashboard, offline_observation
    from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard
    monkeypatch.setattr(offline_observation, "DEFAULT_STORE", tmp_path / "settings")
    monkeypatch.delenv("CONCEPT_BATCH_VERIFIED_INDEXES", raising=False)
    guard = AppTestNetworkGuard(monkeypatch)
    monkeypatch.setattr(dashboard, "run_dashboard_action", guard.deny_legacy_action)
    monkeypatch.setattr(dashboard, "run_approved_action", guard.deny_legacy_action)
    app = AppTest.from_file(str(Path(dashboard.__file__)), default_timeout=20).run()
    next(x for x in app.radio if x.label == "工作区").set_value("捕获批次观察").run()
    for label, value in zip(("批次目录", "命令返回报告文件", "返回报告外部 SHA-256", "最终确认外部 SHA-256"), inputs):
        next(x for x in app.text_input if x.label == label).set_value(value)
    next(x for x in app.button if x.label == "打开指定批次").click().run()
    assert not app.exception
    return app, guard


def navigate(app, identity="SZ:000001"):
    next(x for x in app.selectbox if x.label == "关注的成分股").select(identity).run()
    next(x for x in app.button if x.label == "前往观察工作台").click().run()
    assert not app.exception


@pytest.mark.parametrize("identity", ["SZ:000001", "SZ:000002"])
def test_explicit_handoff_preserves_settings_and_uses_existing_conditions(monkeypatch, tmp_path, captured, identity):
    from overnight_quant.tests.test_offline_price_observation import sample, encoded, NOW
    store = ObservationStore(tmp_path / "settings", "synthetic_test_only")
    original_rule = make_rule("600000", upper=12)
    revision = store.save_settings(["600000"], {"SH:600000": original_rule}, ["600000"])
    before = {str(p): p.read_bytes() for p in store.root.rglob("*.json")}
    for payload in [sample(), sample("test-B", "11", "45")]:
        data, anchor = encoded(payload)
        store.import_bytes(data, anchor, now=NOW)
    app, guard = open_page(monkeypatch, tmp_path, captured)
    assert next(x for x in app.button if x.label == "前往观察工作台").disabled
    navigate(app, identity)
    assert next(x for x in app.radio if x.label == "材料空间").value == "测试材料 · test-only"
    assert len(store.settings()) == 1  # Navigation alone never writes settings.
    assert not any(x.label == "设置条件的证券" for x in app.selectbox)
    assert next(x for x in app.button if x.label == "确认加入自选池").disabled
    next(x for x in app.selectbox if x.label == "加入前采用的设置").select(revision).run()
    next(x for x in app.button if x.label == "确认加入自选池").click().run()
    assert not app.exception and len(store.settings()) == 2
    state = app.session_state["offline_synthetic_test_only_state"]
    assert state["pool"] == ["SH:600000", identity]
    assert state["rules"]["SH:600000"] == original_rule and state["tracking"] == ["SH:600000"]
    next(x for x in app.selectbox if x.label == "观察快照").select("test-A").run()
    assert next(x for x in app.selectbox if x.label == "设置条件的证券").value == identity
    next(x for x in app.text_input if x.label == "上沿：价格达到或高于").input("11")
    next(x for x in app.button if x.label == "保存条件").click().run()
    next(x for x in app.button if x.label == "加入跟踪").click().run()
    assert next(x for x in app.selectbox if x.label == "跟踪证券").value == identity
    next(x for x in app.selectbox if x.label == "对比快照").select("test-B").run()
    assert not app.exception
    if identity == "SZ:000001":
        assert any(x.value == "1.00" for x in app.metric)
        assert any("不满足→满足" in x.value for x in app.markdown)
        next(x for x in app.text_input if x.label == "检查事项").input("人工核对")
        next(x for x in app.text_area if x.label == "结论说明").input("仅供历史观察")
        next(x for x in app.button if x.label == "保存人工备注新版本").click().run()
        assert any("用户记录，系统未核实" in x.value for x in app.success)
    else:
        assert any("尚未接入报价" in x.value for x in app.info)
        assert any("数据不足" in x.value for x in app.markdown)
        assert not app.metric
    assert not any(guard.business_attempts.values())
    assert all(Path(path).read_bytes() == data for path, data in before.items())
    assert ObservationStore(tmp_path / "settings", "historical").settings() == {}


def test_changed_anchor_after_open_is_rejected_before_handoff(monkeypatch, tmp_path, captured):
    app, guard = open_page(monkeypatch, tmp_path, captured)
    next(x for x in app.selectbox if x.label == "关注的成分股").select("SZ:000001").run()
    Path(captured[1]).write_bytes(b"{}")
    next(x for x in app.button if x.label == "前往观察工作台").click().run()
    assert not app.exception
    assert any("批次重新核验失败" in x.value for x in app.error)
    assert not any(x.label == "关注的成分股" for x in app.selectbox)
    assert not any(guard.business_attempts.values())
    assert not (tmp_path / "settings" / "synthetic_test_only" / "settings").exists()


def test_unchanged_revision_is_idempotent_and_removal_clears_handoff(monkeypatch, tmp_path, captured):
    store = ObservationStore(tmp_path / "settings", "synthetic_test_only")
    revision = store.save_settings(["000001", "600000"], {}, [])
    app, guard = open_page(monkeypatch, tmp_path, captured)
    navigate(app)
    next(x for x in app.selectbox if x.label == "加入前采用的设置").select(revision).run()
    next(x for x in app.button if x.label == "确认加入自选池").click().run()
    app.run()
    assert list(store.settings()) == [revision]
    assert any("已关注 SZ:000001" in x.value for x in app.info)
    next(x for x in app.text_area if x.label == "证券代码（每行一个）").input("SH:600000")
    next(x for x in app.button if x.label == "保存自选池").click().run()
    assert not app.exception
    assert not any(x.value.startswith("已关注 SZ:000001") for x in app.info)
    assert any("当前自选池未关注 SZ:000001" in x.value for x in app.info)
    assert next(x for x in app.selectbox if x.label == "设置条件的证券").value == "SH:600000"
    assert revision in store.settings()
    assert not any(guard.business_attempts.values())


def test_save_failure_and_scope_switch_do_not_admit_pending_selection(monkeypatch, tmp_path, captured):
    app, guard = open_page(monkeypatch, tmp_path, captured)
    navigate(app)

    def fail_save(*args, **kwargs):
        raise OSError("fixture_save_failure")

    monkeypatch.setattr(ObservationStore, "save_settings", fail_save)
    next(x for x in app.selectbox if x.label == "加入前采用的设置").select("当前会话设置").run()
    next(x for x in app.button if x.label == "确认加入自选池").click().run()
    assert any("自选保存失败" in x.value for x in app.error)
    assert not any(x.value.startswith("已关注") for x in app.info)
    next(x for x in app.radio if x.label == "材料空间").set_value("真实历史材料").run()
    assert any("未写入当前自选池" in x.value for x in app.info)
    assert not any(x.label == "确认加入自选池" for x in app.button)
    assert all(ObservationStore(tmp_path / "settings", scope).settings() == {}
               for scope in ("historical", "synthetic_test_only"))
    assert not any(guard.business_attempts.values())


def test_anchor_changed_after_navigation_cannot_be_used_to_save(monkeypatch, tmp_path, captured):
    app, guard = open_page(monkeypatch, tmp_path, captured)
    navigate(app)
    next(x for x in app.selectbox if x.label == "加入前采用的设置").select("当前会话设置").run()
    Path(captured[1]).write_bytes(b"{}")
    next(x for x in app.button if x.label == "确认加入自选池").click().run()
    assert any("来源批次不可用" in x.value for x in app.error)
    assert ObservationStore(tmp_path / "settings", "synthetic_test_only").settings() == {}
    assert not any(guard.business_attempts.values())
