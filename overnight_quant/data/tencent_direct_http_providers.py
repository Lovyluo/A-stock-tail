from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
import re
import socket
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash


TENCENT_QUOTE_ENDPOINT = "https://qt.gtimg.cn/q="
TENCENT_ORIGIN_SOURCE = "tencent"
TENCENT_ADAPTER = "direct_http"
TENCENT_SOURCE_VERSION = "qt.gtimg.cn~88_fields_v2026-07-30"
TENCENT_RESPONSE_ENCODING = "gbk"
TENCENT_EXPECTED_FIELD_COUNT = 88
TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2 = (
    "tencent_provider_network_evidence_v2"
)
TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3 = (
    "tencent_provider_network_evidence_v3"
)
TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION = (
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3
)

TENCENT_QUOTE_PROVIDER_KEY = (
    "tencent_direct_http_providers.TencentDirectHttpProviders."
    "collect_quote_records"
)
TENCENT_VALUATION_PROVIDER_KEY = (
    "tencent_direct_http_providers.TencentDirectHttpProviders."
    "collect_valuation_records"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)

_TENCENT_LINE = re.compile(r'^v_(sh|sz|bj)(\d{6})="(.*)"$', re.DOTALL)
_TENCENT_TIMESTAMP = re.compile(r"^\d{14}$")


class TencentProviderContractError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True)
class TencentHttpResponse:
    content: bytes
    status_code: int
    url: str


@dataclass(frozen=True)
class TencentProviderBatch:
    capability: str
    records: tuple[dict[str, Any], ...]
    request_url: str
    request_hash: str
    raw_response_bytes: bytes
    raw_hash: str
    observed_at: str
    available_at: str
    http_status_code: int
    response_url: str


class TencentTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> TencentHttpResponse: ...


class TencentUrllibTransport:
    """Single-attempt Tencent-only transport for explicit network validation."""

    def __init__(self) -> None:
        self.request_count = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> TencentHttpResponse:
        self.request_count += 1
        if method != "GET":
            raise TencentProviderContractError("TENCENT_REQUEST_METHOD_INVALID")
        request = Request(url, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=float(timeout_seconds)) as response:
                return TencentHttpResponse(
                    content=response.read(),
                    status_code=int(response.status),
                    url=str(response.geturl()),
                )
        except (TimeoutError, socket.timeout) as exc:
            raise TencentProviderContractError(
                "TENCENT_REQUEST_TIMEOUT"
            ) from exc
        except HTTPError as exc:
            raise TencentProviderContractError(
                "TENCENT_HTTP_STATUS_INVALID"
            ) from exc
        except (URLError, OSError) as exc:
            raise TencentProviderContractError(
                "TENCENT_REQUEST_FAILED"
            ) from exc


class TencentDirectHttpProviders:
    def __init__(
        self,
        codes: Iterable[str],
        *,
        transport: TencentTransport,
        clock: Callable[[], datetime],
        timeout_seconds: float = 5.0,
    ) -> None:
        self.codes = _normalize_request_codes(codes)
        if not hasattr(transport, "request"):
            raise TencentProviderContractError(
                "TENCENT_TRANSPORT_CONTRACT_INVALID"
            )
        if not callable(clock):
            raise TencentProviderContractError("TENCENT_CLOCK_INVALID")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise TencentProviderContractError("TENCENT_TIMEOUT_INVALID")
        self.transport = transport
        self.clock = clock
        self.timeout_seconds = timeout

    def collect_quote_records(self) -> list[dict[str, Any]]:
        return list(self.collect_quote_batch().records)

    def collect_quote_batch(self) -> TencentProviderBatch:
        response_rows, context = self._fetch()
        records = [
            _build_record(
                capability="quote",
                event_time=event_time,
                context=context,
                payload=_quote_payload(code, values),
            )
            for code, values, event_time in response_rows
        ]
        return _build_batch("quote", records, context)

    def collect_valuation_records(self) -> list[dict[str, Any]]:
        return list(self.collect_valuation_batch().records)

    def collect_valuation_batch(self) -> TencentProviderBatch:
        response_rows, context = self._fetch()
        records = [
            _build_record(
                capability="valuation",
                event_time=event_time,
                context=context,
                payload=_valuation_payload(code, values),
            )
            for code, values, event_time in response_rows
        ]
        return _build_batch("valuation", records, context)

    def _fetch(
        self,
    ) -> tuple[
        list[tuple[str, list[str], datetime]],
        dict[str, Any],
    ]:
        request_hash = compute_tencent_request_hash(self.codes)
        request_url = build_tencent_request_url(self.codes)
        observed_at = _clock_now(self.clock)
        try:
            response = self.transport.request(
                "GET",
                request_url,
                headers={"User-Agent": USER_AGENT},
                timeout_seconds=self.timeout_seconds,
            )
        except TencentProviderContractError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise TencentProviderContractError(
                "TENCENT_REQUEST_TIMEOUT"
            ) from exc
        except Exception as exc:
            raise TencentProviderContractError(
                "TENCENT_REQUEST_FAILED"
            ) from exc
        available_at = _clock_now(self.clock)
        if observed_at > available_at:
            raise TencentProviderContractError("TENCENT_TIME_ORDER_INVALID")
        _validate_response_identity(response, request_url)
        raw_content = response.content
        if not isinstance(raw_content, bytes) or not raw_content:
            raise TencentProviderContractError("TENCENT_RESPONSE_EMPTY")
        raw_hash = hashlib.sha256(raw_content).hexdigest()
        rows = parse_tencent_response_bytes(raw_content, self.codes)
        for _code, _values, event_time in rows:
            if event_time > observed_at:
                raise TencentProviderContractError(
                    "TENCENT_SOURCE_TIME_AFTER_OBSERVED_AT"
                )
        context = {
            "observed_at": observed_at,
            "available_at": available_at,
            "request_hash": request_hash,
            "raw_hash": raw_hash,
            "request_url": request_url,
            "raw_response_bytes": raw_content,
            "http_status_code": response.status_code,
            "response_url": response.url,
        }
        return rows, context


