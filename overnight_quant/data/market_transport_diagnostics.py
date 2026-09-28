from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode, urlparse
import urllib.request

import requests

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_CLIST_URL,
    EASTMONEY_FUND_FLOW_URL,
    EASTMONEY_STOCK_URL,
    EASTMONEY_ULIST_URL,
    FIXED_CODES,
    FUND_FLOW_PREFLIGHT_PARAMS,
    INDUSTRY_BOARD_PARAMS,
    MARKET_BREADTH_PARAMS,
    MARKET_INDEX_PARAMS,
    MAX_RESPONSE_BYTES,
    SOURCE_IDENTITIES,
)
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash


DIAGNOSTIC_SCHEMA_VERSION = "eastmoney_transport_diagnostic_v1"
DIAGNOSTIC_VERIFIER_VERSION = "eastmoney_transport_diagnostic_verifier_v1"
TRANSPORT_REQUESTS_CURRENT = "python_requests_current_v1"
TRANSPORT_REQUESTS_FIXED_HEADERS = "python_requests_fixed_headers_v1"
TRANSPORT_DOTNET_HTTPCLIENT = "dotnet_httpclient_diagnostic_v1"
TRANSPORT_IDS = (
    TRANSPORT_REQUESTS_CURRENT,
    TRANSPORT_REQUESTS_FIXED_HEADERS,
    TRANSPORT_DOTNET_HTTPCLIENT,
)
REQUEST_DEADLINE_MS = 2_000
RECOMMENDATION_MAX_ELAPSED_MS = 2_000
APPROVED_HOST = "push2.eastmoney.com"
SESSION_WINDOW_START = "14:40:00"
SESSION_WINDOW_END = "14:48:59.999999"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)
_FIXED_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": _UA,
}
_CURRENT_HEADERS = {"User-Agent": _UA}
_HEADER_WHITELIST = ("Accept", "Accept-Language", "Referer", "User-Agent")


@dataclass(frozen=True)
class DiagnosticRequest:
    endpoint_id: str
    capability: str
    url: str
    params: tuple[tuple[str, str], ...]
    code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "capability": self.capability,
            "url": self.url,
            "params": [list(item) for item in self.params],
            "code": self.code,
        }


def diagnostic_requests(codes: Iterable[str] = FIXED_CODES) -> tuple[DiagnosticRequest, ...]:
    normalized = tuple(sorted(str(code).zfill(6) for code in codes))
    if normalized != FIXED_CODES:
        raise ValueError("market_transport_fixed_codes_required")
    requests_: list[DiagnosticRequest] = [
        _request("market_breadth_counts", "market_breadth", EASTMONEY_ULIST_URL, MARKET_BREADTH_PARAMS),
        _request("market_benchmark_f170", "market_breadth", EASTMONEY_STOCK_URL, MARKET_INDEX_PARAMS),
        _request("industry_boards", "industry_snapshot", EASTMONEY_CLIST_URL, INDUSTRY_BOARD_PARAMS),
    ]
    for code in normalized:
        requests_.append(_request(
            f"industry_mapping:{code}",
            "industry_snapshot",
            EASTMONEY_STOCK_URL,
            {
                "secid": _secid(code),
                "fltt": "2",
                "invt": "2",
                "fields": "f57,f58,f127,f86",
            },
            code,
        ))
    for code in normalized:
        requests_.append(_request(
            f"fund_flow:{code}",
            "fund_flow",
            EASTMONEY_FUND_FLOW_URL,
            {
                **FUND_FLOW_PREFLIGHT_PARAMS,
                "secid": _secid(code),
            },
            code,
        ))
    return tuple(requests_)


