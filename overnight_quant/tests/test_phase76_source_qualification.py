from __future__ import annotations

from datetime import datetime
import json
import sys

import pandas as pd
import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_label_probe import (
    classify_minute_label_samples,
    write_probe_json_atomic,
)
from overnight_quant.data.minute_probe_sources import (
    MOOTDX_MINUTE_SOURCE_VERSION,
    MootdxMinuteProbeCollectors,
)
from overnight_quant.data.source_qualification import (
    evaluate_minute_source_qualification,
)
from overnight_quant.data.transaction_attribution import (
    attribute_mootdx_minute_intervals,
    compute_transaction_evidence_hash,
)
from overnight_quant.scripts import run_minute_label_probe as probe_script


CODES = ["000001", "000333", "600000", "600519", "601318"]
CALENDAR = ["2026-08-03", "2026-08-04", "2026-08-05"]
CALENDAR_CONTRACT = {
    "trade_dates": CALENDAR,
    "source": "tencent_index_daily_calendar",
    "source_version": "calendar_v1",
    "raw_hash": "c" * 64,
}


def test_probe_evidence_from_different_sources_cannot_be_combined():
    samples = _verified_samples("2026-08-03", "eastmoney")
    samples[2]["probe_source"] = "mootdx"

    result = classify_minute_label_samples(
        samples,
        required_codes=CODES,
        source="eastmoney",
    )

    assert result["status"] == "MINUTE_LABEL_INCONCLUSIVE"
    assert result["reasons"] == [
        "probe_sources_mixed_or_mismatched"
    ]


def test_probe_json_is_atomically_written_as_utf8(tmp_path):
    output = tmp_path / "probe.json"
    result = {
        "status": "MINUTE_LABEL_INCONCLUSIVE",
        "message": "分钟来源未验证",
    }

    written = write_probe_json_atomic(result, output)

    raw = written.read_bytes()
    assert not raw.startswith(b"\xff\xfe")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8")) == result
    assert list(tmp_path.glob("*.tmp")) == []


def test_probe_cli_forwards_source_and_writes_output(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "mootdx.json"
    captured = {}

    def fake_probe(codes, *, trade_date, source):
        captured.update(
            {
                "codes": codes,
                "trade_date": trade_date,
                "source": source,
            }
        )
        return {
            "status": "MINUTE_LABEL_INCONCLUSIVE",
            "source": source,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }

    monkeypatch.setattr(
        probe_script,
        "run_scheduled_minute_label_probe",
        fake_probe,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_minute_label_probe.py",
            "--source",
            "mootdx",
            "--codes",
            "000001,600000",
            "--date",
            "2026-08-03",
            "--output",
            str(output),
        ],
    )

    exit_code = probe_script.main()

    assert exit_code == 2
    assert captured == {
        "codes": ["000001", "600000"],
        "trade_date": "2026-08-03",
        "source": "mootdx",
    }
    assert json.loads(output.read_text(encoding="utf-8"))[
        "source"
    ] == "mootdx"


def test_mootdx_collector_emits_independent_minute_records():
    client = _FakeMootdxClient()
    observed = datetime.fromisoformat(
        "2026-07-31T14:50:05+08:00"
    )
    collector = MootdxMinuteProbeCollectors(
        ["000001"],
        clock=lambda: datetime.fromisoformat(
            "2026-07-31T14:50:06+08:00"
        ),
        client_factory=lambda: client,
    )

    batch = collector.collect_minute_bars(observed)
    collector.close()

    assert len(batch.records) == 2
    assert batch.source_version == MOOTDX_MINUTE_SOURCE_VERSION
    assert len(batch.raw_hash) == 64
    assert all(
        row["source"] == "mootdx_tdx_std_minute"
        and row["source_version"] == MOOTDX_MINUTE_SOURCE_VERSION
        and row["available_at"] == "2026-07-31T14:50:06+08:00"
        for row in batch.records
    )
    assert batch.records[-1]["event_time"] == (
        "2026-07-31T14:50:00+08:00"
    )
    assert client.closed is True


def test_three_consecutive_source_specific_days_qualify_for_review():
    results = [
        _provisional_result(day)
        for day in CALENDAR
    ]

    qualification = evaluate_minute_source_qualification(
        results,
        source="mootdx",
        trading_calendar=CALENDAR_CONTRACT,
        expected_codes=CODES,
    )

    assert qualification["status"] == (
        "SOURCE_QUALIFIED_FOR_PM_REVIEW"
    )
    assert qualification["qualified_for_configuration_review"] is True
    assert qualification["automatic_configuration_change"] is False
    assert qualification["data_ready"] is False
    assert qualification["maximum_consecutive_qualified_days"] == 3
    assert qualification["request_p95_ms"] == pytest.approx(500)
    assert qualification["candidates"] == []
    assert qualification["tickets"] == []
    assert qualification["orders"] == []


