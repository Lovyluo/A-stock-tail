"""Exercise selected quote batches without a live source or user settings."""
import base64
from datetime import timedelta
import json
import time

import pytest

from overnight_quant.data import offline_price_observation as obs
from overnight_quant.data import quote_observation_refresh as refresh
from overnight_quant.data.quote_observation_worker import execute
from overnight_quant.tests.test_concept_observation_handoff import captured, navigate, open_page
from overnight_quant.tests.test_manual_quote_observation import NOW, local_runner, raw_quote


def fixture(path, codes, *, event=None, price="10.5", raw=None):
    response = raw if raw is not None else raw_quote(event=event, price=price, codes=codes)
    path.write_text(json.dumps({"raw_base64": base64.b64encode(response).decode("ascii")}), encoding="utf-8")
    return path


def selected_controller(tmp_path, path):
    store = obs.ObservationStore(tmp_path, "synthetic_test_only")
    return refresh.RefreshController(store, contract=refresh.SELECTION_VERSION, fixture=path)


@pytest.mark.parametrize("selection", [
    ["SH:688575"], ["SZ:300750", "SH:688575"],
    ["SZ:301001", "SH:600519", "SZ:000002"],
    ["SH:605001", "SZ:000001", "SZ:000002", "SZ:300750"],
    sorted(refresh.IDENTITIES),
])
def test_exact_coverage_and_admission(tmp_path, monkeypatch, selection):
    if len(selection) != 1:  # Keep one real isolated worker in the regression.
        monkeypatch.setattr(refresh, "run_probe_worker_process", local_runner)
    monkeypatch.setattr(refresh, "execute_source_adapter", lambda *a, **k: pytest.fail("formal adapter used"))
    codes = [identity[3:] for identity in selection]
    controller = selected_controller(tmp_path, fixture(tmp_path / "quote.json", codes))
    result = controller.refresh([*selection, "SZ:002001"], selection=selection)
    assert result["status"] == "SUCCEEDED", result
    assert result["requests_made"] == 1
    assert result["process"]["payload"] == {"request_count": 1, "network_attempts": 0}
    assert result["selection"] == sorted(selection)
    assert not result["data_ready"] and not result["hard_gate_authorized"]
    assert all(result[k] == [] for k in ("records", "candidates", "tickets", "orders"))
    snap, = controller.store.refresh_snapshots()
    assert snap["observation_contract"] == refresh.SELECTION_VERSION
    assert set(snap["rows"]) == set(selection)
    assert obs.ObservationStore(tmp_path, "historical").refresh_snapshots() == []
    assert controller.refresh(selection, selection=selection)["requests_made"] == 0


@pytest.mark.parametrize("selection", [
    [], [*sorted(refresh.IDENTITIES), "SH:688575"], ["SZ:000001", "SZ:000001"],
    ["000001"], ["SH:000001"], ["BJ:830001"], ["SH:510300"], ["SH:900901"], ["SZ:200002"],
    ["SH:688575"],
])
def test_invalid_or_out_of_pool_selection_dispatches_nothing(tmp_path, monkeypatch, selection):
    monkeypatch.setattr(refresh, "run_probe_worker_process", lambda *a, **k: pytest.fail("worker dispatched"))
    controller = selected_controller(tmp_path, tmp_path / "unused.json")
    result = controller.refresh(sorted(refresh.IDENTITIES), selection=selection)
    assert result["status"] == "REJECTED" and result["requests_made"] == 0
    assert not list(controller.store.root.rglob("*.json"))


def test_selection_cannot_be_activated_by_legacy_network_switch(tmp_path, monkeypatch):
    monkeypatch.setenv(refresh.NETWORK_SWITCH, "1")
    real = obs.ObservationStore(tmp_path, "historical")
    controller = refresh.RefreshController(real, network_enabled=True, contract=refresh.SELECTION_VERSION)
    assert not controller.enabled and controller.refresh(["SZ:000001"], selection=["SZ:000001"])["requests_made"] == 0
    with pytest.raises(ValueError, match="quote_selection_test_only"):
        execute({"contract": refresh.SELECTION_VERSION, "codes": ["000001"], "selection": ["SZ:000001"],
                 "synthetic_test_only": False})


def test_empty_fixture_cannot_fall_through_to_live_transport(tmp_path, monkeypatch):
    import urllib.request

    path = tmp_path / "empty.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: pytest.fail("live transport constructed"))
    with pytest.raises(ValueError, match="worker_fixture_invalid"):
        execute({"contract": refresh.SELECTION_VERSION, "codes": ["000001"], "selection": ["SZ:000001"],
                 "synthetic_test_only": True, "fixture": str(path), "output": str(tmp_path)})


@pytest.mark.parametrize("kind", ["missing", "extra", "duplicate", "market"])
def test_inexact_response_is_never_a_success(tmp_path, monkeypatch, kind):
    def worker(task, *args, **kwargs):
        try:
            return local_runner(task)
        except ValueError as exc:
            return dict(ok=False, error_code="FIXTURE_REJECTED", error=str(exc), cleanup_complete=True)

    monkeypatch.setattr(refresh, "run_probe_worker_process", worker)
    codes = ["000001", "600000"]
    rows = {"missing": ["000001"], "extra": [*codes, "000002"],
            "duplicate": [*codes, "000001"], "market": codes}[kind]
    raw = raw_quote(codes=rows)
    if kind == "market":
        raw = raw.replace(b"v_sz000001", b"v_sh000001")
    controller = selected_controller(tmp_path, fixture(tmp_path / "quote.json", rows, raw=raw))
    pool = ["SZ:000001", "SH:600000"]
    result = controller.refresh(pool, selection=pool)
    assert result["status"] == "FAILED", result
    assert result["requests_made"] == 1
    assert controller.store.refresh_snapshots() == []


