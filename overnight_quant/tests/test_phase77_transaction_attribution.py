from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from overnight_quant.data.close_time_contract import (
    MINUTE_LABEL_END_PROVISIONAL,
    MINUTE_LABEL_START_PROVISIONAL,
    build_close_time_contract,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_label_probe import (
    classify_minute_label_samples,
    compute_probe_evidence_hash,
    run_scheduled_minute_label_probe,
)
from overnight_quant.data.minute_probe_sources import (
    MootdxMinuteProbeCollectors,
)
from overnight_quant.data.probe_evidence import (
    verify_probe_evidence,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.snapshot_store import close_snapshot_hash
from overnight_quant.data.snapshot_store import ProviderBatch
from overnight_quant.data.transaction_attribution import (
    ATTRIBUTION_INCONCLUSIVE,
    MOOTDX_ATTRIBUTION_ALGORITHM_VERSION,
    MOOTDX_CURRENT_TRANSACTION_SOURCE_VERSION,
    MOOTDX_LEGACY_TRANSACTION_SOURCE_VERSION,
    MOOTDX_LEGACY_TRANSACTION_VOLUME_UNIT,
    MOOTDX_LOT_TO_SHARE_BASIS,
    REANALYSIS_INPUT_INVALID,
    aggregate_transaction_interval,
    attribute_mootdx_minute_intervals,
    build_mootdx_probe_reanalysis,
    compute_transaction_evidence_hash,
    normalize_mootdx_transaction_evidence,
)
from overnight_quant.scripts import run_probe_evidence_verify
from overnight_quant.scripts import run_mootdx_probe_reanalysis


DAY = "2026-08-04"
CODES = ["000001", "000333", "600000", "600519", "601318"]


@pytest.mark.parametrize(
    ("matching_interval", "expected"),
    [
        ("14:49", MINUTE_LABEL_END_PROVISIONAL),
        ("14:50", MINUTE_LABEL_START_PROVISIONAL),
    ],
)
def test_same_source_transactions_provisionally_attribute_bar(
    matching_interval,
    expected,
):
    probe = _probe_result(first_present=matching_interval == "14:49")
    transaction = _transaction_evidence(matching_interval)

    result = attribute_mootdx_minute_intervals(
        probe,
        transaction,
    )

    assert result["status"] == expected
    assert result["all_stocks_final"] is True
    assert len(result["combined_evidence_hash"]) == 64
    assert all(
        row["status"] == expected
        and row["is_final"] is True
        and row["bar_label_time"] == f"{DAY}T14:50:00+08:00"
        and row["transaction_evidence_hash"]
        == transaction["transaction_evidence_hash"]
        for row in result["per_stock"].values()
    )
    assert result["attribution_algorithm_version"] == (
        MOOTDX_ATTRIBUTION_ALGORITHM_VERSION
    )
    contract = result["provisional_time_contract"]
    assert contract["minute_label_semantics"] == expected
    assert contract["is_final"] is True
    assert contract["transaction_evidence_hash"] == (
        transaction["transaction_evidence_hash"]
    )
    assert contract["combined_evidence_hash"] == result[
        "combined_evidence_hash"
    ]


def test_minute_precision_buckets_preserve_lots_and_normalize_shares():
    result = attribute_mootdx_minute_intervals(
        _probe_result(first_present=True),
        _transaction_evidence("14:49"),
    )

    stock = result["per_stock"][CODES[0]]
    aggregate = stock["aggregate_1449"]
    assert stock["status"] == MINUTE_LABEL_END_PROVISIONAL
    assert aggregate["timestamp_precision"] == "minute"
    assert aggregate["boundary_aligned"] is True
    assert aggregate["raw_volume"] == pytest.approx(1.0)
    assert aggregate["raw_volume_unit"] == "lot"
    assert aggregate["normalized_volume"] == pytest.approx(100.0)
    assert aggregate["normalized_volume_unit"] == "share"
    assert aggregate["volume_conversion_factor"] == 100.0
    assert aggregate["volume_conversion_basis"] == (
        MOOTDX_LOT_TO_SHARE_BASIS
    )
    assert stock["stable_bar"]["raw_volume"] == 100.0
    assert stock["stable_bar"]["normalized_volume"] == 100.0


@pytest.mark.parametrize(
    ("source_time_text", "event_time"),
    [
        ("14:49:00", f"{DAY}T14:49:00+08:00"),
        ("14:99", f"{DAY}T14:49:00+08:00"),
        ("14:50", f"{DAY}T14:49:00+08:00"),
    ],
)
def test_minute_precision_rejects_invalid_or_mismatched_source_time(
    source_time_text: str,
    event_time: str,
):
    transaction = _transaction_evidence("14:49")
    records = transaction["by_code"][CODES[0]]["records"]
    records[0]["source_time_text"] = source_time_text
    records[0]["event_time"] = event_time

    aggregate = aggregate_transaction_interval(
        records,
        interval_start=f"{DAY}T14:49:00+08:00",
        interval_end=f"{DAY}T14:49:59+08:00",
        normalized_contract=True,
    )

    assert aggregate["complete"] is False


def test_non_aligned_minute_interval_is_incomplete():
    transaction = _transaction_evidence("14:49")
    records = transaction["by_code"][CODES[0]]["records"]

    aggregate = aggregate_transaction_interval(
        records,
        interval_start=f"{DAY}T14:49:00+08:00",
        interval_end=f"{DAY}T14:49:58+08:00",
        normalized_contract=True,
    )

    assert aggregate["boundary_aligned"] is False
    assert aggregate["complete"] is False


def test_both_matching_intervals_are_inconclusive():
    transaction = _transaction_evidence("14:49")
    for position, code in enumerate(CODES):
        stock = transaction["by_code"][code]
        stock["records"] = _interval_records(
            code,
            "14:49",
            matching=True,
            start_position=position * 20,
        ) + _interval_records(
            code,
            "14:50",
            matching=True,
            start_position=position * 20 + 10,
        )
    transaction = normalize_mootdx_transaction_evidence(transaction)

    result = attribute_mootdx_minute_intervals(
        _probe_result(first_present=True),
        transaction,
    )

    assert result["status"] == ATTRIBUTION_INCONCLUSIVE
    assert all(
        row["match_1449"] is True and row["match_1450"] is True
        for row in result["per_stock"].values()
    )


def test_flat_equal_intervals_have_no_unique_attribution():
    flat = {
        "open": 10.0,
        "high": 10.0,
        "low": 10.0,
        "close": 10.0,
        "volume": 100.0,
        "amount": 1000.0,
    }
    probe = _probe_result(first_present=True, ohlcv=flat)
    transaction = _transaction_evidence(
        "14:49",
        matching_values=[(10.0, 0.5), (10.0, 0.5)],
        other_matching=True,
    )

    result = attribute_mootdx_minute_intervals(probe, transaction)

    assert result["status"] == ATTRIBUTION_INCONCLUSIVE
    assert all(
        row["match_1449"] is True and row["match_1450"] is True
        for row in result["per_stock"].values()
    )


def test_changed_bar_requires_three_consecutive_stable_observations():
    probe = _probe_result(first_present=True)
    first_values = {
        **probe["samples"][0]["signatures"][CODES[0]]["ohlcv"],
        "close": 9.9,
    }
    for code in CODES:
        probe["samples"][0]["signatures"][code]["ohlcv"] = first_values
        probe["samples"][0]["signatures"][code]["ohlcv_hash"] = (
            stable_hash(first_values)
        )
    probe = classify_minute_label_samples(
        probe["samples"],
        required_codes=CODES,
        source="mootdx",
    )

    result = attribute_mootdx_minute_intervals(
        probe,
        _transaction_evidence("14:49"),
    )

    assert result["status"] == MINUTE_LABEL_END_PROVISIONAL
    assert all(
        row["is_final"] is True
        and row["stable_bar"]
        and row["finalized_at"].startswith(f"{DAY}T14:51:05")
        for row in result["per_stock"].values()
    )


def test_late_bar_change_prevents_finalization():
    probe = _probe_result(first_present=True)
    for code in CODES:
        signature = probe["samples"][-1]["signatures"][code]
        changed = {**signature["ohlcv"], "close": 10.6}
        signature["ohlcv"] = changed
        signature["ohlcv_hash"] = stable_hash(changed)
    probe = classify_minute_label_samples(
        probe["samples"],
        required_codes=CODES,
        source="mootdx",
    )

    result = attribute_mootdx_minute_intervals(
        probe,
        _transaction_evidence("14:49"),
    )

    assert result["status"] == ATTRIBUTION_INCONCLUSIVE
    assert result["all_stocks_final"] is False
    assert all(
        "minute_bar_not_final" in row["reasons"]
        for row in result["per_stock"].values()
    )


def test_algorithm_version_changes_combined_hash():
    probe = _probe_result(first_present=True)
    transaction = _transaction_evidence("14:49")
    original = attribute_mootdx_minute_intervals(probe, transaction)
    changed = deepcopy(transaction)
    changed["attribution_algorithm_version"] = (
        "mootdx_minute_transaction_attribution_v3"
    )
    changed["transaction_evidence_hash"] = (
        compute_transaction_evidence_hash(changed, source="mootdx")
    )

    revised = attribute_mootdx_minute_intervals(probe, changed)

    assert revised["status"] == ATTRIBUTION_INCONCLUSIVE
    assert revised["combined_evidence_hash"] != original[
        "combined_evidence_hash"
    ]


def test_transaction_source_mixing_is_rejected():
    transaction = _transaction_evidence("14:49")
    transaction["source"] = "eastmoney"

    with pytest.raises(ValueError, match="source_mismatch"):
        attribute_mootdx_minute_intervals(
            _probe_result(first_present=True),
            transaction,
        )


def test_reanalysis_is_deterministic_and_never_updates_qualification():
    payload = _complete_payload()

    first = build_mootdx_probe_reanalysis(payload)
    second = build_mootdx_probe_reanalysis(payload)

    assert first == second
    assert first["status"] == "PM_REVIEW_REQUIRED"
    assert first["minute_label_semantics"] == (
        MINUTE_LABEL_END_PROVISIONAL
    )
    assert first["automatic_qualification_update"] is False
    assert first["data_ready"] is False
    assert first["candidates"] == []
    assert first["tickets"] == []
    assert first["orders"] == []


def test_reanalysis_rejects_probe_timing_drift_before_normalization():
    payload = _complete_payload()
    payload["samples"][0]["request_elapsed_ms"] += 1

    result = build_mootdx_probe_reanalysis(payload)

    _assert_invalid_reanalysis(result)
    assert "minute_probe_evidence_hash_drift" in result[
        "reanalysis_errors"
    ]


@pytest.mark.parametrize("hash_layer", ["transaction", "combined"])
def test_reanalysis_rejects_original_hash_drift(hash_layer):
    payload = _complete_payload()
    if hash_layer == "transaction":
        payload["transaction_evidence"]["by_code"][CODES[0]][
            "records"
        ][0]["volume"] += 1
        expected = "transaction_evidence_hash_drift"
    else:
        payload["combined_evidence_hash"] = "0" * 64
        expected = "combined_evidence_hash_drift"

    result = build_mootdx_probe_reanalysis(payload)

    _assert_invalid_reanalysis(result)
    assert expected in result["reanalysis_errors"]


@pytest.mark.parametrize("output_name", ["candidates", "tickets", "orders"])
def test_reanalysis_rejects_input_with_execution_outputs(output_name):
    payload = _complete_payload()
    payload[output_name] = [{"code": CODES[0]}]

    result = build_mootdx_probe_reanalysis(payload)

    _assert_invalid_reanalysis(result)
    assert f"reanalysis_input_{output_name}_not_empty" in result[
        "reanalysis_errors"
    ]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("unknown_version", "transaction_source_version_not_approved"),
        ("unknown_unit", "transaction_top_level_volume_unit_mismatch"),
        ("mixed_source", "transaction_record_source_mismatch"),
    ],
)
def test_reanalysis_rejects_unapproved_transaction_contract(
    mutation,
    expected,
):
    payload = _complete_payload()
    transaction = payload["transaction_evidence"]
    if mutation == "unknown_version":
        transaction["source_version"] = (
            "mootdx_0.11.8_tdx_std_transaction_v2026-08-07"
        )
        for stock in transaction["by_code"].values():
            stock["source_version"] = transaction["source_version"]
            for row in stock["records"]:
                row["source_version"] = transaction["source_version"]
    elif mutation == "unknown_unit":
        transaction["source_volume_unit"] = "unknown"
        transaction["volume_unit"] = "unknown"
        for stock in transaction["by_code"].values():
            stock["source_volume_unit"] = "unknown"
            stock["volume_unit"] = "unknown"
            for row in stock["records"]:
                row["source_volume_unit"] = "unknown"
                row["raw_volume_unit"] = "unknown"
    else:
        transaction["by_code"][CODES[0]]["records"][0][
            "source"
        ] = "eastmoney"
    _rebuild_original_evidence(payload)

    result = build_mootdx_probe_reanalysis(payload)

    _assert_invalid_reanalysis(result)
    assert expected in result["reanalysis_errors"]