def test_eastmoney_is_audit_only_and_never_counts_as_qualified_day():
    qualification = evaluate_minute_source_qualification(
            [_verified_result(CALENDAR[0], "eastmoney")],
        source="eastmoney",
        trading_calendar=CALENDAR_CONTRACT,
        expected_codes=CODES,
    )

    assert qualification["qualified_for_configuration_review"] is False
    assert (
        "audit_only_source_not_eligible"
        in qualification["qualification_errors"]
    )
    assert qualification["maximum_consecutive_qualified_days"] == 0


def test_unversioned_trading_calendar_cannot_qualify_source():
    results = [
        _provisional_result(day)
        for day in CALENDAR
    ]

    qualification = evaluate_minute_source_qualification(
        results,
        source="mootdx",
        trading_calendar=CALENDAR,
        expected_codes=CODES,
    )

    assert qualification["qualified_for_configuration_review"] is False
    assert "trading_calendar_source_missing" in qualification[
        "qualification_errors"
    ]
    assert "trading_calendar_source_version_missing" in qualification[
        "qualification_errors"
    ]
    assert "trading_calendar_raw_hash_invalid" in qualification[
        "qualification_errors"
    ]


def test_nonconsecutive_days_do_not_satisfy_qualification():
    calendar = [
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
    ]
    results = [
        _provisional_result(day)
        for day in (calendar[0], calendar[2], calendar[3])
    ]

    qualification = evaluate_minute_source_qualification(
        results,
        source="mootdx",
        trading_calendar={
            **CALENDAR_CONTRACT,
            "trade_dates": calendar,
        },
        expected_codes=CODES,
    )

    assert qualification["qualified_for_configuration_review"] is False
    assert qualification["maximum_consecutive_qualified_days"] == 2


def test_request_p95_above_two_seconds_blocks_qualification():
    results = [
        _provisional_result(day)
        for day in CALENDAR
    ]
    results[1]["samples"][2]["request_elapsed_ms"] = 2501
    results[1] = _reclassify_result(results[1])

    qualification = evaluate_minute_source_qualification(
        results,
        source="mootdx",
        trading_calendar=CALENDAR_CONTRACT,
        expected_codes=CODES,
    )

    assert qualification["qualified_for_configuration_review"] is False
    assert qualification["request_p95_ms"] == pytest.approx(2501)
    assert any(
        value.startswith("probe_request_p95_exceeded:")
        for value in qualification["qualification_errors"]
    )


def test_evidence_hash_drift_blocks_qualification():
    results = [
        _provisional_result(day)
        for day in CALENDAR
    ]
    results[0]["samples"][0]["request_completed_at"] = (
        "2026-08-03T14:49:56.999+08:00"
    )

    qualification = evaluate_minute_source_qualification(
        results,
        source="mootdx",
        trading_calendar=CALENDAR_CONTRACT,
        expected_codes=CODES,
    )

    assert qualification["qualified_for_configuration_review"] is False
    assert "probe_evidence_hash_drift" in qualification[
        "qualification_errors"
    ]


def test_inconclusive_day_cannot_be_used_for_source_qualification():
    results = [
        _provisional_result(day)
        for day in CALENDAR
    ]
    results[1]["status"] = "MINUTE_LABEL_INCONCLUSIVE"
    results[1]["minute_label_validation_status"] = "INCONCLUSIVE"

    qualification = evaluate_minute_source_qualification(
        results,
        source="mootdx",
        trading_calendar=CALENDAR_CONTRACT,
        expected_codes=CODES,
    )

    assert qualification["qualified_for_configuration_review"] is False
    assert "probe_day_not_provisional" in qualification[
        "qualification_errors"
    ]


def _verified_result(day, source):
    result = classify_minute_label_samples(
        _verified_samples(day, source),
        required_codes=CODES,
        source=source,
    )
    result.update(
        {
            "execution_ok": True,
            "data_ready": False,
            "trade_date": day,
            "source": source,
            "source_versions": [f"{source}_minute_v1"],
            "requires_manual_review": True,
            "late_record_count": 0,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }
    )
    return result