def run_transport_diagnostic(
    *,
    transport_id: str,
    trade_date: str,
    round_id: str,
    network: bool,
    request_callable: Callable[..., Any] | None = None,
    dotnet_runner: Callable[[list[dict[str, Any]], int], Any] | None = None,
    environment_inspector: Callable[[], dict[str, Any]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    if not network:
        return _safe({
            "status": "MARKET_TRANSPORT_NETWORK_NOT_REQUESTED",
            "execution_ok": True,
            "network_requests_made": 0,
            "transport_id": str(transport_id),
            "round_id": str(round_id),
            "evidence_hash": "",
        })
    if transport_id not in TRANSPORT_IDS:
        return _safe({
            "status": "MARKET_TRANSPORT_REQUEST_INVALID",
            "execution_ok": False,
            "network_requests_made": 0,
            "transport_id": str(transport_id),
            "round_id": str(round_id),
            "reason": "transport_id_invalid",
            "evidence_hash": "",
        })
    try:
        target_date = datetime.fromisoformat(str(trade_date)).date().isoformat()
    except ValueError:
        return _safe({
            "status": "MARKET_TRANSPORT_REQUEST_INVALID",
            "execution_ok": False,
            "network_requests_made": 0,
            "transport_id": transport_id,
            "round_id": str(round_id),
            "reason": "trade_date_invalid",
            "evidence_hash": "",
        })
    if not str(round_id).strip():
        raise ValueError("market_transport_round_id_required")
    now = clock or (lambda: datetime.now(CN_TZ))
    started = _aware_now(now)
    specs = diagnostic_requests()
    headers = _headers_for_transport(transport_id)
    environment = (environment_inspector or inspect_network_environment)()
    if transport_id == TRANSPORT_DOTNET_HTTPCLIENT:
        runner = dotnet_runner or run_dotnet_httpclient_diagnostic
        dotnet_result = runner(
            [spec.as_dict() for spec in specs], REQUEST_DEADLINE_MS
        )
        if isinstance(dotnet_result, Mapping):
            outcomes = list(dotnet_result.get("results") or [])
            if isinstance(dotnet_result.get("proxy_mode"), Mapping):
                environment["proxy_mode"] = dict(dotnet_result["proxy_mode"])
        else:
            outcomes = list(dotnet_result)
    else:
        caller = request_callable or requests.request
        outcomes = [
            _run_requests_once(spec, headers, caller, now)
            for spec in specs
        ]
    completed = _aware_now(now)
    evidence = _safe({
        "evidence_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "verifier_contract_version": DIAGNOSTIC_VERIFIER_VERSION,
        "status": "MARKET_TRANSPORT_DIAGNOSTIC_COMPLETE",
        "execution_ok": True,
        "audit_only": True,
        "trade_date": target_date,
        "round_id": str(round_id).strip(),
        "transport_id": transport_id,
        "source_versions": {
            key: value[2] for key, value in sorted(SOURCE_IDENTITIES.items())
        },
        "fixed_codes": list(FIXED_CODES),
        "request_deadline_ms": REQUEST_DEADLINE_MS,
        "recommendation_max_elapsed_ms": RECOMMENDATION_MAX_ELAPSED_MS,
        "benchmark_started_at": started.isoformat(timespec="microseconds"),
        "benchmark_completed_at": completed.isoformat(timespec="microseconds"),
        "session_window_round": _in_session_window(started, target_date),
        "proxy_mode": environment["proxy_mode"],
        "dns_status": environment["dns_status"],
        "tls_status": environment["tls_status"],
        "proxy_listener_status": environment["proxy_listener_status"],
        "request_headers": _whitelisted_headers(headers),
        "request_headers_hash": stable_hash(_whitelisted_headers(headers)),
        "requests": outcomes,
        "network_requests_made": len(outcomes),
        "retry_count": 0,
        "fallback_count": 0,
        "source_stitch_count": 0,
        "evidence_hash": "",
    })
    validation = _validate_evidence_material(evidence)
    evidence["field_contract_passed"] = validation["field_contract_passed"]
    evidence["candidate_round_passed"] = validation["candidate_round_passed"]
    evidence["validation_errors"] = validation["errors"]
    evidence["evidence_hash"] = compute_diagnostic_evidence_hash(evidence)
    return evidence


def verify_transport_diagnostic(
    evidence: Mapping[str, Any],
    *,
    expected_file_sha256: str,
    actual_file_sha256: str,
) -> dict[str, Any]:
    payload = dict(evidence)
    errors: list[str] = []
    if not _sha(expected_file_sha256) or expected_file_sha256 != actual_file_sha256:
        errors.append("external_file_sha256_mismatch")
    if payload.get("evidence_schema_version") != DIAGNOSTIC_SCHEMA_VERSION:
        errors.append("evidence_schema_version_invalid")
    if payload.get("verifier_contract_version") != DIAGNOSTIC_VERIFIER_VERSION:
        errors.append("verifier_contract_version_invalid")
    expected_hash = compute_diagnostic_evidence_hash(payload)
    if payload.get("evidence_hash") != expected_hash:
        errors.append("evidence_hash_mismatch")
    validation = _validate_evidence_material(payload)
    errors.extend(validation["integrity_errors"])
    if payload.get("field_contract_passed") is not validation["field_contract_passed"]:
        errors.append("field_contract_summary_mismatch")
    if payload.get("candidate_round_passed") is not validation["candidate_round_passed"]:
        errors.append("candidate_round_summary_mismatch")
    if list(payload.get("validation_errors") or []) != validation["errors"]:
        errors.append("validation_errors_summary_mismatch")
    errors = list(dict.fromkeys(errors))
    replay_material = {
        "trade_date": payload.get("trade_date"),
        "round_id": payload.get("round_id"),
        "transport_id": payload.get("transport_id"),
        "request_results": validation["request_results"],
        "field_contract_passed": validation["field_contract_passed"],
        "candidate_round_passed": validation["candidate_round_passed"],
    }
    return _safe({
        "status": (
            "MARKET_TRANSPORT_DIAGNOSTIC_VERIFIED"
            if not errors
            else "MARKET_TRANSPORT_DIAGNOSTIC_INVALID"
        ),
        "execution_ok": not errors,
        "evidence_integrity_verified": not errors,
        "candidate_round_passed": validation["candidate_round_passed"] if not errors else False,
        "errors": errors,
        "evidence_hash": payload.get("evidence_hash", ""),
        "replay_hash": stable_hash(replay_material) if not errors else "",
        "replay": replay_material if not errors else None,
        "transport_id": payload.get("transport_id", ""),
        "round_id": payload.get("round_id", ""),
        "source_evidence": {
            "benchmark_started_at": payload.get("benchmark_started_at", ""),
            "session_window_round": payload.get("session_window_round", False),
            "trade_date": payload.get("trade_date", ""),
        },
        "qualification_status": "AUDIT_ONLY_NOT_A_QUALIFICATION_DAY",
    })


def evaluate_transport_candidates(
    verified_rounds: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [dict(item) for item in verified_rounds]
    errors: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in TRANSPORT_IDS}
    for row in rows:
        transport_id = str(row.get("transport_id") or "")
        if transport_id not in grouped:
            errors.append("transport_id_invalid")
            continue
        if row.get("status") != "MARKET_TRANSPORT_DIAGNOSTIC_VERIFIED":
            errors.append(f"diagnostic_not_verified:{transport_id}")
        grouped[transport_id].append(row)
    passed: list[str] = []
    matrix: dict[str, Any] = {}
    for transport_id in TRANSPORT_IDS:
        group = sorted(
            grouped[transport_id],
            key=lambda value: str((value.get("replay") or {}).get("round_id") or ""),
        )
        distinct_rounds = {
            str((item.get("replay") or {}).get("round_id") or "") for item in group
        }
        starts = sorted(
            parse_cn_datetime((item.get("source_evidence") or {}).get("benchmark_started_at"))
            for item in group
            if parse_cn_datetime((item.get("source_evidence") or {}).get("benchmark_started_at"))
        )
        separated = len(starts) == len(group) and all(
            (right - left).total_seconds() >= 60
            for left, right in zip(starts, starts[1:])
        )
        session_round = any(
            bool((item.get("source_evidence") or {}).get("session_window_round"))
            for item in group
        )
        all_verified = all(
            item.get("status") == "MARKET_TRANSPORT_DIAGNOSTIC_VERIFIED"
            and item.get("evidence_integrity_verified") is True
            for item in group
        )
        all_passed = all(item.get("candidate_round_passed") is True for item in group)
        eligible = (
            len(group) >= 3
            and len(distinct_rounds) == len(group)
            and separated
            and session_round
            and all_verified
            and all_passed
        )
        matrix[transport_id] = {
            "round_count": len(group),
            "distinct_round_count": len(distinct_rounds),
            "rounds_separated": separated,
            "session_window_round_present": session_round,
            "all_evidence_verified": all_verified,
            "all_rounds_passed": all_passed,
            "eligible": eligible,
        }
        if eligible:
            passed.append(transport_id)
    unique = passed[0] if len(passed) == 1 else None
    status = (
        "EASTMONEY_TRANSPORT_CANDIDATE_SELECTED"
        if unique
        else "EASTMONEY_TRANSPORT_UNQUALIFIED"
        if not passed
        else "EASTMONEY_TRANSPORT_SELECTION_AMBIGUOUS"
    )
    result = _safe({
        "status": status,
        "execution_ok": not errors,
        "selected_transport": unique,
        "eligible_transports": passed,
        "matrix": matrix,
        "verified_inputs": sorted(
            [
                {
                    "transport_id": item.get("transport_id", ""),
                    "round_id": item.get("round_id", ""),
                    "evidence_hash": item.get("evidence_hash", ""),
                    "replay_hash": item.get("replay_hash", ""),
                    "trade_date": (item.get("source_evidence") or {}).get(
                        "trade_date", ""
                    ),
                }
                for item in rows
            ],
            key=lambda value: (value["transport_id"], value["round_id"]),
        ),
        "errors": list(dict.fromkeys(errors)),
        "automatic_configuration_change": False,
        "qualification_status": "AUDIT_ONLY_NOT_A_QUALIFICATION_DAY",
    })
    result["qualification_hash"] = stable_hash(result)
    return result


def compute_diagnostic_evidence_hash(evidence: Mapping[str, Any]) -> str:
    material = dict(evidence)
    material.pop("evidence_hash", None)
    return stable_hash(material)


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json_atomic(target: str | Path, payload: Mapping[str, Any]) -> Path:
    path = Path(target).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def inspect_network_environment() -> dict[str, Any]:
    proxies = urllib.request.getproxies()
    sanitized: dict[str, str] = {}
    listeners: dict[str, bool | None] = {}
    for scheme, value in sorted(proxies.items()):
        parsed = urlparse(value if "://" in value else f"http://{value}")
        host = parsed.hostname or ""
        port = parsed.port
        sanitized[scheme] = (
            f"{parsed.scheme or 'http'}://{host}:{port}"
            if host and port
            else f"{parsed.scheme or 'http'}://{host}"
        )
        if host in {"127.0.0.1", "localhost", "::1"} and port:
            listeners[scheme] = _port_listening(host, port)
        else:
            listeners[scheme] = None
    dns = {"ok": False, "addresses": [], "error_type": ""}
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(APPROVED_HOST, 443)})
        dns = {"ok": bool(addresses), "addresses": addresses, "error_type": ""}
    except OSError as exc:
        dns["error_type"] = type(exc).__name__
    tls = {"ok": False, "protocol": "", "error_type": ""}
    try:
        context = ssl.create_default_context()
        with socket.create_connection((APPROVED_HOST, 443), timeout=2.0) as raw:
            with context.wrap_socket(raw, server_hostname=APPROVED_HOST) as wrapped:
                tls = {"ok": True, "protocol": wrapped.version() or "", "error_type": ""}
    except OSError as exc:
        tls["error_type"] = type(exc).__name__
    return {
        "proxy_mode": {
            "mode": "system_environment" if sanitized else "direct",
            "configured": bool(sanitized),
            "endpoints": sanitized,
        },
        "dns_status": dns,
        "tls_status": tls,
        "proxy_listener_status": listeners,
    }


