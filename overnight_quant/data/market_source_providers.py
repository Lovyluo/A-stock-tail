from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import math
import socket
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash


FIXED_CODES = ("000001", "000333", "600000", "600519", "601318")
EASTMONEY_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EASTMONEY_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_FUND_FLOW_URL = (
    "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
)
MARKET_BENCHMARK_SECID = "1.000001"
MARKET_STOCK_POOL_VERSION = "eastmoney_index_breadth_sh_sz_bj_v2026-09-21"
INDUSTRY_CLASSIFICATION_VERSION = "eastmoney_industry_m90t2_v1"
EVIDENCE_SCHEMA_VERSION = "market_source_evidence_v2"
LEGACY_EVIDENCE_SCHEMA_VERSION = "market_source_evidence_v1"
VERIFIER_CONTRACT_VERSION = "market_source_verifier_v2"
LEGACY_VERIFIER_CONTRACT_VERSION = "market_source_verifier_v1"
DEFAULT_BATCH_DEADLINE_MS = 10_000

SOURCE_IDENTITIES = {
    "market_breadth": (
        "eastmoney",
        "direct_http",
        "push2_index_breadth+sse_index_v2026-09-21",
    ),
    "industry_snapshot": (
        "eastmoney",
        "direct_http",
        "push2_stock_industry+board_breadth_v2026-09-23",
    ),
    "fund_flow": (
        "eastmoney",
        "direct_http",
        "push2_fflow_kline_v2026-09-18",
    ),
}

PROVIDER_KEYS = {
    "market_breadth": (
        "market_source_providers.EastmoneyMarketSourceProviders."
        "collect_market_breadth_records"
    ),
    "industry_snapshot": (
        "market_source_providers.EastmoneyMarketSourceProviders."
        "collect_industry_records"
    ),
    "fund_flow": (
        "market_source_providers.EastmoneyMarketSourceProviders."
        "collect_fund_flow_records"
    ),
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)
_ALLOWED_HOSTS = {"push2.eastmoney.com"}


class MarketSourceContractError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True)
class MarketHttpResponse:
    content: bytes
    status_code: int
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketProviderBatch:
    capability: str
    records: tuple[dict[str, Any], ...]
    responses: tuple[dict[str, Any], ...]
    status: str = "AVAILABLE"


class MarketTransport(Protocol):
    request_count: int

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> MarketHttpResponse: ...


class MarketUrllibTransport:
    """Single-attempt transport. The validation CLI runs it in a killable worker."""

    def __init__(self) -> None:
        self.request_count = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> MarketHttpResponse:
        self.request_count += 1
        query = urlencode(_canonical_pairs(params))
        request = Request(
            url + ("?" + query if query else ""),
            headers=dict(headers),
            method=method,
        )
        try:
            with urlopen(request, timeout=float(timeout_seconds)) as response:
                return MarketHttpResponse(
                    response.read(),
                    int(response.status),
                    str(response.geturl()),
                    dict(response.headers.items()),
                )
        except (TimeoutError, socket.timeout) as exc:
            raise MarketSourceContractError("MARKET_SOURCE_TIMEOUT") from exc
        except HTTPError as exc:
            raise MarketSourceContractError(
                f"MARKET_SOURCE_HTTP_{int(exc.code)}"
            ) from exc
        except (URLError, OSError) as exc:
            raise MarketSourceContractError("MARKET_SOURCE_REQUEST_FAILED") from exc