def _build_batch(
    capability: str,
    records: list[dict[str, Any]],
    context: Mapping[str, Any],
) -> TencentProviderBatch:
    return TencentProviderBatch(
        capability=capability,
        records=tuple(records),
        request_url=str(context["request_url"]),
        request_hash=str(context["request_hash"]),
        raw_response_bytes=bytes(context["raw_response_bytes"]),
        raw_hash=str(context["raw_hash"]),
        observed_at=context["observed_at"].isoformat(timespec="microseconds"),
        available_at=context["available_at"].isoformat(timespec="microseconds"),
        http_status_code=int(context["http_status_code"]),
        response_url=str(context["response_url"]),
    )


def _build_record(
    *,
    capability: str,
    event_time: datetime,
    context: Mapping[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "capability": capability,
        "origin_source": TENCENT_ORIGIN_SOURCE,
        "adapter": TENCENT_ADAPTER,
        "source_version": TENCENT_SOURCE_VERSION,
        "event_time": event_time.isoformat(timespec="seconds"),
        "observed_at": context["observed_at"].isoformat(
            timespec="microseconds"
        ),
        "available_at": context["available_at"].isoformat(
            timespec="microseconds"
        ),
        "request_hash": context["request_hash"],
        "raw_hash": context["raw_hash"],
        "payload": payload,
    }


def _quote_payload(code: str, values: list[str]) -> dict[str, Any]:
    bids = [
        {
            "level": level,
            "price": _optional_number(values[9 + (level - 1) * 2]),
            "volume": _optional_number(values[10 + (level - 1) * 2]),
        }
        for level in range(1, 6)
    ]
    asks = [
        {
            "level": level,
            "price": _optional_number(values[19 + (level - 1) * 2]),
            "volume": _optional_number(values[20 + (level - 1) * 2]),
        }
        for level in range(1, 6)
    ]
    return {
        "code": code,
        "name": _required_text(values[1], "name"),
        "price": _required_number(values[3], "price"),
        "prev_close": _required_number(values[4], "prev_close"),
        "open": _required_number(values[5], "open"),
        "high": _required_number(values[33], "high"),
        "low": _required_number(values[34], "low"),
        "change_pct": _required_number(values[32], "change_pct"),
        "volume": _required_number(values[36], "volume"),
        "amount": _required_number(values[37], "amount"),
        "turnover_pct": _required_number(values[38], "turnover_pct"),
        "limit_up": _required_number(values[47], "limit_up"),
        "limit_down": _required_number(values[48], "limit_down"),
        "order_book": {"bids": bids, "asks": asks},
        "source_field_count": len(values),
        "field_units": {
            "price": "CNY_per_share",
            "prev_close": "CNY_per_share",
            "open": "CNY_per_share",
            "high": "CNY_per_share",
            "low": "CNY_per_share",
            "change_pct": "percent",
            "volume": "lot",
            "amount": "CNY_10k",
            "turnover_pct": "percent",
            "limit_up": "CNY_per_share",
            "limit_down": "CNY_per_share",
            "order_book.price": "CNY_per_share",
            "order_book.volume": "lot",
        },
    }


def _valuation_payload(code: str, values: list[str]) -> dict[str, Any]:
    mapped = {
        "pe_ttm": _optional_number(values[39]),
        "market_cap": _optional_number(values[44]),
        "float_market_cap": _optional_number(values[45]),
        "pb": _optional_number(values[46]),
        "pe_static": _optional_number(values[52]),
    }
    return {
        "code": code,
        "name": _required_text(values[1], "name"),
        **mapped,
        "amplitude_pct": _optional_number(values[43]),
        "valuation_availability": {
            field: "available" if value is not None else "missing"
            for field, value in mapped.items()
        },
        "source_field_count": len(values),
        "field_indices": {
            "pe_ttm": 39,
            "amplitude_pct": 43,
            "market_cap": 44,
            "float_market_cap": 45,
            "pb": 46,
            "pe_static": 52,
        },
        "field_units": {
            "pe_ttm": "ratio",
            "market_cap": "CNY_100m",
            "float_market_cap": "CNY_100m",
            "pb": "ratio",
            "pe_static": "ratio",
            "amplitude_pct": "percent",
        },
    }


def build_tencent_payload(
    capability: str,
    code: str,
    values: list[str],
) -> dict[str, Any]:
    if capability == "quote":
        return _quote_payload(code, values)
    if capability == "valuation":
        return _valuation_payload(code, values)
    raise TencentProviderContractError("TENCENT_CAPABILITY_INVALID")


def build_tencent_request_url(codes: Iterable[str]) -> str:
    normalized = _normalize_request_codes(codes)
    return TENCENT_QUOTE_ENDPOINT + ",".join(
        _tencent_symbol(code) for code in normalized
    )


def compute_tencent_request_hash(codes: Iterable[str]) -> str:
    normalized = _normalize_request_codes(codes)
    symbols = [_tencent_symbol(code) for code in normalized]
    return stable_hash(
        {
            "method": "GET",
            "endpoint": TENCENT_QUOTE_ENDPOINT,
            "params": {"q": symbols},
            "response_encoding": TENCENT_RESPONSE_ENCODING,
        }
    )


def parse_tencent_response_bytes(
    raw_content: bytes,
    requested_codes: Iterable[str],
) -> list[tuple[str, list[str], datetime]]:
    normalized = _normalize_request_codes(requested_codes)
    if not isinstance(raw_content, bytes) or not raw_content:
        raise TencentProviderContractError("TENCENT_RESPONSE_EMPTY")
    try:
        text = raw_content.decode(
            TENCENT_RESPONSE_ENCODING,
            errors="strict",
        )
    except UnicodeDecodeError as exc:
        raise TencentProviderContractError(
            "TENCENT_RESPONSE_GBK_INVALID"
        ) from exc
    return _parse_response_rows(text, normalized)


def _parse_response_rows(
    text: str,
    requested_codes: tuple[str, ...],
) -> list[tuple[str, list[str], datetime]]:
    requested = set(requested_codes)
    parsed: dict[str, tuple[list[str], datetime]] = {}
    lines = [line.strip() for line in text.split(";") if line.strip()]
    if not lines:
        raise TencentProviderContractError("TENCENT_RESPONSE_EMPTY")
    for line in lines:
        match = _TENCENT_LINE.fullmatch(line)
        if match is None:
            raise TencentProviderContractError("TENCENT_RESPONSE_LINE_INVALID")
        market, key_code, payload = match.groups()
        if _market_prefix(key_code) != market:
            raise TencentProviderContractError(
                "TENCENT_RESPONSE_MARKET_CODE_MISMATCH"
            )
        values = payload.split("~")
        if len(values) != TENCENT_EXPECTED_FIELD_COUNT:
            raise TencentProviderContractError(
                "TENCENT_RESPONSE_FIELD_COUNT_INVALID"
            )
        value_code = values[2].strip()
        if value_code != key_code:
            raise TencentProviderContractError(
                "TENCENT_RESPONSE_CODE_MISMATCH"
            )
        if key_code not in requested:
            raise TencentProviderContractError(
                "TENCENT_RESPONSE_UNREQUESTED_CODE"
            )
        if key_code in parsed:
            raise TencentProviderContractError(
                "TENCENT_RESPONSE_DUPLICATE_CODE"
            )
        parsed[key_code] = (values, _parse_source_time(values[30]))
    missing = sorted(requested - set(parsed))
    if missing:
        raise TencentProviderContractError(
            "TENCENT_RESPONSE_COVERAGE_INCOMPLETE"
        )
    return [
        (code, parsed[code][0], parsed[code][1])
        for code in sorted(parsed)
    ]


def _parse_source_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if _TENCENT_TIMESTAMP.fullmatch(text) is None:
        raise TencentProviderContractError("TENCENT_SOURCE_TIME_INVALID")
    try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise TencentProviderContractError(
            "TENCENT_SOURCE_TIME_INVALID"
        ) from exc
    return parsed.replace(tzinfo=CN_TZ)


def _clock_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise TencentProviderContractError("TENCENT_CLOCK_INVALID")
    return value.astimezone(CN_TZ)


def _validate_response_identity(
    response: TencentHttpResponse,
    request_url: str,
) -> None:
    if not isinstance(response, TencentHttpResponse):
        raise TencentProviderContractError(
            "TENCENT_TRANSPORT_RESPONSE_INVALID"
        )
    if type(response.status_code) is not int or response.status_code != 200:
        raise TencentProviderContractError("TENCENT_HTTP_STATUS_INVALID")
    requested = urlparse(request_url)
    returned = urlparse(response.url)
    if (
        requested.scheme != "https"
        or requested.hostname != "qt.gtimg.cn"
        or returned.scheme != "https"
        or returned.hostname != "qt.gtimg.cn"
    ):
        raise TencentProviderContractError(
            "TENCENT_RESPONSE_SOURCE_INVALID"
        )


def _normalize_request_codes(codes: Iterable[str]) -> tuple[str, ...]:
    if isinstance(codes, (str, bytes)):
        raise TencentProviderContractError("TENCENT_REQUEST_CODES_INVALID")
    normalized = []
    seen = set()
    for raw in codes:
        text = str(raw or "").strip().lower()
        prefix = ""
        suffix = ""
        if text.startswith(("sh", "sz", "bj")):
            prefix, text = text[:2], text[2:]
        if "." in text:
            text, suffix = text.rsplit(".", 1)
        if not re.fullmatch(r"\d{6}", text):
            raise TencentProviderContractError(
                "TENCENT_REQUEST_CODES_INVALID"
            )
        expected_market = _market_prefix(text)
        if prefix and prefix != expected_market:
            raise TencentProviderContractError(
                "TENCENT_REQUEST_CODE_MARKET_MISMATCH"
            )
        if suffix and suffix != expected_market:
            raise TencentProviderContractError(
                "TENCENT_REQUEST_CODE_MARKET_MISMATCH"
            )
        if text in seen:
            raise TencentProviderContractError(
                "TENCENT_REQUEST_CODES_DUPLICATE"
            )
        seen.add(text)
        normalized.append(text)
    if not normalized:
        raise TencentProviderContractError("TENCENT_REQUEST_CODES_INVALID")
    return tuple(sorted(normalized))


def _tencent_symbol(code: str) -> str:
    return _market_prefix(code) + code


def _market_prefix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    raise TencentProviderContractError("TENCENT_REQUEST_CODES_INVALID")


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TencentProviderContractError(
            f"TENCENT_REQUIRED_FIELD_MISSING:{field}"
        )
    return text


def _required_number(value: Any, field: str) -> float:
    parsed = _optional_number(value)
    if parsed is None:
        raise TencentProviderContractError(
            f"TENCENT_REQUIRED_FIELD_INVALID:{field}"
        )
    return parsed


def _optional_number(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        parsed = float(text)
    except (TypeError, ValueError) as exc:
        raise TencentProviderContractError(
            "TENCENT_NUMERIC_FIELD_INVALID"
        ) from exc
    if not math.isfinite(parsed):
        raise TencentProviderContractError("TENCENT_NUMERIC_FIELD_INVALID")
    return parsed


__all__ = [
    "TENCENT_ADAPTER",
    "TENCENT_ORIGIN_SOURCE",
    "TENCENT_QUOTE_ENDPOINT",
    "TENCENT_QUOTE_PROVIDER_KEY",
    "TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2",
    "TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3",
    "TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION",
    "TENCENT_SOURCE_VERSION",
    "TENCENT_VALUATION_PROVIDER_KEY",
    "TencentDirectHttpProviders",
    "TencentHttpResponse",
    "TencentProviderBatch",
    "TencentProviderContractError",
    "TencentUrllibTransport",
    "build_tencent_payload",
    "build_tencent_request_url",
    "compute_tencent_request_hash",
    "parse_tencent_response_bytes",
]