@pytest.mark.parametrize(
    "mutation",
    ["trade_date", "code_set"],
)
def test_reanalysis_rejects_identity_mismatch(mutation):
    payload = _complete_payload()
    if mutation == "trade_date":
        payload["trade_date"] = "2026-08-05"
        expected = "reanalysis_transaction_trade_date_mismatch"
    else:
        payload["tracked_codes"] = [*CODES, "600001"]
        expected = "reanalysis_transaction_code_set_mismatch"

    result = build_mootdx_probe_reanalysis(payload)

    _assert_invalid_reanalysis(result)
    assert expected in result["reanalysis_errors"]


def test_approved_legacy_transaction_evidence_replays_safely():
    result = build_mootdx_probe_reanalysis(_legacy_complete_payload())

    assert result["status"] == "PM_REVIEW_REQUIRED"
    assert result["input_evidence_verification"]["status"] == (
        "PROBE_EVIDENCE_VERIFIED"
    )
    assert result["transaction_evidence"]["source_version"] == (
        MOOTDX_LEGACY_TRANSACTION_SOURCE_VERSION
    )
    assert result["transaction_evidence"]["source_volume_unit"] == (
        MOOTDX_LEGACY_TRANSACTION_VOLUME_UNIT
    )
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def test_current_transaction_version_replays_with_lot_contract():
    result = build_mootdx_probe_reanalysis(_complete_payload())

    assert result["status"] == "PM_REVIEW_REQUIRED"
    transaction = result["transaction_evidence"]
    assert transaction["source_version"] == (
        MOOTDX_CURRENT_TRANSACTION_SOURCE_VERSION
    )
    assert transaction["source_volume_unit"] == "lot"
    assert all(
        row["source"] == "mootdx"
        and row["source_version"]
        == MOOTDX_CURRENT_TRANSACTION_SOURCE_VERSION
        and row["source_volume_unit"] == "lot"
        for stock in transaction["by_code"].values()
        for row in stock["records"]
    )


