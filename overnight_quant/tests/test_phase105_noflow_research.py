"""Synthetic-only no-flow research tests; not market evidence or qualification."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import random
import socket
import subprocess
import sys

import pytest
import requests

from overnight_quant.data.close_confirmation_readiness import validate_close_confirmation_readiness
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import FIXED_CODES, security_identity
from overnight_quant.data.t24_offline_rehearsal import synthetic_fixture as old_fixture, FIXTURE_TIMES
from overnight_quant.scripts.run_noflow_research import run
from overnight_quant.strategy.close_confirmation_noflow_v1.contracts import (
    BENCHMARK, CLASSIFICATION, INPUT_SCHEMA, PARAMETERS, RECORD_SCHEMA, STRATEGY_ID,
    formal_time_contract, parameter_hash, requirement_hash,
)
from overnight_quant.strategy.close_confirmation_noflow_v1.readiness import timestamp
from overnight_quant.strategy.close_confirmation_noflow_v1.strategy import NoFundFlowResearchStrategy
from overnight_quant.strategy.legacy_frozen_baseline import legacy_baseline_is_intact
from overnight_quant.strategy.close_confirmation_v1.strategy import CloseConfirmationStrategy
from overnight_quant.strategy.registry import ensure_default_strategies_registered, list_strategies

ROOT = Path(__file__).resolve().parents[2]
DAY = "2026-10-19"  # Fabricated fixture date, never a calendar assertion.
BOARDS = {"000001": "BK0475", "000333": "BK1239", "600000": "BK0475", "600519": "BK1277", "601318": "BK0474"}


def synthetic_input() -> dict:
    contract = formal_time_contract(DAY)
    contract.update(minute_label_validation_status="VERIFIED", probe_evidence_hash=stable_hash("synthetic_probe_only"))
    value = {
        "schema_version": INPUT_SCHEMA, "strategy_id": STRATEGY_ID,
        "test_only": True, "trade_date": DAY, "batch_id": stable_hash("synthetic_noflow_batch"),
        "time_contract": contract, "records": [],
    }

    def add(kind, payload, *, event=None):
        from overnight_quant.strategy.close_confirmation_noflow_v1.contracts import CAPABILITIES
        row = {
            "data_type": kind, "capability": CAPABILITIES[kind], "payload": payload,
            "record_schema_version": RECORD_SCHEMA, "test_only": True,
            "batch_id": value["batch_id"], "trade_date": DAY,
            "source": "synthetic_test_only.noflow", "origin_source": "synthetic_test_only",
            "source_version": "synthetic_noflow_v1", "adapter": "test_only",
            "requirements_hash": requirement_hash(), "time_contract_hash": stable_hash(contract),
            "event_time": event or contract["feature_event_cutoff"],
            "observed_at": f"{DAY}T14:50:00+08:00", "available_at": f"{DAY}T14:50:01+08:00",
            "decision_cutoff": contract["decision_time"], "published_at": None,
            "raw_hash": stable_hash({"synthetic": payload}), "request_hash": stable_hash({"request": kind}),
            "payload_hash": stable_hash(payload),
        }
        value["records"].append(row)

    counts = {"up_count": 6, "down_count": 3, "flat_count": 1, "invalid_count": 0,
              "valid_count": 10, "total_count": 10, "breadth_ratio": 0.6}
    add("trading_calendar", {"trade_dates": ["2026-10-15", "2026-10-16"], "latest_completed_trade_date": "2026-10-16"})
    add("market", {**counts, "index_change_pct": 0.5, "change_unit": "percent", "benchmark_identity": BENCHMARK,
                   "stock_pool_scope": "sse+szse+bse_index_breadth_counts", "stock_pool_version": stable_hash("synthetic_pool")})
    add("global_news", {"status": "SUCCESS", "risk_review_status": "CLEAR", "items": []})
    for code in FIXED_CODES:
        add("quote", {"code": code, "price": 10.1, "prev_close": 10, "change_pct": 1.0,
                      "price_unit": "CNY", "change_unit": "percent", "suspended": False,
                      "is_limit_up": False, "is_limit_down": False, "tradability_status": "VERIFIED_TRADABLE"})
        add("industry", {
            **counts, "code": code, "board_code": BOARDS[code], "classification_version": CLASSIFICATION,
            "change_pct": 0.8, "change_unit": "percent", "invalid_count": 2 if code == "000333" else 0,
            "total_count": 12 if code == "000333" else 10,
            "association_contract": {"security_identity": security_identity(code), "board_code": BOARDS[code],
                "classification_version": CLASSIFICATION, "source_version": "synthetic_noflow_v1",
                "origin_source": "synthetic_test_only", "request_hash": stable_hash(code), "raw_hash": stable_hash(BOARDS[code])},
        })
        for kind in ("stock_news", "announcement"):
            add(kind, {"code": code, "status": "SUCCESS", "risk_review_status": "CLEAR", "items": []})
        for minute in range(39, 51):
            price = 10.1 if minute == 50 else 10.0
            add("minute_bar", {"code": code, "open": 10.0, "high": 10.2, "low": 9.8, "close": price,
                "volume": 20000, "amount": price * 20000, "price_unit": "CNY", "volume_unit": "shares", "amount_unit": "CNY",
                "aggregation_semantics": "interval", "interval_seconds": 60, "is_final": True},
                event=f"{DAY}T14:{minute:02}:00+08:00")
    return value


def evaluate(value):
    return NoFundFlowResearchStrategy().evaluate_snapshot(value, test_only=True)


def safe(result):
    assert result["data_ready"] is False and result["hard_gate_authorized"] is False
    assert result["qualification"] == "0/3"
    assert result["records"] == result["candidates"] == result["tickets"] == result["orders"] == []


def row(value, kind):
    return next(r for r in value["records"] if r["data_type"] == kind)


def resign(value):
    for r in value["records"]:
        if "payload" in r:
            r["payload_hash"] = stable_hash(r["payload"])


def test_complete_synthetic_chain_without_flow_and_no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    source = synthetic_input()
    original = deepcopy(source)
    result = evaluate(source)
    assert result["status"] == "SYNTHETIC_RESEARCH_RANKING", result["missing_reasons"]
    assert result["research_ready"] is True and len(result["research_ranking"]) == 5
    assert source == original
    assert result["research_selection"] == []
    assert result["network_requests_made"] == 0
    for item in result["research_ranking"]:
        features = item["features"]
        assert features["stock_vs_market_pp"] == 0.5
        assert features["stock_vs_industry_pp"] == pytest.approx(0.2)
        assert features["vwap_scope"] == "last_12_contiguous_minutes_not_full_day"
        assert "order_imbalance" not in features and "main_net" not in features
        assert item["score"] == pytest.approx(sum(x["contribution"] for x in item["score_explanation"].values()))
        if item["code"] == "000333":
            assert features["industry_invalid_count"] == 2
    safe(result)


@pytest.mark.parametrize("payload", [None, [], {"main_net": 1e99}, {"aggregation_semantics": "incremental", "eligible_for_hard_gate": True}, {"status": "UNPROVEN"}])
def test_flow_metamorphic_invariance(payload):
    source = synthetic_input()
    before = evaluate(source)
    source["fund_flow"] = payload
    source["records"].append({"data_type": "fund_flow", "payload": payload, "source": "forged", "raw_hash": "broken"})
    after = evaluate(source)
    for key in ("decision_hash", "snapshot_hash", "research_ready", "research_ranking", "missing_reasons"):
        assert before[key] == after[key]
    assert before["ingest_hash"] != after["ingest_hash"]
    safe(after)


def test_shuffle_exact_duplicate_and_repeated_replay_determinism():
    source = synthetic_input()
    before = evaluate(source)
    source["records"].extend(deepcopy(source["records"][:20]))
    random.Random(12).shuffle(source["records"])
    after = evaluate(source)
    assert before["decision_hash"] == after["decision_hash"]
    assert before["snapshot_hash"] == after["snapshot_hash"]
    assert json.dumps(after, sort_keys=True) == json.dumps(evaluate(source), sort_keys=True)


@pytest.mark.parametrize("kind", ["market", "industry", "quote", "minute_bar", "stock_news", "global_news", "announcement", "trading_calendar"])
def test_missing_core_type_refuses_without_weight_redistribution(kind):
    value = synthetic_input()
    value["records"] = [r for r in value["records"] if r["data_type"] != kind]
    result = evaluate(value)
    assert result["research_ready"] is False and result["research_ranking"] == []
    safe(result)


@pytest.mark.parametrize("kind,field,bad", [
    ("industry", "change_pct", None), ("industry", "change_unit", "ratio"),
    ("industry", "classification_version", "guessed_names"), ("quote", "price_unit", "fen"),
    ("quote", "suspended", None), ("quote", "tradability_status", "UNKNOWN"),
    ("market", "index_change_pct", float("nan")), ("market", "benchmark_identity", "SZ:000001"),
    ("market", "stock_pool_scope", "five_stocks"), ("market", "total_count", 11),
    ("minute_bar", "volume_unit", "hands"), ("minute_bar", "price_unit", "USD"),
    ("minute_bar", "aggregation_semantics", "cumulative"),
    ("minute_bar", "amount", None), ("minute_bar", "high", 1),
    ("stock_news", "status", "FAILED"), ("announcement", "risk_review_status", "UNKNOWN"),
])
def test_invalid_core_fields_fail_closed_even_resigned(kind, field, bad):
    value = synthetic_input()
    row(value, kind)["payload"][field] = bad
    resign(value)
    result = evaluate(value)
    assert result["research_ready"] is False
    safe(result)


@pytest.mark.parametrize("mutate", [
    lambda v: row(v, "industry")["payload"]["association_contract"].update(board_code="BK9999"),
    lambda v: row(v, "industry")["payload"]["association_contract"].update(security_identity="SH:600519"),
    lambda v: row(v, "industry")["payload"].pop("association_contract"),
    lambda v: row(v, "quote")["payload"].update(code="SH000001"),
    lambda v: row(v, "industry").update(batch_id="1" * 64),
    lambda v: row(v, "market").update(event_time=f"{DAY}T14:49:59+08:00"),
    lambda v: row(v, "industry").update(event_time=f"{DAY}T14:50:00.001+08:00"),
    lambda v: row(v, "quote").update(observed_at=f"{DAY}T14:49:59+08:00"),
    lambda v: row(v, "quote").update(available_at=f"{DAY}T14:51:05.001+08:00"),
    lambda v: row(v, "minute_bar").update(event_time="2026-10-16T14:39:00+08:00"),
    lambda v: row(v, "quote").update(source_version="forged"),
    lambda v: row(v, "quote").update(request_hash="broken"),
    lambda v: row(v, "quote").update(capability="valuation"),
    lambda v: v.update(strategy_id="close_confirmation_v1"),
    lambda v: v["time_contract"].update(feature_event_cutoff=f"{DAY}T14:40:00+08:00"),
    lambda v: v["time_contract"].update(contract_version="legacy_single_cutoff_v1"),
    lambda v: v["time_contract"].update(minute_label_validation_status="PENDING_REAL_OBSERVATION"),
])
def test_identity_batch_time_and_version_isolation(mutate):
    value = synthetic_input()
    mutate(value)
    resign(value)
    result = evaluate(value)
    assert result["research_ready"] is False
    safe(result)


@pytest.mark.parametrize("state", ["missing", "late", "date_only"])
def test_risk_publication_is_not_inferred(state):
    value = synthetic_input()
    item = {"published_at": f"{DAY}T14:49:00+08:00", "published_at_precision": "datetime",
            "raw_hash": stable_hash("synthetic_news"), "origin_source": "synthetic_test_only"}
    if state == "missing":
        item.pop("published_at")
    elif state == "late":
        item["published_at"] = f"{DAY}T14:50:00.001+08:00"
    else:
        item.update(published_at=f"{DAY}T00:00:00+08:00", published_at_precision="date")
    row(value, "stock_news")["payload"]["items"] = [item]
    resign(value)
    result = evaluate(value)
    assert result["research_ready"] is False
    safe(result)


def test_minute_gap_duplicate_conflict_and_1450_missing():
    for minute in (39, 50):
        value = synthetic_input()
        value["records"] = [r for r in value["records"] if not (
            r["data_type"] == "minute_bar" and r["payload"]["code"] == "000001"
            and r["event_time"] == f"{DAY}T14:{minute}:00+08:00")]
        assert evaluate(value)["research_ready"] is False
    value = synthetic_input()
    conflict = deepcopy(row(value, "minute_bar"))
    conflict["payload"]["volume"] += 1
    conflict["payload_hash"] = stable_hash(conflict["payload"])
    value["records"].append(conflict)
    assert evaluate(value)["research_ready"] is False


def test_incomplete_decision_is_also_flow_invariant():
    value = synthetic_input()
    value["records"] = [r for r in value["records"] if r["data_type"] != "market"]
    before = evaluate(value)
    value["records"].append({"data_type": "fund_flow", "payload": {"main_net": 100}})
    assert evaluate(value)["decision_hash"] == before["decision_hash"]


def test_public_path_cannot_admit_synthetic_or_self_declared_qualification():
    value = synthetic_input()
    strategy = NoFundFlowResearchStrategy()
    assert strategy.evaluate_snapshot(value)["research_ready"] is False
    value["test_only"] = False
    for r in value["records"]:
        r.update(test_only=False, qualification_status="qualified", eligible_for_hard_gate=True)
    result = strategy.evaluate_snapshot(value)
    assert result["research_ready"] is False
    safe(result)


def test_old_strategy_still_requires_flow_and_is_unchanged():
    old = [r for r in old_fixture() if r["data_type"] != "fund_flow"]
    contract = formal_time_contract("2026-09-18")
    result = validate_close_confirmation_readiness({"records": old, "time_contract": contract},
                                                   decision_time=FIXTURE_TIMES["decision_time"])
    assert result["coverage_by_type"]["fund_flow"] == 0
    assert result["data_ready"] is False
    assert any("fund_flow" in e for r in result["stock_readiness"].values() for e in r["errors"])
    assert legacy_baseline_is_intact()
    ensure_default_strategies_registered()
    assert STRATEGY_ID not in [r.name for r in list_strategies()]
    same_new_input = synthetic_input()
    same_new_input["decision_time"] = same_new_input["time_contract"]["decision_time"]
    old_result = CloseConfirmationStrategy().evaluate_snapshot(same_new_input)
    assert old_result["data_ready"] is False
    assert old_result["tickets"] == old_result["orders"] == []


def test_actual_cli_external_anchor_no_network_and_write_once(tmp_path):
    source = tmp_path / "synthetic-test-only.json"
    source.write_text(json.dumps(synthetic_input()), encoding="utf-8")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    command = [sys.executable, str(ROOT / "overnight_quant/scripts/run_noflow_research.py"),
               "--strategy", STRATEGY_ID, "--input", str(source), "--expected-input-sha256", sha,
               "--test-only", "--output-dir", str(tmp_path / "reports")]
    # A transport cannot be supplied to this CLI; it has no network/worker branch.
    child = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(child.stdout)
    assert result["research_ready"] is True
    safe(result)
    report = next((tmp_path / "reports" / STRATEGY_ID / "test_only").glob("*.json"))
    before = report.read_bytes()
    replay = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=30)
    assert replay.returncode == 2 and json.loads(replay.stdout)["status"] == "OUTPUT_REJECTED"
    assert report.read_bytes() == before
    assert run(input_path=source, expected_sha256="0" * 64, test_only=True)["execution_ok"] is False
    # The same file cannot silently become real research data.
    assert run(input_path=source, expected_sha256=sha)["research_ready"] is False


def test_cli_without_input_is_offline_refusal():
    child = subprocess.run([sys.executable, str(ROOT / "overnight_quant/scripts/run_noflow_research.py"),
                            "--strategy", STRATEGY_ID], capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert child.returncode == 2
    result = json.loads(child.stdout)
    assert result["network_requests_made"] == 0
    safe(result)


def test_parameters_not_reweighted_or_mutable():
    assert PARAMETERS["group_weight"] == 0.25
    with pytest.raises(TypeError):
        PARAMETERS["group_weight"] = 1
    assert len(parameter_hash()) == len(requirement_hash()) == 64


@pytest.mark.parametrize("change", [-2.0, 0.0, 2.0])
def test_native_industry_negative_zero_positive_are_legal(change):
    value = synthetic_input()
    for r in value["records"]:
        if r["data_type"] == "industry":
            r["payload"]["change_pct"] = change
    resign(value)
    assert evaluate(value)["research_ready"] is True


def test_optional_audit_inputs_have_no_decision_effect():
    value = synthetic_input()
    before = evaluate(value)
    for kind in ("cost_proxy", "order_book", "transaction", "daily_bar", "fund_flow_audit"):
        value["records"].append({"data_type": kind, "payload": {"fabricated": 999}})
    assert evaluate(value)["decision_hash"] == before["decision_hash"]


def test_aggregate_overflow_and_zero_liquidity_refuse():
    for volume, amount in ((0, 0), (1e307, 1e308)):
        value = synthetic_input()
        for r in value["records"]:
            if r["data_type"] == "minute_bar":
                r["payload"].update(volume=volume, amount=amount)
        resign(value)
        assert evaluate(value)["research_ready"] is False


def test_payload_tampering_and_late_row_not_silently_dropped():
    value = synthetic_input()
    row(value, "quote")["payload"]["price"] = 999
    assert "payload_hash_invalid" in evaluate(value)["missing_reasons"]
    value = synthetic_input()
    future = deepcopy(row(value, "minute_bar"))
    future["event_time"] = f"{DAY}T14:51:00+08:00"
    value["records"].append(future)
    assert evaluate(value)["research_ready"] is False


def test_actual_cli_in_process_forbids_network_and_subprocess(tmp_path, monkeypatch, capsys):
    from overnight_quant.scripts.run_noflow_research import main
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(synthetic_input()), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("network or child process forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert main(["--strategy", STRATEGY_ID, "--input", str(path), "--test-only",
                 "--expected-input-sha256", hashlib.sha256(path.read_bytes()).hexdigest()]) == 0
    result = json.loads(capsys.readouterr().out)
    safe(result)


@pytest.mark.parametrize("value", [None, [], 1, {"schema_version": INPUT_SCHEMA}, {"records": "bad"}])
def test_malformed_input_has_safe_outputs(value):
    result = NoFundFlowResearchStrategy().evaluate_snapshot(value)
    assert result["research_ready"] is False
    safe(result)