class EastmoneyMarketSourceProviders:
    def __init__(
        self,
        codes: Iterable[str],
        *,
        trade_date: str,
        feature_cutoff: str | datetime,
        collection_deadline: str | datetime,
        transport: MarketTransport,
        clock: Callable[[], datetime],
        timeout_seconds: float = 2.0,
    ) -> None:
        self.codes = _normalize_codes(codes)
        if self.codes != FIXED_CODES:
            raise MarketSourceContractError("MARKET_SOURCE_FIXED_CODES_REQUIRED")
        self.trade_date = str(trade_date)
        self.feature_cutoff = _as_cn(feature_cutoff)
        self.collection_deadline = _as_cn(collection_deadline)
        if self.feature_cutoff.date().isoformat() != self.trade_date:
            raise MarketSourceContractError("MARKET_SOURCE_TRADE_DATE_MISMATCH")
        if self.collection_deadline <= self.feature_cutoff:
            raise MarketSourceContractError("MARKET_SOURCE_DEADLINE_INVALID")
        if not hasattr(transport, "request") or not callable(clock):
            raise MarketSourceContractError("MARKET_SOURCE_DEPENDENCY_INVALID")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise MarketSourceContractError("MARKET_SOURCE_TIMEOUT_INVALID")
        self.transport = transport
        self.clock = clock
        self.timeout_seconds = float(timeout_seconds)

    def collect_market_breadth_records(self) -> list[dict[str, Any]]:
        return list(self.collect_market_breadth_batch().records)

    def collect_market_breadth_batch(self) -> MarketProviderBatch:
        breadth = self._request(
            "market_breadth",
            EASTMONEY_ULIST_URL,
            {
                "fltt": "2",
                "invt": "2",
                "secids": "1.000001,0.399001,0.899050",
                "fields": "f3,f12,f14,f104,f105,f106,f124",
            },
        )
        index = self._request(
            "market_breadth",
            EASTMONEY_STOCK_URL,
            {
                "secid": MARKET_BENCHMARK_SECID,
                "fltt": "2",
                "invt": "2",
                "fields": "f3,f57,f58,f86",
            },
        )
        breadth_payload = _json_payload(breadth["raw_bytes"])
        rows = (breadth_payload.get("data") or {}).get("diff") or []
        expected_index_codes = {"000001", "399001", "899050"}
        rows_by_code = {
            str(item.get("f12") or "").zfill(6): item
            for item in rows
        }
        if set(rows_by_code) != expected_index_codes:
            raise MarketSourceContractError("MARKET_STOCK_POOL_INCOMPLETE")
        up_count = down_count = flat_count = invalid_count = 0
        event_times: list[datetime] = []
        index_breadth: dict[str, dict[str, Any]] = {}
        for code in sorted(expected_index_codes):
            item = rows_by_code[code]
            item_up = _strict_int(item.get("f104"), "MARKET_UP_COUNT_INVALID")
            item_down = _strict_int(item.get("f105"), "MARKET_DOWN_COUNT_INVALID")
            item_flat = _strict_int(item.get("f106"), "MARKET_FLAT_COUNT_INVALID")
            item_total = item_up + item_down + item_flat
            if item_total <= 0:
                raise MarketSourceContractError("MARKET_STOCK_POOL_INCOMPLETE")
            up_count += item_up
            down_count += item_down
            flat_count += item_flat
            timestamp = _timestamp(item.get("f124"))
            if timestamp is None:
                raise MarketSourceContractError("MARKET_STOCK_EVENT_TIME_MISSING")
            event_times.append(timestamp)
            index_breadth[code] = {
                "name": str(item.get("f14") or ""),
                "up_count": item_up,
                "down_count": item_down,
                "flat_count": item_flat,
                "total_count": item_total,
                "raw_event_time": timestamp.isoformat(),
            }
        valid_count = up_count + down_count + flat_count
        total_count = valid_count + invalid_count
        if valid_count <= 0:
            raise MarketSourceContractError("MARKET_BREADTH_COUNTS_INVALID")
        index_data = (_json_payload(index["raw_bytes"]).get("data") or {})
        index_change_pct = _required_number(
            index_data.get("f3"), "MARKET_INDEX_CHANGE_INVALID"
        )
        index_event = _timestamp(index_data.get("f86"))
        if index_event is None:
            raise MarketSourceContractError("MARKET_INDEX_EVENT_TIME_MISSING")
        event_times.append(index_event)
        latest_event = max(event_times)
        event_label = _minute_event_label(
            latest_event,
            self.feature_cutoff,
            "MARKET_EVENT_AFTER_CUTOFF",
        )
        payload = {
            "index_change_pct": index_change_pct,
            "breadth_ratio": up_count / valid_count,
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "invalid_count": invalid_count,
            "valid_count": valid_count,
            "total_count": total_count,
            "stock_pool_version": MARKET_STOCK_POOL_VERSION,
            "stock_pool_scope": "sse+szse+bse_index_breadth_counts",
            "index_breadth": index_breadth,
            "raw_latest_event_time": latest_event.isoformat(),
            "raw_benchmark_event_time": index_event.isoformat(),
            "event_time_granularity": "minute",
            "benchmark_secid": MARKET_BENCHMARK_SECID,
            "benchmark_name": str(index_data.get("f58") or ""),
            "field_units": {
                "index_change_pct": "percent",
                "breadth_ratio": "ratio_0_1",
                "counts": "stock_count",
            },
        }
        record = self._record(
            "market_breadth",
            "market",
            event_label,
            max(_as_cn(breadth["observed_at"]), _as_cn(index["observed_at"])),
            max(_as_cn(breadth["available_at"]), _as_cn(index["available_at"])),
            stable_hash([breadth["request_hash"], index["request_hash"]]),
            stable_hash([breadth["raw_hash"], index["raw_hash"]]),
            payload,
        )
        return MarketProviderBatch(
            "market_breadth", (record,), (breadth, index)
        )

    def collect_industry_records(self) -> list[dict[str, Any]]:
        return list(self.collect_industry_batch().records)

    def collect_industry_batch(self) -> MarketProviderBatch:
        board_response = self._request(
            "industry_snapshot",
            EASTMONEY_CLIST_URL,
            {
                "pn": "1", "pz": "500", "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fs": "m:90+t:2",
                "fields": "f3,f12,f14,f104,f105,f106,f124",
            },
        )
        board_data = (_json_payload(board_response["raw_bytes"]).get("data") or {})
        board_rows = board_data.get("diff") or []
        by_name: dict[str, dict[str, Any]] = {}
        for item in board_rows:
            name = str(item.get("f14") or "").strip()
            if not name or name in by_name:
                raise MarketSourceContractError("INDUSTRY_BOARD_IDENTITY_INVALID")
            by_name[name] = item
        records: list[dict[str, Any]] = []
        responses = [board_response]
        for code in self.codes:
            mapping = self._request(
                "industry_snapshot",
                EASTMONEY_STOCK_URL,
                {
                    "secid": _secid(code), "fltt": "2", "invt": "2",
                    "fields": "f57,f58,f127,f86",
                },
            )
            responses.append(mapping)
            item = (_json_payload(mapping["raw_bytes"]).get("data") or {})
            if str(item.get("f57") or "").zfill(6) != code:
                raise MarketSourceContractError(f"INDUSTRY_CODE_MISMATCH:{code}")
            industry_name = str(item.get("f127") or "").strip()
            board = by_name.get(industry_name)
            if board is None:
                raise MarketSourceContractError(f"INDUSTRY_MAPPING_MISSING:{code}")
            up = _strict_int(board.get("f104"), "INDUSTRY_UP_COUNT_INVALID")
            down = _strict_int(board.get("f105"), "INDUSTRY_DOWN_COUNT_INVALID")
            flat = _strict_int(board.get("f106"), "INDUSTRY_FLAT_COUNT_INVALID")
            total = up + down + flat
            if total <= 0:
                raise MarketSourceContractError(f"INDUSTRY_BREADTH_INVALID:{code}")
            change = _required_number(board.get("f3"), "INDUSTRY_CHANGE_INVALID")
            board_event = _timestamp(board.get("f124"))
            mapping_event = _timestamp(item.get("f86"))
            if board_event is None or mapping_event is None:
                raise MarketSourceContractError(f"INDUSTRY_EVENT_TIME_MISSING:{code}")
            event_time = _minute_event_label(
                board_event,
                self.feature_cutoff,
                f"INDUSTRY_EVENT_AFTER_CUTOFF:{code}",
            )
            if mapping_event > self.collection_deadline:
                raise MarketSourceContractError(
                    f"INDUSTRY_MAPPING_AFTER_DEADLINE:{code}"
                )
            records.append(self._record(
                "industry_snapshot",
                "industry",
                event_time,
                max(_as_cn(board_response["observed_at"]), _as_cn(mapping["observed_at"])),
                max(_as_cn(board_response["available_at"]), _as_cn(mapping["available_at"])),
                stable_hash([board_response["request_hash"], mapping["request_hash"]]),
                stable_hash([board_response["raw_hash"], mapping["raw_hash"]]),
                {
                    "code": code,
                    "name": industry_name,
                    "industry": industry_name,
                    "industry_name": industry_name,
                    "board_code": str(board.get("f12") or ""),
                    "classification_version": INDUSTRY_CLASSIFICATION_VERSION,
                    "change_pct": change,
                    "breadth_ratio": up / total,
                    "raw_board_event_time": board_event.isoformat(),
                    "raw_mapping_event_time": mapping_event.isoformat(),
                    "event_time_granularity": "minute",
                    "mapping_time_semantics": "static_classification_observed_before_deadline",
                    "up_count": up,
                    "down_count": down,
                    "flat_count": flat,
                    "total_count": total,
                    "field_units": {
                        "change_pct": "percent",
                        "breadth_ratio": "ratio_0_1",
                        "counts": "stock_count",
                    },
                },
            ))
        return MarketProviderBatch(
            "industry_snapshot", tuple(records), tuple(responses)
        )

    def collect_fund_flow_records(self) -> list[dict[str, Any]]:
        return list(self.collect_fund_flow_batch().records)

    def collect_fund_flow_batch(self) -> MarketProviderBatch:
        records: list[dict[str, Any]] = []
        responses = []
        for code in self.codes:
            response = self._request(
                "fund_flow",
                EASTMONEY_FUND_FLOW_URL,
                {
                    "secid": _secid(code), "klt": "1",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                },
            )
            responses.append(response)
            lines = ((_json_payload(response["raw_bytes"]).get("data") or {}).get("klines") or [])
            eligible = []
            for line in lines:
                parts = str(line).split(",")
                if len(parts) < 6:
                    continue
                event = parse_cn_datetime(parts[0])
                if event is not None and event <= self.feature_cutoff:
                    eligible.append((event, parts))
            if not eligible:
                raise MarketSourceContractError(f"FUND_FLOW_MISSING:{code}")
            event, parts = max(eligible, key=lambda value: value[0])
            if event != self.feature_cutoff:
                raise MarketSourceContractError(f"FUND_FLOW_CUTOFF_MISSING:{code}")
            values = [_required_number(value, "FUND_FLOW_VALUE_INVALID") for value in parts[1:6]]
            records.append(self._record(
                "fund_flow", "fund_flow", event,
                response["observed_at"], response["available_at"],
                response["request_hash"], response["raw_hash"],
                {
                    "code": code,
                    "main_net": values[0],
                    "small_net": values[1],
                    "mid_net": values[2],
                    "large_net": values[3],
                    "super_net": values[4],
                    "amount_unit": "CNY",
                    "value_semantics": "minute_interval_net_flow",
                    "aggregation_semantics": "incremental",
                    "eligible_for_hard_gate": True,
                    "is_proxy": False,
                    "field_units": {
                        "main_net": "CNY", "small_net": "CNY",
                        "mid_net": "CNY", "large_net": "CNY",
                        "super_net": "CNY",
                    },
                },
            ))
        return MarketProviderBatch("fund_flow", tuple(records), tuple(responses))

    def _request(
        self,
        capability: str,
        url: str,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        observed = _as_cn(self.clock())
        if observed >= self.collection_deadline:
            raise MarketSourceContractError("MARKET_SOURCE_COLLECTION_DEADLINE_EXCEEDED")
        remaining = (self.collection_deadline - observed).total_seconds()
        timeout = min(self.timeout_seconds, remaining)
        response = self.transport.request(
            "GET", url, params=params, headers={"User-Agent": _UA},
            timeout_seconds=timeout,
        )
        available = _as_cn(self.clock())
        if available > self.collection_deadline:
            raise MarketSourceContractError("MARKET_SOURCE_COLLECTION_DEADLINE_EXCEEDED")
        if response.status_code != 200:
            raise MarketSourceContractError(f"MARKET_SOURCE_HTTP_{response.status_code}")
        parsed = urlparse(response.url)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
            raise MarketSourceContractError("MARKET_SOURCE_RESPONSE_URL_INVALID")
        raw = bytes(response.content)
        return {
            "capability": capability,
            "method": "GET",
            "url": url,
            "params": _canonical_pairs(params),
            "observed_at": observed.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
            "request_hash": stable_hash({"method": "GET", "url": url, "params": _canonical_pairs(params)}),
            "raw_hash": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": raw,
            "http_status_code": 200,
            "response_url": response.url,
        }

    def _record(
        self,
        capability: str,
        data_type: str,
        event_time: str | datetime,
        observed_at: str | datetime,
        available_at: str | datetime,
        request_hash: str,
        raw_hash: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        origin_source, adapter, source_version = SOURCE_IDENTITIES[capability]
        event = _as_cn(event_time)
        observed = _as_cn(observed_at)
        available = _as_cn(available_at)
        if event > self.feature_cutoff or available > self.collection_deadline:
            raise MarketSourceContractError("MARKET_SOURCE_TEMPORAL_CONTRACT_FAILED")
        if event > observed or observed > available:
            raise MarketSourceContractError("MARKET_SOURCE_TIME_ORDER_INVALID")
        return {
            "capability": capability,
            "data_type": data_type,
            "origin_source": origin_source,
            "adapter": adapter,
            "source": "eastmoney_direct",
            "source_version": source_version,
            "event_time": event.isoformat(),
            "observed_at": observed.isoformat(),
            "available_at": available.isoformat(),
            "decision_cutoff": self.feature_cutoff.isoformat(),
            "request_hash": request_hash,
            "raw_hash": raw_hash,
            "payload": dict(payload),
        }


def validate_market_source_records(
    capability: str,
    records: Iterable[Mapping[str, Any]],
    *,
    codes: Iterable[str] = FIXED_CODES,
) -> dict[str, Any]:
    rows = [dict(row) for row in records]
    expected_codes = set(_normalize_codes(codes))
    errors: list[str] = []
    identity = SOURCE_IDENTITIES.get(capability)
    if identity is None:
        errors.append("capability_unknown")
    for row in rows:
        if row.get("capability") != capability:
            errors.append("capability_mismatch")
        if identity and tuple(row.get(key) for key in ("origin_source", "adapter", "source_version")) != identity:
            errors.append("source_identity_mismatch")
        for field in ("event_time", "observed_at", "available_at", "decision_cutoff"):
            if parse_cn_datetime(row.get(field)) is None:
                errors.append(f"time_missing:{field}")
        if not _sha(row.get("request_hash")) or not _sha(row.get("raw_hash")):
            errors.append("hash_invalid")
    if capability == "market_breadth":
        if len(rows) != 1:
            errors.append("market_record_count_invalid")
        elif not _valid_market_record(rows[0]):
            errors.append("market_payload_invalid")
    elif capability in {"industry_snapshot", "fund_flow"}:
        row_codes = [str((row.get("payload") or {}).get("code") or "").zfill(6) for row in rows]
        if len(row_codes) != len(set(row_codes)) or set(row_codes) != expected_codes:
            errors.append("stock_coverage_invalid")
        if capability == "industry_snapshot":
            classifications = {(row.get("payload") or {}).get("classification_version") for row in rows}
            if classifications != {INDUSTRY_CLASSIFICATION_VERSION}:
                errors.append("industry_classification_mismatch")
            if any(not _valid_industry_record(row) for row in rows):
                errors.append("industry_payload_invalid")
        else:
            if any(not _valid_fund_payload(row.get("payload") or {}) for row in rows):
                errors.append("fund_flow_payload_invalid")
    else:
        errors.append("capability_unknown")
    return {
        "status": "MARKET_SOURCE_RECORDS_VALID" if not errors else "MARKET_SOURCE_RECORDS_INVALID",
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "records_hash": stable_hash(sorted(rows, key=_record_sort_key)),
    }


def compute_market_source_verifier_contract_hash() -> str:
    return stable_hash({
        "version": VERIFIER_CONTRACT_VERSION,
        "source_identities": SOURCE_IDENTITIES,
        "provider_keys": PROVIDER_KEYS,
        "fixed_codes": FIXED_CODES,
        "market_stock_pool_version": MARKET_STOCK_POOL_VERSION,
        "industry_classification_version": INDUSTRY_CLASSIFICATION_VERSION,
    })


def compute_legacy_market_source_verifier_contract_hash() -> str:
    legacy_identities = {
        **SOURCE_IDENTITIES,
        "industry_snapshot": (
            "eastmoney",
            "direct_http",
            "push2_stock_industry+board_breadth_v2026-09-18",
        ),
    }
    return stable_hash({
        "version": LEGACY_VERIFIER_CONTRACT_VERSION,
        "source_identities": legacy_identities,
        "provider_keys": PROVIDER_KEYS,
        "fixed_codes": FIXED_CODES,
        "market_stock_pool_version": MARKET_STOCK_POOL_VERSION,
        "industry_classification_version": INDUSTRY_CLASSIFICATION_VERSION,
    })


def _valid_market_record(record: Mapping[str, Any]) -> bool:
    payload = record.get("payload") or {}
    counts = [payload.get(key) for key in ("up_count", "down_count", "flat_count", "invalid_count", "valid_count", "total_count")]
    if any(type(value) is not int or value < 0 for value in counts):
        return False
    up, down, flat, invalid, valid, total = counts
    if not (
        valid == up + down + flat
        and total == valid + invalid
        and valid > 0
        and payload.get("stock_pool_version") == MARKET_STOCK_POOL_VERSION
        and payload.get("stock_pool_scope")
        == "sse+szse+bse_index_breadth_counts"
        and payload.get("event_time_granularity") == "minute"
        and payload.get("benchmark_secid") == MARKET_BENCHMARK_SECID
        and _optional_number(payload.get("index_change_pct")) is not None
        and _ratio(payload.get("breadth_ratio"))
        and abs(float(payload["breadth_ratio"]) - up / valid) <= 1e-12
    ):
        return False
    details = payload.get("index_breadth")
    if not isinstance(details, Mapping) or set(details) != {
        "000001", "399001", "899050"
    }:
        return False
    detail_sums = {"up_count": 0, "down_count": 0, "flat_count": 0}
    raw_times: list[datetime] = []
    for code in ("000001", "399001", "899050"):
        detail = details.get(code)
        if not isinstance(detail, Mapping):
            return False
        detail_counts = [
            detail.get(key)
            for key in ("up_count", "down_count", "flat_count", "total_count")
        ]
        if any(type(value) is not int or value < 0 for value in detail_counts):
            return False
        detail_up, detail_down, detail_flat, detail_total = detail_counts
        if detail_total != detail_up + detail_down + detail_flat:
            return False
        raw_event = parse_cn_datetime(detail.get("raw_event_time"))
        if raw_event is None:
            return False
        raw_times.append(raw_event)
        detail_sums["up_count"] += detail_up
        detail_sums["down_count"] += detail_down
        detail_sums["flat_count"] += detail_flat
    if detail_sums != {
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
    }:
        return False
    benchmark_event = parse_cn_datetime(payload.get("raw_benchmark_event_time"))
    raw_latest = parse_cn_datetime(payload.get("raw_latest_event_time"))
    cutoff = parse_cn_datetime(record.get("decision_cutoff"))
    formal_event = parse_cn_datetime(record.get("event_time"))
    if None in (benchmark_event, raw_latest, cutoff, formal_event):
        return False
    if raw_latest != max([*raw_times, benchmark_event]):
        return False
    try:
        expected_event = _minute_event_label(
            raw_latest, cutoff, "MARKET_EVENT_AFTER_CUTOFF"
        )
    except MarketSourceContractError:
        return False
    return formal_event == expected_event


def _valid_industry_record(record: Mapping[str, Any]) -> bool:
    payload = record.get("payload") or {}
    up = payload.get("up_count")
    down = payload.get("down_count")
    flat = payload.get("flat_count")
    total = payload.get("total_count")
    if not (
        bool(str(payload.get("industry_name") or "").strip())
        and bool(str(payload.get("board_code") or "").strip())
        and all(type(value) is int and value >= 0 for value in (up, down, flat, total))
        and total == up + down + flat
        and total > 0
        and _ratio(payload.get("breadth_ratio"))
        and abs(float(payload["breadth_ratio"]) - up / total) <= 1e-12
        and _optional_number(payload.get("change_pct")) is not None
        and payload.get("event_time_granularity") == "minute"
        and payload.get("mapping_time_semantics")
        == "static_classification_observed_before_deadline"
    ):
        return False
    board_event = parse_cn_datetime(payload.get("raw_board_event_time"))
    mapping_event = parse_cn_datetime(payload.get("raw_mapping_event_time"))
    formal_event = parse_cn_datetime(record.get("event_time"))
    cutoff = parse_cn_datetime(record.get("decision_cutoff"))
    available = parse_cn_datetime(record.get("available_at"))
    if None in (board_event, mapping_event, formal_event, cutoff, available):
        return False
    try:
        expected_event = _minute_event_label(
            board_event, cutoff, "INDUSTRY_EVENT_AFTER_CUTOFF"
        )
    except MarketSourceContractError:
        return False
    return (
        formal_event == expected_event
        and mapping_event <= cutoff + timedelta(seconds=65)
    )


def _valid_fund_payload(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("amount_unit") == "CNY"
        and payload.get("value_semantics") == "minute_interval_net_flow"
        and payload.get("aggregation_semantics") == "incremental"
        and payload.get("is_proxy") is False
        and payload.get("eligible_for_hard_gate") is True
        and any(_optional_number(payload.get(field)) is not None for field in ("main_net", "large_net", "super_net"))
    )


def _normalize_codes(values: Iterable[str]) -> tuple[str, ...]:
    codes = [str(value).strip().zfill(6) for value in values]
    if any(len(code) != 6 or not code.isdigit() for code in codes):
        raise MarketSourceContractError("MARKET_SOURCE_CODE_INVALID")
    if len(codes) != len(set(codes)):
        raise MarketSourceContractError("MARKET_SOURCE_CODE_DUPLICATE")
    return tuple(sorted(codes))


def _secid(code: str) -> str:
    return ("1." if code.startswith("6") else "0.") + code


def _canonical_pairs(values: Mapping[str, Any]) -> list[tuple[str, str]]:
    return sorted((str(key), str(value)) for key, value in values.items())


def _json_payload(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketSourceContractError("MARKET_SOURCE_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise MarketSourceContractError("MARKET_SOURCE_JSON_INVALID")
    return value


def _as_cn(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else parse_cn_datetime(value)
    if parsed is None:
        raise MarketSourceContractError("MARKET_SOURCE_TIME_INVALID")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def _timestamp(value: Any) -> datetime | None:
    if value in (None, "", "-"):
        return None
    try:
        number = int(float(value))
        return datetime.fromtimestamp(number, tz=CN_TZ)
    except (TypeError, ValueError, OverflowError):
        return parse_cn_datetime(value)


def _minute_event_label(
    value: datetime,
    cutoff: datetime,
    error: str,
) -> datetime:
    """Normalize second-level Eastmoney push2 updates to the decision minute."""
    label = value.replace(second=0, microsecond=0)
    if label.date() != cutoff.date() or label > cutoff:
        raise MarketSourceContractError(error)
    return label


def _optional_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _required_number(value: Any, error: str) -> float:
    result = _optional_number(value)
    if result is None:
        raise MarketSourceContractError(error)
    return result


def _strict_int(value: Any, error: str) -> int:
    if isinstance(value, bool):
        raise MarketSourceContractError(error)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketSourceContractError(error) from exc
    if str(value).strip() not in {str(number), f"{number}.0"}:
        raise MarketSourceContractError(error)
    return number


def _ratio(value: Any) -> bool:
    result = _optional_number(value)
    return result is not None and 0.0 <= result <= 1.0


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _record_sort_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    payload = row.get("payload") or {}
    return (
        str(row.get("capability") or ""),
        str(payload.get("code") or ""),
        str(row.get("event_time") or ""),
        str(row.get("raw_hash") or ""),
    )