def test_reanalysis_cli_fails_closed_when_independent_verification_fails(
    tmp_path,
    monkeypatch,
    capsys,
):
    source = tmp_path / "source.json"
    output = tmp_path / "derived.json"
    source.write_text(
        json.dumps(_complete_payload()),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_mootdx_probe_reanalysis,
        "verify_probe_evidence",
        lambda payload, source: {
            "status": "PROBE_EVIDENCE_INVALID",
            "execution_ok": True,
            "data_ready": False,
            "source": source,
            "errors": ["forced_verification_failure"],
            "candidates": [],
            "tickets": [],
            "orders": [],
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_mootdx_probe_reanalysis.py",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
    )

    exit_code = run_mootdx_probe_reanalysis.main()
    printed = json.loads(capsys.readouterr().out)
    written = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code != 0
    assert printed["status"] == "REANALYSIS_OUTPUT_INVALID"
    assert written["status"] == "REANALYSIS_OUTPUT_INVALID"
    assert written["status"] != "PM_REVIEW_REQUIRED"
    assert written["data_ready"] is False
    assert written["candidates"] == []
    assert written["tickets"] == []
    assert written["orders"] == []


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("incomplete", "transaction_records_incomplete"),
        (
            "mixed_precision",
            "transaction_timestamp_precision_unknown_or_mixed",
        ),
        (
            "unit_mismatch",
            "transaction_volume_conversion_contract_invalid",
        ),
        ("neither", "transaction_intervals_both_or_neither_match"),
    ],
)
def test_incomplete_or_nonmatching_transaction_evidence_is_inconclusive(
    mutation,
    expected_reason,
):
    probe = _probe_result(first_present=True)
    transaction = _transaction_evidence("14:49")
    if mutation == "incomplete":
        transaction["by_code"][CODES[0]]["coverage_complete"] = False
    elif mutation == "mixed_precision":
        transaction["by_code"][CODES[0]]["timestamp_precision"] = "mixed"
    elif mutation == "unit_mismatch":
        transaction["by_code"][CODES[0]][
            "volume_conversion_factor"
        ] = 99.0
    else:
        for row in transaction["by_code"][CODES[0]]["records"]:
            row["price"] = 8.0
    transaction["transaction_evidence_hash"] = (
        compute_transaction_evidence_hash(transaction, source="mootdx")
    )

    result = attribute_mootdx_minute_intervals(probe, transaction)

    assert result["status"] == ATTRIBUTION_INCONCLUSIVE
    assert expected_reason in result["per_stock"][CODES[0]]["reasons"]
    assert result["provisional_time_contract"] == {}