def run_dotnet_httpclient_diagnostic(
    requests_: list[dict[str, Any]],
    deadline_ms: int,
) -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    script = root / "overnight_quant" / "scripts" / "market_httpclient_diagnostic.ps1"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    payload = {
        "requests": requests_,
        "headers": _whitelisted_headers(_FIXED_HEADERS),
        "request_deadline_ms": deadline_ms,
        "max_response_bytes": MAX_RESPONSE_BYTES,
    }
    completed = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=max(10, int(len(requests_) * deadline_ms / 1000) + 10),
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip()
        return [
            _failed_outcome(
                _dict_to_spec(item),
                "DOTNET_WORKER_FAILED",
                "worker_process",
                "PowerShellWorkerError",
                error[:500],
                _FIXED_HEADERS,
            )
            for item in requests_
        ]
    parsed = json.loads(completed.stdout.lstrip("\ufeff"))
    outcomes = list(parsed.get("results") or [])
    if len(outcomes) != len(requests_):
        raise ValueError("dotnet_diagnostic_result_count_invalid")
    for item, request_value in zip(outcomes, requests_):
        spec = _dict_to_spec(request_value)
        item["exception_message"] = _sanitize_message(
            str(item.get("exception_message") or "")
        )
        item["source_version"] = SOURCE_IDENTITIES[spec.capability][2]
        item["request_deadline_ms"] = deadline_ms
        item["request_headers"] = _whitelisted_headers(_FIXED_HEADERS)
        item["request_headers_hash"] = stable_hash(
            _whitelisted_headers(_FIXED_HEADERS)
        )
        item["request_hash"] = _request_hash(spec, _FIXED_HEADERS)
    return {
        "results": outcomes,
        "proxy_mode": dict(parsed.get("proxy_mode") or {}),
    }


