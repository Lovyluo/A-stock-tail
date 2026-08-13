from __future__ import annotations

from datetime import date, datetime, time
import json
import os
from pathlib import Path
import time as time_module
from typing import Any, Callable, Iterable
from uuid import uuid4

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash
from overnight_quant.data.probe_worker_process import run_probe_worker_process


AUDIT_CODE = "600000"
AUDIT_CLOCKS = (
    time(14, 49, 57),
    time(14, 50, 1),
    time(14, 50, 8),
)
AUDIT_REQUEST_DEADLINE_MS = 2000
AUDIT_SCHEMA_VERSION = "mootdx_600000_boundary_audit_v1"
MAX_AUDIT_START_LAG_MS = 2000


def run_mootdx_boundary_audit(
    *,
    trade_date: str | date,
    endpoint: dict[str, Any],
    output_dir: str | Path,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]] | None = None,
    audit_clocks: Iterable[time] = AUDIT_CLOCKS,
    request_deadline_ms: int = AUDIT_REQUEST_DEADLINE_MS,
) -> dict[str, Any]:
    """Capture three isolated transaction snapshots for boundary audit only."""
    if int(request_deadline_ms) != AUDIT_REQUEST_DEADLINE_MS:
        raise ValueError("boundary_audit_deadline_must_equal_2000ms")
    day = (
        trade_date
        if isinstance(trade_date, date)
        else date.fromisoformat(str(trade_date))
    )
    normalized_endpoint = normalize_audit_endpoint(endpoint)
    runtime_clock = clock or (lambda: datetime.now(CN_TZ))
    runtime_sleep = sleep or time_module.sleep
    runtime_worker = worker_runner or run_probe_worker_process
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    targets = [
        datetime.combine(day, value, tzinfo=CN_TZ)
        for value in audit_clocks
    ]
    if not targets:
        raise ValueError("boundary_audit_targets_missing")

    first_seen: dict[str, dict[str, str]] = {}
    samples = []
    for target in targets:
        current = _as_cn(runtime_clock())
        if current < target:
            runtime_sleep((target - current).total_seconds())
        request_started = _as_cn(runtime_clock())
        schedule_lag_ms = round(
            max(0.0, (request_started - target).total_seconds() * 1000),
            3,
        )
        if request_started.date() != day or (
            schedule_lag_ms > MAX_AUDIT_START_LAG_MS
        ):
            request_completed = request_started
            worker_result = {
                "ok": False,
                "error_code": "SAMPLE_WINDOW_MISSED",
                "error": "SAMPLE_WINDOW_MISSED",
                "request_timed_out": False,
                "worker_terminated": False,
            }
            sample_window_missed = True
        else:
            worker_result = runtime_worker(
                {
                    "operation": "transaction",
                    "source": "mootdx",
                    "codes": [AUDIT_CODE],
                    "observed_at": target.isoformat(),
                    "endpoint": normalized_endpoint,
                    "provider_timeout_seconds": (
                        AUDIT_REQUEST_DEADLINE_MS / 1000.0
                    ),
                },
                AUDIT_REQUEST_DEADLINE_MS,
            )
            request_completed = _as_cn(runtime_clock())
            sample_window_missed = False

        sample = _build_sample(
            target=target,
            endpoint=normalized_endpoint,
            request_started=request_started,
            request_completed=request_completed,
            schedule_lag_ms=schedule_lag_ms,
            worker_result=worker_result,
            sample_window_missed=sample_window_missed,
            first_seen=first_seen,
        )
        output_path = target_dir / audit_output_name(target)
        sample["output_file"] = output_path.name
        sample["audit_record_hash"] = stable_hash(
            {
                key: value
                for key, value in sample.items()
                if key != "audit_record_hash"
            }
        )
        write_json_exclusive(sample, output_path)
        samples.append(
            {
                "target_at": sample["target_at"],
                "status": sample["status"],
                "output_file": output_path.name,
                "audit_record_hash": sample["audit_record_hash"],
                "packet_count": sample["packet_count"],
                "new_packet_count": sample["new_packet_count"],
                "error_code": sample["error_code"],
            }
        )

    return {
        "status": (
            "BOUNDARY_AUDIT_COMPLETE"
            if all(item["status"] == "BOUNDARY_AUDIT_CAPTURED" for item in samples)
            else "BOUNDARY_AUDIT_INCOMPLETE"
        ),
        "execution_ok": True,
        "data_ready": False,
        "qualification_eligible": False,
        "formal_evidence_integration": False,
        "trade_date": day.isoformat(),
        "code": AUDIT_CODE,
        "source": "mootdx",
        "endpoint_id": normalized_endpoint["id"],
        "request_deadline_ms": AUDIT_REQUEST_DEADLINE_MS,
        "samples": samples,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def normalize_audit_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    host = str((endpoint or {}).get("host") or "").strip()
    try:
        port = int((endpoint or {}).get("port") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("boundary_audit_endpoint_invalid") from exc
    identifier = str((endpoint or {}).get("id") or "").strip()
    if not host or port <= 0 or not identifier:
        raise ValueError("boundary_audit_endpoint_invalid")
    return {
        "id": identifier,
        "name": str((endpoint or {}).get("name") or "mootdx_preferred"),
        "host": host,
        "port": port,
    }


def parse_audit_endpoint(value: str) -> dict[str, Any]:
    host, separator, raw_port = str(value or "").strip().rpartition(":")
    if not separator:
        raise ValueError("boundary_audit_endpoint_invalid")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("boundary_audit_endpoint_invalid") from exc
    return normalize_audit_endpoint(
        {
            "id": f"mootdx_preferred@{host}:{port}",
            "name": "mootdx_preferred",
            "host": host,
            "port": port,
        }
    )


def audit_output_name(target: datetime) -> str:
    return (
        f"mootdx_boundary_audit_{AUDIT_CODE}_"
        f"{target.date().isoformat()}_{target.strftime('%H%M%S')}.json"
    )


def write_json_exclusive(payload: dict[str, Any], path: str | Path) -> None:
    """Publish UTF-8 JSON atomically without replacing prior evidence."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _build_sample(
    *,
    target: datetime,
    endpoint: dict[str, Any],
    request_started: datetime,
    request_completed: datetime,
    schedule_lag_ms: float,
    worker_result: dict[str, Any],
    sample_window_missed: bool,
    first_seen: dict[str, dict[str, str]],
) -> dict[str, Any]:
    payload = worker_result.get("payload") or {}
    evidence = payload.get("transaction_evidence") or {}
    endpoint_matches = str(evidence.get("endpoint_id") or "") == endpoint["id"]
    packets = []
    contract_error = ""
    if worker_result.get("ok") is True and endpoint_matches:
        rows = (
            ((evidence.get("by_code") or {}).get(AUDIT_CODE) or {}).get(
                "records"
            )
            or []
        )
        try:
            packets = _audit_packets(
                rows,
                observed_at=request_completed,
                target=target,
                first_seen=first_seen,
            )
        except (TypeError, ValueError):
            contract_error = "AUDIT_TRANSACTION_CONTRACT_INVALID"

    error_code = str(worker_result.get("error_code") or "")
    error = str(worker_result.get("error") or "")
    if worker_result.get("ok") is True and not endpoint_matches:
        error_code = "AUDIT_ENDPOINT_MISMATCH"
        error = error_code
    elif contract_error:
        error_code = contract_error
        error = error_code
    elif worker_result.get("ok") is True and not packets:
        error_code = "AUDIT_TRANSACTION_DATA_UNAVAILABLE"
        error = error_code
    status = (
        "BOUNDARY_AUDIT_CAPTURED"
        if not error_code and packets
        else "BOUNDARY_AUDIT_FAILED"
    )
    return {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "status": status,
        "execution_ok": True,
        "data_ready": False,
        "qualification_eligible": False,
        "formal_evidence_integration": False,
        "trade_date": target.date().isoformat(),
        "code": AUDIT_CODE,
        "source": "mootdx",
        "source_version": str(evidence.get("source_version") or ""),
        "endpoint_id": endpoint["id"],
        "target_at": target.isoformat(timespec="seconds"),
        "request_started_at": request_started.isoformat(
            timespec="milliseconds"
        ),
        "request_completed_at": request_completed.isoformat(
            timespec="milliseconds"
        ),
        "schedule_lag_ms": schedule_lag_ms,
        "completion_lag_ms": round(
            max(0.0, (request_completed - target).total_seconds() * 1000),
            3,
        ),
        "request_elapsed_ms": round(
            max(
                0.0,
                (request_completed - request_started).total_seconds()
                * 1000,
            ),
            3,
        ),
        "request_deadline_ms": AUDIT_REQUEST_DEADLINE_MS,
        "request_timed_out": bool(worker_result.get("request_timed_out")),
        "sample_window_missed": sample_window_missed,
        "worker_terminated": bool(worker_result.get("worker_terminated")),
        "error_code": error_code,
        "error": error,
        "raw_response_hashes": sorted(
            {
                str(value)
                for value in _raw_response_hashes(evidence)
                if value
            }
        ),
        "packet_count": len(packets),
        "new_packet_count": sum(packet["first_seen_in_sample"] for packet in packets),
        "packets": packets,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _audit_packets(
    rows: Iterable[dict[str, Any]],
    *,
    observed_at: datetime,
    target: datetime,
    first_seen: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    packets = []
    for row in rows:
        event = parse_cn_datetime(row.get("event_time"))
        if event is None or event.date() != target.date():
            continue
        if (event.hour, event.minute) not in {(14, 49), (14, 50)}:
            continue
        raw = {
            "event_time": event.isoformat(timespec="seconds"),
            "source_time_text": str(row.get("source_time_text") or ""),
            "source_position": int(row.get("source_position") or 0),
            "minute_label": event.strftime("%H:%M"),
            "price": row.get("price"),
            "raw_volume": row.get("raw_volume", row.get("volume")),
            "raw_volume_unit": str(row.get("raw_volume_unit") or ""),
            "normalized_volume": _normalized_share_volume(row),
            "normalized_volume_unit": "share",
            "volume_conversion_factor": 100.0,
            "volume_conversion_basis": "A_share_round_lot_100_shares",
            "trade_count": int(row.get("trade_count") or 0),
            "buy_or_sell": row.get("buy_or_sell"),
        }
        packet_hash = stable_hash(raw)
        is_new = packet_hash not in first_seen
        if is_new:
            first_seen[packet_hash] = {
                "first_observed_at": observed_at.isoformat(
                    timespec="milliseconds"
                ),
                "first_target_at": target.isoformat(timespec="seconds"),
            }
        packets.append(
            {
                **raw,
                **first_seen[packet_hash],
                "first_seen_in_sample": is_new,
                "packet_hash": packet_hash,
            }
        )
    return sorted(
        packets,
        key=lambda row: (
            row["event_time"],
            row["source_position"],
            row["packet_hash"],
        ),
    )


def _raw_response_hashes(evidence: dict[str, Any]) -> list[str]:
    stock = ((evidence.get("by_code") or {}).get(AUDIT_CODE) or {})
    return list(stock.get("raw_response_hashes") or [])


def _normalized_share_volume(row: dict[str, Any]) -> float:
    raw_unit = str(row.get("raw_volume_unit") or "").strip().lower()
    if raw_unit != "lot":
        raise ValueError("boundary_audit_transaction_volume_unit_invalid")
    try:
        return float(row.get("raw_volume", row.get("volume"))) * 100.0
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "boundary_audit_transaction_volume_invalid"
        ) from exc


def _as_cn(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)
