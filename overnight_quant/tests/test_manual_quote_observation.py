from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import pytest

from overnight_quant.data import offline_price_observation as obs
from overnight_quant.data import quote_observation_refresh as refresh
from overnight_quant.data.quote_observation_worker import execute
from overnight_quant.data.tencent_direct_http_providers import build_tencent_request_url
from overnight_quant.tests.test_phase82_tencent_provider_candidates import _line, _values

NOW = datetime.fromisoformat("2026-11-03T14:50:00+08:00")


def raw_quote(event=None, price="10.5", codes=refresh.CODES):
    event = event or datetime.now(timezone(timedelta(hours=8))) - timedelta(seconds=2)
    return (";\n".join(_line(code, _values(code, **{"30": event.strftime("%Y%m%d%H%M%S"),
                                                 "3": price})) for code in codes) + ";\n").encode("gbk")


def envelope(batch="first", *, event=None, price="10.5", codes=refresh.CODES):
    raw = raw_quote(event or NOW - timedelta(seconds=5), price, codes)
    return dict(contract=refresh.VERSION, batch_id=batch, codes=list(refresh.CODES),
                source_version=refresh.TENCENT_SOURCE_VERSION,
                response_complete=True,
                synthetic_test_only=True, request_started_at=(NOW - timedelta(seconds=2)).isoformat(),
                response_completed_at=(NOW - timedelta(seconds=1)).isoformat(), completed_at=NOW.isoformat(),
                request_url=build_tencent_request_url(refresh.CODES), response_url=build_tencent_request_url(refresh.CODES),
                request_count=1, http_status=200, raw_base64=base64.b64encode(raw).decode(),
                raw_sha256=hashlib.sha256(raw).hexdigest())


def fixture_file(tmp_path, **values):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(dict(raw_base64=base64.b64encode(raw_quote()).decode(), **values)), encoding="utf-8")
    return path


def store(tmp_path):
    return obs.ObservationStore(tmp_path, "synthetic_test_only")


def mark_test_receipt(active, batch):
    assert active.scope == "synthetic_test_only"
    body = obs._json((active.root / refresh.VERSION / "snapshots" / (obs.stable_hash(batch) + ".json")).read_bytes())
    target = active.root / refresh.VERSION / "attempts" / batch
    obs.write_once(target / "response.json", body["response"])
    obs.write_once(active.root / refresh.VERSION / "control" / "active.lock", {"batch_id": batch})
    started = time.perf_counter()
    final = refresh._finish_refresh(active, target, refresh.safe("SUCCEEDED", batch_id=batch, cleanup_complete=True),
        started=started, hard=started + 10, owned=True, cleaned=True, dispatched=True, published=True)
    assert final["status"] == "SUCCEEDED", final


def local_runner(task, *args, **kwargs):
    # Page injection only. Real isolated subprocess cases are separate below.
    result = execute(task)
    return dict(ok=True, cleanup_complete=True, payload=result, elapsed_ms=1, pid=None,
                error_code="", network_fixture=True)


def test_default_gate_no_worker_or_files(tmp_path, monkeypatch):
    monkeypatch.delenv(refresh.NETWORK_SWITCH, raising=False)
    monkeypatch.setattr(refresh, "run_probe_worker_process", lambda *a, **k: pytest.fail("worker dispatched"))
    controller = refresh.RefreshController(store(tmp_path))
    assert controller.refresh(refresh.CODES)["requests_made"] == 0
    real = obs.ObservationStore(tmp_path, "historical")
    assert not refresh.RefreshController(real, network_enabled=True).enabled
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("age,expected", [(0, "MET"), (60, "MET"), (60.000001, "INSUFFICIENT"),
                                         (61, "INSUFFICIENT"), (-1, "INSUFFICIENT")])
