from __future__ import annotations

from datetime import datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.mootdx_boundary_audit import (
    AUDIT_CODE,
    AUDIT_REQUEST_DEADLINE_MS,
    audit_output_name,
    run_mootdx_boundary_audit,
    write_json_exclusive,
)
from overnight_quant.data.mootdx_boundary_audit_worker import (
    execute_boundary_audit_worker_task,
)


DAY = "2026-08-14"
ENDPOINT = {
    "id": "mootdx_preferred@60.191.117.167:7709",
    "name": "mootdx_preferred",
    "host": "60.191.117.167",
    "port": 7709,
}
TARGETS = (time(14, 49, 57), time(14, 50, 1), time(14, 50, 8))


class AdvancingClock:
    def __init__(self, value: datetime):
        self.now = value

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_three_points_are_isolated_fixed_endpoint_and_non_trading(tmp_path):
    clock = AdvancingClock(
        datetime.fromisoformat(f"{DAY}T14:49:56+08:00")
    )
    calls = []

    def worker(task, deadline_ms):
        calls.append((task, deadline_ms))
        clock.advance(0.1)
        return _worker_success(task, packet_suffix=len(calls))

    result = run_mootdx_boundary_audit(
        trade_date=DAY,
        endpoint=ENDPOINT,
        output_dir=tmp_path,
        clock=clock,
        sleep=clock.advance,
        worker_runner=worker,
        audit_clocks=TARGETS,
    )

    assert result["status"] == "BOUNDARY_AUDIT_COMPLETE"
    assert len(calls) == 3
    assert all(call[1] == AUDIT_REQUEST_DEADLINE_MS for call in calls)
    assert all(call[0]["codes"] == [AUDIT_CODE] for call in calls)
    assert all(call[0]["endpoint"] == ENDPOINT for call in calls)
    assert all(
        call[0]["operation"] == "boundary_audit_transaction"
        for call in calls
    )
    assert len({item["output_file"] for item in result["samples"]}) == 3
    _assert_safe(result)
    for target in TARGETS:
        path = tmp_path / audit_output_name(
            datetime.combine(date_from_day(), target, tzinfo=CN_TZ)
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["endpoint_id"] == ENDPOINT["id"]
        assert payload["qualification_eligible"] is False
        assert payload["formal_evidence_integration"] is False
        assert "probe_evidence_hash" not in payload
        _assert_safe(payload)


def test_timeout_does_not_block_later_points_or_retry(tmp_path):
    clock = AdvancingClock(
        datetime.fromisoformat(f"{DAY}T14:49:56+08:00")
    )
    calls = []

    def worker(task, deadline_ms):
        calls.append(task["observed_at"])
        if len(calls) == 1:
            clock.advance(2.0)
            return {
                "ok": False,
                "error_code": "REQUEST_DEADLINE_EXCEEDED",
                "error": "REQUEST_DEADLINE_EXCEEDED",
                "request_timed_out": True,
                "worker_terminated": True,
            }
        clock.advance(0.1)
        return _worker_success(task, packet_suffix=len(calls))

    result = run_mootdx_boundary_audit(
        trade_date=DAY,
        endpoint=ENDPOINT,
        output_dir=tmp_path,
        clock=clock,
        sleep=clock.advance,
        worker_runner=worker,
        audit_clocks=TARGETS,
    )

    assert len(calls) == 3
    assert result["status"] == "BOUNDARY_AUDIT_INCOMPLETE"
    assert result["samples"][0]["error_code"] == (
        "REQUEST_DEADLINE_EXCEEDED"
    )
    assert result["samples"][1]["status"] == "BOUNDARY_AUDIT_CAPTURED"
    assert result["samples"][2]["status"] == "BOUNDARY_AUDIT_CAPTURED"
    _assert_safe(result)


def test_missed_point_is_recorded_without_makeup_request(tmp_path):
    clock = AdvancingClock(
        datetime.fromisoformat(f"{DAY}T14:50:00.500+08:00")
    )
    calls = []

    def worker(task, deadline_ms):
        calls.append(task["observed_at"])
        clock.advance(0.1)
        return _worker_success(task, packet_suffix=len(calls))

    result = run_mootdx_boundary_audit(
        trade_date=DAY,
        endpoint=ENDPOINT,
        output_dir=tmp_path,
        clock=clock,
        sleep=clock.advance,
        worker_runner=worker,
        audit_clocks=TARGETS,
    )

    assert len(calls) == 2
    assert result["samples"][0]["error_code"] == "SAMPLE_WINDOW_MISSED"
    assert result["samples"][0]["status"] == "BOUNDARY_AUDIT_FAILED"
    _assert_safe(result)


def test_first_observed_packet_is_carried_across_files(tmp_path):
    clock = AdvancingClock(
        datetime.fromisoformat(f"{DAY}T14:49:56+08:00")
    )

    def worker(task, deadline_ms):
        clock.advance(0.1)
        return _worker_success(task, packet_suffix=1)

    run_mootdx_boundary_audit(
        trade_date=DAY,
        endpoint=ENDPOINT,
        output_dir=tmp_path,
        clock=clock,
        sleep=clock.advance,
        worker_runner=worker,
        audit_clocks=TARGETS[:2],
    )
    first = _read_target(tmp_path, TARGETS[0])
    second = _read_target(tmp_path, TARGETS[1])

    assert first["new_packet_count"] == first["packet_count"]
    assert second["new_packet_count"] == 0
    assert second["packets"][0]["first_observed_at"] == (
        first["packets"][0]["first_observed_at"]
    )
    assert second["packets"][0]["source_position"] == 7
    assert second["packets"][0]["minute_label"] == "14:49"
    assert second["packets"][0]["raw_volume"] == 12.0
    assert second["packets"][0]["normalized_volume"] == 1200.0
    assert second["packets"][0]["normalized_volume_unit"] == "share"
    assert second["packets"][0]["trade_count"] == 3
    assert second["packets"][0]["packet_hash"]


def test_existing_output_is_never_overwritten(tmp_path):
    path = tmp_path / "evidence.json"
    write_json_exclusive({"value": 1}, path)
    original_hash = _sha256(path)

    with pytest.raises(FileExistsError):
        write_json_exclusive({"value": 2}, path)

    assert _sha256(path) == original_hash
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 1}


