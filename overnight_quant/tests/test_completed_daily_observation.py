from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from overnight_quant.data.completed_daily_observation import (
    CONTRACT_HASH, MANIFEST_SCHEMA, observe, read_anchored_json,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import FIXED_CODES
from overnight_quant.data.static_source_providers import (
    SOURCE_IDENTITIES, STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION,
    StaticHttpResponse, StaticSourceProviders, TENCENT_KLINE_URL,
)
from overnight_quant.scripts.run_static_source_validation import compute_evidence_hash
from overnight_quant.scripts.run_completed_daily_observation import main

RUN = "2026-09-17"
NOW = "2026-09-17T23:00:00+08:00"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("external networking forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def seal(evidence):
    evidence["evidence_hash"] = compute_evidence_hash(evidence)
    return evidence


def file_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def manifest_for(evidence):
    return {
        "schema_version": MANIFEST_SCHEMA, "contract_hash": CONTRACT_HASH,
        "provenance_kind": "synthetic_test_only", "batch_id": "synthetic-batch-only",
        "run_date": RUN, "source_file_sha256": file_hash(evidence),
        "frozen_at": "2026-09-17T22:00:00+08:00",
        "capture_bindings": [
            {"batch_id": "synthetic-batch-only", "capability": capability,
             "source_version": SOURCE_IDENTITIES[capability][2],
             "raw_hash": response["raw_hash"], "request_hash": response["request_hash"]}
            for capability in ("trading_calendar", "daily_bar_qfq")
            for response in evidence["raw_responses"][capability]
        ],
    }


def sample():
    days, current = [], date(2026, 9, 16)
    while len(days) < 70:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current -= timedelta(days=1)
    days.sort()

    class LocalOnlyTransport:
        def request(self, method, url, **kwargs):
            symbol, _, _, _, count, adjustment = kwargs["params"]["param"].split(",")
            assert method == "GET" and url == TENCENT_KLINE_URL and adjustment == "qfq"
            rows = [[day, str(100 + i), str(100 + i), str(101 + i), str(99 + i), "1000"]
                    for i, day in enumerate(days)]
            return StaticHttpResponse(json.dumps({"data": {symbol: {"qfqday": rows}}}).encode(),
                                      200, TENCENT_KLINE_URL)

    clock = iter(datetime(2026, 9, 17, 21, tzinfo=CN_TZ) + timedelta(seconds=i)
                 for i in range(12))
    provider = StaticSourceProviders(FIXED_CODES, target_trade_date=RUN,
                                    feature_cutoff=f"{RUN}T14:50:00+08:00",
                                    transport=LocalOnlyTransport(), clock=lambda: next(clock))
    calendar = provider.collect_trading_calendar_batch()
    daily = provider.collect_qfq_daily_batch(days)
    evidence = {
        "synthetic_test_only": True,
        "evidence_schema_version": STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION,
        "trade_date": RUN, "feature_cutoff": f"{RUN}T14:50:00+08:00",
        "requested_codes": list(FIXED_CODES), "raw_responses": {}, "records_by_capability": {},
    }
    for capability, batch in (("trading_calendar", calendar), ("daily_bar_qfq", daily)):
        evidence["records_by_capability"][capability] = list(batch.records)
        evidence["raw_responses"][capability] = []
        for original in batch.responses:
            response = dict(original)
            raw = response.pop("raw_bytes")
            response["byte_count"] = len(raw)
            response["content_base64"] = base64.b64encode(raw).decode()
            evidence["raw_responses"][capability].append(response)
    return seal(evidence)


def run(evidence=None, manifest=None, **kwargs):
    evidence = sample() if evidence is None else evidence
    return observe(evidence, source_file_sha256=file_hash(evidence),
                   manifest=manifest_for(evidence) if manifest is None else manifest,
                   run_date=RUN, generated_at=NOW, synthetic_test_only=True, **kwargs)


def safe(result):
    assert result["risk_assessment"] == "NOT_ASSESSED"
    assert result["qualification"] == "0/3"
    assert result["data_ready"] is result["hard_gate_authorized"] is False
    for field in ("records", "candidates", "tickets", "orders"):
        assert result[field] == []


def rejected(result, reason):
    safe(result)
    assert not result["observation_input_ready"]
    assert result["observation_ranking"] == []
    assert reason in str(result["errors"])


def test_formula_uses_21_closes_20_trading_sessions_and_ties():
    result = run()
    safe(result)
    assert result["observation_input_ready"] and result["synthetic_test_only"]
    ranking = result["observation_ranking"]
    assert [r["security_identity"] for r in ranking] == sorted(r["security_identity"] for r in ranking)
    assert [r["rank"] for r in ranking] == list(range(1, 6))
    for row in ranking:
        assert row["start_close"] == 149 and row["end_close"] == 169
        assert row["R20"] == pytest.approx(100 * (169 / 149 - 1))
        assert (date.fromisoformat(row["end_date"]) - date.fromisoformat(row["start_date"])).days > 20
        assert row["end_date"] == "2026-09-16"
        assert len(row["evidence_references"]) == 2


@pytest.mark.parametrize("field,value,reason", [
    ("batch_id", "", "batch_identity_missing"),
    ("run_date", "2026-09-16", "manifest_run_date_mismatch"),
    ("frozen_at", "2026-09-17T20:59:59+08:00", "capture_available_or_date_invalid"),
    ("frozen_at", "2026-09-17T23:00:01+08:00", "freeze_time_invalid"),
    ("frozen_at", "2026-09-17T22:00:00", "timestamp_timezone_invalid"),
    ("source_file_sha256", "0" * 64, "manifest_source_anchor_mismatch"),
    ("schema_version", "old_contract", "manifest_schema_invalid"),
    ("contract_hash", "0" * 64, "observation_contract_hash_mismatch"),
])
def test_manifest_fail_closed(field, value, reason):
    evidence = sample()
    manifest = manifest_for(evidence)
    manifest[field] = value
    rejected(run(evidence, manifest), reason)


@pytest.mark.parametrize("field", ["batch_id", "source_version", "raw_hash", "request_hash"])
def test_no_cross_batch_or_version(field):
    evidence = sample()
    manifest = manifest_for(evidence)
    manifest["capture_bindings"][2][field] = "other-batch-or-version"
    rejected(run(evidence, manifest), "batch_capture_or_version_mismatch")


@pytest.mark.parametrize("mutation,reason", [
    (lambda e: e["requested_codes"].pop(), "fixed_five_codes_mismatch"),
    (lambda e: e["raw_responses"]["daily_bar_qfq"].pop(), "capture_count_invalid"),
    (lambda e: e["records_by_capability"]["daily_bar_qfq"].pop(), "raw_record_replay_mismatch"),
    (lambda e: e["records_by_capability"]["daily_bar_qfq"][0]["payload"].update(code="000002"), "raw_record_replay_mismatch"),
    (lambda e: e["records_by_capability"]["daily_bar_qfq"][0].update(source_version="unknown"), "raw_record_replay_mismatch"),
    (lambda e: e["raw_responses"]["daily_bar_qfq"][0].update(available_at="2026-09-18T00:00:00+08:00"), "raw_record_replay_mismatch"),
])
def test_missing_and_forged_records(mutation, reason):
    evidence = sample()
    mutation(evidence)
    rejected(run(seal(evidence)), reason)


def edit_raw(evidence, index, edit):
    response = evidence["raw_responses"]["daily_bar_qfq"][index]
    payload = json.loads(base64.b64decode(response["content_base64"]))
    rows = next(iter(payload["data"].values()))["qfqday"]
    edit(rows)
    raw = json.dumps(payload).encode()
    response.update(content_base64=base64.b64encode(raw).decode(),
                    byte_count=len(raw), raw_hash=hashlib.sha256(raw).hexdigest())
    return seal(evidence)


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "-Infinity"])
def test_bad_price_raw_rejected(value):
    evidence = edit_raw(sample(), 0, lambda rows: rows[-1].__setitem__(2, value))
    rejected(run(evidence), "STATIC_")


def test_conflicting_duplicate_not_collapsed():
    evidence = sample()
    conflict = deepcopy(evidence["records_by_capability"]["daily_bar_qfq"][0])
    conflict["payload"]["close"] = 123
    evidence["records_by_capability"]["daily_bar_qfq"].append(conflict)
    rejected(run(seal(evidence)), "raw_record_replay_mismatch")


def test_identical_normalized_duplicate_and_reordering_deterministic():
    evidence = sample()
    baseline = run(evidence)
    records = evidence["records_by_capability"]["daily_bar_qfq"]
    records.append(deepcopy(records[0]))
    records.reverse()
    evidence["raw_responses"]["daily_bar_qfq"].reverse()
    evidence["requested_codes"].reverse()
    replay = run(seal(evidence))
    assert replay["observation_input_ready"]
    assert replay["decision_hash"] == baseline["decision_hash"]
    assert replay["observation_ranking"] == baseline["observation_ranking"]


def test_stale_common_day_cannot_replace_latest_calendar_day():
    evidence = edit_raw(sample(), 0, lambda rows: rows.pop())
    rederive(evidence)
    rejected(run(seal(evidence)), "latest_60_calendar_sessions_missing")


def rederive(evidence):
    # Test-only reconstruction: never used to repair a historical capture.
    from overnight_quant.scripts.run_static_source_evidence_verify import _ReplayTransport, _ReplayClock
    responses = evidence["raw_responses"]["daily_bar_qfq"]
    provider = StaticSourceProviders(FIXED_CODES, target_trade_date=RUN,
                                    feature_cutoff=evidence["feature_cutoff"],
                                    transport=_ReplayTransport(responses), clock=_ReplayClock(responses))
    days = evidence["records_by_capability"]["trading_calendar"][0]["payload"]["trade_dates"]
    evidence["records_by_capability"]["daily_bar_qfq"] = list(provider.collect_qfq_daily_batch(days).records)
    return seal(evidence)


