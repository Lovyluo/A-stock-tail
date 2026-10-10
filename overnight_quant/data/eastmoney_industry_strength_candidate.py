"""Offline fltt=1 candidate parsing, never a production adapter binding."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlparse

from overnight_quant.data.eastmoney_derived_industry_breadth import EXPECTED_INDUSTRIES
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_STOCK_URL, MARKET_DOTNET_TRANSPORT_VERSION, MAX_RESPONSE_BYTES,
)
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash


CONTRACT_VERSION = "eastmoney_bk_fltt1_strength_candidate_v1"
SOURCE_VERSION = "push2_bk_f170_f152_fltt1_candidate_v2026-10-10"
UPSTREAM_VENDOR_SHA256 = "504cabef35a0f41ceb8ab4905c911ad317f47e36eabf82e3775bed30acc6d683"
BOARD_NAMES = dict(sorted(set(EXPECTED_INDUSTRIES.values())))
FIELDS = "f57,f58,f86,f170,f152,f169"


def board_strength_request(board_code: str) -> dict[str, Any]:
    if type(board_code) is not str or board_code not in BOARD_NAMES:
        raise ValueError("INDUSTRY_STRENGTH_BOARD_UNKNOWN")
    return {
        "method": "GET", "url": EASTMONEY_STOCK_URL,
        "params": {"secid": f"90.{board_code}", "fltt": "1", "invt": "2", "fields": FIELDS},
    }


def candidate_contract_hash() -> str:
    return stable_hash({
        "version": CONTRACT_VERSION, "source_version": SOURCE_VERSION,
        "requests": [board_strength_request(board) for board in BOARD_NAMES],
        "upstream_vendor_sha256": UPSTREAM_VENDOR_SHA256,
        "change_field": "f170", "precision_field": "f152",
        "formula": "f170/10**f152", "unit": "percent",
        "accepted_precision": "strict_integer_0_through_8_candidate_subset",
        "event_field": "f86_unix_seconds_no_truncation",
        "request_deadline_ms": 2000, "production_records": "always_empty",
    })


def board_strength_request_plan() -> dict[str, Any]:
    material = {
        "contract_version": CONTRACT_VERSION, "source_version": SOURCE_VERSION,
        "contract_hash": candidate_contract_hash(),
        "requests": [board_strength_request(board) for board in BOARD_NAMES],
        "planned_request_count": 4, "request_deadline_ms": 2000,
        "max_workers": 2, "host_start_interval_ms": 500,
        "requires_verified_completed_calendar": True,
        "requires_verified_current_session": True,
        "stop_dispatch_on_failure": True, "retry_count": 0, "fallback_count": 0,
    }
    return _safe({**material, "request_plan_hash": stable_hash(material), "network_requests_made": 0})


def parse_captured_board_strength(
    capture: Mapping[str, Any], *, request: Mapping[str, Any],
    trade_date: str, feature_cutoff: str, collection_deadline: str,
    expected_raw_sha256: str, test_only: bool = False,
) -> dict[str, Any]:
    """Validate supplied bytes/timestamps; no transport, clock repair or activation."""
    result = _safe({
        "status": "INDUSTRY_STRENGTH_CANDIDATE_REJECTED", "execution_ok": False,
        "contract_version": CONTRACT_VERSION, "contract_hash": candidate_contract_hash(),
        "source_version": SOURCE_VERSION, "test_only": test_only is True,
        "network_requests_made": 0, "error_code": "", "audit_records": [],
    })
    try:
        if type(test_only) is not bool or not isinstance(capture, Mapping) or not isinstance(request, Mapping):
            raise ValueError("INDUSTRY_STRENGTH_REQUEST_INVALID")
        params = request.get("params")
        if not isinstance(params, Mapping):
            raise ValueError("INDUSTRY_STRENGTH_REQUEST_INVALID")
        secid = params.get("secid")
        if type(secid) is not str or not secid.startswith("90."):
            raise ValueError("INDUSTRY_STRENGTH_REQUEST_INVALID")
        board = secid[3:]
        if request != board_strength_request(board):
            raise ValueError("INDUSTRY_STRENGTH_REQUEST_CONTRACT_MISMATCH")
        request_hash = stable_hash({
            "method": "GET", "url": EASTMONEY_STOCK_URL,
            "params": sorted(params.items()),
        })
        if (capture.get("origin_source") != "eastmoney" or capture.get("adapter") != "direct_http"
                or capture.get("source_version") != SOURCE_VERSION
                or capture.get("contract_version") != CONTRACT_VERSION
                or capture.get("transport_version") != MARKET_DOTNET_TRANSPORT_VERSION
                or capture.get("request_hash") != request_hash):
            raise ValueError("INDUSTRY_STRENGTH_IDENTITY_OR_VERSION_MISMATCH")
        url = urlparse(str(capture.get("response_url", "")))
        if (url.scheme != "https" or url.netloc != "push2.eastmoney.com"
                or url.path != "/api/qt/stock/get" or url.fragment
                or sorted(parse_qsl(url.query, keep_blank_values=True)) != sorted(params.items())
                or capture.get("redirect_chain") != []):
            raise ValueError("INDUSTRY_STRENGTH_RESPONSE_URL_INVALID")
        if type(capture.get("http_status_code")) is not int or capture["http_status_code"] != 200:
            raise ValueError("INDUSTRY_STRENGTH_HTTP_INVALID")
        if not _sha(expected_raw_sha256):
            raise ValueError("INDUSTRY_STRENGTH_EXTERNAL_RAW_ANCHOR_INVALID")
        raw = base64.b64decode(capture["content_base64"], validate=True)
        if not 0 < len(raw) <= MAX_RESPONSE_BYTES:
            raise ValueError("INDUSTRY_STRENGTH_RESPONSE_SIZE_INVALID")
        raw_hash = hashlib.sha256(raw).hexdigest()
        if raw_hash != expected_raw_sha256 or raw_hash != capture.get("raw_hash"):
            raise ValueError("INDUSTRY_STRENGTH_RAW_HASH_MISMATCH")
        payload = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_unique_object)
        if type(payload) is not dict or type(payload.get("rc")) is not int or payload["rc"] != 0:
            raise ValueError("INDUSTRY_STRENGTH_RESPONSE_INVALID")
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("f57") != board or data.get("f58") != BOARD_NAMES[board]:
            raise ValueError("INDUSTRY_STRENGTH_BOARD_IDENTITY_MISMATCH")
        precision = data.get("f152")
        if type(precision) is not int or not 0 <= precision <= 8:
            raise ValueError("INDUSTRY_STRENGTH_PRECISION_INVALID")
        value = data.get("f170")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("INDUSTRY_STRENGTH_VALUE_INVALID")
        change = float(Decimal(str(value)).scaleb(-precision))
        if not math.isfinite(change):
            raise ValueError("INDUSTRY_STRENGTH_VALUE_INVALID")
        native_time = data.get("f86")
        if type(native_time) is not int or native_time <= 0:
            raise ValueError("INDUSTRY_STRENGTH_EVENT_INVALID")
        event = datetime.fromtimestamp(native_time, CN_TZ)
        cutoff, deadline = _time(feature_cutoff), _time(collection_deadline)
        start, complete, observed, available = (
            _time(capture[key]) for key in
            ("request_started_at", "request_completed_at", "observed_at", "available_at")
        )
        if (not cutoff < deadline or type(trade_date) is not str
                or trade_date != cutoff.date().isoformat()
                or any(t.date() != cutoff.date() for t in (deadline, event, start, complete, observed, available))
                or not start <= complete <= observed <= available <= deadline
                or event > complete):
            raise ValueError("INDUSTRY_STRENGTH_TIME_CONTRACT_INVALID")
        if event > cutoff:
            raise ValueError("INDUSTRY_STRENGTH_EVENT_AFTER_CUTOFF")
        process_start, process_complete = (_time(capture[key]) for key in ("process_started_at", "process_completed_at"))
        if (process_start.date() != cutoff.date() or process_complete.date() != cutoff.date()
                or not process_start <= start <= complete <= process_complete <= observed
                or (process_complete - process_start).total_seconds() * 1000 > 2000):
            raise ValueError("INDUSTRY_STRENGTH_REQUEST_DEADLINE_INVALID")
        record = {
            "capability": "industry_snapshot", "origin_source": "eastmoney", "adapter": "direct_http",
            "source_version": SOURCE_VERSION, "contract_version": CONTRACT_VERSION,
            "event_time": event.isoformat(), "observed_at": capture["observed_at"],
            "available_at": capture["available_at"], "request_hash": request_hash, "raw_hash": raw_hash,
            "request_started_at": capture["request_started_at"], "request_completed_at": capture["request_completed_at"],
            "upstream_semantics_sha256": UPSTREAM_VENDOR_SHA256,
            "test_only": test_only, "usage_scope": "candidate_audit_only",
            "payload": {
                "board_code": board, "board_name": data["f58"], "change_pct": change,
                "field_units": {"change_pct": "percent"},
                "raw_fields": {key: data[key] for key in ("f57", "f58", "f86", "f170", "f152")},
                "conversion": "f170/10**f152", "native_time_precision": "second",
            },
        }
        result.update(status="INDUSTRY_STRENGTH_CANDIDATE_PARSED", execution_ok=True, audit_records=[record])
    except (ValueError, TypeError, KeyError, OverflowError, OSError, InvalidOperation) as exc:
        error = str(exc)
        result["error_code"] = error if error.startswith("INDUSTRY_STRENGTH_") else "INDUSTRY_STRENGTH_CAPTURE_INVALID"
    return result


def _time(value: Any) -> datetime:
    parsed = parse_cn_datetime(value) if type(value) is str else None
    if parsed is None or datetime.fromisoformat(value).utcoffset() != timedelta(hours=8):
        raise ValueError("INDUSTRY_STRENGTH_TIME_INVALID")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("INDUSTRY_STRENGTH_DUPLICATE_JSON_FIELD")
        value[key] = item
    return value


def _sha(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _safe(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **value, "records": [], "provider_activated": False, "source_qualification_verified": False,
        "provider_contract_complete": False, "qualification": "0/3", "data_ready": False,
        "hard_gate_authorized": False, "automatic_configuration_change": False,
        "candidates": [], "tickets": [], "orders": [],
    }