def test_age_boundary_recomputed_without_reset(age, expected):
    snap = refresh.decode_response(envelope())
    event = obs._time(snap["rows"]["SZ:000001"]["event_time"])
    # Evaluation before capture is also rejected, so age=0 uses a coherent frozen sample.
    if age <= 5:
        snap["captured_at"] = snap["rows"]["SZ:000001"]["observed_at"] = event.isoformat()
        snap["rows"]["SZ:000001"]["available_at"] = event.isoformat()
    result = refresh.evaluate_current(snap, "000001", obs.make_rule("000001", upper=10),
                                     now=event + timedelta(seconds=age),
                                     attempt={"status": "SUCCEEDED", "batch_id": "first"})
    assert result["conditions"]["upper"]["state"] == expected
    assert result["quote_age_seconds"] == age
    assert result["risk_assessment"] == "NOT_ASSESSED"
    assert not result["data_ready"] and not result["hard_gate_authorized"]
    assert all(result[k] == [] for k in ("records", "candidates", "tickets", "orders"))


@pytest.mark.parametrize("field,value", [("event_time", None), ("event_time", "bad"),
    ("event_time", "2026-11-04T14:50:00+08:00"), ("price_basis", "QFQ"),
    ("security_identity", "SH:000001"), ("price", 0), ("observed_at", None)])
def test_invalid_current_quote_is_insufficient(field, value):
    snap = refresh.decode_response(envelope())
    snap["rows"]["SZ:000001"][field] = value
    result = refresh.evaluate_current(snap, "000001", obs.make_rule("000001", upper=10), now=NOW,
                                     attempt={"status": "SUCCEEDED", "batch_id": "first"})
    assert result["conditions"]["upper"]["state"] == "INSUFFICIENT"


def test_failed_attempt_or_old_selection_never_reuses_old_quote():
    snap = refresh.decode_response(envelope())
    for attempt in (None, {"status": "FAILED", "error": "missing_code"},
                    {"status": "SUCCEEDED", "batch_id": "other"}):
        result = refresh.evaluate_current(snap, "000001", obs.make_rule("000001", upper=10),
                                         now=NOW, attempt=attempt)
        assert result["conditions"]["upper"]["comparison_price"] is None
        assert result["conditions"]["upper"]["state"] == "INSUFFICIENT"
    missing = refresh.evaluate_current(snap, "000002", obs.make_rule("000002", upper=10), now=NOW)
    assert missing["reason"] == "本轮刷新范围未支持"


def test_provider_rejects_partial_identity_response_and_storage_tampering(tmp_path):
    with pytest.raises(ValueError, match="adapter_rejected"):
        refresh.decode_response(envelope(codes=refresh.CODES[:-1]))
    data = envelope()
    data["codes"][-1] = "000002"
    with pytest.raises(ValueError):
        refresh.decode_response(data)
    active = store(tmp_path)
    snap = active.save_refresh(envelope())
    assert not active.refresh_snapshots()  # Producer bytes alone do not publish success.
    mark_test_receipt(active, "first")
    assert active.refresh_snapshots() == [snap]
    assert not obs.ObservationStore(tmp_path, "historical").refresh_snapshots()
    with pytest.raises(FileExistsError):
        active.save_refresh(envelope())
    path = next((active.root / refresh.VERSION / "snapshots").glob("*.json"))
    body = json.loads(path.read_text(encoding="utf-8"))
    body["response"]["batch_id"] = "other"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="sha"):
        active.refresh_snapshots()


def test_raw_hash_batch_scope_and_time_contract():
    for field, value in [("raw_sha256", "a" * 64), ("request_count", 2),
                         ("request_url", "https://example.invalid"), ("batch_id", None),
                         ("synthetic_test_only", "true"), ("completed_at", "2000-01-01T00:00:00+08:00")]:
        data = envelope()
        data[field] = value
        with pytest.raises(ValueError):
            refresh.decode_response(data)