@pytest.mark.parametrize("missing_index", [-2, -21, -40])
def test_missing_required_date_even_with_60_other_dates(missing_index):
    evidence = edit_raw(sample(), 0, lambda rows: rows.pop(missing_index))
    rejected(run(rederive(evidence)), "latest_60_calendar_sessions_missing")


def test_all_five_stale_cannot_choose_older_common_D():
    evidence = sample()
    for index in range(5):
        edit_raw(evidence, index, lambda rows: rows.pop())
    rejected(run(rederive(evidence)), "latest_60_calendar_sessions_missing")


def test_descending_score_and_negative_return_legal():
    evidence = sample()
    def lower_last_price(rows):
        rows[-1][1:5] = ["100", "100", "101", "99"]
    edit_raw(evidence, 0, lower_last_price)
    result = run(rederive(evidence))
    assert result["observation_input_ready"]
    assert result["observation_ranking"][-1]["code"] == "000001"
    assert result["observation_ranking"][-1]["R20"] < 0
    assert [r["R20"] for r in result["observation_ranking"]] == sorted(
        (r["R20"] for r in result["observation_ranking"]), reverse=True)


def test_future_availability_cannot_pass_after_records_recomputed():
    evidence = sample()
    response = evidence["raw_responses"]["daily_bar_qfq"][0]
    response["available_at"] = "2026-09-17T23:01:00+08:00"
    rejected(run(rederive(evidence)), "capture_available_or_date_invalid")


def test_conflicting_raw_duplicate_keeps_original_parser_rejection():
    evidence = edit_raw(sample(), 0, lambda rows: rows.append(list(rows[-1])))
    rejected(run(evidence), "STATIC_QFQ_DUPLICATE_DATE")


def test_run_day_mismatch_and_unmarked_history_refused():
    evidence = sample()
    evidence["trade_date"] = "2026-09-16"
    rejected(run(seal(evidence)), "source_run_date_mismatch")
    evidence = sample()
    result = observe(evidence, source_file_sha256=file_hash(evidence), manifest=manifest_for(evidence),
                     run_date=RUN, generated_at="2026-10-31T23:00:00+08:00", synthetic_test_only=True)
    rejected(result, "historical_input_requires_explicit_mode")


def test_no_production_registration_or_old_strategy_change():
    from overnight_quant.data.source_capability_adapters import get_source_adapter_registry
    from overnight_quant.tests.test_phase105_noflow_research import test_old_strategy_still_requires_flow_and_is_unchanged
    test_old_strategy_still_requires_flow_and_is_unchanged()
    assert "completed_daily_price_observation" not in str(get_source_adapter_registry())


