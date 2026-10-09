"""Version-isolated records-first contracts; no transport or live activation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import math
import re
from typing import Any

from overnight_quant.data.point_in_time import stable_hash


RECORD_SCHEMA = "s2_pit_integration_record_v1"
ASSOCIATION_SCHEMA = "eastmoney_security_board_association_v1"
CLASSIFICATION = "eastmoney_coreconception_industry"
CLASSIFICATION_VERSION = "eastmoney_coreconception+board_constituent_breadth_v1"
INDUSTRY_BLOCKER = "INDUSTRY_STRENGTH_NATIVE_SCHEMA_UNPROVEN"
FUND_BLOCKER = "FUND_FLOW_SEMANTICS_UNPROVEN"
FIXED_CODES = ("000001", "000333", "600000", "600519", "601318")
TIME_FIELDS = ("feature_cutoff", "collection_deadline", "decision_time", "execution_start")


def security_identity(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("security_identity_invalid")
    text = value.upper().strip()
    match = re.fullmatch(r"(?:(SH|SZ|BJ))?(\d{6})(?:\.(SH|SZ|BJ))?", text)
    if not match:
        raise ValueError("security_identity_invalid")
    prefix, code, suffix = match.groups()
    market = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ" if code.startswith(("4", "8", "9")) else ""
    if not market or (prefix and prefix != market) or (suffix and suffix != market):
        raise ValueError("security_identity_market_mismatch")
    return f"{market}:{code}"


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("time_invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(hours=8):
        raise ValueError("timezone_invalid")
    return parsed


def _hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def validate_record(row: dict[str, Any], *, expected_times: dict | None = None) -> list[str]:
    """Never repairs metadata. Old records are handled only by their old contract."""
    if row.get("record_schema_version") != RECORD_SCHEMA:
        return ["integration_record_schema_invalid"]
    errors: list[str] = []
    try:
        declared = row["temporal_contract"]
        if not isinstance(declared, dict) or set(declared) != set(TIME_FIELDS):
            raise ValueError("temporal_contract_shape_invalid")
        clocks = {key: _time(declared[key]) for key in TIME_FIELDS}
        ordered = list(clocks.values())
        if not ordered[0] < ordered[1] <= ordered[2] <= ordered[3]:
            errors.append("integration_time_contract_order_invalid")
        if len({value.date() for value in ordered}) != 1:
            errors.append("integration_time_contract_date_mismatch")
        if expected_times is not None and declared != expected_times:
            errors.append("integration_time_contract_mismatch")
        if _time(row["decision_cutoff"]) != clocks["decision_time"]:
            errors.append("decision_cutoff_must_authorize_decision_time")
        event, observed, available = (_time(row[key]) for key in ("event_time", "observed_at", "available_at"))
        if not event <= observed <= available <= clocks["collection_deadline"]:
            errors.append("integration_record_time_order_invalid")
        if observed.date() != clocks["feature_cutoff"].date() or available.date() != observed.date():
            errors.append("integration_collection_date_mismatch")
        historical = row.get("data_type") in {"daily_bar", "trading_calendar", "news", "announcement"}
        if not historical and event.date() != observed.date():
            errors.append("integration_event_date_mismatch")
        if event > clocks["feature_cutoff"]:
            errors.append("integration_future_event")
        if "published_at" not in row:
            errors.append("published_at_key_missing")
        elif row["published_at"] is None:
            if row.get("data_type") in {"news", "announcement"}:
                errors.append("published_at_required")
        else:
            published = _time(row["published_at"])
            if published > clocks["feature_cutoff"] or published > observed:
                errors.append("published_at_time_invalid")
        children = row.get("child_provenance", [])
        if not isinstance(children, list):
            raise ValueError("child_provenance_invalid")
        for child in children:
            start, completion, child_available = (_time(child[key]) for key in ("request_started_at", "request_completed_at", "available_at"))
            if not observed <= start <= completion <= child_available <= available:
                errors.append("child_request_time_invalid")
            if start.date() != observed.date() or child_available.date() != observed.date():
                errors.append("child_request_date_mismatch")
            if (completion - start).total_seconds() * 1000 > 2000:
                errors.append("child_request_deadline_exceeded")
            if child.get("time_semantics") not in {"static_classification_audit_only", "native_feature_event"}:
                errors.append("child_time_semantics_invalid")
            # Every native timestamp is inspected; a maximum cannot hide old/future rows.
            events = child["native_event_times"]
            if not isinstance(events, list) or not events:
                errors.append("child_native_events_missing")
            else:
                for native in events:
                    instant = _time(native)
                    if instant > completion:
                        errors.append("child_future_observation")
                    if child.get("time_semantics") == "native_feature_event" and (
                        instant.date() != observed.date() or instant > clocks["feature_cutoff"]
                    ):
                        errors.append("child_native_event_invalid")
            if not all(_hash(child.get(key)) for key in ("request_hash", "raw_hash")):
                errors.append("child_hash_invalid")
    except (KeyError, TypeError, ValueError, OverflowError):
        errors.append("integration_temporal_fields_invalid")
    if type(row.get("test_only")) is not bool:
        errors.append("test_only_flag_invalid")
    if not all(_hash(row.get(key)) for key in ("request_hash", "raw_hash")):
        errors.append("integration_hash_invalid")
    payload = row.get("payload") or {}
    if not isinstance(payload, dict):
        return [*errors, "integration_payload_invalid"]
    if row.get("data_type") in {"quote", "industry", "minute_bar", "daily_bar", "fund_flow"}:
        try:
            identity = security_identity(payload.get("code"))
            if identity.split(":")[1] not in FIXED_CODES:
                errors.append("integration_stock_scope_invalid")
        except ValueError:
            errors.append("integration_security_identity_invalid")
    if row.get("data_type") == "industry":
        errors.extend(association_errors(row))
        if not _number(payload.get("change_pct")):
            errors.append("industry_strength_missing")
        units = payload.get("field_units")
        if not isinstance(units, dict) or units.get("change_pct") != "percent":
            errors.append("industry_strength_unit_invalid")
    if row.get("data_type") == "fund_flow":
        # No source evidence has established fflow cumulative/incremental semantics yet.
        if row.get("test_only") is not True:
            errors.append(FUND_BLOCKER)
        if payload.get("aggregation_semantics") != "incremental":
            errors.append("fund_flow_not_verified_incremental")
        if payload.get("semantics_evidence_scope") != "synthetic_test_only":
            errors.append(FUND_BLOCKER)
        if payload.get("amount_unit") != "CNY":
            errors.append("fund_flow_unit_invalid")
        if not all(_number(payload.get(key)) for key in ("main_net", "large_net", "super_net")):
            errors.append("fund_flow_value_invalid")
    if row.get("data_type") == "industry" and row.get("test_only") is not True:
        errors.append(INDUSTRY_BLOCKER)
    return list(dict.fromkeys(errors))


def association_errors(row: dict) -> list[str]:
    payload = row.get("payload") or {}
    mapping = payload.get("association_contract")
    if not isinstance(mapping, dict):
        return ["industry_mapping_missing"]
    try:
        identity = security_identity(str(payload.get("code") or ""))
    except ValueError:
        return ["industry_mapping_security_invalid"]
    checks = {
        "industry_mapping_version_invalid": mapping.get("schema_version") == ASSOCIATION_SCHEMA,
        "industry_mapping_security_mismatch": mapping.get("security_identity") == identity,
        "industry_mapping_classification_mismatch": mapping.get("classification_system") == CLASSIFICATION and mapping.get("classification_version") == CLASSIFICATION_VERSION and payload.get("classification_version") == CLASSIFICATION_VERSION,
        "industry_mapping_source_mismatch": mapping.get("origin_source") == row.get("origin_source") == "eastmoney" and mapping.get("source_version") == row.get("source_version"),
        "industry_mapping_board_mismatch": isinstance(mapping.get("board_code"), str) and re.fullmatch(r"BK\d{4}", mapping["board_code"]) is not None and mapping["board_code"] == payload.get("board_code"),
        "industry_mapping_name_mismatch": isinstance(mapping.get("board_name"), str) and bool(mapping["board_name"].strip()) and mapping["board_name"] == (payload.get("name") or payload.get("industry")),
        "industry_mapping_evidence_invalid": _hash(mapping.get("request_hash")) and _hash(mapping.get("raw_hash")),
    }
    return [reason for reason, valid in checks.items() if not valid]


def apply_industry_associations(stocks: dict[str, dict], industry_rows: list[dict]) -> None:
    """Join derived stock state, not raw quote records, by security and BK identity."""
    modern = [row for row in industry_rows if row.get("record_schema_version") == RECORD_SCHEMA]
    for code, stock in stocks.items():
        if stock.get("record_schema_version") != RECORD_SCHEMA and not modern:
            continue
        candidates = [row for row in modern if security_identity((row.get("payload") or {}).get("code")).split(":")[1] == code]
        errors: list[str] = []
        if not candidates:
            errors.append("industry_mapping_missing")
        if len({stable_hash(row) for row in candidates}) > 1:
            errors.append("industry_mapping_conflict")
        if stock.get("record_schema_version") != RECORD_SCHEMA:
            errors.append("industry_classification_contract_mixed")
        if any(row.get("record_schema_version") != RECORD_SCHEMA for row in industry_rows):
            errors.append("industry_classification_contract_mixed")
        for row in candidates:
            errors.extend(association_errors(row))
            name = (row.get("payload") or {}).get("name") or (row.get("payload") or {}).get("industry")
            if stock.get("industry_name") and stock["industry_name"] != name:
                errors.append("industry_mapping_display_name_conflict")
        stock["industry_association_errors"] = list(dict.fromkeys(errors))
        stock["industry"] = {}
        if not errors:
            row = candidates[0]
            payload = deepcopy(row["payload"])
            stock["industry_association"] = deepcopy(payload["association_contract"])
            stock["industry"] = {**payload, **{key: deepcopy(value) for key, value in row.items() if key != "payload"}}


def validate_fund_window(rows: list[dict], target: str) -> list[str]:
    """Exactly five minute intervals, never five cumulative snapshots or a stale row."""
    errors: list[str] = []
    instant = _time(target)
    expected = [instant - timedelta(minutes=index) for index in range(4, -1, -1)]
    try:
        events = sorted(_time(row["event_time"]) for row in rows)
        if not events or events[-1] != instant:
            errors.append("FUND_FLOW_TARGET_ROW_NOT_PUBLISHED")
        if events != expected:
            errors.append("fund_flow_five_interval_window_invalid")
        for row in rows:
            errors.extend(validate_record(row))
    except (KeyError, TypeError, ValueError):
        errors.append("fund_flow_window_time_invalid")
    return list(dict.fromkeys(errors))


def _difference_cumulative_for_test(rows: list[dict], *, target: str) -> list[dict]:
    """Synthetic algorithm demonstration only; not an unproved live conversion."""
    ordered = sorted(deepcopy(rows), key=lambda row: row["event_time"])
    end = _time(target)
    if len(ordered) != 6 or [_time(row["event_time"]) for row in ordered] != [end - timedelta(minutes=index) for index in range(5, -1, -1)]:
        raise ValueError("cumulative_baseline_or_day_boundary_invalid")
    if any(row.get("test_only") is not True or _time(row["event_time"]).date() != end.date() for row in ordered):
        raise ValueError("cumulative_test_scope_invalid")
    if len({security_identity(row["payload"]["code"]) for row in ordered}) != 1:
        raise ValueError("cumulative_security_identity_mixed")
    result = []
    for previous, current in zip(ordered, ordered[1:]):
        if any((row.get("payload") or {}).get("aggregation_semantics") != "cumulative" or (row.get("payload") or {}).get("amount_unit") != "CNY" for row in (previous, current)):
            raise ValueError("cumulative_input_semantics_invalid")
        value = deepcopy(current)
        for field in ("main_net", "large_net", "super_net"):
            before, after = previous["payload"][field], current["payload"][field]
            if not _number(before) or not _number(after):
                raise ValueError("cumulative_value_invalid")
            value["payload"][field] = after - before
        value["payload"]["aggregation_semantics"] = "incremental"
        value["payload"]["semantics_evidence_scope"] = "synthetic_test_only"
        value["synthetic_derivation"] = {"baseline_record_hash": stable_hash(previous), "current_record_hash": stable_hash(current)}
        result.append(value)
    return result


def phase_budget(*, started_at: str, cutoff: str, deadline: str, publication_delay_ms: int = 0, workers: int = 1, industry_pages: int = 7) -> dict:
    """Virtual-clock bounds only. Does not authorize concurrency or live scheduling."""
    start, feature, end = map(_time, (started_at, cutoff, deadline))
    if workers not in (1, 4) or type(publication_delay_ms) is not int or publication_delay_ms < 0 or type(industry_pages) is not int or industry_pages < 4:
        raise ValueError("phase_budget_parameter_invalid")
    if start >= feature or feature >= end or len({time.date() for time in (start, feature, end)}) != 1:
        raise ValueError("phase_budget_time_invalid")
    pre_requests = 2 + 5 + industry_pages + 4
    # 2000ms includes process startup; 100ms orchestration per wave, 500ms freeze.
    pre_stage_groups = (2, 5, industry_pages, 4)
    pre_ms = sum(math.ceil(count / workers) for count in pre_stage_groups) * 2100
    fund_ms = math.ceil(5 / workers) * 2100
    pre_end = start + timedelta(milliseconds=pre_ms)
    fund_start = max(pre_end, feature + timedelta(milliseconds=publication_delay_ms))
    fund_end = fund_start + timedelta(milliseconds=fund_ms)
    frozen = fund_end + timedelta(milliseconds=500)
    return {
        "test_only": True, "scheduling_authorized": False, "virtual_clock": True,
        "request_count": pre_requests + 5, "industry_pages": industry_pages,
        "pre_stage_dependency_groups": list(pre_stage_groups),
        "workers": workers, "per_request_hard_deadline_ms": 2000,
        "startup_included_in_request_deadline": True, "orchestration_per_wave_ms": 100,
        "processing_margin_ms": 500, "pre_stage_worst_ms": pre_ms,
        "fund_stage_worst_ms": fund_ms, "pre_stage_completed_at": pre_end.isoformat(),
        "fund_stage_started_at": fund_start.isoformat(), "fund_stage_completed_at": fund_end.isoformat(),
        "freeze_completed_at": frozen.isoformat(), "worst_elapsed_ms": (frozen - start).total_seconds() * 1000,
        "feasible_under_assumptions": pre_end <= feature and frozen <= end,
        "remaining_margin_ms": (end - frozen).total_seconds() * 1000,
        "production_budget_status": "PM_SCHEDULING_PARAMETERS_REQUIRED",
    }


def simulate_phased_execution(*, started_at: str, cutoff: str, deadline: str,
                              durations_ms: list[int], target_available_at: str | None,
                              workers: int = 1, industry_pages: int = 7) -> dict:
    """Advance a virtual clock through fixed request waves and the publication wait."""
    budget = phase_budget(started_at=started_at, cutoff=cutoff, deadline=deadline,
                          workers=workers, industry_pages=industry_pages)
    if len(durations_ms) != budget["request_count"]:
        raise ValueError("request_plan_changed")
    cursor, feature, end = map(_time, (started_at, cutoff, deadline))
    pre_count = len(durations_ms) - 5
    phases = []
    error = ""

    def stage(name: str, values: list[int], limit: datetime, groups: list[int] | None = None) -> None:
        nonlocal cursor, error
        started = cursor
        executed = 0
        waves = []
        position = 0
        for count in groups or [len(values)]:
            group = values[position:position + count]
            waves.extend(group[index:index + workers] for index in range(0, count, workers))
            position += count
        for wave in waves:
            if cursor >= limit:
                error = "PHASE_BUDGET_EXHAUSTED"
                break
            if any(type(value) is not int or value < 0 for value in wave):
                error = "REQUEST_DURATION_INVALID"
                break
            executed += len(wave)
            remaining = (limit - cursor).total_seconds() * 1000
            elapsed = max(wave)
            cursor += timedelta(milliseconds=min(elapsed, 2000, remaining))
            if elapsed > 2000 or elapsed > remaining:
                error = "REQUEST_HARD_DEADLINE_EXCEEDED"
                break
            cursor += timedelta(milliseconds=100)
            if cursor > limit:
                error = "PHASE_BUDGET_EXHAUSTED"
                break
        phases.append({"phase": name, "started_at": started.isoformat(),
                       "completed_at": cursor.isoformat(), "request_count": executed})

    stage("market_and_industry", durations_ms[:pre_count], feature, [2, 5, industry_pages, 4])
    if not error:
        waiting_at = cursor
        published = _time(target_available_at) if target_available_at else None
        if published is None or published < feature or published >= end or published.date() != feature.date():
            error = "FUND_FLOW_TARGET_ROW_NOT_PUBLISHED"
        else:
            cursor = max(cursor, published)
            phases.append({"phase": "wait_for_exact_fund_target", "started_at": waiting_at.isoformat(),
                           "completed_at": cursor.isoformat(), "request_count": 0})
            stage("five_stock_fund_flow", durations_ms[-5:], end)
    if not error:
        freeze_started = cursor
        cursor += timedelta(milliseconds=500)
        if cursor > end:
            error = "FREEZE_DEADLINE_EXCEEDED"
        phases.append({"phase": "validate_and_freeze", "started_at": freeze_started.isoformat(),
                       "completed_at": cursor.isoformat(), "request_count": 0})
    return {
        "test_only": True, "virtual_clock": True, "scheduling_authorized": False,
        "status": "VIRTUAL_PHASES_COMPLETE" if not error else "VIRTUAL_PHASES_REJECTED",
        "error_code": error, "phases": phases, "completed_at": cursor.isoformat(),
        "network_requests_made": 0, "data_ready": False, "hard_gate_authorized": False,
        "candidates": [], "tickets": [], "orders": [],
    }