def _provisional_result(day):
    result = _verified_result(day, "mootdx")
    transaction = {
        "source": "mootdx",
        "source_version": "mootdx_transaction_v1",
        "trade_date": day,
        "requested_codes": list(CODES),
        "request_started_at": f"{day}T14:51:06+08:00",
        "request_completed_at": f"{day}T14:51:07+08:00",
        "by_code": {
            code: _transaction_rows(day, code) for code in CODES
        },
    }
    transaction["transaction_evidence_hash"] = (
        compute_transaction_evidence_hash(
            transaction,
            source="mootdx",
        )
    )
    attribution = attribute_mootdx_minute_intervals(
        result,
        transaction,
    )
    result.update(
        {
            "status": "MINUTE_LABEL_PROVISIONAL",
            "minute_label_semantics": attribution["status"],
            "minute_label_validation_status": (
                "PROVISIONAL_TRANSACTION_ATTRIBUTION"
            ),
            "source_role": "qualification_candidate",
            "transaction_evidence": transaction,
            "transaction_attribution": attribution,
            "transaction_evidence_hash": transaction[
                "transaction_evidence_hash"
            ],
            "combined_evidence_hash": attribution[
                "combined_evidence_hash"
            ],
            "recommended_time_contract": attribution[
                "provisional_time_contract"
            ],
        }
    )
    return result


def _transaction_rows(day, code):
    return {
        "source": "mootdx",
        "source_version": "mootdx_transaction_v1",
        "timestamp_precision": "second",
        "volume_unit": "mootdx_native_volume",
        "coverage_complete": True,
        "records": [
            _transaction(day, code, "14:49:00", 10.0, 40, 0),
            _transaction(day, code, "14:49:59", 10.0, 60, 1),
            _transaction(day, code, "14:50:00", 9.9, 10, 2),
            _transaction(day, code, "14:50:59", 9.8, 20, 3),
        ],
        "error": "",
    }


def _transaction(day, code, clock, price, volume, position):
    return {
        "code": code,
        "event_time": f"{day}T{clock}+08:00",
        "timestamp_precision": "second",
        "price": price,
        "volume": volume,
        "trade_count": 1,
        "source_position": position,
    }


def _reclassify_result(result):
    source = result["source"]
    rebuilt = classify_minute_label_samples(
        result["samples"],
        required_codes=result["tracked_codes"],
        source=source,
    )
    rebuilt.update(
        {
            "execution_ok": True,
            "data_ready": False,
            "trade_date": result["trade_date"],
            "source": source,
            "source_versions": result["source_versions"],
            "requires_manual_review": True,
            "late_record_count": 0,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }
    )
    if source == "mootdx" and result.get("transaction_evidence"):
        transaction = result["transaction_evidence"]
        attribution = attribute_mootdx_minute_intervals(
            rebuilt,
            transaction,
        )
        rebuilt.update(
            {
                "status": "MINUTE_LABEL_PROVISIONAL",
                "minute_label_semantics": attribution["status"],
                "minute_label_validation_status": (
                    "PROVISIONAL_TRANSACTION_ATTRIBUTION"
                ),
                "source_role": "qualification_candidate",
                "transaction_evidence": transaction,
                "transaction_attribution": attribution,
                "transaction_evidence_hash": transaction[
                    "transaction_evidence_hash"
                ],
                "combined_evidence_hash": attribution[
                    "combined_evidence_hash"
                ],
                "recommended_time_contract": attribution[
                    "provisional_time_contract"
                ],
            }
        )
    return rebuilt


def _verified_samples(day, source):
    clocks = ("14:49:55", "14:50:05", "14:50:30", "14:51:05")
    samples = []
    for index, clock in enumerate(clocks):
        target = f"{day}T{clock}+08:00"
        signatures = {
            code: {
                "ohlcv_hash": "a" * 64,
                "ohlcv": {
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 100.0,
                    "amount": 1000.0,
                },
                "volume_unit": "mootdx_native_volume",
                "source": source,
                "source_version": f"{source}_minute_v1",
                "raw_hash": f"{index + 1:x}" * 64,
            }
            for code in CODES
        }
        samples.append(
            {
                "probe_source": source,
                "target_at": target,
                "sampled_at": target,
                "request_started_at": target,
                "request_completed_at": target,
                "request_elapsed_ms": 500,
                "requested_codes": list(CODES),
                "covered_codes": list(CODES),
                "presence_by_code": {
                    code: True for code in CODES
                },
                "signatures": signatures,
                "raw_response_hashes": [f"{index + 1:x}" * 64],
                "provider_raw_hash": f"{index + 5:x}" * 64,
                "source_versions": [f"{source}_minute_v1"],
                "sample_trade_date": day,
                "error": "",
            }
        )
    return samples


class _FakeMootdxClient:
    def __init__(self):
        self.closed = False

    def bars(self, **kwargs):
        assert kwargs["frequency"] == "1m"
        return pd.DataFrame(
            [
                {
                    "datetime": "2026-07-31 14:49",
                    "open": 10.0,
                    "close": 10.1,
                    "high": 10.2,
                    "low": 9.9,
                    "vol": 1000,
                    "amount": 10100,
                },
                {
                    "datetime": "2026-07-31 14:50",
                    "open": 10.1,
                    "close": 10.2,
                    "high": 10.3,
                    "low": 10.0,
                    "vol": 1200,
                    "amount": 12240,
                },
            ]
        )

    def close(self):
        self.closed = True
