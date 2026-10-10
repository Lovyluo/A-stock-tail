from __future__ import annotations

import base64
from collections import deque
from datetime import datetime
import gzip
import hashlib
import json
import math
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_CLIST_URL,
    FIXED_CODES,
    MARKET_DOTNET_TRANSPORT_VERSION,
    MarketHttpResponse,
    MarketSourceContractError,
    MarketTransport,
)
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash


CORE_CONCEPTION_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/"
    "CoreConception/PageAjax"
)
SOURCE_VERSION = "eastmoney_coreconception+board_constituent_breadth_v1"
EVIDENCE_SCHEMA_VERSION = "eastmoney_derived_industry_breadth_evidence_v1"
VERIFIER_VERSION = "eastmoney_derived_industry_breadth_verifier_v1"
PAGE_SIZE = 100
MAX_SCHEMA_AUDIT_REQUESTS = 12
ALLOWED_HOSTS = {
    "emweb.securities.eastmoney.com",
    "push2.eastmoney.com",
}
EXPECTED_INDUSTRIES = {
    "000001": ("BK0475", "银行Ⅱ"),
    "000333": ("BK1239", "白色家电"),
    "600000": ("BK0475", "银行Ⅱ"),
    "600519": ("BK1277", "白酒Ⅱ"),
    "601318": ("BK0474", "保险Ⅱ"),
}


def compute_verifier_hash() -> str:
    return stable_hash({
        "version": VERIFIER_VERSION,
        "source_version": SOURCE_VERSION,
        "fixed_codes": FIXED_CODES,
        "expected_industries": EXPECTED_INDUSTRIES,
        "page_size": PAGE_SIZE,
        "max_schema_audit_requests": MAX_SCHEMA_AUDIT_REQUESTS,
        "allowed_hosts": sorted(ALLOWED_HOSTS),
        "classification": {
            "up": "change_pct>0",
            "down": "change_pct<0",
            "flat": "change_pct==0",
            "invalid": "missing_or_non_finite_change_pct",
        },
        "event_time": "maximum_native_constituent_f124_minute",
        "request_count": "recomputed_from_request_tree_leaves",
    })