def test_success_actual_worker_once_and_cooldown(tmp_path, monkeypatch):
    monkeypatch.delenv(refresh.NETWORK_SWITCH, raising=False)
    active = store(tmp_path)
    controller = refresh.RefreshController(active, fixture=fixture_file(tmp_path))
    result = controller.refresh(refresh.CODES)
    assert result["status"] == "SUCCEEDED", result
    assert result["requests_made"] == 1 and result["process"]["cleanup_complete"]
    assert result["process"]["payload"]["network_attempts"] == 0
    assert result["elapsed_ms"] < 10000
    assert len(active.refresh_snapshots()) == 1
    assert not (controller.state / "active.lock").exists()
    second = refresh.RefreshController(active, fixture=controller.fixture).refresh(refresh.CODES)
    assert second["status"] == "REJECTED" and second["requests_made"] == 0
    assert "cooldown" in second["error"]
    assert len(list((active.root / refresh.VERSION / "attempts").iterdir())) == 1


def test_concurrent_and_reentrant_clicks_do_not_dispatch(tmp_path, monkeypatch):
    active = store(tmp_path)
    controller = refresh.RefreshController(active, fixture=fixture_file(tmp_path))
    entered, release = threading.Event(), threading.Event()
    calls = []
    def runner(task, *args, **kwargs):
        calls.append(task)
        entered.set()
        assert release.wait(3)
        return local_runner(task)
    monkeypatch.setattr(refresh, "run_probe_worker_process", runner)
    results = []
    thread = threading.Thread(target=lambda: results.append(controller.refresh(refresh.CODES)))
    thread.start()
    try:
        assert entered.wait(3)
        second = refresh.RefreshController(active, fixture=controller.fixture).refresh(refresh.CODES)
        assert second["requests_made"] == 0 and "FileExistsError" in second["error"]
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and len(calls) == 1
    assert results[0]["status"] == "SUCCEEDED", results


def test_partial_response_retained_failure_no_success_snapshot(tmp_path):
    file = fixture_file(tmp_path)
    file.write_text(json.dumps({"raw_base64": base64.b64encode(raw_quote(codes=refresh.CODES[:-1])).decode()}))
    active = store(tmp_path)
    result = refresh.RefreshController(active, fixture=file).refresh(refresh.CODES)
    assert result["status"] == "FAILED" and result["cleanup_complete"]
    raw = json.loads((Path(result["output"]) / "response-received.json").read_text())
    assert hashlib.sha256(base64.b64decode(raw["raw_base64"])).hexdigest() == raw["raw_sha256"]
    assert not active.refresh_snapshots()


def test_real_process_timeout_reaped_under_total_budget(tmp_path):
    controller = refresh.RefreshController(store(tmp_path), fixture=fixture_file(tmp_path, delay_seconds=30))
    started = time.perf_counter()
    result = controller.refresh(refresh.CODES)
    assert time.perf_counter() - started < 10
    assert result["status"] == "FAILED" and result["process"]["request_timed_out"]
    assert result["cleanup_complete"] and result["process"]["tree_empty_observed"]
    assert result["process"]["pipes_closed"] and not controller.store.refresh_snapshots()


def test_cleanup_unknown_persists_guard_and_blocks_next(tmp_path, monkeypatch):
    controller = refresh.RefreshController(store(tmp_path), fixture=fixture_file(tmp_path))
    calls = []
    def bad(*args, **kwargs):
        calls.append(1)
        return dict(ok=False, cleanup_complete=False, error_code="WORKER_CLEANUP_INCOMPLETE")
    monkeypatch.setattr(refresh, "run_probe_worker_process", bad)
    first = controller.refresh(refresh.CODES)
    second = controller.refresh(refresh.CODES)
    assert first["status"] == "FAILED" and first["cleanup_complete"] is False
    assert "cleanup_incomplete_block" in second["error"]
    assert len(calls) == 1 and (controller.state / "active.lock").exists()


def test_store_write_failure_and_process_exception_do_not_look_successful(tmp_path, monkeypatch):
    active = store(tmp_path)
    controller = refresh.RefreshController(active, fixture=fixture_file(tmp_path))
    monkeypatch.setattr(refresh, "run_probe_worker_process", local_runner)
    monkeypatch.setattr(active, "save_refresh", lambda *args: (_ for _ in ()).throw(OSError("fixture disk denied")))
    result = controller.refresh(refresh.CODES)
    assert result["status"] == "FAILED" and not active.refresh_snapshots()
    assert result["cleanup_complete"] and "disk denied" in result["error"]