def test_running_day_rows_excluded_by_original_parser():
    evidence = edit_raw(sample(), 0, lambda rows: rows.append([RUN, "999", "999", "999", "999", "1"]))
    # Raw/request hashes are evidence fields, so regenerate only the trace reference.
    response = evidence["raw_responses"]["daily_bar_qfq"][0]
    for row in evidence["records_by_capability"]["daily_bar_qfq"]:
        if row["payload"]["code"] == "000001":
            row["raw_hash"] = response["raw_hash"]
    result = run(seal(evidence))
    assert result["observation_input_ready"]
    assert all(row["end_date"] < RUN for row in result["observation_ranking"])


def test_fundflow_and_optional_material_cannot_change_decision():
    evidence = sample()
    baseline = run(evidence)
    for extra in ({}, {"fund_flow": {"eligible": True, "incremental": "forged", "value": -999}},
                  {"fund_flow": None, "news": ["unrelated"]}):
        modified = deepcopy(evidence)
        modified.update(extra)
        output = run(seal(modified))
        assert output["decision_hash"] == baseline["decision_hash"]
        assert output["observation_ranking"] == baseline["observation_ranking"]
        assert output["source_file_sha256"] != baseline["source_file_sha256"] or not extra


def test_synthetic_cannot_enter_normal_path_and_old_contract_cannot_enter_new():
    evidence = sample()
    result = observe(evidence, source_file_sha256=file_hash(evidence), manifest=manifest_for(evidence),
                     run_date=RUN, generated_at=NOW)
    rejected(result, "synthetic_test_only_isolation")
    rejected(run(evidence, {"strategy_id": "close_confirmation_v1"}), "manifest_schema_invalid")


def test_history_without_freeze_is_rejected_after_component_replay():
    evidence = sample()
    result = observe(evidence, source_file_sha256=file_hash(evidence), manifest=None,
                     run_date=RUN, generated_at="2026-10-31T12:00:00+08:00", historical_offline=True,
                     synthetic_test_only=True)
    assert result["audit"]["component_replay_passed"]
    rejected(result, "frozen_batch_manifest_missing")


def write_input(tmp_path):
    evidence = sample()
    raw = json.dumps(evidence).encode()
    source = tmp_path / "source.json"
    source.write_bytes(raw)
    source_sha = hashlib.sha256(raw).hexdigest()
    manifest = manifest_for(evidence)
    manifest["source_file_sha256"] = source_sha
    raw = json.dumps(manifest).encode()
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    return ["--source-evidence", str(source), "--source-sha256", source_sha,
            "--manifest", str(path), "--manifest-sha256", hashlib.sha256(raw).hexdigest(),
            "--run-date", RUN, "--historical-offline", "--synthetic-test-only"]


def test_actual_cli_twice_zero_network_and_no_overwrite(tmp_path):
    args = write_input(tmp_path)
    outputs = []
    for index in range(2):
        output = tmp_path / f"replay-{index}.json"
        assert main(args + ["--output", str(output)]) == 0
        outputs.append(json.loads(output.read_text(encoding="utf-8")))
    assert outputs[0]["decision_hash"] == outputs[1]["decision_hash"]
    assert outputs[0]["observation_ranking"] == outputs[1]["observation_ranking"]
    original = output.read_bytes()
    with pytest.raises(SystemExit):
        main(args + ["--output", str(output)])
    assert output.read_bytes() == original


def test_true_cli_process_and_default_no_collection(tmp_path):
    args = write_input(tmp_path)
    completed = subprocess.run([sys.executable, str(ROOT / "overnight_quant/scripts/run_completed_daily_observation.py"),
                                *args, "--output", str(tmp_path / "result.json")],
                               capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    assert "OBSERVATION_COMPUTED" in completed.stdout
    completed = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_completed_daily_observation"],
                               capture_output=True, text=True, timeout=15, cwd=ROOT)
    assert completed.returncode == 2 and "required" in completed.stderr


def test_external_anchor_and_nonfinite_json_fail(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b'{"x":NaN}')
    with pytest.raises(ValueError, match="sha256_mismatch"):
        read_anchored_json(path, "0" * 64)
    with pytest.raises(ValueError, match="non_finite"):
        read_anchored_json(path, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize("bad", [[], "bad", 42, None, {"daily_bar_qfq": ["not a capture"]}])
def test_malformed_capture_groups_return_empty_refusal(bad):
    evidence = sample()
    manifest = manifest_for(evidence)
    evidence["raw_responses"] = bad
    result = run(seal(evidence), manifest)
    safe(result)
    assert result["errors"] and not result["observation_input_ready"]
    assert result["observation_ranking"] == []