def test_endpoint_mismatch_fails_closed(tmp_path):
    clock = AdvancingClock(
        datetime.fromisoformat(f"{DAY}T14:49:56+08:00")
    )

    def worker(task, deadline_ms):
        clock.advance(0.1)
        result = _worker_success(task, packet_suffix=1)
        result["payload"]["transaction_evidence"]["endpoint_id"] = (
            "different@127.0.0.1:7709"
        )
        return result

    result = run_mootdx_boundary_audit(
        trade_date=DAY,
        endpoint=ENDPOINT,
        output_dir=tmp_path,
        clock=clock,
        sleep=clock.advance,
        worker_runner=worker,
        audit_clocks=TARGETS[:1],
    )

    assert result["samples"][0]["error_code"] == "AUDIT_ENDPOINT_MISMATCH"
    _assert_safe(result)


def test_unknown_transaction_volume_unit_fails_closed(tmp_path):
    clock = AdvancingClock(
        datetime.fromisoformat(f"{DAY}T14:49:56+08:00")
    )

    def worker(task, deadline_ms):
        clock.advance(0.1)
        result = _worker_success(task, packet_suffix=1)
        rows = result["payload"]["transaction_evidence"]["by_code"][
            AUDIT_CODE
        ]["records"]
        rows[0]["raw_volume_unit"] = "unknown"
        return result

    result = run_mootdx_boundary_audit(
        trade_date=DAY,
        endpoint=ENDPOINT,
        output_dir=tmp_path,
        clock=clock,
        sleep=clock.advance,
        worker_runner=worker,
        audit_clocks=TARGETS[:1],
    )

    assert result["samples"][0]["error_code"] == (
        "AUDIT_TRANSACTION_CONTRACT_INVALID"
    )
    _assert_safe(result)


def test_validate_only_is_safe_and_fixed_to_600000():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_mootdx_boundary_audit.py"
    )
    completed = subprocess.run(
        [
            str(
                Path(__file__).resolve().parents[2]
                / ".venv"
                / "Scripts"
                / "python.exe"
            ),
            str(script),
            "--date",
            DAY,
            "--endpoint",
            "60.191.117.167:7709",
            "--validate-only",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "BOUNDARY_AUDIT_VALIDATED"
    assert result["code"] == AUDIT_CODE
    assert result["targets"] == [
        f"{DAY}T14:49:57+08:00",
        f"{DAY}T14:50:01+08:00",
        f"{DAY}T14:50:08+08:00",
    ]
    assert result["request_deadline_ms"] == 2000
    _assert_safe(result)


def test_boundary_worker_fetches_one_page_without_waiting_for_full_window():
    collector = FakeBoundaryCollector()

    result = execute_boundary_audit_worker_task(
        {
            "operation": "boundary_audit_transaction",
            "source": "mootdx",
            "codes": [AUDIT_CODE],
            "observed_at": f"{DAY}T14:49:57+08:00",
            "endpoint": ENDPOINT,
            "provider_timeout_seconds": 2.0,
        },
        collector_factory=lambda *args, **kwargs: collector,
    )

    assert collector.calls == [(800, 1)]
    assert collector.closed is True
    assert result["transaction_evidence"]["endpoint_id"] == ENDPOINT["id"]


def _worker_success(task, *, packet_suffix: int):
    row = {
        "event_time": f"{DAY}T14:49:00+08:00",
        "source_time_text": "14:49",
        "source_position": 7,
        "price": 9.18,
        "raw_volume": 12.0,
        "raw_volume_unit": "lot",
        "trade_count": 3,
        "buy_or_sell": 0,
    }
    evidence = {
        "source": "mootdx",
        "source_version": "mootdx_0.11.7_tdx_std_transaction_v2026-08-06",
        "endpoint_id": task["endpoint"]["id"],
        "by_code": {
            AUDIT_CODE: {
                "raw_response_hashes": [f"raw-{packet_suffix}"],
                "records": [row],
            }
        },
    }
    return {
        "ok": True,
        "payload": {"transaction_evidence": evidence},
        "error_code": "",
        "error": "",
        "request_timed_out": False,
        "worker_terminated": False,
    }


class FakeBoundaryCollector:
    def __init__(self):
        self.calls = []
        self.closed = False

    def collect_transaction_evidence(
        self,
        observed_at,
        *,
        page_size,
        max_pages,
    ):
        self.calls.append((page_size, max_pages))
        return {
            "source": "mootdx",
            "source_version": (
                "mootdx_0.11.7_tdx_std_transaction_v2026-08-06"
            ),
            "endpoint_id": ENDPOINT["id"],
            "by_code": {AUDIT_CODE: {"records": []}},
        }

    def close(self):
        self.closed = True


def _read_target(path: Path, value: time):
    target = datetime.combine(date_from_day(), value, tzinfo=CN_TZ)
    return json.loads(
        (path / audit_output_name(target)).read_text(encoding="utf-8")
    )


def date_from_day():
    return datetime.fromisoformat(DAY).date()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_safe(result):
    assert result["data_ready"] is False
    assert result["qualification_eligible"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []
