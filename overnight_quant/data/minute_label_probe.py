from __future__ import annotations

from datetime import date, datetime, time
import json
import os
from pathlib import Path
import tempfile
import time as time_module
from typing import Any, Callable

from overnight_quant.data.close_time_contract import (
    MINUTE_LABEL_END,
    MINUTE_LABEL_START,
    MINUTE_LABEL_UNVERIFIED,
    build_close_time_contract,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_probe_sources import (
    PROBE_SOURCE_EASTMONEY,
    build_minute_probe_collector,
    normalize_probe_source,
)
from overnight_quant.data.point_in_time import stable_hash


PROBE_CLOCKS = (
    time(14, 49, 55),
    time(14, 50, 5),
    time(14, 50, 30),
    time(14, 51, 5),
)
MAX_PROBE_START_LAG_SECONDS = 2.0


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
    *,
    required_codes: list[str] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    ordered = sorted(
        samples,
        key=lambda item: (
            item.get("target_at")
            or item.get("sampled_at")
            or ""
        ),
    )
    expected = [
        "14:49:55",
        "14:50:05",
        "14:50:30",
        "14:51:05",
    ]
    by_clock = {
        str(
            item.get("target_at")
            or item.get("sampled_at")
            or ""
        )[11:19]: item
        for item in ordered
    }
    sample_sources = sorted(
        {
            str(item.get("probe_source") or "").strip().lower()
            for item in ordered
            if str(item.get("probe_source") or "").strip()
        }
    )
    resolved_source = str(source or "").strip().lower()
    if not resolved_source and len(sample_sources) == 1:
        resolved_source = sample_sources[0]
    if len(sample_sources) > 1 or (
        resolved_source
        and sample_sources
        and sample_sources != [resolved_source]
    ):
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=["probe_sources_mixed_or_mismatched"],
            source=resolved_source,
        )
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
            source=resolved_source,
        )

    tracked_codes = sorted(
        {
            str(code).zfill(6)
            for code in (
                required_codes
                or ordered[0].get("requested_codes")
                or []
            )
            if str(code).strip()
        }
    )
    if not tracked_codes:
        tracked_codes = sorted(
            set().union(
                *(
                    set(
                        (
                            by_clock[clock].get("signatures")
                            or {}
                        ).keys()
                    )
                    for clock in expected
                )
            )
        )
    evidence_hash = _probe_evidence_hash(
        ordered,
        tracked_codes,
        source=resolved_source,
    )
    timing_errors = _probe_timing_errors(by_clock, expected)
    if timing_errors:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=timing_errors,
            tracked_codes=tracked_codes,
            probe_evidence_hash=evidence_hash,
            source=resolved_source,
        )
    coverage_errors = []
    for clock in expected:
        covered = {
            str(code).zfill(6)
            for code in (
                by_clock[clock].get("covered_codes") or []
            )
        }
        missing = sorted(set(tracked_codes) - covered)
        if missing:
            coverage_errors.append(
                f"probe_stock_coverage_missing:{clock}:"
                + ",".join(missing)
            )
    if coverage_errors:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=coverage_errors,
            tracked_codes=tracked_codes,
            probe_evidence_hash=evidence_hash,
            source=resolved_source,
        )
    if not tracked_codes:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=["probe_required_codes_missing"],
            probe_evidence_hash=evidence_hash,
            source=resolved_source,
        )

    first_present = []
    later_present = []
    changed_during_1450 = []
    stable_after_1450 = []
    stable_from_first = []
    for code in tracked_codes:
        signatures = [
            (by_clock[clock].get("signatures") or {}).get(code)
            for clock in expected
        ]
        first_present.append(signatures[0] is not None)
        later_present.append(
            all(item is not None for item in signatures[1:])
        )
        if all(item is not None for item in signatures[1:]):
            hashes = [
                str(item["ohlcv_hash"])
                for item in signatures[1:]
                if item is not None
            ]
            changed_during_1450.append(hashes[0] != hashes[1])
            stable_after_1450.append(hashes[1] == hashes[2])
        else:
            changed_during_1450.append(False)
            stable_after_1450.append(False)
        if all(item is not None for item in signatures):
            all_hashes = [
                str(item["ohlcv_hash"])
                for item in signatures
                if item is not None
            ]
            stable_from_first.append(len(set(all_hashes)) == 1)
        else:
            stable_from_first.append(False)

    if (
        all(not value for value in first_present)
        and all(later_present)
        and all(changed_during_1450)
        and all(stable_after_1450)
    ):
        semantics = MINUTE_LABEL_START
        conclusion = "VERIFIED"
        reasons = [
            "minute_1450_absent_before_1450_changed_inside_minute_and_stabilized_after_1451"
        ]
    elif (
        all(first_present)
        and all(later_present)
        and all(stable_from_first)
    ):
        semantics = MINUTE_LABEL_END
        conclusion = "VERIFIED"
        reasons = [
            "minute_1450_present_before_1450_and_stable_through_1451"
        ]
    else:
        semantics = MINUTE_LABEL_UNVERIFIED
        conclusion = "INCONCLUSIVE"
        reasons = [
            "minute_1450_presence_or_change_pattern_inconclusive"
        ]
    return _probe_result(
        semantics,
        conclusion,
        samples=ordered,
        reasons=reasons,
        tracked_codes=tracked_codes,
        probe_evidence_hash=evidence_hash,
        source=resolved_source,
    )