class EastmoneyDerivedIndustryBreadthProvider:
    def __init__(
        self,
        codes: Iterable[str],
        *,
        trade_date: str,
        cutoff: str | datetime,
        decision_time: str | datetime,
        transport: MarketTransport,
        clock: Callable[[], datetime],
        timeout_seconds: float = 2.0,
        schema_audit_only: bool = False,
    ) -> None:
        self.codes = _normalize_codes(codes)
        if self.codes != FIXED_CODES:
            raise MarketSourceContractError("MARKET_SOURCE_FIXED_CODES_REQUIRED")
        self.trade_date = str(trade_date)
        self.cutoff = _as_cn(cutoff)
        self.decision_time = _as_cn(decision_time)
        if self.decision_time <= self.cutoff:
            raise MarketSourceContractError("INDUSTRY_DECISION_TIME_INVALID")
        if not callable(clock) or not hasattr(transport, "request"):
            raise MarketSourceContractError("MARKET_SOURCE_DEPENDENCY_INVALID")
        if not math.isfinite(float(timeout_seconds)) or not 0 < timeout_seconds <= 2:
            raise MarketSourceContractError("MARKET_SOURCE_TIMEOUT_INVALID")
        self.transport = transport
        self.clock = clock
        self.timeout_seconds = float(timeout_seconds)
        self.schema_audit_only = bool(schema_audit_only)
        self.request_tree: dict[str, Any] = {"mappings": [], "boards": {}}

    def collect_records(self) -> list[dict[str, Any]]:
        mappings: dict[str, dict[str, Any]] = {}
        mapping_requests: dict[str, dict[str, Any]] = {}
        for code in self.codes:
            response = self._request(
                CORE_CONCEPTION_URL,
                {"code": _eastmoney_web_code(code)},
                kind="mapping",
                identity=code,
            )
            payload = _json_payload(response)
            rows = payload.get("ssbk")
            if not isinstance(rows, list):
                raise MarketSourceContractError(f"INDUSTRY_MAPPING_MISSING:{code}")
            expected_code, expected_name = EXPECTED_INDUSTRIES[code]
            matches = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                board_code = _board_code(row.get("BOARD_CODE"))
                board_name = str(row.get("BOARD_NAME") or "").strip()
                security_code = str(row.get("SECURITY_CODE") or "").zfill(6)
                rank = _strict_int(row.get("BOARD_RANK"), "INDUSTRY_MAPPING_RANK_INVALID")
                if rank == 2:
                    matches.append((security_code, board_code, board_name))
            if matches != [(code, expected_code, expected_name)]:
                raise MarketSourceContractError(f"INDUSTRY_MAPPING_MISMATCH:{code}")
            mappings[code] = {
                "code": code,
                "board_code": expected_code,
                "board_name": expected_name,
                "mapping_observed_at": response["observed_at"],
                "mapping_available_at": response["available_at"],
            }
            mapping_requests[code] = response

        unique_boards = sorted({value[0] for value in EXPECTED_INDUSTRIES.values()})
        board_results: dict[str, dict[str, Any]] = {}
        for board_code in unique_boards:
            board_results[board_code] = self._collect_board(board_code)

        records = []
        for code in self.codes:
            mapping = mappings[code]
            board = board_results[mapping["board_code"]]
            if code not in board["constituent_codes"]:
                raise MarketSourceContractError(
                    f"INDUSTRY_MAPPED_STOCK_NOT_IN_BOARD:{code}"
                )
            mapping_request = mapping_requests[code]
            page_requests = board["requests"]
            records.append({
                "capability": "industry_snapshot",
                "data_type": "industry",
                "origin_source": "eastmoney",
                "adapter": "direct_http",
                "source": "eastmoney_direct",
                "source_version": SOURCE_VERSION,
                "event_time": board["event_time"],
                "observed_at": max(
                    [mapping_request["observed_at"]]
                    + [item["observed_at"] for item in page_requests]
                ),
                "available_at": max(
                    [mapping_request["available_at"]]
                    + [item["available_at"] for item in page_requests]
                ),
                "decision_cutoff": self.cutoff.isoformat(),
                "request_hash": stable_hash([
                    mapping_request["request_hash"],
                    *[item["request_hash"] for item in page_requests],
                ]),
                "raw_hash": stable_hash([
                    mapping_request["raw_hash"],
                    *[item["raw_hash"] for item in page_requests],
                ]),
                "payload": {
                    "code": code,
                    "industry": mapping["board_name"],
                    "industry_name": mapping["board_name"],
                    "board_code": mapping["board_code"],
                    "classification_version": SOURCE_VERSION,
                    "mapping_time_semantics": "static_classification_audit_only",
                    "mapping_observed_at": mapping["mapping_observed_at"],
                    "mapping_available_at": mapping["mapping_available_at"],
                    "event_time_granularity": "minute",
                    "raw_board_event_time": board["raw_event_time"],
                    "page_size": PAGE_SIZE,
                    "page_count": board["page_count"],
                    "declared_total": board["declared_total"],
                    "page_raw_hashes": [item["raw_hash"] for item in page_requests],
                    "up_count": board["up_count"],
                    "down_count": board["down_count"],
                    "flat_count": board["flat_count"],
                    "invalid_count": board["invalid_count"],
                    "valid_count": board["valid_count"],
                    "total_count": board["total_count"],
                    "breadth_ratio": board["breadth_ratio"],
                    "field_units": {
                        "breadth_ratio": "ratio_0_1",
                        "change_pct": "percent",
                        "counts": "stock_count",
                    },
                },
            })
        return records

    def _collect_board(self, board_code: str) -> dict[str, Any]:
        requests: list[dict[str, Any]] = []
        constituents: dict[str, dict[str, Any]] = {}
        declared_total: int | None = None
        page_count: int | None = None
        page = 1
        while page_count is None or page <= page_count:
            response = self._request(
                EASTMONEY_CLIST_URL,
                {
                    "pn": str(page),
                    "pz": str(PAGE_SIZE),
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": f"b:{board_code}",
                    "fields": "f2,f3,f12,f14,f124",
                },
                kind="board_page",
                identity=f"{board_code}:{page}",
            )
            requests.append(response)
            data = _json_payload(response).get("data") or {}
            total = _strict_int(data.get("total"), "INDUSTRY_BOARD_TOTAL_INVALID")
            if total <= 0:
                raise MarketSourceContractError("INDUSTRY_BOARD_TOTAL_INVALID")
            if declared_total is None:
                declared_total = total
                page_count = math.ceil(total / PAGE_SIZE)
            elif total != declared_total:
                raise MarketSourceContractError("INDUSTRY_BOARD_TOTAL_DRIFT")
            rows = data.get("diff")
            if not isinstance(rows, list) or not rows:
                raise MarketSourceContractError(f"INDUSTRY_BOARD_PAGE_MISSING:{board_code}:{page}")
            if page < page_count and len(rows) != PAGE_SIZE:
                raise MarketSourceContractError(f"INDUSTRY_BOARD_PAGE_INCOMPLETE:{board_code}:{page}")
            for row in rows:
                code = str((row or {}).get("f12") or "").strip().zfill(6)
                if len(code) != 6 or not code.isdigit():
                    raise MarketSourceContractError("INDUSTRY_CONSTITUENT_CODE_INVALID")
                if code in constituents:
                    raise MarketSourceContractError(f"INDUSTRY_CONSTITUENT_DUPLICATE:{code}")
                event_time = _timestamp((row or {}).get("f124"))
                if event_time is None:
                    raise MarketSourceContractError(f"INDUSTRY_EVENT_TIME_MISSING:{code}")
                constituents[code] = {
                    "code": code,
                    "name": str((row or {}).get("f14") or "").strip(),
                    "change_pct": _number_or_none((row or {}).get("f3")),
                    "event_time": event_time,
                }
            page += 1
        if declared_total is None or len(constituents) != declared_total:
            raise MarketSourceContractError("INDUSTRY_BOARD_TOTAL_COUNT_MISMATCH")
        events = [value["event_time"] for value in constituents.values()]
        latest_event = max(events)
        if latest_event > self.cutoff:
            raise MarketSourceContractError("INDUSTRY_EVENT_AFTER_CUTOFF")
        if not self.schema_audit_only and latest_event.date().isoformat() != self.trade_date:
            raise MarketSourceContractError("INDUSTRY_EVENT_TRADE_DATE_MISMATCH")
        up = down = flat = invalid = 0
        for value in constituents.values():
            change = value["change_pct"]
            if change is None:
                invalid += 1
            elif change > 0:
                up += 1
            elif change < 0:
                down += 1
            else:
                flat += 1
        valid = up + down + flat
        total_count = valid + invalid
        if valid <= 0 or total_count != declared_total:
            raise MarketSourceContractError("INDUSTRY_BREADTH_COUNTS_INVALID")
        event_time = latest_event.replace(second=0, microsecond=0)
        return {
            "requests": requests,
            "constituent_codes": frozenset(constituents),
            "page_count": page_count,
            "declared_total": declared_total,
            "up_count": up,
            "down_count": down,
            "flat_count": flat,
            "invalid_count": invalid,
            "valid_count": valid,
            "total_count": total_count,
            "breadth_ratio": up / valid,
            "raw_event_time": latest_event.isoformat(),
            "event_time": event_time.isoformat(),
        }

    def _request(
        self,
        url: str,
        params: Mapping[str, Any],
        *,
        kind: str,
        identity: str,
    ) -> dict[str, Any]:
        if len(request_leaves(self.request_tree)) >= MAX_SCHEMA_AUDIT_REQUESTS:
            raise MarketSourceContractError("INDUSTRY_REQUEST_BUDGET_EXCEEDED")
        observed = _as_cn(self.clock())
        if observed >= self.decision_time:
            raise MarketSourceContractError("INDUSTRY_DECISION_TIME_EXCEEDED")
        remaining = (self.decision_time - observed).total_seconds()
        response = self.transport.request(
            "GET",
            url,
            params=params,
            headers={"Referer": "https://emweb.securities.eastmoney.com/"},
            timeout_seconds=min(self.timeout_seconds, remaining),
        )
        available = _as_cn(self.clock())
        started = parse_cn_datetime(response.request_started_at)
        completed = parse_cn_datetime(response.request_completed_at)
        if (
            started is None
            or completed is None
            or completed < started
            or completed > available
            or available > self.decision_time
        ):
            raise MarketSourceContractError("INDUSTRY_REQUEST_TIME_INVALID")
        parsed = urlparse(response.url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise MarketSourceContractError("INDUSTRY_RESPONSE_URL_INVALID")
        raw = bytes(response.content)
        request_hash = stable_hash({
            "method": "GET",
            "url": url,
            "params": _canonical_pairs(params),
        })
        audit = {
            "kind": kind,
            "identity": identity,
            "method": "GET",
            "url": url,
            "params": _canonical_pairs(params),
            "request_hash": request_hash,
            "raw_hash": hashlib.sha256(raw).hexdigest(),
            "encoding": "base64",
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "byte_count": len(raw),
            "request_started_at": started.isoformat(timespec="microseconds"),
            "request_completed_at": completed.isoformat(timespec="microseconds"),
            "observed_at": observed.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
            "process_started_at": response.process_started_at,
            "process_completed_at": response.process_completed_at,
            "process_elapsed_ms": response.process_elapsed_ms,
            "transport_version": response.transport_version,
            "http_status_code": response.status_code,
            "response_url": response.url,
            "worker_terminated": response.worker_terminated,
        }
        if kind == "mapping":
            self.request_tree["mappings"].append(audit)
        else:
            board_code = identity.split(":", 1)[0]
            self.request_tree["boards"].setdefault(board_code, []).append(audit)
        return audit


class ReplayTransport:
    transport_version = MARKET_DOTNET_TRANSPORT_VERSION

    def __init__(self, leaves: Iterable[Mapping[str, Any]]) -> None:
        self.leaves = deque(dict(item) for item in leaves)
        self.request_count = 0

    def request(self, method, url, *, params, headers, timeout_seconds):
        self.request_count += 1
        if not self.leaves:
            raise ValueError("replay_request_missing")
        item = self.leaves.popleft()
        expected_params = _canonical_pairs(params)
        if (
            item.get("method") != method
            or item.get("url") != url
            or [tuple(value) for value in item.get("params") or []] != expected_params
        ):
            raise ValueError("replay_request_mismatch")
        expected_hash = stable_hash({"method": method, "url": url, "params": expected_params})
        if item.get("request_hash") != expected_hash:
            raise ValueError("replay_request_hash_mismatch")
        raw = base64.b64decode(str(item.get("content_base64") or ""), validate=True)
        if len(raw) != item.get("byte_count") or hashlib.sha256(raw).hexdigest() != item.get("raw_hash"):
            raise ValueError("replay_raw_hash_mismatch")
        return MarketHttpResponse(
            raw,
            int(item["http_status_code"]),
            str(item["response_url"]),
            {},
            str(item["request_started_at"]),
            str(item["request_completed_at"]),
            str(item["transport_version"]),
            str(item.get("process_started_at") or ""),
            str(item.get("process_completed_at") or ""),
            item.get("process_elapsed_ms"),
            item.get("worker_terminated") is True,
        )


class ReplayClock:
    def __init__(self, leaves: Iterable[Mapping[str, Any]]) -> None:
        values = []
        for item in leaves:
            values.extend((item["observed_at"], item["available_at"]))
        self.values = deque(datetime.fromisoformat(value) for value in values)

    def __call__(self) -> datetime:
        if not self.values:
            raise ValueError("replay_clock_exhausted")
        return self.values.popleft()


def request_leaves(request_tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    mappings = request_tree.get("mappings")
    boards = request_tree.get("boards")
    if not isinstance(mappings, list) or not isinstance(boards, Mapping):
        raise ValueError("request_tree_invalid")
    leaves = [dict(item) for item in mappings]
    for board_code in sorted(boards):
        pages = boards[board_code]
        if not isinstance(pages, list):
            raise ValueError("request_tree_invalid")
        leaves.extend(dict(item) for item in pages)
    return leaves


def compute_evidence_hash(evidence: Mapping[str, Any]) -> str:
    material = dict(evidence)
    material.pop("evidence_hash", None)
    return stable_hash(material)


def verify_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_file_sha256: str | None,
) -> dict[str, Any]:
    payload = dict(evidence)
    errors: list[str] = []
    if payload.get("evidence_schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if payload.get("source_version") != SOURCE_VERSION:
        errors.append("source_version_invalid")
    if payload.get("verifier_hash") != compute_verifier_hash():
        errors.append("verifier_hash_invalid")
    if payload.get("evidence_hash") != compute_evidence_hash(payload):
        errors.append("evidence_hash_mismatch")
    if not expected_file_sha256:
        errors.append("external_sha256_anchor_required")
    for key in ("data_ready", "hard_gate_authorized"):
        if payload.get(key) is not False:
            errors.append(f"safety_flag_invalid:{key}")
    for key in ("candidates", "tickets", "orders"):
        if payload.get(key) != []:
            errors.append(f"safety_output_invalid:{key}")
    replayed: list[dict[str, Any]] = []
    try:
        leaves = request_leaves(payload.get("request_tree") or {})
        leaf_count = len(leaves)
        if payload.get("network_requests_made") != leaf_count:
            errors.append("network_request_count_mismatch")
        if leaf_count > MAX_SCHEMA_AUDIT_REQUESTS:
            errors.append("network_request_budget_exceeded")
        transport = ReplayTransport(leaves)
        provider = EastmoneyDerivedIndustryBreadthProvider(
            payload.get("requested_codes") or [],
            trade_date=str(payload.get("trade_date") or ""),
            cutoff=str(payload.get("cutoff") or ""),
            decision_time=str(payload.get("decision_time") or ""),
            transport=transport,
            clock=ReplayClock(leaves),
            schema_audit_only=payload.get("schema_audit_only") is True,
        )
        replayed = provider.collect_records()
        if transport.leaves:
            errors.append("unused_replay_requests")
        if replayed != payload.get("records"):
            errors.append("records_replay_mismatch")
        replayed_request_tree = json.loads(json.dumps(provider.request_tree))
        persisted_request_tree = json.loads(json.dumps(payload.get("request_tree")))
        if replayed_request_tree != persisted_request_tree:
            errors.append("request_tree_replay_mismatch")
    except Exception as exc:
        errors.append(f"replay_failed:{type(exc).__name__}:{exc}")
    verified = not errors and bool(expected_file_sha256)
    replay_hash = stable_hash({
        "evidence_hash": payload.get("evidence_hash"),
        "records": replayed,
        "errors": errors,
    })
    return {
        "status": (
            "DERIVED_INDUSTRY_BREADTH_EVIDENCE_VERIFIED"
            if verified
            else "DERIVED_INDUSTRY_BREADTH_EVIDENCE_INVALID"
        ),
        "evidence_integrity_verified": verified,
        "provider_contract_complete": bool(
            verified and payload.get("provider_contract_complete") is True
        ),
        "qualification_eligible": False,
        "evidence_hash": str(payload.get("evidence_hash") or ""),
        "replay_hash": replay_hash,
        "network_requests_recomputed": (
            len(request_leaves(payload.get("request_tree") or {}))
            if isinstance(payload.get("request_tree"), Mapping)
            else 0
        ),
        "errors": errors,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _normalize_codes(codes: Iterable[str]) -> tuple[str, ...]:
    values = tuple(sorted({str(code).strip().zfill(6) for code in codes}))
    if any(len(code) != 6 or not code.isdigit() for code in values):
        raise MarketSourceContractError("MARKET_SOURCE_CODE_INVALID")
    return values


def _eastmoney_web_code(code: str) -> str:
    return ("SH" if code.startswith(("6", "9")) else "SZ") + code


def _board_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("BK"):
        text = text[2:]
    return "BK" + text.zfill(4) if text.isdigit() else ""


def _canonical_pairs(params: Mapping[str, Any]) -> list[tuple[str, str]]:
    return sorted((str(key), str(value)) for key, value in params.items())


def _as_cn(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else parse_cn_datetime(value)
    if parsed is None:
        raise MarketSourceContractError("INDUSTRY_TIME_INVALID")
    return parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed.astimezone(CN_TZ)


def _json_payload(response: Mapping[str, Any]) -> dict[str, Any]:
    try:
        raw = base64.b64decode(response["content_base64"])
        decoded = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
        value = json.loads(decoded.decode("utf-8"))
    except (
        KeyError,
        ValueError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise MarketSourceContractError("INDUSTRY_RESPONSE_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise MarketSourceContractError("INDUSTRY_RESPONSE_JSON_INVALID")
    return value


def _strict_int(value: Any, code: str) -> int:
    if type(value) is bool:
        raise MarketSourceContractError(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketSourceContractError(code) from exc
    if str(value).strip() not in {str(parsed), f"{parsed}.0"}:
        raise MarketSourceContractError(code)
    return parsed


def _number_or_none(value: Any) -> float | None:
    if value in (None, "", "-") or type(value) is bool:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp(value: Any) -> datetime | None:
    if value in (None, "", "-"):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=CN_TZ)
    except (TypeError, ValueError, OSError):
        return parse_cn_datetime(value)