@pytest.mark.parametrize("failure_name", ["receipt.json", "release-"])
def test_final_publication_failure_keeps_guard_and_hides_candidate(tmp_path, monkeypatch, failure_name):
    active = store(tmp_path)
    controller = refresh.RefreshController(active, fixture=fixture_file(tmp_path))
    monkeypatch.setattr(refresh, "run_probe_worker_process", local_runner)
    original = obs.write_once
    def fail(path, payload):
        if Path(path).name.startswith(failure_name):
            raise OSError("fixture finalization denied")
        return original(path, payload)
    monkeypatch.setattr(obs, "write_once", fail)
    result = controller.refresh(refresh.CODES)
    assert result["status"] == "FAILED"
    assert (controller.state / "active.lock").exists()
    assert not active.refresh_snapshots()
    assert controller.refresh(refresh.CODES)["requests_made"] == 0


def test_cooldown_exact_boundary_no_clock_reset(tmp_path, monkeypatch):
    controller = refresh.RefreshController(store(tmp_path), fixture=fixture_file(tmp_path))
    monkeypatch.setattr(refresh, "run_probe_worker_process", local_runner)
    assert controller.refresh(refresh.CODES)["status"] == "SUCCEEDED"
    release = obs._json(next(controller.state.glob("release-*.json")).read_bytes())["dispatch_monotonic"]
    monkeypatch.setattr(refresh.time, "monotonic", lambda: release + 9.999)
    assert controller.refresh(refresh.CODES)["requests_made"] == 0
    monkeypatch.setattr(refresh.time, "monotonic", lambda: release + 10)
    assert controller.refresh(refresh.CODES)["status"] == "SUCCEEDED"


@pytest.mark.parametrize("value", [True, -1, float("-inf"), "unknown"])
def test_corrupt_cooldown_record_is_not_permission(tmp_path, monkeypatch, value):
    controller = refresh.RefreshController(store(tmp_path), fixture=fixture_file(tmp_path))
    controller.state.mkdir(parents=True)
    (controller.state / "release-bad.json").write_text(json.dumps({"dispatch_monotonic": value}))
    monkeypatch.setattr(refresh, "run_probe_worker_process", lambda *a, **k: pytest.fail("worker dispatched"))
    assert controller.refresh(refresh.CODES)["requests_made"] == 0


def test_real_transport_path_one_open_no_redirect_and_five_second_timeout(tmp_path, monkeypatch):
    import urllib.request
    from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard
    guard = AppTestNetworkGuard(monkeypatch)
    monkeypatch.setenv(refresh.NETWORK_SWITCH, "1")
    target = tmp_path / "attempt"
    target.mkdir()
    task = dict(contract=refresh.VERSION, batch_id="isolated-real-path-test", codes=list(refresh.CODES),
                output=str(target), synthetic_test_only=False, fixture=None)
    events = []
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            events.append(("read", size))
            return raw_quote()
        def geturl(self):
            return build_tencent_request_url(refresh.CODES)
    class Opener:
        def open(self, request, *, timeout):
            events.append(("open", request.full_url, timeout))
            return Response()
    def build(handler):
        with pytest.raises(ValueError, match="redirect_forbidden"):
            handler.redirect_request(None, None, None, None, None, None)
        return Opener()
    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert execute(task)["request_count"] == 1
    assert events == [("open", build_tencent_request_url(refresh.CODES), 5), ("read", 262145)]
    assert not any(guard.business_attempts.values())