def _run_requests_once(
    spec: DiagnosticRequest,
    headers: Mapping[str, str],
    request_callable: Callable[..., Any],
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    started = _aware_now(clock)
    response = None
    try:
        response = request_callable(
            "GET",
            spec.url,
            params=list(spec.params),
            headers=dict(headers),
            timeout=REQUEST_DEADLINE_MS / 1000,
            allow_redirects=False,
            verify=True,
        )
        completed = _aware_now(clock)
        raw = bytes(response.content)
        final_url = str(response.url)
        redirect_chain = []
        if 300 <= int(response.status_code) < 400:
            redirect_chain.append(str(response.headers.get("Location") or ""))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")
        return {
            **spec.as_dict(),
            "source_version": SOURCE_IDENTITIES[spec.capability][2],
            "started_at": started.isoformat(timespec="microseconds"),
            "completed_at": completed.isoformat(timespec="microseconds"),
            "elapsed_ms": round((completed - started).total_seconds() * 1000, 3),
            "request_deadline_ms": REQUEST_DEADLINE_MS,
            "request_headers": _whitelisted_headers(headers),
            "request_headers_hash": stable_hash(_whitelisted_headers(headers)),
            "request_hash": _request_hash(spec, headers),
            "http_status_code": int(response.status_code),
            "final_url": final_url,
            "redirect_chain": redirect_chain,
            "response_byte_count": len(raw),
            "raw_hash": hashlib.sha256(raw).hexdigest(),
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "exception_type": "",
            "exception_stage": "",
            "innermost_exception_type": "",
            "exception_message": "",
        }
    except Exception as exc:
        completed = _aware_now(clock)
        outcome = _failed_outcome(
            spec,
            type(exc).__name__,
            "http_request",
            _innermost_exception_type(exc),
            _sanitize_message(str(exc)),
            headers,
        )
        outcome.update({
            "started_at": started.isoformat(timespec="microseconds"),
            "completed_at": completed.isoformat(timespec="microseconds"),
            "elapsed_ms": round((completed - started).total_seconds() * 1000, 3),
        })
        return outcome
    finally:
        if response is not None and hasattr(response, "close"):
            response.close()


def _validate_evidence_material(evidence: Mapping[str, Any]) -> dict[str, Any]:
    integrity_errors: list[str] = []
    candidate_errors: list[str] = []
    request_results: dict[str, Any] = {}
    if evidence.get("status") != "MARKET_TRANSPORT_DIAGNOSTIC_COMPLETE":
        integrity_errors.append("status_invalid")
    if evidence.get("audit_only") is not True:
        integrity_errors.append("audit_scope_invalid")
    if evidence.get("transport_id") not in TRANSPORT_IDS:
        integrity_errors.append("transport_id_invalid")
    expected_versions = {
        key: value[2] for key, value in sorted(SOURCE_IDENTITIES.items())
    }
    if evidence.get("source_versions") != expected_versions:
        integrity_errors.append("source_versions_invalid")
    if tuple(evidence.get("fixed_codes") or []) != FIXED_CODES:
        integrity_errors.append("fixed_codes_invalid")
    if evidence.get("request_deadline_ms") != REQUEST_DEADLINE_MS:
        integrity_errors.append("request_deadline_invalid")
    if evidence.get("recommendation_max_elapsed_ms") != RECOMMENDATION_MAX_ELAPSED_MS:
        integrity_errors.append("recommendation_limit_invalid")
    if evidence.get("retry_count") != 0 or evidence.get("fallback_count") != 0:
        integrity_errors.append("retry_or_fallback_detected")
    if evidence.get("source_stitch_count") != 0:
        integrity_errors.append("source_stitch_detected")
    if any(evidence.get(key) not in (False, [], None) for key in ("data_ready", "hard_gate_authorized")):
        integrity_errors.append("safety_state_invalid")
    if any(evidence.get(key) != [] for key in ("candidates", "tickets", "orders")):
        integrity_errors.append("trading_output_not_empty")
    started = parse_cn_datetime(evidence.get("benchmark_started_at"))
    completed = parse_cn_datetime(evidence.get("benchmark_completed_at"))
    if started is None or completed is None or completed < started:
        integrity_errors.append("benchmark_time_invalid")
    elif evidence.get("session_window_round") is not _in_session_window(
        started, str(evidence.get("trade_date") or "")
    ):
        integrity_errors.append("session_window_summary_mismatch")
    expected_specs = diagnostic_requests()
    outcomes = list(evidence.get("requests") or [])
    if evidence.get("network_requests_made") != len(expected_specs) or len(outcomes) != len(expected_specs):
        integrity_errors.append("request_count_invalid")
    expected_headers = _headers_for_transport(str(evidence.get("transport_id") or ""))
    if evidence.get("request_headers") != _whitelisted_headers(expected_headers):
        integrity_errors.append("request_headers_invalid")
    if evidence.get("request_headers_hash") != stable_hash(_whitelisted_headers(expected_headers)):
        integrity_errors.append("request_headers_hash_invalid")
    proxy_mode = evidence.get("proxy_mode") or {}
    if not isinstance(proxy_mode, Mapping) or proxy_mode.get("mode") not in {
        "direct",
        "system_environment",
        "windows_system_proxy",
    }:
        integrity_errors.append("proxy_mode_invalid")
    elif any(
        "@" in str(value)
        for value in (proxy_mode.get("endpoints") or {}).values()
    ):
        integrity_errors.append("proxy_credentials_exposed")
    if not bool((evidence.get("dns_status") or {}).get("ok")):
        candidate_errors.append("dns_unavailable")
    if not bool((evidence.get("tls_status") or {}).get("ok")):
        candidate_errors.append("tls_unavailable")
    listeners = evidence.get("proxy_listener_status") or {}
    if any(value is False for value in listeners.values()):
        candidate_errors.append("proxy_listener_unavailable")
    payloads: dict[str, dict[str, Any]] = {}
    for index, spec in enumerate(expected_specs):
        if index >= len(outcomes):
            break
        outcome = dict(outcomes[index])
        request_integrity_errors, request_candidate_errors = _validate_request_outcome(
            spec, outcome, expected_headers
        )
        if not request_integrity_errors and not request_candidate_errors:
            try:
                payloads[spec.endpoint_id] = _decode_json(outcome)
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                request_candidate_errors.append("response_json_invalid")
        request_results[spec.endpoint_id] = {
            "integrity_valid": not request_integrity_errors,
            "candidate_valid": not request_integrity_errors and not request_candidate_errors,
            "errors": [*request_integrity_errors, *request_candidate_errors],
            "raw_hash": outcome.get("raw_hash", ""),
            "elapsed_ms": outcome.get("elapsed_ms"),
        }
        integrity_errors.extend(
            f"{spec.endpoint_id}:{value}" for value in request_integrity_errors
        )
        candidate_errors.extend(
            f"{spec.endpoint_id}:{value}" for value in request_candidate_errors
        )
    field_errors = _validate_field_contracts(payloads, str(evidence.get("trade_date") or ""))
    candidate_errors.extend(field_errors)
    integrity_errors = list(dict.fromkeys(integrity_errors))
    candidate_errors = list(dict.fromkeys(candidate_errors))
    errors = list(dict.fromkeys([*integrity_errors, *candidate_errors]))
    request_transport_ok = all(
        result["candidate_valid"]
        and float(result["elapsed_ms"] or math.inf) <= RECOMMENDATION_MAX_ELAPSED_MS
        for result in request_results.values()
    ) and len(request_results) == len(expected_specs)
    field_contract_passed = not field_errors and len(payloads) == len(expected_specs)
    return {
        "errors": errors,
        "integrity_errors": integrity_errors,
        "candidate_errors": candidate_errors,
        "request_results": request_results,
        "field_contract_passed": field_contract_passed,
        "candidate_round_passed": (
            not integrity_errors
            and not candidate_errors
            and request_transport_ok
            and field_contract_passed
        ),
    }


def _validate_request_outcome(
    spec: DiagnosticRequest,
    outcome: Mapping[str, Any],
    headers: Mapping[str, str],
) -> tuple[list[str], list[str]]:
    integrity_errors: list[str] = []
    candidate_errors: list[str] = []
    if any(outcome.get(key) != value for key, value in spec.as_dict().items()):
        integrity_errors.append("request_identity_mismatch")
    if outcome.get("source_version") != SOURCE_IDENTITIES[spec.capability][2]:
        integrity_errors.append("source_version_mismatch")
    if outcome.get("request_deadline_ms") != REQUEST_DEADLINE_MS:
        integrity_errors.append("request_deadline_invalid")
    if outcome.get("request_headers") != _whitelisted_headers(headers):
        integrity_errors.append("request_headers_invalid")
    if outcome.get("request_headers_hash") != stable_hash(_whitelisted_headers(headers)):
        integrity_errors.append("request_headers_hash_invalid")
    if outcome.get("request_hash") != _request_hash(spec, headers):
        integrity_errors.append("request_hash_invalid")
    started = parse_cn_datetime(outcome.get("started_at"))
    completed = parse_cn_datetime(outcome.get("completed_at"))
    elapsed = outcome.get("elapsed_ms")
    if started is None or completed is None or completed < started:
        integrity_errors.append("request_time_invalid")
    elif not isinstance(elapsed, (int, float)) or abs(elapsed - (completed - started).total_seconds() * 1000) > 5:
        integrity_errors.append("request_elapsed_invalid")
    elif elapsed > REQUEST_DEADLINE_MS:
        candidate_errors.append("request_deadline_exceeded")
    if outcome.get("exception_type"):
        candidate_errors.append(f"request_failed:{outcome.get('exception_type')}")
    if outcome.get("http_status_code") != 200:
        candidate_errors.append("http_status_invalid")
    parsed = urlparse(str(outcome.get("final_url") or ""))
    if parsed.scheme != "https" or parsed.hostname != APPROVED_HOST:
        candidate_errors.append("final_url_invalid")
    if list(outcome.get("redirect_chain") or []):
        candidate_errors.append("redirect_not_allowed")
    try:
        raw = base64.b64decode(str(outcome.get("content_base64") or ""), validate=True)
    except ValueError:
        raw = b""
        integrity_errors.append("response_base64_invalid")
    if len(raw) != outcome.get("response_byte_count"):
        integrity_errors.append("response_bytes_invalid")
    if not raw and not outcome.get("exception_type"):
        candidate_errors.append("response_empty")
    if hashlib.sha256(raw).hexdigest() != outcome.get("raw_hash"):
        integrity_errors.append("raw_hash_invalid")
    return (
        list(dict.fromkeys(integrity_errors)),
        list(dict.fromkeys(candidate_errors)),
    )


def _validate_field_contracts(payloads: Mapping[str, dict[str, Any]], trade_date: str) -> list[str]:
    errors: list[str] = []
    counts = ((payloads.get("market_breadth_counts") or {}).get("data") or {}).get("diff") or []
    by_code = {str(item.get("f12") or "").zfill(6): item for item in counts if isinstance(item, dict)}
    if set(by_code) != {"000001", "399001", "899050"}:
        errors.append("market_breadth_stock_pool_invalid")
    else:
        for code, row in by_code.items():
            values = [_nonnegative_int(row.get(key)) for key in ("f104", "f105", "f106")]
            if any(value is None for value in values) or sum(value or 0 for value in values) <= 0:
                errors.append(f"market_breadth_counts_invalid:{code}")
            event = _event_time(row.get("f124"))
            if event is None or event.date().isoformat() != trade_date:
                errors.append(f"market_breadth_time_invalid:{code}")
    benchmark = (payloads.get("market_benchmark_f170") or {}).get("data") or {}
    if str(benchmark.get("f57") or "").zfill(6) != "000001":
        errors.append("market_benchmark_identity_invalid")
    if _finite_number(benchmark.get("f170")) is None:
        errors.append("market_benchmark_f170_invalid")
    benchmark_event = _event_time(benchmark.get("f86"))
    if benchmark_event is None or benchmark_event.date().isoformat() != trade_date:
        errors.append("market_benchmark_time_invalid")
    boards = ((payloads.get("industry_boards") or {}).get("data") or {}).get("diff") or []
    board_by_name: dict[str, dict[str, Any]] = {}
    for board in boards:
        if not isinstance(board, dict):
            continue
        name = str(board.get("f14") or "").strip()
        if name and name not in board_by_name:
            board_by_name[name] = board
    if not board_by_name:
        errors.append("industry_boards_empty")
    else:
        for name, board in board_by_name.items():
            values = [_nonnegative_int(board.get(key)) for key in ("f104", "f105", "f106")]
            if any(value is None for value in values) or sum(value or 0 for value in values) <= 0:
                errors.append(f"industry_board_counts_invalid:{name}")
            board_event = _event_time(board.get("f124"))
            if (
                _finite_number(board.get("f3")) is None
                or board_event is None
                or board_event.date().isoformat() != trade_date
            ):
                errors.append(f"industry_board_fields_invalid:{name}")
    for code in FIXED_CODES:
        mapping = (payloads.get(f"industry_mapping:{code}") or {}).get("data") or {}
        industry = str(mapping.get("f127") or "").strip()
        if str(mapping.get("f57") or "").zfill(6) != code:
            errors.append(f"industry_mapping_code_invalid:{code}")
        if not industry or industry not in board_by_name:
            errors.append(f"industry_mapping_missing:{code}")
        mapping_event = _event_time(mapping.get("f86"))
        if mapping_event is None or mapping_event.date().isoformat() != trade_date:
            errors.append(f"industry_mapping_time_invalid:{code}")
        flow_lines = ((payloads.get(f"fund_flow:{code}") or {}).get("data") or {}).get("klines") or []
        current_day_rows = []
        for line in flow_lines:
            parts = str(line).split(",")
            if len(parts) < 6:
                continue
            event = parse_cn_datetime(parts[0])
            values = [_finite_number(value) for value in parts[1:6]]
            if (
                event is not None
                and event.date().isoformat() == trade_date
                and all(value is not None for value in values)
            ):
                current_day_rows.append(line)
        if not current_day_rows:
            errors.append(f"fund_flow_current_day_invalid:{code}")
    return list(dict.fromkeys(errors))


def _headers_for_transport(transport_id: str) -> dict[str, str]:
    if transport_id == TRANSPORT_REQUESTS_CURRENT:
        return dict(_CURRENT_HEADERS)
    if transport_id in {TRANSPORT_REQUESTS_FIXED_HEADERS, TRANSPORT_DOTNET_HTTPCLIENT}:
        return dict(_FIXED_HEADERS)
    return {}


def _failed_outcome(
    spec: DiagnosticRequest,
    exception_type: str,
    stage: str,
    innermost: str,
    message: str,
    headers: Mapping[str, str],
) -> dict[str, Any]:
    return {
        **spec.as_dict(),
        "source_version": SOURCE_IDENTITIES[spec.capability][2],
        "started_at": "",
        "completed_at": "",
        "elapsed_ms": None,
        "request_deadline_ms": REQUEST_DEADLINE_MS,
        "request_headers": _whitelisted_headers(headers),
        "request_headers_hash": stable_hash(_whitelisted_headers(headers)),
        "request_hash": _request_hash(spec, headers),
        "http_status_code": None,
        "final_url": "",
        "redirect_chain": [],
        "response_byte_count": 0,
        "raw_hash": hashlib.sha256(b"").hexdigest(),
        "content_base64": "",
        "exception_type": exception_type,
        "exception_stage": stage,
        "innermost_exception_type": innermost,
        "exception_message": str(message)[:500],
    }


def _request(endpoint_id: str, capability: str, url: str, params: Mapping[str, Any], code: str = "") -> DiagnosticRequest:
    return DiagnosticRequest(
        endpoint_id,
        capability,
        url,
        tuple(sorted((str(key), str(value)) for key, value in params.items())),
        code,
    )


def _dict_to_spec(value: Mapping[str, Any]) -> DiagnosticRequest:
    return DiagnosticRequest(
        str(value.get("endpoint_id") or ""),
        str(value.get("capability") or ""),
        str(value.get("url") or ""),
        tuple((str(item[0]), str(item[1])) for item in value.get("params") or []),
        str(value.get("code") or ""),
    )


def _request_hash(spec: DiagnosticRequest, headers: Mapping[str, str]) -> str:
    return stable_hash({
        "method": "GET",
        "url": spec.url,
        "params": [list(item) for item in spec.params],
        "headers": _whitelisted_headers(headers),
    })


def _whitelisted_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key: str(headers[key])
        for key in _HEADER_WHITELIST
        if key in headers
    }


