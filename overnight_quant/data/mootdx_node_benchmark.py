from __future__ import annotations

from datetime import date, datetime
import math
from pathlib import Path
import time
from typing import Any, Callable, Iterable

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_probe_sources import (
    mootdx_server_candidates,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.probe_worker_process import (
    run_probe_worker_process,
)


NODE_BENCHMARK_VERSION = "mootdx_node_benchmark_v1"
FORMAL_MINUTE_OFFSET = 800
COMPARISON_MINUTE_OFFSET = 320
REQUEST_DEADLINE_MS = 2000
RECOMMENDATION_MAX_ELAPSED_MS = 1500


def run_mootdx_node_benchmark(
    codes: Iterable[str],
    *,
    trade_date: str | date | None = None,
    endpoint_candidates: list[dict[str, Any]] | None = None,
    top_count: int = 3,
    qualification_rounds: int = 5,
    deadline_ms: int = REQUEST_DEADLINE_MS,
    recommendation_max_ms: int = RECOMMENDATION_MAX_ELAPSED_MS,
    compare_offsets: bool = True,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]] = (
        run_probe_worker_process
    ),
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Benchmark mootdx endpoints without changing runtime configuration."""
    if int(deadline_ms) != REQUEST_DEADLINE_MS:
        raise ValueError("node_benchmark_deadline_must_be_2000ms")
    normalized_codes = sorted(
        {
            str(code).strip().zfill(6)
            for code in codes
            if str(code).strip()
        }
    )
    if not normalized_codes:
        raise ValueError("node_benchmark_codes_missing")
    rounds = max(5, int(qualification_rounds))
    limit = max(1, min(3, int(top_count)))
    runtime_clock = clock or (lambda: datetime.now(CN_TZ))
    clock_now = runtime_clock()
    expected_date = _trade_date_text(trade_date or clock_now.date())
    observed = _benchmark_observed_at(clock_now, expected_date)
    candidates = _deduplicate_endpoints(
        endpoint_candidates
        if endpoint_candidates is not None
        else mootdx_server_candidates(limit=1000)
    )

    screening = [
        _run_endpoint(
            endpoint,
            normalized_codes,
            observed_at=observed,
            deadline_ms=deadline_ms,
            worker_runner=worker_runner,
            operation="preflight",
        )
        for endpoint in candidates
    ]
    ranked = sorted(screening, key=_screening_rank)
    finalists = [row["endpoint"] for row in ranked[:limit]]

    qualification: list[dict[str, Any]] = []
    for endpoint in finalists:
        runs = [
            _run_endpoint(
                endpoint,
                normalized_codes,
                observed_at=observed,
                deadline_ms=deadline_ms,
                worker_runner=worker_runner,
                operation="preflight",
            )
            for _ in range(rounds)
        ]
        qualification.append(
            _summarize_endpoint(
                endpoint,
                runs,
                expected_codes=normalized_codes,
                recommendation_max_ms=recommendation_max_ms,
            )
        )

    eligible = sorted(
        [row for row in qualification if row["recommendation_eligible"]],
        key=lambda row: (
            float(row["latency_ms"]["p95"]),
            float(row["latency_ms"]["max"]),
            str(row["endpoint"]["id"]),
        ),
    )
    recommended = eligible[0]["endpoint"] if eligible else None
    comparison_endpoint = recommended or (
        finalists[0] if finalists else None
    )
    offset_comparison = (
        _compare_offsets(
            comparison_endpoint,
            normalized_codes,
            observed_at=observed,
            deadline_ms=deadline_ms,
            worker_runner=worker_runner,
        )
        if compare_offsets and comparison_endpoint is not None
        else _empty_offset_comparison()
    )
    result = {
        "status": "MOOTDX_NODE_BENCHMARK_COMPLETED",
        "execution_ok": True,
        "data_ready": False,
        "audit_only": True,
        "benchmark_version": NODE_BENCHMARK_VERSION,
        "trade_date": expected_date,
        "observed_at": observed.isoformat(timespec="milliseconds"),
        "tracked_codes": normalized_codes,
        "request_deadline_ms": REQUEST_DEADLINE_MS,
        "recommendation_max_elapsed_ms": int(recommendation_max_ms),
        "screened_endpoint_count": len(candidates),
        "screening": screening,
        "qualification_rounds": rounds,
        "qualification": qualification,
        "recommended_endpoint": recommended,
        "automatic_configuration_change": False,
        "formal_minute_offset": FORMAL_MINUTE_OFFSET,
        "offset_comparison": offset_comparison,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    result["benchmark_evidence_hash"] = stable_hash(result)
    return result


def write_benchmark_json_atomic(
    path: str | Path,
    payload: dict[str, Any],
) -> Path:
    import json
    import os

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"benchmark_output_already_exists:{target}")
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _run_endpoint(
    endpoint: dict[str, Any],
    codes: list[str],
    *,
    observed_at: datetime,
    deadline_ms: int,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]],
    operation: str,
    minute_offset: int | None = None,
) -> dict[str, Any]:
    task = {
        "operation": operation,
        "source": "mootdx",
        "codes": list(codes),
        "observed_at": observed_at.isoformat(),
        "endpoint": endpoint,
        "provider_timeout_seconds": deadline_ms / 1000.0,
    }
    if minute_offset is not None:
        task["minute_offset"] = int(minute_offset)
    wall_started = time.perf_counter()
    worker_result = worker_runner(task, deadline_ms)
    wall_elapsed_ms = round((time.perf_counter() - wall_started) * 1000, 3)
    payload = worker_result.get("payload") or {}
    e2e_ms = float(worker_result.get("elapsed_ms") or wall_elapsed_ms)
    provider_ms = _optional_float(payload.get("worker_request_elapsed_ms"))
    covered = sorted(str(code).zfill(6) for code in payload.get("covered_codes") or [])
    return {
        "endpoint": dict(endpoint),
        "operation": operation,
        "minute_offset": minute_offset,
        "ok": worker_result.get("ok") is True,
        "covered_codes": covered,
        "coverage_count": len(covered),
        "coverage_ratio": round(len(covered) / len(codes), 6),
        "returned_record_count": int(payload.get("returned_record_count") or 0),
        "request_timed_out": bool(worker_result.get("request_timed_out")),
        "worker_terminated": bool(worker_result.get("worker_terminated")),
        "error_code": str(worker_result.get("error_code") or ""),
        "error": str(worker_result.get("error") or "")[:500],
        "provider_elapsed_ms": provider_ms,
        "end_to_end_elapsed_ms": round(e2e_ms, 3),
        "process_overhead_ms": (
            round(max(0.0, e2e_ms - provider_ms), 3)
            if provider_ms is not None
            else None
        ),
        "endpoint_id": str(payload.get("endpoint_id") or ""),
        "source_versions": sorted(payload.get("source_versions") or []),
        "provider_raw_hash": str(payload.get("provider_raw_hash") or ""),
        "raw_response_hashes": sorted(payload.get("raw_response_hashes") or []),
        "signatures": payload.get("signatures") or {},
        "minute_record_count_by_code": (
            payload.get("minute_record_count_by_code") or {}
        ),
        "canonical_minute_hash_by_code": (
            payload.get("canonical_minute_hash_by_code") or {}
        ),
    }


def _summarize_endpoint(
    endpoint: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    expected_codes: list[str],
    recommendation_max_ms: int,
) -> dict[str, Any]:
    latencies = [float(row["end_to_end_elapsed_ms"]) for row in runs]
    overheads = [
        float(row["process_overhead_ms"])
        for row in runs
        if row.get("process_overhead_ms") is not None
    ]
    complete = [
        row
        for row in runs
        if row["ok"]
        and row["covered_codes"] == expected_codes
        and row["endpoint_id"] == str(endpoint.get("id") or "")
    ]
    timeout_count = sum(row["request_timed_out"] for row in runs)
    error_count = sum(bool(row["error_code"]) for row in runs)
    maximum = max(latencies) if latencies else None
    eligible = bool(runs) and (
        len(complete) == len(runs)
        and timeout_count == 0
        and error_count == 0
        and maximum is not None
        and maximum <= float(recommendation_max_ms)
    )
    return {
        "endpoint": dict(endpoint),
        "round_count": len(runs),
        "successful_round_count": len(complete),
        "complete_coverage_round_count": len(complete),
        "timeout_count": timeout_count,
        "error_count": error_count,
        "latency_ms": {
            "p50": _nearest_rank(latencies, 50),
            "p95": _nearest_rank(latencies, 95),
            "max": round(maximum, 3) if maximum is not None else None,
        },
        "process_overhead_ms": {
            "p50": _nearest_rank(overheads, 50),
            "p95": _nearest_rank(overheads, 95),
            "max": round(max(overheads), 3) if overheads else None,
        },
        "recommendation_eligible": eligible,
        "runs": runs,
    }


def _compare_offsets(
    endpoint: dict[str, Any],
    codes: list[str],
    *,
    observed_at: datetime,
    deadline_ms: int,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]],
) -> dict[str, Any]:
    runs = {
        str(offset): _run_endpoint(
            endpoint,
            codes,
            observed_at=observed_at,
            deadline_ms=deadline_ms,
            worker_runner=worker_runner,
            operation="benchmark_minute",
            minute_offset=offset,
        )
        for offset in (FORMAL_MINUTE_OFFSET, COMPARISON_MINUTE_OFFSET)
    }
    formal = runs[str(FORMAL_MINUTE_OFFSET)]
    comparison = runs[str(COMPARISON_MINUTE_OFFSET)]
    both_complete = all(
        row["ok"] and row["covered_codes"] == codes
        for row in (formal, comparison)
    )
    return {
        "status": (
            "OFFSET_COMPARISON_COMPLETED"
            if both_complete
            else "OFFSET_COMPARISON_INCOMPLETE"
        ),
        "audit_only": True,
        "formal_default_unchanged": True,
        "runs": runs,
        "same_record_counts": (
            both_complete
            and formal["minute_record_count_by_code"]
            == comparison["minute_record_count_by_code"]
        ),
        "same_1450_ohlcv_signatures": (
            both_complete and formal["signatures"] == comparison["signatures"]
        ),
        "same_canonical_hashes": (
            both_complete
            and formal["canonical_minute_hash_by_code"]
            == comparison["canonical_minute_hash_by_code"]
        ),
    }


def _empty_offset_comparison() -> dict[str, Any]:
    return {
        "status": "OFFSET_COMPARISON_NOT_RUN",
        "audit_only": True,
        "formal_default_unchanged": True,
        "runs": {},
        "same_record_counts": False,
        "same_1450_ohlcv_signatures": False,
        "same_canonical_hashes": False,
    }


def _screening_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if row["ok"] else 1,
        -int(row["coverage_count"]),
        1 if row["request_timed_out"] else 0,
        float(row["end_to_end_elapsed_ms"]),
        str((row.get("endpoint") or {}).get("id") or ""),
    )


def _deduplicate_endpoints(
    values: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for value in values:
        host = str(value.get("host") or "").strip()
        try:
            port = int(value.get("port") or 0)
        except (TypeError, ValueError):
            continue
        if not host or port <= 0 or (host, port) in seen:
            continue
        seen.add((host, port))
        name = str(value.get("name") or "").strip()
        result.append(
            {
                "id": str(value.get("id") or f"{name or 'mootdx'}@{host}:{port}"),
                "name": name,
                "host": host,
                "port": port,
            }
        )
    return result


def _nearest_rank(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(len(ordered) * percentile / 100.0))
    return round(ordered[rank - 1], 3)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trade_date_text(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def _benchmark_observed_at(value: datetime, trade_date: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=CN_TZ)
    else:
        value = value.astimezone(CN_TZ)
    expected = date.fromisoformat(trade_date)
    if value.date() == expected:
        return value
    return datetime(
        expected.year,
        expected.month,
        expected.day,
        15,
        0,
        tzinfo=CN_TZ,
    )