def test_probe_hash_api_requires_nonempty_source():
    probe = _probe_result(first_present=True)

    with pytest.raises(ValueError, match="evidence_source_required"):
        compute_probe_evidence_hash(
            probe["samples"],
            CODES,
            source="",
        )


def test_evidence_verifier_recomputes_all_three_hash_layers():
    payload = _complete_payload()

    verified = verify_probe_evidence(payload, source="mootdx")
    drifted = deepcopy(payload)
    drifted["transaction_evidence"]["by_code"][CODES[0]][
        "records"
    ][0]["volume"] += 1
    invalid = verify_probe_evidence(drifted, source="mootdx")

    assert verified["status"] == "PROBE_EVIDENCE_VERIFIED"
    assert verified["data_ready"] is False
    assert invalid["status"] == "PROBE_EVIDENCE_INVALID"
    assert "transaction_evidence_hash_drift" in invalid["errors"]


def test_verify_cli_requires_explicit_source_and_rejects_bom(
    tmp_path,
    monkeypatch,
    capsys,
):
    path = tmp_path / "probe.json"
    path.write_text(
        json.dumps(_complete_payload()),
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_probe_evidence_verify.py",
            "--input",
            str(path),
            "--source",
            "mootdx",
        ],
    )

    exit_code = run_probe_evidence_verify.main()
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["status"] == "PROBE_EVIDENCE_INVALID"
    assert "probe_json_must_be_utf8_without_bom" in output["errors"]