def _decode_json(outcome: Mapping[str, Any]) -> dict[str, Any]:
    raw = base64.b64decode(str(outcome.get("content_base64") or ""), validate=True)
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("response_json_object_required")
    return value


def _safe(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _aware_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise ValueError("diagnostic_clock_timezone_required")
    return value.astimezone(CN_TZ)


def _secid(code: str) -> str:
    return ("1." if code.startswith(("6", "9")) else "0.") + code


def _sha(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 and str(value).strip() not in {"", "-"} else None


def _event_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), CN_TZ)
        except (OverflowError, OSError, ValueError):
            return None
    return parse_cn_datetime(value)


def _port_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _innermost_exception_type(exc: BaseException) -> str:
    current: BaseException = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        nested = current.__cause__ or current.__context__
        if nested is None:
            break
        current = nested
    return type(current).__name__


def _sanitize_message(value: str) -> str:
    text = re.sub(
        r"(?i)(https?://)([^/@\s]+)@",
        r"\1***@",
        str(value),
    )
    return text[:500]


def _in_session_window(started: datetime, trade_date: str) -> bool:
    return (
        started.date().isoformat() == trade_date
        and SESSION_WINDOW_START <= started.strftime("%H:%M:%S.%f") <= SESSION_WINDOW_END
    )


__all__ = [
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DIAGNOSTIC_VERIFIER_VERSION",
    "RECOMMENDATION_MAX_ELAPSED_MS",
    "REQUEST_DEADLINE_MS",
    "TRANSPORT_DOTNET_HTTPCLIENT",
    "TRANSPORT_IDS",
    "TRANSPORT_REQUESTS_CURRENT",
    "TRANSPORT_REQUESTS_FIXED_HEADERS",
    "compute_diagnostic_evidence_hash",
    "diagnostic_requests",
    "evaluate_transport_candidates",
    "file_sha256",
    "run_transport_diagnostic",
    "verify_transport_diagnostic",
    "write_json_atomic",
]