def run_scheduled_minute_label_probe(
    codes: list[str],
    *,
    trade_date: str | date | None = None,
    source: str = PROBE_SOURCE_EASTMONEY,
    collectors: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    normalized_source = normalize_probe_source(source)
    runtime_clock = clock or (lambda: datetime.now(CN_TZ))
    runtime_sleep = sleep or time_module.sleep
    runtime_monotonic = monotonic or time_module.monotonic
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
            "source": normalized_source,
            "required_sample_times": [
                item.isoformat(timespec="seconds")
                for item in targets
            ],
            "samples": [],
            "candidates": [],
            "tickets": [],
            "orders": [],
        }

    runtime_collectors = collectors or build_minute_probe_collector(
        normalized_source,
        codes,
        clock=runtime_clock,
    )
    collector_source = str(
        getattr(
            runtime_collectors,
            "probe_source",
            normalized_source,
        )
        or ""
    ).strip().lower()
    if collector_source != normalized_source:
        raise ValueError(
            "minute_probe_collector_source_mismatch:"
            f"{collector_source}:{normalized_source}"
        )
    samples = []
    try:
        for target in targets:
            wait_seconds = max(
                0.0,
                (target - runtime_clock()).total_seconds(),
            )
            if wait_seconds:
                runtime_sleep(wait_seconds)
            request_started = runtime_clock()
            started_monotonic = runtime_monotonic()
            signatures = {}
            covered_codes = []
            raw_response_hashes = []
            source_versions = []
            provider_raw_hash = ""
            try:
                batch = runtime_collectors.collect_minute_bars(
                    request_started
                )
                request_completed = runtime_clock()
                signatures = minute_1450_signature(batch.records)
                covered_codes = sorted(
                    {
                        str(
                            (row.get("payload") or {}).get("code")
                            or ""
                        ).zfill(6)
                        for row in batch.records
                        if str(row.get("data_type") or "")
                        == "minute_bar"
                        and (row.get("payload") or {}).get("code")
                    }
                )
                raw_response_hashes = sorted(
                    {
                        str(row.get("raw_hash") or "")
                        for row in batch.records
                        if row.get("raw_hash")
                    }
                )
                source_versions = sorted(
                    {
                        str(row.get("source_version") or "")
                        for row in batch.records
                        if row.get("source_version")
                    }
                )
                provider_raw_hash = str(batch.raw_hash or "")
                error = ""
            except Exception as exc:
                request_completed = runtime_clock()
                signatures = {}
                error = f"{type(exc).__name__}: {exc}"
            elapsed_ms = round(
                (runtime_monotonic() - started_monotonic) * 1000,
                3,
            )
            samples.append(
                {
                    "probe_source": normalized_source,
                    "target_at": target.isoformat(
                        timespec="seconds"
                    ),
                    "sampled_at": request_started.isoformat(
                        timespec="seconds"
                    ),
                    "request_started_at": (
                        request_started.isoformat(
                            timespec="milliseconds"
                        )
                    ),
                    "request_completed_at": (
                        request_completed.isoformat(
                            timespec="milliseconds"
                        )
                    ),
                    "request_elapsed_ms": elapsed_ms,
                    "requested_codes": list(runtime_collectors.codes),
                    "covered_codes": covered_codes,
                    "presence_by_code": {
                        code: code in signatures
                        for code in runtime_collectors.codes
                    },
                    "signatures": signatures,
                    "raw_response_hashes": raw_response_hashes,
                    "provider_raw_hash": provider_raw_hash,
                    "source_versions": source_versions,
                    "sample_trade_date": day.isoformat(),
                    "error": error,
                }
            )
    finally:
        close = getattr(runtime_collectors, "close", None)
        if callable(close):
            close()
    result = classify_minute_label_samples(
        samples,
        required_codes=list(runtime_collectors.codes),
        source=normalized_source,
    )
    all_source_versions = sorted(
        {
            version
            for sample in samples
            for version in (sample.get("source_versions") or [])
            if version
        }
    )
    result.update(
        {
            "execution_ok": True,
            "data_ready": False,
            "trade_date": day.isoformat(),
            "source": normalized_source,
            "source_versions": all_source_versions,
            "requires_manual_review": True,
            "late_record_count": 0,
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
    probe_evidence_hash: str = "",
    source: str = "",
) -> dict[str, Any]:
    verified = conclusion == "VERIFIED"
    contract = build_close_time_contract(
        str(samples[0]["sampled_at"])[:10]
        if samples
        else date.today(),
        minute_label_semantics=semantics,
        verified=verified,
        probe_evidence_hash=(
            probe_evidence_hash if verified else ""
        ),
    )
    return {
        "status": (
            "MINUTE_LABEL_VERIFIED"
            if verified
            else "MINUTE_LABEL_INCONCLUSIVE"
        ),
        "minute_label_semantics": semantics,
        "minute_label_validation_status": conclusion,
        "source": source,
        "tracked_codes": tracked_codes or [],
        "probe_evidence_hash": probe_evidence_hash,
        "reasons": reasons,
        "samples": samples,
        "recommended_time_contract": contract.as_dict(),
    }


def _probe_timing_errors(
    by_clock: dict[str, dict[str, Any]],
    expected: list[str],
) -> list[str]:
    errors = []
    for clock in expected:
        sample = by_clock[clock]
        if sample.get("error"):
            errors.append(f"probe_request_failed:{clock}")
            continue
        target = _sample_datetime(
            sample.get("target_at") or sample.get("sampled_at")
        )
        started = _sample_datetime(
            sample.get("request_started_at")
            or sample.get("sampled_at")
        )
        completed = _sample_datetime(
            sample.get("request_completed_at")
            or sample.get("sampled_at")
        )
        if target is None or started is None or completed is None:
            errors.append(f"probe_timing_missing:{clock}")
            continue
        if completed < started:
            errors.append(f"probe_completion_before_start:{clock}")
        if (
            started - target
        ).total_seconds() > MAX_PROBE_START_LAG_SECONDS:
            errors.append(f"probe_started_late:{clock}")
    return errors


def _probe_evidence_hash(
    samples: list[dict[str, Any]],
    tracked_codes: list[str],
    *,
    source: str = "",
) -> str:
    evidence = {
        "source": str(source or "").strip().lower(),
        "tracked_codes": list(tracked_codes),
        "samples": [
            {
                "probe_source": str(
                    item.get("probe_source") or source or ""
                ).strip().lower(),
                "target_at": item.get("target_at")
                or item.get("sampled_at"),
                "request_started_at": item.get(
                    "request_started_at"
                )
                or item.get("sampled_at"),
                "request_completed_at": item.get(
                    "request_completed_at"
                )
                or item.get("sampled_at"),
                "request_elapsed_ms": item.get(
                    "request_elapsed_ms"
                ),
                "covered_codes": sorted(
                    item.get("covered_codes") or []
                ),
                "presence_by_code": item.get(
                    "presence_by_code"
                )
                or {
                    code: code
                    in (item.get("signatures") or {})
                    for code in tracked_codes
                },
                "signatures": item.get("signatures") or {},
                "raw_response_hashes": sorted(
                    item.get("raw_response_hashes") or []
                ),
                "provider_raw_hash": item.get(
                    "provider_raw_hash"
                )
                or "",
                "source_versions": sorted(
                    item.get("source_versions") or []
                ),
                "sample_trade_date": item.get(
                    "sample_trade_date"
                )
                or str(
                    item.get("sampled_at") or ""
                )[:10],
                "error": item.get("error") or "",
            }
            for item in samples
        ],
    }
    return stable_hash(evidence)


def compute_probe_evidence_hash(
    samples: list[dict[str, Any]],
    tracked_codes: list[str],
    *,
    source: str = "",
) -> str:
    return _probe_evidence_hash(
        samples,
        tracked_codes,
        source=source,
    )


def write_probe_json_atomic(
    result: dict[str, Any],
    output: str | Path,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


def _sample_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)