def test_nonfinal_minute_record_is_excluded_from_snapshot_hash():
    contract = build_close_time_contract(DAY)
    row = {
        "event_time": f"{DAY}T14:50:00+08:00",
        "published_at": "",
        "observed_at": f"{DAY}T14:50:05+08:00",
        "available_at": f"{DAY}T14:50:05+08:00",
        "decision_cutoff": contract.decision_time,
        "feature_event_cutoff": contract.feature_event_cutoff,
        "collection_deadline": contract.collection_deadline,
        "decision_time": contract.decision_time,
        "execution_not_before": contract.execution_not_before,
        "time_contract_version": contract.contract_version,
        "minute_label_semantics": contract.minute_label_semantics,
        "minute_label_validation_status": (
            contract.minute_label_validation_status
        ),
        "source": "mootdx_tdx_std_minute",
        "source_version": "mootdx_minute_v1",
        "request_hash": "a" * 64,
        "raw_hash": "b" * 64,
        "data_type": "minute_bar",
        "is_final": False,
        "payload": {"code": "000001", "is_final": False},
    }

    empty_hash = close_snapshot_hash(
        decision_time=contract.decision_time,
        records=[],
        source_status=[],
        time_contract=contract,
    )
    nonfinal_hash = close_snapshot_hash(
        decision_time=contract.decision_time,
        records=[row],
        source_status=[],
        time_contract=contract,
    )

    assert nonfinal_hash == empty_hash


