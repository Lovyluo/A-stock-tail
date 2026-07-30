from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Callable

from overnight_quant.data.close_time_contract import (
    MINUTE_LABEL_END,
    MINUTE_LABEL_START,
    MINUTE_LABEL_UNVERIFIED,
    build_close_time_contract,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.real_point_in_time_collectors import (
    RealPointInTimeCollectors,
)


PROBE_CLOCKS = (
    time(14, 49, 55),
    time(14, 50, 5),
    time(14, 50, 30),
    time(14, 51, 5),
)


def minute_1450_signature(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    signatures: dict[str, dict[str, Any]] = {}
    for row in records:
        if str(row.get("data_type") or "") != "minute_bar":
            continue
        if str(row.get("event_time") or "")[11:16] != "14:50":
            continue
        payload = row.get("payload") or {}
        code = str(payload.get("code") or "").zfill(6)
        values = {
            field: payload.get(field)
            for field in (
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            )
        }
        signatures[code] = {
            "ohlcv": values,
            "ohlcv_hash": stable_hash(values),
            "source": row.get("source"),
            "source_version": row.get("source_version"),
            "raw_hash": row.get("raw_hash"),
        }
    return dict(sorted(signatures.items()))


def classify_minute_label_samples(
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(samples, key=lambda item: item["sampled_at"])
    expected = [
        "14:49:55",
        "14:50:05",
        "14:50:30",
        "14:51:05",
    ]
    by_clock = {
        str(item.get("sampled_at") or "")[11:19]: item
        for item in ordered
    }
    missing_points = [
        value for value in expected if value not in by_clock
    ]
    if missing_points:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=[
                "required_probe_points_missing:"
                + ",".join(missing_points)
            ],
        )

    tracked_codes = sorted(
        set.intersection(
            *(
                set((by_clock[clock].get("signatures") or {}).keys())
                for clock in expected[1:]
            )
        )
    )
    if not tracked_codes:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=["minute_1450_not_present_at_required_points"],
        )

    changed_during_1450 = False
    stable_after_1450 = True
    stable_from_1450_start = True
    for code in tracked_codes:
        hashes = [
            (
                by_clock[clock].get("signatures") or {}
            )[code]["ohlcv_hash"]
            for clock in expected[1:]
        ]
        changed_during_1450 = changed_during_1450 or (
            hashes[0] != hashes[1]
        )
        stable_after_1450 = stable_after_1450 and (
            hashes[1] == hashes[2]
        )
        stable_from_1450_start = stable_from_1450_start and (
            hashes[0] == hashes[1] == hashes[2]
        )

    if changed_during_1450 and stable_after_1450:
        semantics = MINUTE_LABEL_START
        conclusion = "VERIFIED"
        reasons = [
            "minute_1450_changed_inside_1450_and_stabilized_after_1451"
        ]
    elif stable_from_1450_start:
        semantics = MINUTE_LABEL_END
        conclusion = "VERIFIED"
        reasons = [
            "minute_1450_was_stable_from_1450_start_through_1451"
        ]
    else:
        semantics = MINUTE_LABEL_UNVERIFIED
        conclusion = "INCONCLUSIVE"
        reasons = ["minute_1450_change_pattern_inconclusive"]
    return _probe_result(
        semantics,
        conclusion,
        samples=ordered,
        reasons=reasons,
        tracked_codes=tracked_codes,
    )


def run_scheduled_minute_label_probe(
    codes: list[str],
    *,
    trade_date: str | date | None = None,
    collectors: RealPointInTimeCollectors | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    import time as time_module

    runtime_clock = clock or (lambda: datetime.now(CN_TZ))
    runtime_sleep = sleep or time_module.sleep
    current = runtime_clock()
    day = (
        current.date()
        if trade_date is None
        else (
            trade_date
            if isinstance(trade_date, date)
            else date.fromisoformat(str(trade_date))
        )
    )
    targets = [
        datetime.combine(day, value, tzinfo=CN_TZ)
        for value in PROBE_CLOCKS
    ]
    if current > targets[0]:
        return {
            "status": "PROBE_WINDOW_MISSED",
            "execution_ok": True,
            "data_ready": False,
            "trade_date": day.isoformat(),
            "required_sample_times": [
                item.isoformat(timespec="seconds")
                for item in targets
            ],
            "samples": [],
            "candidates": [],
            "tickets": [],
            "orders": [],
        }

    runtime_collectors = collectors or RealPointInTimeCollectors(
        codes,
        clock=runtime_clock,
    )
    samples = []
    for target in targets:
        wait_seconds = max(
            0.0,
            (target - runtime_clock()).total_seconds(),
        )
        if wait_seconds:
            runtime_sleep(wait_seconds)
        observed = runtime_clock()
        try:
            batch = runtime_collectors.collect_minute_bars(observed)
            signatures = minute_1450_signature(batch.records)
            error = ""
        except Exception as exc:
            signatures = {}
            error = f"{type(exc).__name__}: {exc}"
        samples.append(
            {
                "sampled_at": observed.isoformat(timespec="seconds"),
                "signatures": signatures,
                "error": error,
            }
        )
    result = classify_minute_label_samples(samples)
    result.update(
        {
            "execution_ok": True,
            "data_ready": False,
            "trade_date": day.isoformat(),
            "candidates": [],
            "tickets": [],
            "orders": [],
        }
    )
    return result


def _probe_result(
    semantics: str,
    conclusion: str,
    *,
    samples: list[dict[str, Any]],
    reasons: list[str],
    tracked_codes: list[str] | None = None,
) -> dict[str, Any]:
    verified = conclusion == "VERIFIED"
    contract = build_close_time_contract(
        str(samples[0]["sampled_at"])[:10]
        if samples
        else date.today(),
        minute_label_semantics=semantics,
        verified=verified,
    )
    return {
        "status": (
            "MINUTE_LABEL_VERIFIED"
            if verified
            else "MINUTE_LABEL_INCONCLUSIVE"
        ),
        "minute_label_semantics": semantics,
        "minute_label_validation_status": conclusion,
        "tracked_codes": tracked_codes or [],
        "reasons": reasons,
        "samples": samples,
        "recommended_time_contract": contract.as_dict(),
    }
