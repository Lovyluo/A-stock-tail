"""All positive composite captures in this file are SYNTHETIC / TEST ONLY."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import random
import socket
from urllib.parse import urlencode

import pytest

from overnight_quant.data import complete_industry_candidate as combined
from overnight_quant.data import eastmoney_industry_strength_candidate as strength
from overnight_quant.data.close_confirmation_readiness import (
    materialize_point_in_time_records, validate_close_confirmation_readiness,
)
from overnight_quant.data.eastmoney_derived_industry_breadth import (
    CORE_CONCEPTION_URL, EXPECTED_INDUSTRIES, SOURCE_VERSION as WIDTH_VERSION,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import EASTMONEY_CLIST_URL, MARKET_DOTNET_TRANSPORT_VERSION
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import validate_record
from overnight_quant.data.source_capability_adapters import (
    SourceProviderEnvelope, audit_source_adapters, compute_source_adapter_registry_hash, execute_source_adapter,
)
from overnight_quant.data.source_capability_registry import compute_source_capability_registry_hash
from overnight_quant.data.t24_offline_rehearsal import FIXTURE_TIMES, synthetic_fixture
from overnight_quant.scripts.run_complete_industry_candidate import main
from overnight_quant.strategy.close_confirmation_v1.features import build_close_confirmation_features


DAY = "2026-09-18"
TIMES = deepcopy(FIXTURE_TIMES)
BATCH_ID = stable_hash({"SYNTHETIC_TEST_ONLY": "T30"})
BASE = datetime(2026, 9, 18, 14, 49, 10, tzinfo=CN_TZ)


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("T30 tests must never connect")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def _context(test_only=True):
    return {"batch_id": BATCH_ID, "trade_date": DAY, "test_only": test_only,
            "time_contract_hash": stable_hash(TIMES)}


def _capture(url, params, payload, index, *, version=WIDTH_VERSION, is_strength=False):
    """Fabricated response and worker timestamps, not real evidence."""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    start = BASE + timedelta(milliseconds=index * 600)
    end = start + timedelta(milliseconds=20)
    ps, pe = start - timedelta(milliseconds=5), end + timedelta(milliseconds=5)
    return {**_context(), "origin_source": "eastmoney", "adapter": "direct_http",
        "source_version": version, "transport_version": MARKET_DOTNET_TRANSPORT_VERSION,
        "method": "GET", "url": url, "params": sorted(params.items()),
        "response_url": url + "?" + urlencode(params), "http_status_code": 200,
        "redirect_chain": [], "request_hash": stable_hash({"method": "GET", "url": url, "params": sorted(params.items())}),
        "raw_hash": hashlib.sha256(raw).hexdigest(), "content_base64": base64.b64encode(raw).decode(),
        "byte_count": len(raw), "request_started_at": start.isoformat(),
        "request_completed_at": end.isoformat(), "process_started_at": ps.isoformat(),
        "process_completed_at": pe.isoformat(), "process_elapsed_ms": 30.0,
        "observed_at": (pe if is_strength else ps).isoformat(),
        "available_at": (pe + timedelta(milliseconds=5)).isoformat(), "worker_terminated": False,
        "client_call_started_monotonic_seconds": 1000 + index * .6,
        "http_start_coordination_version": "http_client_call_spacing_v1",
        "request_started_at_semantics": "client_SendAsync_call_boundary_not_packet_transmission",
        "request_completed_at_semantics": "response_body_fully_read"}


def synthetic_batch(*, multi_page=False):
    tree = {"mappings": [], "boards": {}}
    index = 0
    for code, (board, name) in EXPECTED_INDUSTRIES.items():
        params = {"code": ("SH" if code.startswith("6") else "SZ") + code}
        capture = _capture(CORE_CONCEPTION_URL, params, {"ssbk": [{
            "SECURITY_CODE": code, "BOARD_CODE": board[2:], "BOARD_NAME": name, "BOARD_RANK": 2,
        }]}, index)
        capture.update(kind="mapping", identity=code)
        tree["mappings"].append(capture)
        index += 1
    members = {
        "BK0475": [("000001", 1), ("600000", -1)],
        "BK1239": [("000333", 0), ("000651", "-"), ("002032", "-"), ("000921", -2)],
        "BK1277": [("600519", -1), ("000858", 2)],
        "BK0474": [("601318", 3), ("601601", 0)],
    }
    if multi_page:
        members["BK0475"].extend((f"{1000+i:06d}", i % 3 - 1) for i in range(100))
    for board in combined.BOARDS:
        rows = [{"f12": code, "f14": code, "f2": 10, "f3": change,
                 "f124": int((BASE - timedelta(seconds=7)).timestamp())} for code, change in members[board]]
        tree["boards"][board] = []
        for page, offset in enumerate(range(0, len(rows), 100), 1):
            params = {"pn": str(page), "pz": "100", "po": "1", "np": "1", "fltt": "2",
                      "invt": "2", "fid": "f3", "fs": f"b:{board}", "fields": "f2,f3,f12,f14,f124"}
            capture = _capture(EASTMONEY_CLIST_URL, params, {"data": {
                "total": len(rows), "diff": rows[offset:offset+100]}}, index)
            capture.update(kind="board_page", identity=f"{board}:{page}")
            tree["boards"][board].append(capture)
            index += 1
    captures = []
    for board, value in zip(combined.BOARDS, (-55, 0, -146, 99)):
        request = strength.board_strength_request(board)
        capture = _capture(request["url"], request["params"], {"rc": 0, "data": {
            "f57": board, "f58": strength.BOARD_NAMES[board], "f170": value,
            "f152": 2, "f86": int((BASE - timedelta(seconds=6)).timestamp()),
        }}, index, version=strength.SOURCE_VERSION, is_strength=True)
        capture["contract_version"] = strength.CONTRACT_VERSION
        captures.append({**_context(), "board_code": board, "capture": capture})
        index += 1
    return {"schema_version": combined.INPUT_SCHEMA, "batch_id": BATCH_ID, "trade_date": DAY,
        "test_only": True, "codes": list(combined.FIXED_CODES), "temporal_contract": deepcopy(TIMES),
        "started_at": (BASE - timedelta(seconds=1)).isoformat(), "completed_at": f"{DAY}T14:50:05+08:00",
        "breadth": {**_context(), "source_version": WIDTH_VERSION, "request_tree": tree},
        "strength": {**_context(), "source_version": strength.SOURCE_VERSION, "captures": captures}}


def run(batch):
    return combined.CompleteIndustryCandidateProvider(combined.CompleteIndustryCandidateEnvelope(
        combined.PROVIDER_KEY, batch)).collect_batch()


def assert_safe(result):
    assert result["data_ready"] is result["hard_gate_authorized"] is False
    assert result["records"] == result["candidates"] == result["tickets"] == result["orders"] == []
    assert result["provider_activated"] is result["provider_contract_complete"] is False
    assert result["qualification"] == "0/3" and result["network_requests_made"] == 0


def reseal(leaf, change):
    data = json.loads(base64.b64decode(leaf["content_base64"]))
    change(data)
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    leaf.update(content_base64=base64.b64encode(raw).decode(), byte_count=len(raw), raw_hash=hashlib.sha256(raw).hexdigest())


def test_complete_synthetic_five_stock_batch_preserves_units_zero_negative_invalid_and_lineage():
    batch = synthetic_batch()
    saved = deepcopy(batch)
    result = run(batch)
    assert result["execution_ok"], result["errors"]
    assert_safe(result)
    assert result["candidate_batch_complete"] and result["captured_request_count"] == 13
    assert result["strength_request_count"] == 4
    assert batch == saved
    for row in result["audit_records"]:
        assert not validate_record(row, expected_times=TIMES)
        assert row["test_only"] is True and row["event_time"].endswith("14:49:04+08:00")
        assert "relative_strength_pct" not in row["payload"]
        assert row["payload"]["field_units"]["change_pct"] == "percent"
        assert all(child["raw_hash"] for child in row["component_provenance"])
    white = next(r for r in result["audit_records"] if r["payload"]["code"] == "000333")
    assert white["payload"]["invalid_count"] == 2 and white["payload"]["flat_count"] == 1
    assert {r["payload"]["change_pct"] for r in result["audit_records"]} == {-.55, 0, -1.46, .99}


def test_order_security_aliases_and_complete_pages_replay_deterministically():
    batch = synthetic_batch(multi_page=True)
    original = run(batch)
    assert original["execution_ok"], original["errors"]
    assert original["captured_request_count"] == 14
    batch["codes"] = ["SZ000333", "600000.SH", "000001", "SH600519", "601318.SH", "000001.SZ"]
    random.Random(7).shuffle(batch["breadth"]["request_tree"]["mappings"])
    random.Random(9).shuffle(batch["strength"]["captures"])
    for pages in batch["breadth"]["request_tree"]["boards"].values():
        pages.reverse()
    assert run(batch) == original


@pytest.mark.parametrize("component,key,value", [
    ("breadth", "batch_id", "b"*64), ("strength", "trade_date", "2026-10-15"),
    ("strength", "time_contract_hash", "c"*64), ("breadth", "test_only", False),
    ("breadth", "source_version", "wrong"),
])
def test_component_batch_date_scope_or_version_cannot_be_mixed(component, key, value):
    batch = synthetic_batch()
    batch[component][key] = value
    result = run(batch)
    assert not result["execution_ok"] and result["audit_records"] == []
    assert_safe(result)


@pytest.mark.parametrize("change", ["mapping_missing", "strength_missing", "strength_duplicate", "board_missing",
                                    "wrong_mapping", "wrong_strength", "page_gap", "duplicate_constituent", "missing_member"])
def test_incomplete_or_mismatched_child_invalidates_whole_candidate(change):
    batch = synthetic_batch()
    tree = batch["breadth"]["request_tree"]
    if change == "mapping_missing": tree["mappings"].pop()
    elif change == "strength_missing": batch["strength"]["captures"].pop()
    elif change == "strength_duplicate": batch["strength"]["captures"].append(deepcopy(batch["strength"]["captures"][0]))
    elif change == "board_missing": tree["boards"].pop("BK1239")
    elif change == "wrong_mapping": reseal(tree["mappings"][0], lambda p: p["ssbk"][0].update(BOARD_CODE="1277"))
    elif change == "wrong_strength": reseal(batch["strength"]["captures"][0]["capture"], lambda p: p["data"].update(f57="BK9999"))
    elif change == "page_gap": tree["boards"]["BK0475"][0]["identity"] = "BK0475:2"
    elif change == "duplicate_constituent": reseal(tree["boards"]["BK0475"][0], lambda p: p["data"]["diff"].append(p["data"]["diff"][0]))
    else: reseal(tree["boards"]["BK1239"][0], lambda p: p["data"]["diff"][0].update(f12="000002"))
    result = run(batch)
    assert not result["execution_ok"] and result["audit_records"] == []
    assert_safe(result)


@pytest.mark.parametrize("failure", ["missing_page", "total_drift", "extra_page"])
def test_pagination_cannot_be_repaired_or_partially_returned(failure):
    batch = synthetic_batch(multi_page=True)
    pages = batch["breadth"]["request_tree"]["boards"]["BK0475"]
    if failure == "missing_page": pages.pop()
    elif failure == "total_drift": reseal(pages[1], lambda p: p["data"].update(total=103))
    else: pages.append(deepcopy(pages[-1]))
    result = run(batch)
    assert not result["execution_ok"] and not result["audit_records"]
    assert_safe(result)


@pytest.mark.parametrize("field,value", [("http_status_code", 403), ("http_status_code", True),
    ("source_version", "wrong"), ("request_hash", "a"), ("raw_hash", "b"*64),
    ("available_at", f"{DAY}T14:51:05.001+08:00"), ("process_elapsed_ms", 2000.001),
    ("batch_id", "0"*64), ("test_only", 1), ("transport_version", "urllib"),
    ("redirect_chain", ["https://other.invalid/"]), ("client_call_started_monotonic_seconds", None)])
def test_child_failure_metadata_hash_and_time_are_fail_closed(field, value):
    batch = synthetic_batch()
    batch["breadth"]["request_tree"]["mappings"][0][field] = value
    result = run(batch)
    assert not result["execution_ok"] and not result["audit_records"]
    assert_safe(result)


@pytest.mark.parametrize("event", [f"{DAY}T14:50:00.001+08:00", "2026-09-17T14:49:00+08:00", f"{DAY}T14:49:50+08:00"])
def test_every_constituent_native_time_is_checked_not_only_the_maximum(event):
    batch = synthetic_batch()
    stamp = int(datetime.fromisoformat(event).timestamp())
    if ".001" in event: stamp += 1
    reseal(batch["breadth"]["request_tree"]["boards"]["BK1239"][0], lambda p: p["data"]["diff"][1].update(f124=stamp))
    assert not run(batch)["execution_ok"]


def test_strength_late_completion_and_missing_precision_never_make_partial_records():
    for missing_precision in (False, True):
        batch = synthetic_batch()
        capture = batch["strength"]["captures"][1]["capture"]
        if missing_precision: reseal(capture, lambda p: p["data"].pop("f152"))
        else: capture["request_completed_at"] = f"{DAY}T14:51:05.001+08:00"
        result = run(batch)
        assert not result["execution_ok"] and not result["audit_records"]
        assert_safe(result)


@pytest.mark.parametrize("field,value", [("process_elapsed_ms", 2000.001),
                                        ("process_elapsed_ms", None), ("worker_terminated", True)])
def test_strength_monotonic_budget_and_worker_completion_are_required(field, value):
    batch = synthetic_batch()
    batch["strength"]["captures"][0]["capture"][field] = value
    result = run(batch)
    assert not result["execution_ok"] and not result["audit_records"]
    assert "candidate_child_deadline_exceeded" in result["errors"]
    assert_safe(result)


def test_actual_client_call_spacing_is_not_replaced_by_parent_dispatch_spacing():
    batch = synthetic_batch()
    pages = batch["breadth"]["request_tree"]["boards"]
    pages["BK0475"][0]["client_call_started_monotonic_seconds"] = pages["BK0474"][0]["client_call_started_monotonic_seconds"] + .1
    result = run(batch)
    assert not result["execution_ok"] and "candidate_client_call_spacing_invalid" in result["errors"]


def test_more_than_two_overlapping_processes_is_rejected():
    batch = synthetic_batch()
    batch["started_at"] = f"{DAY}T14:49:07+08:00"
    for leaf in batch["breadth"]["request_tree"]["mappings"]:
        ps = datetime.fromisoformat(leaf["request_started_at"]) - timedelta(milliseconds=1400)
        leaf["process_started_at"] = leaf["observed_at"] = ps.isoformat()
        leaf["process_elapsed_ms"] = 1425.0
    result = run(batch)
    assert not result["execution_ok"] and "candidate_worker_limit_invalid" in result["errors"]


def test_cross_day_captures_cannot_be_composed_even_if_batch_labels_are_resigned():
    batch = synthetic_batch()
    capture = batch["strength"]["captures"][0]["capture"]
    for key in ("request_started_at", "request_completed_at", "process_started_at", "process_completed_at", "observed_at", "available_at"):
        capture[key] = capture[key].replace(DAY, "2026-10-15")
    reseal(capture, lambda p: p["data"].update(f86=int(datetime(2026, 10, 15, 14, 37, tzinfo=CN_TZ).timestamp())))
    result = run(batch)
    assert not result["execution_ok"] and not result["audit_records"]
    assert_safe(result)


def test_no_bare_callable_or_wrong_envelope_can_execute():
    calls = []
    for envelope in (lambda: calls.append(1), combined.CompleteIndustryCandidateEnvelope("wrong", synthetic_batch())):
        result = combined.CompleteIndustryCandidateProvider(envelope).collect_batch()
        assert not result["execution_ok"]
        assert_safe(result)
    assert calls == []


def test_public_production_adapter_never_calls_or_authorizes_new_candidate():
    calls = []
    envelope = SourceProviderEnvelope(combined.PROVIDER_KEY, lambda: calls.append(1))
    for version in (combined.SOURCE_VERSION, WIDTH_VERSION):
        result = execute_source_adapter("industry_snapshot", origin_source="eastmoney", adapter="direct_http",
                                        source_version=version, provider_envelope=envelope, environ={})
        assert not result["provider_called"] and result["hard_gate_authorized"] is False
        assert result["data_ready"] is False
    assert calls == []
    audit = audit_source_adapters(environ={})
    assert audit["bound_count"] == 8 and audit["candidate_count"] == 7 and audit["adapter_entry_count"] == 32
    assert compute_source_capability_registry_hash() == "dc542eb72213e483827f7fd9f2b357f56f18bda2af101b36012b88f73d8756ec"
    assert compute_source_adapter_registry_hash() == "bb00c1311f8d214c9e2d088943675aac5a7980b69362ca791470c237beb0b9c1"


def test_synthetic_industry_reaches_existing_materialization_readiness_and_features_without_quote_repair():
    result = run(synthetic_batch())
    assert result["execution_ok"], result["errors"]
    quotes = [r for r in synthetic_fixture() if r["data_type"] == "quote"]
    saved = deepcopy(quotes)
    snapshot = {"trade_date": DAY, "decision_time": TIMES["decision_time"], "records": [*quotes, *result["audit_records"]]}
    readiness = validate_close_confirmation_readiness(snapshot)
    materialized = materialize_point_in_time_records(snapshot["records"])
    assert len(materialized["stocks"]) == 5
    for stock in materialized["stocks"]:
        assert stock["industry_association"]["board_code"] == EXPECTED_INDUSTRIES[stock["code"]][0]
        assert stock["industry"]["change_pct"] is not None
        features = build_close_confirmation_features(stock, decision_time=TIMES["decision_time"])
        assert features["industry_relative_strength"] is not None
    assert all("industry_strength_missing" not in state["errors"] for state in readiness["stock_readiness"].values())
    assert readiness["data_ready"] is False
    assert quotes == saved
    assert_safe(result)


def test_non_test_records_remain_blocked_by_existing_frozen_admission():
    batch = synthetic_batch()
    def change_scope(value):
        if isinstance(value, dict):
            if "test_only" in value: value["test_only"] = False
            for child in value.values(): change_scope(child)
        elif isinstance(value, list):
            for child in value: change_scope(child)
    change_scope(batch)
    result = run(batch)
    assert result["execution_ok"]
    assert all("INDUSTRY_STRENGTH_NATIVE_SCHEMA_UNPROVEN" in validate_record(row) for row in result["audit_records"])
    assert_safe(result)


def test_cli_external_anchor_atomic_write_and_byte_identical_replay(tmp_path, capsys):
    source = tmp_path / "synthetic-test-only.json"
    source.write_text(json.dumps({"provider_key": combined.PROVIDER_KEY, "batch": synthetic_batch()}, ensure_ascii=False), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    outputs = [tmp_path / "replay-a.json", tmp_path / "replay-b.json"]
    for out in outputs:
        assert main(["--input", str(source), "--expected-file-sha256", digest, "--output", str(out)]) == 0
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    before = outputs[0].read_bytes()
    assert main(["--input", str(source), "--expected-file-sha256", digest, "--output", str(outputs[0])]) == 2
    assert outputs[0].read_bytes() == before
    changed = json.loads(source.read_bytes())
    changed["batch"]["strength"]["captures"][0]["capture"]["raw_hash"] = "0"*64
    source.write_text(json.dumps(changed), encoding="utf-8")
    assert main(["--input", str(source), "--expected-file-sha256", digest]) == 2
    assert "candidate_input_external_sha256_mismatch" in capsys.readouterr().out


def test_plan_counts_dependencies_and_conservative_budget_do_not_expand_execution_limits():
    result = combined.complete_industry_audit_plan(DAY)
    assert result["request_count"] == 13 and result["maximum_request_count"] == 16
    assert result["conservative_total_ms"] == 33000
    assert result["planned_1437_feasible"] is True and result["approved_latest_start_safe_under_bound"] is False
    assert result["max_workers"] == result["same_host_in_flight_limit"] == 2
    assert result["actual_client_start_interval_ms"] == 500
    assert result["scheduling_authorized"] is False
    pages = dict.fromkeys(combined.BOARDS, 2)
    pages["BK1239"] = 1
    worst = combined.complete_industry_audit_plan(DAY, page_counts=pages)
    assert worst["request_count"] == 16 and worst["conservative_total_ms"] == 40500
    with pytest.raises(ValueError): combined.complete_industry_audit_plan(DAY, page_counts=dict.fromkeys(combined.BOARDS, 2))
    assert main(["--plan", "--date", DAY]) == 0
    assert_safe(result)
