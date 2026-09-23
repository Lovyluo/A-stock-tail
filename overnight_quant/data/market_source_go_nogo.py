from __future__ import annotations

from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, getproxies

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_CLIST_URL,
    EASTMONEY_FUND_FLOW_URL,
    EASTMONEY_ULIST_URL,
    FIXED_CODES,
    PROVIDER_KEYS,
    SOURCE_IDENTITIES,
)
from overnight_quant.data.market_session_confirmation import (
    SESSION_CONFIRMED,
    verify_market_session_confirmation_evidence,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    get_source_capability_registry,
)
from overnight_quant.data.source_capability_adapters import (
    get_source_adapter_registry,
)
from overnight_quant.data.static_source_qualification import (
    S1_PARTIAL_QUALIFICATION_RECORD_HASH,
)


GO_NOGO_EVIDENCE_SCHEMA_V1 = "market_source_go_nogo_evidence_v1"
GO_NOGO_EVIDENCE_SCHEMA_VERSION = "market_source_go_nogo_evidence_v2"
GO_NOGO_VERIFIER_CONTRACT_V1 = "market_source_go_nogo_verifier_v1"
GO_NOGO_VERIFIER_CONTRACT_VERSION = "market_source_go_nogo_verifier_v2"
SAMPLING_GO = "SAMPLING_GO"
NO_GO = "NO_GO_FOR_QUALIFICATION_SAMPLE"
OFFICIAL_HOST = "push2.eastmoney.com"
DEFAULT_CUTOFF_CLOCK = "13:30:00"
MAX_CLOCK_SKEW_MS = 2_000
TASK_INVENTORY_TIMEOUT_SECONDS = 10.0
OFFICIAL_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)
OFFICIAL_ENDPOINTS = {
    "market_breadth": EASTMONEY_ULIST_URL
    + "?"
    + urlencode(
        sorted(
            {
                "fltt": "2",
                "invt": "2",
                "secids": "1.000001,0.399001,0.899050",
                "fields": "f3,f12,f14,f104,f105,f106,f124",
            }.items()
        )
    ),
    "industry_snapshot": EASTMONEY_CLIST_URL
    + "?"
    + urlencode(
        sorted(
            {
                "pn": "1",
                "pz": "500",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": "m:90+t:2",
                "fields": "f3,f12,f14,f104,f105,f106,f124",
            }.items()
        )
    ),
    "fund_flow": EASTMONEY_FUND_FLOW_URL
    + "?"
    + urlencode(
        sorted(
            {
                "secid": "0.000001",
                "klt": "1",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            }.items()
        )
    ),
}


