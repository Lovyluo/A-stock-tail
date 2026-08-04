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
from overnight_quant.data.snapshot_store import close_snapshot_hash
from overnight_quant.data.snapshot_store import ProviderBatch
from overnight_quant.data.transaction_attribution import (
    ATTRIBUTION_INCONCLUSIVE,
    attribute_mootdx_minute_intervals,
    compute_transaction_evidence_hash,
)
from overnight_quant.scripts import run_probe_evidence_verify


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
    contract = result["provisional_time_contract"]
    assert contract["minute_label_semantics"] == expected
    assert contract["is_final"] is True
    assert contract["transaction_evidence_hash"] == (
        transaction["transaction_evidence_hash"]
    )
    assert contract["combined_evidence_hash"] == result[
        "combined_evidence_hash"
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("incomplete", "transaction_records_incomplete"),
        ("minute_precision", "transaction_timestamp_precision_insufficient"),
        ("unit_mismatch", "transaction_volume_unit_mismatch"),
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
    elif mutation == "minute_precision":
        transaction["by_code"][CODES[0]]["timestamp_precision"] = "insufficient"
    elif mutation == "unit_mismatch":
        transaction["by_code"][CODES[0]]["volume_unit"] = "lot"
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
    assert stock["timestamp_precision"] == "second"
    assert stock["volume_unit"] == "mootdx_native_volume"
    assert stock["records"][0]["event_time"] == (
        f"{DAY}T14:49:00+08:00"
    )
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


def _probe_result(*, first_present):
    clocks = ("14:49:55", "14:50:05", "14:50:30", "14:51:05")
    samples = []
    for index, clock in enumerate(clocks):
        signatures = {}
        if first_present or index > 0:
            signatures = {
                code: {
                    "ohlcv": {
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.5,
                        "close": 10.5,
                        "volume": 100.0,
                        "amount": 1050.0,
                    },
                    "ohlcv_hash": "a" * 64,
                    "volume_unit": "mootdx_native_volume",
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


def _transaction_evidence(matching_interval):
    by_code = {}
    for code in CODES:
        rows = []
        rows.extend(
            _interval_records(
                code,
                matching_interval,
                matching=True,
                start_position=0,
            )
        )
        other = "14:50" if matching_interval == "14:49" else "14:49"
        rows.extend(
            _interval_records(
                code,
                other,
                matching=False,
                start_position=10,
            )
        )
        by_code[code] = {
            "source": "mootdx",
            "source_version": "mootdx_transaction_v1",
            "timestamp_precision": "second",
            "volume_unit": "mootdx_native_volume",
            "coverage_complete": True,
            "records": rows,
            "error": "",
        }
    evidence = {
        "source": "mootdx",
        "source_version": "mootdx_transaction_v1",
        "trade_date": DAY,
        "requested_codes": list(CODES),
        "request_started_at": f"{DAY}T14:51:06+08:00",
        "request_completed_at": f"{DAY}T14:51:07+08:00",
        "by_code": by_code,
    }
    evidence["transaction_evidence_hash"] = (
        compute_transaction_evidence_hash(
            evidence,
            source="mootdx",
        )
    )
    return evidence


def _interval_records(code, minute, *, matching, start_position):
    if matching:
        values = [
            ("00", 10.0, 20),
            ("20", 11.0, 30),
            ("40", 9.5, 10),
            ("59", 10.5, 40),
        ]
    else:
        values = [("00", 8.0, 25), ("59", 8.1, 25)]
    return [
        {
            "code": code,
            "event_time": f"{DAY}T{minute}:{second}+08:00",
            "timestamp_precision": "second",
            "price": price,
            "volume": volume,
            "trade_count": 1,
            "source_position": start_position + index,
        }
        for index, (second, price, volume) in enumerate(values)
    ]


class _TransactionClient:
    def __init__(self):
        self.closed = False

    def transaction(self, **kwargs):
        assert kwargs["symbol"] == "000001"
        return pd.DataFrame(
            [
                {
                    "time": "14:49:00",
                    "price": 10.0,
                    "vol": 20,
                    "num": 1,
                    "buyorsell": 0,
                },
                {
                    "time": "14:50:59",
                    "price": 10.1,
                    "vol": 30,
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
                            "volume": "mootdx_native_volume"
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
