from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
from statistics import median
import time as time_module
from typing import Any, Callable

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.snapshot_store import ProviderBatch


def run_provider_stress(
    providers: dict[str, Callable[[datetime], Any]],
    *,
    observed_at: datetime | None = None,
    deadline_seconds: float = 30.0,
    max_workers: int = 4,
    monotonic: Callable[[], float] = time_module.monotonic,
) -> dict[str, Any]:
    observed = observed_at or datetime.now(CN_TZ)
    started = monotonic()
    deadline = started + max(0.1, float(deadline_seconds))
    _set_deadlines(providers, deadline)
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(int(max_workers), len(providers) or 1)),
        thread_name_prefix="pit-stress",
    )
    futures: dict[Future, str] = {}
    skipped = []
    for source in sorted(providers):
        if monotonic() >= deadline:
            skipped.append(source)
            continue
        futures[
            executor.submit(
                _run_one,
                providers[source],
                observed,
                monotonic,
            )
        ] = source
    done, pending = wait(
        futures,
        timeout=max(0.0, deadline - monotonic()),
    )
    outcomes = {
        futures[future]: future.result() for future in done
    }
    deadline_sources = sorted(
        [*skipped, *(futures[future] for future in pending)]
    )
    for future in pending:
        future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)
    _set_deadlines(providers, None)

    provider_metrics: dict[str, dict[str, Any]] = {}
    coverage: dict[str, set[str]] = {}
    for source in sorted(providers):
        if source in deadline_sources:
            provider_metrics[source] = {
                "status": "DEADLINE_EXCEEDED",
                "elapsed_ms": None,
                "record_count": 0,
            }
            continue
        outcome = outcomes[source]
        rows, data_types = _rows_and_types(outcome.get("result"))
        status = (
            "SUCCESS"
            if not outcome.get("error")
            else (
                "DEADLINE_EXCEEDED"
                if "deadline" in outcome["error"].lower()
                else "FAILED"
            )
        )
        provider_metrics[source] = {
            "status": status,
            "elapsed_ms": outcome["elapsed_ms"],
            "record_count": len(rows),
            "error": outcome.get("error") or "",
        }
        for row in rows:
            payload = row.get("payload") or {}
            data_type = str(
                row.get("data_type")
                or (data_types[0] if len(data_types) == 1 else "")
            )
            if (
                data_type == "fund_flow"
                and (
                    bool(payload.get("is_proxy"))
                    or payload.get("eligible_for_hard_gate") is False
                )
            ):
                data_type = "fund_flow_proxy"
            if not data_type:
                continue
            codes = coverage.setdefault(data_type, set())
            code = str(payload.get("code") or "")
            if code:
                codes.add(code)

    transport_metrics = _transport_metrics(providers)
    elapsed_ms = round((monotonic() - started) * 1000, 3)
    success_count = sum(
        1
        for item in provider_metrics.values()
        if item["status"] == "SUCCESS"
    )
    latencies = [
        float(item["elapsed_ms"])
        for item in provider_metrics.values()
        if item.get("elapsed_ms") is not None
    ]
    return {
        "status": "PROVIDER_STRESS_COMPLETED",
        "execution_ok": True,
        "data_ready": False,
        "total_elapsed_ms": elapsed_ms,
        "provider_latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
        "provider_metrics": provider_metrics,
        "request_count": int(
            transport_metrics.get("request_count", 0)
        ),
        "retry_count": int(
            transport_metrics.get("retry_count", 0)
        ),
        "failure_count": sum(
            1
            for item in provider_metrics.values()
            if item["status"] == "FAILED"
        ),
        "completed_before_deadline_ratio": round(
            success_count / max(1, len(providers)),
            6,
        ),
        "coverage_stock_count_by_type": {
            key: len(value)
            for key, value in sorted(coverage.items())
        },
        "rate_limit_trigger_count": int(
            transport_metrics.get("rate_limit_wait_count", 0)
        ),
        "deadline_trigger_count": int(
            transport_metrics.get("deadline_trigger_count", 0)
        )
        + sum(
            1
            for item in provider_metrics.values()
            if item["status"] == "DEADLINE_EXCEEDED"
        ),
        "audit_only": True,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def summarize_stress_runs(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    provider_values: dict[str, list[float]] = {}
    for row in rows:
        for source, item in (
            row.get("provider_metrics") or {}
        ).items():
            if item.get("elapsed_ms") is not None:
                provider_values.setdefault(source, []).append(
                    float(item["elapsed_ms"])
                )
    return {
        "runs": rows,
        "provider_latency_summary_ms": {
            source: {
                "p50": round(median(values), 3),
                "p95": _percentile(values, 95),
                "max": round(max(values), 3),
            }
            for source, values in sorted(provider_values.items())
        },
        "data_ready": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _run_one(
    provider: Callable[[datetime], Any],
    observed_at: datetime,
    monotonic: Callable[[], float],
) -> dict[str, Any]:
    started = monotonic()
    try:
        result = provider(observed_at)
        error = ""
    except Exception as exc:
        result = None
        error = f"{type(exc).__name__}: {exc}"
    return {
        "result": result,
        "error": error,
        "elapsed_ms": round((monotonic() - started) * 1000, 3),
    }


def _rows_and_types(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(value, ProviderBatch):
        return (
            [dict(item) for item in value.records],
            list(value.data_types),
        )
    rows = [dict(item) for item in (value or [])]
    return (
        rows,
        sorted(
            {
                str(row.get("data_type") or "")
                for row in rows
                if row.get("data_type")
            }
        ),
    )


def _set_deadlines(
    providers: dict[str, Callable[[datetime], Any]],
    deadline: float | None,
) -> None:
    for provider in providers.values():
        setter = getattr(provider, "deadline_setter", None)
        if setter is not None:
            setter(deadline)


def _transport_metrics(
    providers: dict[str, Callable[[datetime], Any]],
) -> dict[str, Any]:
    for provider in providers.values():
        getter = getattr(provider, "metrics_getter", None)
        if getter is not None:
            return dict(getter())
    return {}


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return round(
        ordered[lower] * (1.0 - weight)
        + ordered[upper] * weight,
        3,
    )