def test_mootdx_transaction_collector_preserves_source_specific_evidence():
    client = _TransactionClient()
    now = datetime.fromisoformat(f"{DAY}T14:51:06+08:00")
    collector = MootdxMinuteProbeCollectors(
        ["000001"],
        clock=lambda: now,
        client_factory=lambda: client,
    )

    evidence = collector.collect_transaction_evidence(now)
    collector.close()

    stock = evidence["by_code"]["000001"]
    assert evidence["source"] == "mootdx"
    assert len(evidence["transaction_evidence_hash"]) == 64
    assert stock["coverage_complete"] is True
    assert stock["timestamp_precision"] == "minute"
    assert stock["volume_unit"] == "lot"
    assert stock["normalized_volume_unit"] == "share"
    record = next(
        row
        for row in stock["records"]
        if row["source_time_text"] == "14:49"
    )
    assert record["event_time"] == f"{DAY}T14:49:00+08:00"
    assert record["timestamp_precision"] == "minute"
    assert record["raw_volume"] == record["volume"]
    assert record["normalized_volume"] == record["volume"] * 100
    assert record["volume_conversion_basis"] == (
        MOOTDX_LOT_TO_SHARE_BASIS
    )
    assert record["source_position"] == 1
    assert client.closed is True


def test_scheduled_mootdx_probe_stays_research_only_with_provisional_result():
    clock = _AdvancingClock(
        datetime.fromisoformat(f"{DAY}T14:49:50+08:00")
    )
    collector = _ScheduledMootdxCollector(clock)

    result = run_scheduled_minute_label_probe(
        CODES,
        trade_date=DAY,
        source="mootdx",
        collectors=collector,
        clock=clock,
        sleep=clock.advance,
        monotonic=lambda: clock.now.timestamp(),
    )

    assert result["status"] == "MINUTE_LABEL_PROVISIONAL"
    assert result["source_role"] == "qualification_candidate"
    assert result["data_ready"] is False
    assert result["transaction_attribution"]["status"] == (
        MINUTE_LABEL_END_PROVISIONAL
    )
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []
    assert collector.closed is True