def test_two_snapshots_rule_changes_remain_historical_not_freshness_reset(tmp_path):
    first = refresh.decode_response(envelope())
    later = envelope("second", event=NOW + timedelta(seconds=10), price="11")
    for field in ("request_started_at", "response_completed_at", "completed_at"):
        later[field] = (obs._time(later[field]) + timedelta(seconds=20)).isoformat()
    active = store(tmp_path)
    active.save_refresh(envelope())
    mark_test_receipt(active, "first")
    second = active.save_refresh(later)
    mark_test_receipt(active, "second")
    rule = obs.make_rule("000001", upper=11)
    changed = obs.make_rule("000001", upper=12)
    result = obs.compare(first, second, "000001", rule, changed, now=NOW + timedelta(minutes=2))
    assert result["comparable"] and result["rule_changed"] and "不能归因" in result["message"]
    assert result["price_change"] == "0.5"
    assert len(active.refresh_snapshots()) == 2


def test_actual_page_refresh_explain_save_compare_and_zero_passive_network(tmp_path, monkeypatch, record_property):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard
    from overnight_quant.ui import offline_observation as ui
    from overnight_quant.ui import dashboard
    from overnight_quant.ui import quote_observation_refresh as refresh_ui
    active = store(tmp_path)
    file = fixture_file(tmp_path)
    injected = refresh.RefreshController(active, fixture=file)
    monkeypatch.setattr(ui, "DEFAULT_STORE", tmp_path)
    original_render = refresh_ui.render_refresh
    monkeypatch.setattr(ui, "render_refresh", lambda st, store, state, prefix, **kwargs:
                        original_render(st, store, state, prefix, controller=injected))
    calls = []
    def runner(task, *args, **kwargs):
        calls.append(task)
        return local_runner(task)
    monkeypatch.setattr(refresh, "run_probe_worker_process", runner)
    guard = AppTestNetworkGuard(monkeypatch)
    monkeypatch.setattr(dashboard, "run_dashboard_action", guard.deny_legacy_action)
    monkeypatch.setattr(dashboard, "run_approved_action", guard.deny_legacy_action)
    path = Path(ui.__file__).with_name("dashboard.py")
    app = AppTest.from_file(str(path), default_timeout=15).run()
    assert not app.exception and not calls
    next(x for x in app.radio if x.label == "材料空间").set_value("测试材料 · test-only").run()
    next(x for x in app.radio if x.label == "观察方式").set_value("主动刷新观察").run()
    assert not app.exception and not calls
    click = lambda label: next(x for x in app.button if x.label == label).click().run()
    click("刷新一次报价")
    assert not app.exception and len(calls) == 1
    assert any("本次报价已保存" in x.value for x in app.success)
    assert any("尚无可比较快照" in x.value for x in app.info)
    next(x for x in app.selectbox if x.label == "设置条件的证券").select("SZ:000001").run()
    next(x for x in app.text_input if x.label == "上沿：价格达到或高于").input("11")
    click("保存条件")
    click("加入跟踪")
    assert len(calls) == 1 and not app.exception
    # Advance only the persisted cooldown clock, not event/capture/freshness time.
    original_monotonic = time.monotonic
    monkeypatch.setattr(refresh.time, "monotonic", lambda: original_monotonic() + 20)
    time.sleep(3.1)  # Second native quote must be later than first response completion.
    file.write_text(json.dumps({"raw_base64": base64.b64encode(raw_quote(price="11")).decode()}))
    click("刷新一次报价")
    assert not app.exception and len(calls) == 2
    batches = [s["batch_id"] for s in active.refresh_snapshots()]
    first = calls[0]["batch_id"]
    second = calls[1]["batch_id"]
    assert set(batches) == {first, second}
    next(x for x in app.selectbox if x.label == "观察快照").select(first).run()
    next(x for x in app.selectbox if x.label == "对比快照").select(second).run()
    assert not app.exception and any("0.5" in str(x.value) for x in app.metric)
    assert len(calls) == 2 and not any(guard.business_attempts.values())
    assert any("test-only" in x.value for x in app.info)
    record_property("fixture_quote_requests", len(calls))
    record_property("business_attempts", json.dumps(guard.business_attempts))