def run_market_source_go_nogo(
    *,
    trade_date: str,
    codes: Iterable[str],
    cutoff_clock: str = DEFAULT_CUTOFF_CLOCK,
    calendar_contract: Mapping[str, Any],
    calendar_expected_file_sha256: str | None = None,
    calendar_actual_file_sha256: str | None = None,
    session_confirmation_contract: Mapping[str, Any] | None = None,
    session_expected_file_sha256: str | None = None,
    session_actual_file_sha256: str | None = None,
    environment: Mapping[str, Any],
    started_at: str,
    completed_at: str,
    evidence_scope: str = "production",
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    normalized_codes = tuple(str(value).strip() for value in codes)
    add(
        "fixed_codes_exact",
        normalized_codes == FIXED_CODES,
        list(normalized_codes),
    )
    now = _parse_iso(environment.get("now"))
    started = _parse_iso(started_at)
    completed = _parse_iso(completed_at)
    add(
        "evidence_time_order",
        bool(started and completed and started <= completed),
        {"started_at": started_at, "completed_at": completed_at},
    )
    cutoff = _cutoff(trade_date, cutoff_clock)
    add(
        "before_cutoff",
        bool(now and cutoff and now <= cutoff),
        {"now": environment.get("now"), "cutoff": cutoff.isoformat() if cutoff else ""},
    )
    add(
        "timezone_china_standard_time",
        environment.get("timezone_id") == "China Standard Time",
        environment.get("timezone_id"),
    )
    skew = environment.get("clock_skew_ms")
    add(
        "clock_skew_within_limit",
        type(skew) in (int, float) and abs(float(skew)) <= MAX_CLOCK_SKEW_MS,
        skew,
    )
    calendar_errors = _completed_calendar_errors(
        calendar_contract,
        trade_date,
        expected_file_sha256=calendar_expected_file_sha256,
        actual_file_sha256=calendar_actual_file_sha256,
    )
    add(
        "completed_calendar_contract",
        not calendar_errors,
        {"errors": calendar_errors, "source": calendar_contract.get("source"), "source_version": calendar_contract.get("source_version")},
    )
    confirmation = dict(session_confirmation_contract or {})
    confirmation_verification = verify_market_session_confirmation_evidence(
        confirmation,
        expected_file_sha256=session_expected_file_sha256,
        actual_file_sha256=session_actual_file_sha256,
    )
    session_errors = list(confirmation_verification.get("errors") or [])
    if confirmation_verification.get("session_confirmed") is not True:
        session_errors.append("current_session_not_confirmed")
    if confirmation.get("trade_date") != trade_date:
        session_errors.append("session_trade_date_mismatch")
    add(
        "current_session_confirmation_contract",
        not session_errors,
        {
            "errors": list(dict.fromkeys(session_errors)),
            "source": confirmation.get("origin_source"),
            "source_version": confirmation.get("source_version"),
            "status": confirmation.get("status"),
        },
    )
    identity_errors = _candidate_identity_errors()
    add(
        "candidate_identities",
        not identity_errors,
        {"errors": identity_errors, "source_versions": _source_versions()},
    )
    for name in ("dns", "tls"):
        add(f"official_{name}", environment.get(name) is True, environment.get(name))
    endpoint_reachability = dict(environment.get("endpoint_reachability") or {})
    request_hashes = dict(environment.get("request_hashes") or {})
    for capability in sorted(SOURCE_IDENTITIES):
        add(
            f"official_endpoint:{capability}",
            endpoint_reachability.get(capability) is True,
            endpoint_reachability.get(capability),
        )
        request_hash = request_hashes.get(capability)
        add(
            f"request_hash:{capability}",
            _is_sha256(request_hash),
            request_hash or "",
        )
    proxy = dict(environment.get("proxy") or {})
    proxy_ok = (
        proxy.get("in_use") is False
        or (
            proxy.get("in_use") is True
            and proxy.get("local") is True
            and proxy.get("listening") is True
        )
        or (proxy.get("in_use") is True and proxy.get("local") is False)
    )
    add("proxy_readiness", proxy_ok, proxy)
    add("output_directory_writable", environment.get("output_writable") is True, environment.get("output_writable"))
    add("same_day_result_absent", environment.get("same_day_result_exists") is False, environment.get("same_day_result_exists"))
    for name in ("residual_workers", "duplicate_tasks", "similar_collectors"):
        values = list(environment.get(name) or [])
        add(f"no_{name}", not values, values)

    failures = [row["name"] for row in checks if not row["passed"]]
    go = not failures
    if evidence_scope not in {"production", "test_only"}:
        raise ValueError("go_nogo_evidence_scope_invalid")
    result = _safe(
        {
            "evidence_schema_version": GO_NOGO_EVIDENCE_SCHEMA_VERSION,
            "verifier_contract_version": GO_NOGO_VERIFIER_CONTRACT_VERSION,
            "status": SAMPLING_GO if go else NO_GO,
            "execution_ok": True,
            "evidence_integrity_verified": False,
            "evidence_scope": evidence_scope,
            "sampling_authorized": go and evidence_scope == "production",
            "qualification_result": "NOT_EVALUATED",
            "consecutive_qualified_days": 0,
            "trade_date": trade_date,
            "started_at": started_at,
            "completed_at": completed_at,
            "cutoff_clock": cutoff_clock,
            "fixed_codes": list(normalized_codes),
            "source_versions": _source_versions(),
            "request_hashes": {key: request_hashes.get(key, "") for key in sorted(SOURCE_IDENTITIES)},
            "completed_calendar_contract": {
                "contract_hash": stable_hash(dict(calendar_contract)),
                "expected_file_sha256": calendar_expected_file_sha256 or "",
                "actual_file_sha256": calendar_actual_file_sha256 or "",
            },
            "current_session_confirmation_contract": {
                "contract_hash": stable_hash(confirmation),
                "evidence_hash": confirmation.get(
                    "market_session_confirmation_evidence_hash"
                )
                or "",
                "expected_file_sha256": session_expected_file_sha256 or "",
                "actual_file_sha256": session_actual_file_sha256 or "",
            },
            "environment_checks": checks,
            "failure_reasons": failures,
            "task_actions": [],
            "enabled_tasks": [],
            "go_nogo_evidence_hash": "",
        }
    )
    result["go_nogo_evidence_hash"] = compute_go_nogo_evidence_hash(result)
    return result


def verify_market_source_go_nogo_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_file_sha256: str | None,
    actual_file_sha256: str | None = None,
    completed_calendar_contract: Mapping[str, Any] | None = None,
    completed_calendar_expected_file_sha256: str | None = None,
    completed_calendar_actual_file_sha256: str | None = None,
    current_session_confirmation_contract: Mapping[str, Any] | None = None,
    current_session_expected_file_sha256: str | None = None,
    current_session_actual_file_sha256: str | None = None,
) -> dict[str, Any]:
    payload = dict(evidence)
    if payload.get("evidence_schema_version") == GO_NOGO_EVIDENCE_SCHEMA_V1:
        return _verify_v1_evidence(
            payload,
            expected_file_sha256=expected_file_sha256,
            actual_file_sha256=actual_file_sha256,
        )
    errors: list[str] = []
    if payload.get("evidence_schema_version") != GO_NOGO_EVIDENCE_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if payload.get("verifier_contract_version") != GO_NOGO_VERIFIER_CONTRACT_VERSION:
        errors.append("verifier_contract_version_invalid")
    if payload.get("execution_ok") is not True:
        errors.append("execution_ok_invalid")
    if payload.get("go_nogo_evidence_hash") != compute_go_nogo_evidence_hash(payload):
        errors.append("evidence_hash_mismatch")
    if not expected_file_sha256:
        errors.append("external_sha256_anchor_required")
    elif actual_file_sha256 != expected_file_sha256:
        errors.append("external_sha256_anchor_mismatch")
    if payload.get("fixed_codes") != list(FIXED_CODES):
        errors.append("fixed_codes_mismatch")
    if payload.get("source_versions") != _source_versions():
        errors.append("source_versions_mismatch")
    trade_date = str(payload.get("trade_date") or "")
    calendar = dict(completed_calendar_contract or {})
    calendar_errors = _completed_calendar_errors(
        calendar,
        trade_date,
        expected_file_sha256=completed_calendar_expected_file_sha256,
        actual_file_sha256=completed_calendar_actual_file_sha256,
    )
    calendar_reference = payload.get("completed_calendar_contract") or {}
    expected_calendar_reference = {
        "contract_hash": stable_hash(calendar),
        "expected_file_sha256": completed_calendar_expected_file_sha256 or "",
        "actual_file_sha256": completed_calendar_actual_file_sha256 or "",
    }
    if calendar_reference != expected_calendar_reference:
        errors.append("completed_calendar_contract_reference_mismatch")

    confirmation = dict(current_session_confirmation_contract or {})
    confirmation_verification = verify_market_session_confirmation_evidence(
        confirmation,
        expected_file_sha256=current_session_expected_file_sha256,
        actual_file_sha256=current_session_actual_file_sha256,
    )
    session_errors = list(confirmation_verification.get("errors") or [])
    if confirmation_verification.get("session_confirmed") is not True:
        session_errors.append("current_session_not_confirmed")
    if confirmation.get("trade_date") != trade_date:
        session_errors.append("session_trade_date_mismatch")
    session_errors = list(dict.fromkeys(session_errors))
    session_reference = payload.get("current_session_confirmation_contract") or {}
    expected_session_reference = {
        "contract_hash": stable_hash(confirmation),
        "evidence_hash": confirmation.get(
            "market_session_confirmation_evidence_hash"
        )
        or "",
        "expected_file_sha256": current_session_expected_file_sha256 or "",
        "actual_file_sha256": current_session_actual_file_sha256 or "",
    }
    if session_reference != expected_session_reference:
        errors.append("current_session_contract_reference_mismatch")
    evidence_scope = payload.get("evidence_scope")
    if evidence_scope not in {"production", "test_only"}:
        errors.append("evidence_scope_invalid")
    checks = list(payload.get("environment_checks") or [])
    names = [row.get("name") for row in checks if isinstance(row, Mapping)]
    if len(names) != len(set(names)) or not names:
        errors.append("environment_checks_invalid")
    _validate_contract_check(
        checks,
        "completed_calendar_contract",
        calendar_errors,
        errors,
    )
    _validate_contract_check(
        checks,
        "current_session_confirmation_contract",
        session_errors,
        errors,
    )
    failed_names = [row.get("name") for row in checks if row.get("passed") is not True]
    if payload.get("failure_reasons") != failed_names:
        errors.append("failure_reasons_mismatch")
    expected_status = SAMPLING_GO if not failed_names else NO_GO
    if payload.get("status") != expected_status:
        errors.append("status_mismatch")
    expected_authorized = (
        expected_status == SAMPLING_GO and evidence_scope == "production"
    )
    if payload.get("sampling_authorized") is not expected_authorized:
        errors.append("sampling_authorized_mismatch")
    _append_go_nogo_safety_errors(payload, errors)
    verified = not errors
    return _safe(
        {
            "status": "MARKET_SOURCE_GO_NOGO_EVIDENCE_VERIFIED" if verified else "MARKET_SOURCE_GO_NOGO_EVIDENCE_INVALID",
            "execution_ok": True,
            "evidence_integrity_verified": verified,
            "sampling_authorized": bool(payload.get("sampling_authorized")) if verified else False,
            "audit_only": False,
            "qualification_result": "NOT_EVALUATED",
            "consecutive_qualified_days": 0,
            "expected_file_sha256": expected_file_sha256 or "",
            "actual_file_sha256": actual_file_sha256 or "",
            "go_nogo_evidence_hash": payload.get("go_nogo_evidence_hash") or "",
            "errors": errors,
        }
    )