def test_watchdog_validate_only_exposes_safe_start_window():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_minute_probe_watchdog.ps1"
    )

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Date",
            "2026-08-05",
            "-ValidateOnly",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "WATCHDOG_VALIDATED"
    assert result["start_at"].endswith("T14:40:00")
    assert result["last_safe_start"].endswith("T14:49:50")
    assert result["sources"] == ["mootdx", "eastmoney"]
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def _complete_payload():
    probe = _probe_result(first_present=True)
    transaction = _transaction_evidence("14:49")
    attribution = attribute_mootdx_minute_intervals(probe, transaction)
    return {
        **probe,
        "source": "mootdx",
        "source_role": "qualification_candidate",
        "trade_date": DAY,
        "transaction_evidence": transaction,
        "transaction_attribution": attribution,
        "transaction_evidence_hash": transaction[
            "transaction_evidence_hash"
        ],
        "combined_evidence_hash": attribution[
            "combined_evidence_hash"
        ],
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _legacy_complete_payload():
    payload = _complete_payload()
    transaction = deepcopy(payload["transaction_evidence"])
    transaction["source_version"] = (
        MOOTDX_LEGACY_TRANSACTION_SOURCE_VERSION
    )
    for field in (
        "source_volume_unit",
        "volume_unit",
        "raw_volume_unit",
        "normalized_volume_unit",
        "volume_conversion_factor",
        "volume_conversion_basis",
        "volume_normalization_version",
        "attribution_algorithm_version",
    ):
        transaction.pop(field, None)
    for stock in transaction["by_code"].values():
        stock["source_version"] = (
            MOOTDX_LEGACY_TRANSACTION_SOURCE_VERSION
        )
        stock["timestamp_precision"] = "insufficient"
        stock["volume_unit"] = MOOTDX_LEGACY_TRANSACTION_VOLUME_UNIT
        for field in (
            "source_volume_unit",
            "raw_volume_unit",
            "normalized_volume_unit",
            "volume_conversion_factor",
            "volume_conversion_basis",
            "volume_normalization_version",
            "source_timestamp_precision",
        ):
            stock.pop(field, None)
        for row in stock["records"]:
            for field in (
                "source",
                "source_version",
                "source_volume_unit",
                "source_time_text",
                "source_time_origin",
                "raw_volume",
                "raw_volume_unit",
                "normalized_volume",
                "normalized_volume_unit",
                "volume_conversion_factor",
                "volume_conversion_basis",
            ):
                row.pop(field, None)
    payload["transaction_evidence"] = transaction
    _rebuild_original_evidence(payload)
    return payload


def _rebuild_original_evidence(payload):
    transaction = payload["transaction_evidence"]
    transaction["transaction_evidence_hash"] = (
        compute_transaction_evidence_hash(transaction, source="mootdx")
    )
    attribution = attribute_mootdx_minute_intervals(
        payload,
        transaction,
    )
    payload["transaction_attribution"] = attribution
    payload["transaction_evidence_hash"] = transaction[
        "transaction_evidence_hash"
    ]
    payload["combined_evidence_hash"] = attribution[
        "combined_evidence_hash"
    ]


def _assert_invalid_reanalysis(result):
    assert result["status"] == REANALYSIS_INPUT_INVALID
    assert result["execution_ok"] is False
    assert result["data_ready"] is False
    assert result["automatic_qualification_update"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def _probe_result(*, first_present, ohlcv=None):
    stable_values = dict(
        ohlcv
        or {
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 100.0,
            "amount": 1050.0,
        }
    )
    clocks = ("14:49:55", "14:50:05", "14:50:30", "14:51:05")
    samples = []
    for index, clock in enumerate(clocks):
        signatures = {}
        if first_present or index > 0:
            signatures = {
                code: {
                    "ohlcv": dict(stable_values),
                    "ohlcv_hash": stable_hash(stable_values),
                    "volume_unit": "share",
                    "source": "mootdx_tdx_std_minute",
                    "source_version": "mootdx_minute_v1",
                    "raw_hash": f"{index + 1:x}" * 64,
                }
                for code in CODES
            }
        target = f"{DAY}T{clock}+08:00"
        samples.append(
            {
                "probe_source": "mootdx",
                "target_at": target,
                "sampled_at": target,
                "request_started_at": target,
                "request_completed_at": target,
                "request_elapsed_ms": 500,
                "requested_codes": list(CODES),
                "covered_codes": list(CODES),
                "presence_by_code": {
                    code: code in signatures for code in CODES
                },
                "signatures": signatures,
                "raw_response_hashes": [f"{index + 1:x}" * 64],
                "provider_raw_hash": f"{index + 5:x}" * 64,
                "source_versions": ["mootdx_minute_v1"],
                "sample_trade_date": DAY,
                "error": "",
            }
        )
    return classify_minute_label_samples(
        samples,
        required_codes=CODES,
        source="mootdx",
    )


def _transaction_evidence(
    matching_interval,
    *,
    matching_values=None,
    other_matching=False,
):
    by_code = {}
    for code in CODES:
        rows = []
        rows.extend(
            _interval_records(
                code,
                matching_interval,
                matching=True,
                start_position=0,
                values=matching_values,
            )
        )
        other = "14:50" if matching_interval == "14:49" else "14:49"
        rows.extend(
            _interval_records(
                code,
                other,
                matching=other_matching,
                start_position=10,
                values=matching_values if other_matching else None,
            )
        )
        by_code[code] = {
            "source": "mootdx",
            "source_version": MOOTDX_CURRENT_TRANSACTION_SOURCE_VERSION,
            "source_volume_unit": "lot",
            "timestamp_precision": "minute",
            "volume_unit": "lot",
            "coverage_complete": True,
            "records": rows,
            "error": "",
        }
    evidence = {
        "source": "mootdx",
        "source_version": MOOTDX_CURRENT_TRANSACTION_SOURCE_VERSION,
        "source_volume_unit": "lot",
        "volume_unit": "lot",
        "trade_date": DAY,
        "requested_codes": list(CODES),
        "request_started_at": f"{DAY}T14:51:06+08:00",
        "request_completed_at": f"{DAY}T14:51:07+08:00",
        "by_code": by_code,
    }
    return normalize_mootdx_transaction_evidence(evidence)


def _interval_records(
    code,
    minute,
    *,
    matching,
    start_position,
    values=None,
):
    if values is not None:
        values = list(values)
    elif matching:
        values = [
            (10.0, 0.2),
            (11.0, 0.3),
            (9.5, 0.1),
            (10.5, 0.4),
        ]
    else:
        values = [(8.0, 0.25), (8.1, 0.25)]
    return [
        {
            "code": code,
            "source": "mootdx",
            "source_version": MOOTDX_CURRENT_TRANSACTION_SOURCE_VERSION,
            "source_volume_unit": "lot",
            "event_time": f"{DAY}T{minute}:00+08:00",
            "source_time_text": minute,
            "source_time_origin": "source",
            "timestamp_precision": "minute",
            "price": price,
            "volume": volume,
            "raw_volume_unit": "lot",
            "trade_count": 1,
            "source_position": start_position + index,
        }
        for index, (price, volume) in enumerate(values)
    ]


class _TransactionClient:
    def __init__(self):
        self.closed = False

    def transaction(self, **kwargs):
        assert kwargs["symbol"] == "000001"
        return pd.DataFrame(
            [
                {
                    "time": "14:48",
                    "price": 9.9,
                    "vol": 10,
                    "num": 1,
                    "buyorsell": 0,
                },
                {
                    "time": "14:49",
                    "price": 10.0,
                    "vol": 20,
                    "num": 1,
                    "buyorsell": 0,
                },
                {
                    "time": "14:50",
                    "price": 10.1,
                    "vol": 30,
                    "num": 2,
                    "buyorsell": 1,
                },
                {
                    "time": "14:51",
                    "price": 10.2,
                    "vol": 40,
                    "num": 2,
                    "buyorsell": 1,
                },
            ]
        )

    def close(self):
        self.closed = True


class _AdvancingClock:
    def __init__(self, value):
        self.now = value

    def __call__(self):
        return self.now

    def advance(self, seconds):
        from datetime import timedelta

        self.now += timedelta(seconds=float(seconds))


class _ScheduledMootdxCollector:
    probe_source = "mootdx"
    source_version = "mootdx_minute_v1"

    def __init__(self, clock):
        self.codes = list(CODES)
        self.clock = clock
        self.closed = False

    def collect_minute_bars(self, observed_at):
        records = []
        for code in self.codes:
            records.append(
                {
                    "data_type": "minute_bar",
                    "event_time": f"{DAY}T14:50:00+08:00",
                    "source": "mootdx_tdx_std_minute",
                    "source_version": self.source_version,
                    "raw_hash": "a" * 64,
                    "payload": {
                        "code": code,
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.5,
                        "close": 10.5,
                        "volume": 100.0,
                        "amount": 1050.0,
                        "field_units": {
                            "volume": "share"
                        },
                        "is_final": False,
                    },
                }
            )
        return ProviderBatch(
            records=records,
            data_types=["minute_bar"],
            source_version=self.source_version,
            raw_hash="b" * 64,
        )

    def collect_transaction_evidence(self, observed_at):
        return _transaction_evidence("14:49")

    def close(self):
        self.closed = True
