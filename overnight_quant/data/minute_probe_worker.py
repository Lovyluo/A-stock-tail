from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

from overnight_quant.data.minute_label_probe import minute_1450_signature
from overnight_quant.data.minute_probe_sources import (
    build_minute_probe_collector,
    normalize_probe_source,
)
from overnight_quant.data.point_in_time import parse_cn_datetime
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.market_calendar import CN_TZ


def execute_probe_worker_task(task: dict[str, Any]) -> dict[str, Any]:
    source = normalize_probe_source(str(task.get("source") or ""))
    codes = [str(code).zfill(6) for code in task.get("codes") or []]
    operation = str(task.get("operation") or "").strip().lower()
    if operation not in {
        "preflight",
        "minute",
        "transaction",
        "benchmark_minute",
    }:
        raise ValueError(f"worker_operation_invalid:{operation}")
    observed_at = parse_cn_datetime(task.get("observed_at"))
    if observed_at is None:
        raise ValueError("worker_observed_at_invalid")
    collector = build_minute_probe_collector(
        source,
        codes,
        endpoint=task.get("endpoint") or None,
        request_timeout_seconds=float(
            task.get("provider_timeout_seconds") or 2.0
        ),
        minute_offset=(
            int(task.get("minute_offset") or 800)
            if operation == "benchmark_minute"
            else 800
        ),
    )
    try:
        request_started = datetime.now(CN_TZ)
        if operation in {"preflight", "minute", "benchmark_minute"}:
            batch = collector.collect_minute_bars(observed_at)
            payload = _summarize_minute_batch(
                batch,
                codes=codes,
                endpoint_id=str(
                    getattr(collector, "endpoint_id", "") or ""
                ),
            )
        elif operation == "transaction":
            collect = getattr(
                collector,
                "collect_transaction_evidence",
                None,
            )
            if not callable(collect):
                raise ValueError("transaction_collector_unavailable")
            payload = {"transaction_evidence": collect(observed_at)}
        request_completed = datetime.now(CN_TZ)
        payload["worker_request_started_at"] = (
            request_started.isoformat(timespec="milliseconds")
        )
        payload["worker_request_completed_at"] = (
            request_completed.isoformat(timespec="milliseconds")
        )
        payload["worker_request_elapsed_ms"] = round(
            (request_completed - request_started).total_seconds() * 1000,
            3,
        )
        return payload
    finally:
        close = getattr(collector, "close", None)
        if callable(close):
            close()


def _summarize_minute_batch(
    batch: Any,
    *,
    codes: list[str],
    endpoint_id: str,
) -> dict[str, Any]:
    records = list(batch.records or [])
    signatures = minute_1450_signature(records)
    covered_codes = sorted(
        {
            str((row.get("payload") or {}).get("code") or "").zfill(6)
            for row in records
            if str(row.get("data_type") or "") == "minute_bar"
            and (row.get("payload") or {}).get("code")
        }
    )
    canonical_by_code: dict[str, list[dict[str, Any]]] = {
        code: [] for code in codes
    }
    for row in records:
        if str(row.get("data_type") or "") != "minute_bar":
            continue
        payload = row.get("payload") or {}
        code = str(payload.get("code") or "").zfill(6)
        if code not in canonical_by_code:
            continue
        canonical_by_code[code].append(
            {
                "event_time": str(row.get("event_time") or ""),
                "open": payload.get("open"),
                "high": payload.get("high"),
                "low": payload.get("low"),
                "close": payload.get("close"),
                "volume": payload.get("volume"),
                "amount": payload.get("amount"),
            }
        )
    canonical_by_code = {
        code: sorted(rows, key=lambda item: item["event_time"])
        for code, rows in canonical_by_code.items()
    }
    return {
        "requested_codes": list(codes),
        "covered_codes": covered_codes,
        "presence_by_code": {
            code: code in signatures for code in codes
        },
        "signatures": signatures,
        "raw_response_hashes": sorted(
            {
                str(row.get("raw_hash") or "")
                for row in records
                if row.get("raw_hash")
            }
        ),
        "provider_raw_hash": str(batch.raw_hash or ""),
        "source_versions": sorted(
            {
                str(row.get("source_version") or "")
                for row in records
                if row.get("source_version")
            }
        ),
        "returned_record_count": len(records),
        "endpoint_id": endpoint_id,
        "minute_record_count_by_code": {
            code: len(rows)
            for code, rows in canonical_by_code.items()
        },
        "canonical_minute_hash_by_code": {
            code: stable_hash(rows)
            for code, rows in canonical_by_code.items()
        },
    }


def _stable_error_code(exc: Exception) -> str:
    text = str(exc or "").lower()
    if "mootdx_minute_bar_empty" in text:
        return "MOOTDX_MINUTE_BAR_EMPTY"
    if "mootdx_trade_date_rows_empty" in text:
        return "MOOTDX_TRADE_DATE_ROWS_EMPTY"
    if "http_request_failed" in text:
        return "HTTP_REQUEST_FAILED"
    if "transaction_collector_unavailable" in text:
        return "TRANSACTION_COLLECTOR_UNAVAILABLE"
    return "PROBE_WORKER_FAILED"


def main() -> int:
    try:
        task = json.loads(sys.stdin.read())
        payload = execute_probe_worker_task(task)
        result = {"ok": True, "payload": payload}
        exit_code = 0
    except Exception as exc:
        result = {
            "ok": False,
            "error_code": _stable_error_code(exc),
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
    )
    sys.stdout.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