def _verify_v1_evidence(
    payload: Mapping[str, Any],
    *,
    expected_file_sha256: str | None,
    actual_file_sha256: str | None,
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("verifier_contract_version") != GO_NOGO_VERIFIER_CONTRACT_V1:
        errors.append("verifier_contract_version_invalid")
    if payload.get("go_nogo_evidence_hash") != compute_go_nogo_evidence_hash(payload):
        errors.append("evidence_hash_mismatch")
    if not expected_file_sha256:
        errors.append("external_sha256_anchor_required")
    elif actual_file_sha256 != expected_file_sha256:
        errors.append("external_sha256_anchor_mismatch")
    if payload.get("fixed_codes") != list(FIXED_CODES):
        errors.append("fixed_codes_mismatch")
    if payload.get("source_versions") != _source_versions():
        errors.append("source_versions_mismatch")
    evidence_scope = payload.get("evidence_scope")
    if evidence_scope not in {"production", "test_only"}:
        errors.append("evidence_scope_invalid")
    checks = list(payload.get("environment_checks") or [])
    names = [row.get("name") for row in checks if isinstance(row, Mapping)]
    if len(names) != len(set(names)) or not names:
        errors.append("environment_checks_invalid")
    failed_names = [row.get("name") for row in checks if row.get("passed") is not True]
    if payload.get("failure_reasons") != failed_names:
        errors.append("failure_reasons_mismatch")
    expected_status = SAMPLING_GO if not failed_names else NO_GO
    if payload.get("status") != expected_status:
        errors.append("status_mismatch")
    claimed_authorized = expected_status == SAMPLING_GO and evidence_scope == "production"
    if payload.get("sampling_authorized") is not claimed_authorized:
        errors.append("sampling_authorized_mismatch")
    _append_go_nogo_safety_errors(payload, errors)
    verified = not errors
    return _safe(
        {
            "status": (
                "MARKET_SOURCE_GO_NOGO_V1_EVIDENCE_VERIFIED_AUDIT_ONLY"
                if verified
                else "MARKET_SOURCE_GO_NOGO_EVIDENCE_INVALID"
            ),
            "execution_ok": True,
            "evidence_integrity_verified": verified,
            "sampling_authorized": False,
            "audit_only": True,
            "qualification_result": "NOT_EVALUATED",
            "consecutive_qualified_days": 0,
            "expected_file_sha256": expected_file_sha256 or "",
            "actual_file_sha256": actual_file_sha256 or "",
            "go_nogo_evidence_hash": payload.get("go_nogo_evidence_hash") or "",
            "errors": errors,
        }
    )


def _validate_contract_check(
    checks: list[Any],
    name: str,
    expected_errors: list[str],
    errors: list[str],
) -> None:
    rows = [
        row
        for row in checks
        if isinstance(row, Mapping) and row.get("name") == name
    ]
    if len(rows) != 1:
        errors.append(f"contract_check_missing:{name}")
        return
    row = rows[0]
    if row.get("passed") is not (not expected_errors):
        errors.append(f"contract_check_result_mismatch:{name}")
    detail = row.get("detail") or {}
    if not isinstance(detail, Mapping) or list(detail.get("errors") or []) != expected_errors:
        errors.append(f"contract_check_errors_mismatch:{name}")


def _append_go_nogo_safety_errors(
    payload: Mapping[str, Any], errors: list[str]
) -> None:
    for key in (
        "data_ready",
        "hard_gate_authorized",
        "automatic_configuration_change",
        "automatic_qualification_change",
    ):
        if payload.get(key) is not False:
            errors.append(f"safety_flag_invalid:{key}")
    for key in ("candidates", "tickets", "orders", "task_actions", "enabled_tasks"):
        if payload.get(key) != []:
            errors.append(f"safety_output_invalid:{key}")
    if payload.get("consecutive_qualified_days") != 0:
        errors.append("qualification_count_changed")
    if payload.get("qualification_result") != "NOT_EVALUATED":
        errors.append("qualification_result_invalid")


def compute_go_nogo_evidence_hash(evidence: Mapping[str, Any]) -> str:
    material = dict(evidence)
    material.pop("go_nogo_evidence_hash", None)
    return stable_hash(material)


def write_go_nogo_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collect_live_environment(*, output_path: str | Path, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(CN_TZ)
    proxies = getproxies()
    proxy_url = proxies.get("https") or proxies.get("http") or ""
    proxy = _proxy_status(proxy_url)
    endpoint_reachability: dict[str, bool] = {}
    request_hashes: dict[str, str] = {}
    date_samples: list[datetime] = []
    dns_ok = tls_ok = False
    try:
        dns_ok = bool(socket.getaddrinfo(OFFICIAL_HOST, 443, type=socket.SOCK_STREAM))
        context = ssl.create_default_context()
        with socket.create_connection((OFFICIAL_HOST, 443), timeout=2.0) as sock:
            with context.wrap_socket(sock, server_hostname=OFFICIAL_HOST):
                tls_ok = True
    except OSError:
        pass
    opener = build_opener()
    for capability, url in sorted(OFFICIAL_ENDPOINTS.items()):
        request_hashes[capability] = stable_hash({"method": "GET", "url": url})
        try:
            request = Request(
                url,
                headers={"User-Agent": OFFICIAL_USER_AGENT},
                method="GET",
            )
            before = datetime.now(timezone.utc)
            with opener.open(request, timeout=2.0) as response:
                response.read(1)
                final = urlparse(response.geturl())
                endpoint_reachability[capability] = int(response.status) == 200 and final.scheme == "https" and final.hostname == OFFICIAL_HOST
                if endpoint_reachability[capability]:
                    tls_ok = True
                header = response.headers.get("Date")
                if header:
                    from email.utils import parsedate_to_datetime
                    date_samples.append(parsedate_to_datetime(header).astimezone(timezone.utc))
            after = datetime.now(timezone.utc)
        except Exception:
            endpoint_reachability[capability] = False
            continue
    if date_samples:
        local = datetime.now(timezone.utc)
        clock_skew_ms = min(abs((local - value).total_seconds() * 1000) for value in date_samples)
    else:
        clock_skew_ms = None
    output = Path(output_path)
    writable = output.parent.exists() and os.access(output.parent, os.W_OK)
    return {
        "now": current.isoformat(timespec="milliseconds"),
        "timezone_id": _windows_timezone_id(),
        "clock_skew_ms": clock_skew_ms,
        "dns": dns_ok,
        "tls": tls_ok,
        "proxy": proxy,
        "endpoint_reachability": endpoint_reachability,
        "request_hashes": request_hashes,
        "output_writable": writable,
        "same_day_result_exists": output.exists(),
        "residual_workers": _matching_processes(),
        "duplicate_tasks": _matching_tasks(),
        "similar_collectors": [],
    }


def _completed_calendar_errors(
    contract: Mapping[str, Any],
    trade_date: str,
    *,
    expected_file_sha256: str | None,
    actual_file_sha256: str | None,
) -> list[str]:
    errors: list[str] = []
    if contract.get("source") != "tencent":
        errors.append("calendar_source_invalid")
    if contract.get("source_version") != "ifzq_fqkline_day_v2026-07-30":
        errors.append("calendar_source_version_invalid")
    if (
        contract.get("qualification_record_hash")
        != S1_PARTIAL_QUALIFICATION_RECORD_HASH
    ):
        errors.append("calendar_qualification_record_invalid")
    if contract.get("qualification_status") != "qualified":
        errors.append("calendar_qualification_status_invalid")
    if not _is_sha256(contract.get("raw_hash")):
        errors.append("calendar_raw_hash_invalid")
    if not _is_sha256(expected_file_sha256):
        errors.append("calendar_external_sha256_anchor_required")
    elif actual_file_sha256 != expected_file_sha256:
        errors.append("calendar_external_sha256_anchor_mismatch")

    target = _parse_date(trade_date)
    values = contract.get("trade_dates")
    if not isinstance(values, list) or not values:
        errors.append("calendar_trade_dates_invalid")
        return list(dict.fromkeys(errors))
    parsed_dates: list[date] = []
    for value in values:
        parsed = _parse_date(value)
        if parsed is None or parsed.isoformat() != value:
            errors.append("calendar_trade_date_invalid")
            continue
        parsed_dates.append(parsed)
    if len(parsed_dates) != len(set(parsed_dates)):
        errors.append("calendar_trade_dates_duplicate")
    if parsed_dates != sorted(parsed_dates):
        errors.append("calendar_trade_dates_not_sorted")
    if any(value.weekday() >= 5 for value in parsed_dates):
        errors.append("calendar_non_trading_weekend_present")
    if len(parsed_dates) < 60:
        errors.append("calendar_trade_dates_below_60")
    if target is None:
        errors.append("target_trade_date_invalid")
    elif any(value >= target for value in parsed_dates):
        errors.append("calendar_contains_target_or_future_date")
    latest = parsed_dates[-1].isoformat() if parsed_dates else ""
    if contract.get("latest_completed_trade_date") != latest:
        errors.append("calendar_latest_completed_trade_date_invalid")
    return list(dict.fromkeys(errors))


def _candidate_identity_errors() -> list[str]:
    rows = get_source_capability_registry()
    index = {(row["capability"], row["origin_source"], row["adapter"], row["source_version"]): row for row in rows}
    adapters = get_source_adapter_registry()
    adapter_index = {
        (
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        ): row
        for row in adapters
    }
    errors = []
    for capability, identity in sorted(SOURCE_IDENTITIES.items()):
        key = (capability, *identity)
        row = index.get(key)
        if not row:
            errors.append(f"candidate_missing:{capability}")
        elif row.get("qualification_status") != "unqualified":
            errors.append(f"qualification_status_invalid:{capability}")
        adapter_row = adapter_index.get(key)
        if not adapter_row:
            errors.append(f"adapter_candidate_missing:{capability}")
        elif (
            adapter_row.get("implementation_status")
            != "candidate_not_activated"
            or adapter_row.get("candidate_provider_key")
            != PROVIDER_KEYS[capability]
            or adapter_row.get("provider_key")
        ):
            errors.append(f"adapter_candidate_identity_invalid:{capability}")
    return errors


def _source_versions() -> dict[str, str]:
    return {capability: identity[2] for capability, identity in sorted(SOURCE_IDENTITIES.items())}


def _cutoff(trade_date: str, cutoff_clock: str) -> datetime | None:
    try:
        return datetime.combine(datetime.fromisoformat(trade_date).date(), time.fromisoformat(cutoff_clock), tzinfo=CN_TZ)
    except ValueError:
        return None


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(CN_TZ)


def _parse_date(value: Any) -> date | None:
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _proxy_status(proxy_url: str) -> dict[str, Any]:
    if not proxy_url:
        return {"in_use": False, "local": False, "listening": None, "endpoint": ""}
    parsed = urlparse(proxy_url)
    local = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
    listening = None
    if local:
        try:
            with socket.create_connection((parsed.hostname or "", int(parsed.port or 80)), timeout=0.25):
                listening = True
        except OSError:
            listening = False
    return {"in_use": True, "local": local, "listening": listening, "endpoint": f"{parsed.hostname}:{parsed.port or ''}"}


def _windows_timezone_id() -> str:
    if os.name != "nt":
        return "China Standard Time" if datetime.now().astimezone().utcoffset().total_seconds() == 28800 else str(datetime.now().astimezone().tzinfo)
    try:
        return subprocess.check_output(["tzutil", "/g"], text=True, timeout=2).strip()
    except Exception:
        return ""


def _matching_processes() -> list[str]:
    if os.name != "nt":
        return []
    script = "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'market_source_validation|market_source_worker' } | Select-Object -ExpandProperty ProcessId"
    try:
        raw = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", script], text=True, timeout=3)
        return sorted(line.strip() for line in raw.splitlines() if line.strip() and line.strip() != str(os.getpid()))
    except Exception:
        return ["process_inventory_failed"]


def _matching_tasks() -> list[str]:
    if os.name != "nt":
        return []
    script = "Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like 'AStockMarketSource*' } | Select-Object -ExpandProperty TaskName"
    try:
        raw = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command", script],
            text=True,
            timeout=TASK_INVENTORY_TIMEOUT_SECONDS,
        )
        return sorted(line.strip() for line in raw.splitlines() if line.strip())
    except Exception:
        return ["task_inventory_failed"]


def _safe(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "automatic_configuration_change": False,
        "automatic_qualification_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


__all__ = [
    "GO_NOGO_EVIDENCE_SCHEMA_V1",
    "GO_NOGO_EVIDENCE_SCHEMA_VERSION",
    "GO_NOGO_VERIFIER_CONTRACT_V1",
    "GO_NOGO_VERIFIER_CONTRACT_VERSION",
    "NO_GO",
    "SAMPLING_GO",
    "collect_live_environment",
    "compute_go_nogo_evidence_hash",
    "file_sha256",
    "run_market_source_go_nogo",
    "verify_market_source_go_nogo_evidence",
    "write_go_nogo_json_atomic",
]