def test_changed_selection_evidence_is_not_admitted_on_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "run_probe_worker_process", local_runner)
    controller = selected_controller(tmp_path, fixture(tmp_path / "quote.json", ["688575"]))
    result = controller.refresh(["SH:688575"], selection=["SH:688575"])
    assert result["status"] == "SUCCEEDED", result
    target = controller.store.root / refresh.SELECTION_VERSION / "attempts" / result["batch_id"]
    intent = obs._json((target / "intent.json").read_bytes())
    intent.update(selection=["SZ:000001"], codes=["000001"])
    for path in (target / "intent.json", controller.state / ("attempt-" + result["batch_id"] + ".json")):
        path.write_text(json.dumps(intent), encoding="utf-8")
    assert controller.store.refresh_snapshots() == []


def test_earlier_selected_quote_never_fills_an_unselected_or_failed_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "run_probe_worker_process", local_runner)
    controller = selected_controller(tmp_path, fixture(tmp_path / "quote.json", ["000001"]))
    result = controller.refresh(["SZ:000001"], selection=["SZ:000001"])
    snapshot, = controller.store.refresh_snapshots()
    rule = obs.make_rule("000001", upper=10)
    for attempt in ({**result, "selection": ["SZ:000002"]},
                    {**result, "status": "FAILED", "error": "coverage_incomplete"},
                    {**result, "batch_id": "different"}):
        observed = refresh.evaluate_current(snapshot, "000001", rule, attempt=attempt,
                                            contract=refresh.SELECTION_VERSION)
        assert observed["conditions"]["upper"]["state"] == "INSUFFICIENT"
        assert observed["conditions"]["upper"]["comparison_price"] is None
        assert observed["quote_age_seconds"] is None


def test_concept_handoff_to_two_selected_quote_snapshots(tmp_path, monkeypatch, captured):
    from overnight_quant.ui import offline_observation as ui
    from overnight_quant.ui import quote_observation_refresh as refresh_ui

    wall = [NOW]
    path = fixture(tmp_path / "selected-quote.json", ["000002"], event=wall[0] - timedelta(seconds=2))
    controller = selected_controller(tmp_path / "settings", path)
    original_render = refresh_ui.render_refresh
    monkeypatch.setattr(ui, "render_refresh", lambda st, store, state, prefix, **kwargs:
                        original_render(st, store, state, prefix, controller=controller))
    monkeypatch.setattr(refresh, "utc_now", lambda: wall[0])
    monkeypatch.setattr(ui, "utc_now", lambda: wall[0])
    calls = []

    def runner(task, *args, **kwargs):
        calls.append(task)
        return local_runner(task)

    monkeypatch.setattr(refresh, "run_probe_worker_process", runner)
    app, guard = open_page(monkeypatch, tmp_path, captured)
    navigate(app, "SZ:000002")
    next(x for x in app.selectbox if x.label == "加入前采用的设置").select("当前会话设置").run()
    next(x for x in app.button if x.label == "确认加入自选池").click().run()
    assert next(x for x in app.selectbox if x.label == "设置条件的证券").value == "SZ:000002"
    next(x for x in app.radio if x.label == "观察方式").set_value("主动刷新观察").run()
    assert not calls and next(x for x in app.button if x.label == "刷新一次报价").disabled
    app.multiselect[0].set_value(["SZ:000002"]).run()
    assert not calls and any("待确认集合：SZ:000002" in x.value for x in app.caption)
    next(x for x in app.button if x.label == "刷新一次报价").click().run()
    assert len(calls) == 1 and not app.error and not app.exception
    table = next(x.value for x in app.dataframe if "本次报价" in x.value.columns)
    assert table.set_index("证券身份").at["SZ:000001", "本次报价"] == "未获取"
    next(x for x in app.selectbox if x.label == "设置条件的证券").select("SZ:000002").run()
    next(x for x in app.text_input if x.label == "上沿：价格达到或高于").input("11")
    next(x for x in app.button if x.label == "保存条件").click().run()
    next(x for x in app.button if x.label == "加入跟踪").click().run()
    wall[0] += timedelta(seconds=20)
    monotonic = time.monotonic
    monkeypatch.setattr(refresh.time, "monotonic", lambda: monotonic() + 20)
    fixture(path, ["000002"], event=wall[0] - timedelta(seconds=2), price="11")
    next(x for x in app.button if x.label == "刷新一次报价").click().run()
    assert len(calls) == 2 and not app.error and not app.exception
    assert all(task["selection"] == ["SZ:000002"] and task["codes"] == ["000002"] for task in calls)
    next(x for x in app.selectbox if x.label == "观察快照").select(calls[0]["batch_id"]).run()
    next(x for x in app.selectbox if x.label == "对比快照").select(calls[1]["batch_id"]).run()
    assert any(x.value == "0.5" for x in app.metric)
    assert any("不满足→满足" in x.value for x in app.markdown)
    assert not any(guard.business_attempts.values())
    assert obs.ObservationStore(tmp_path / "settings", "historical").settings() == {}
