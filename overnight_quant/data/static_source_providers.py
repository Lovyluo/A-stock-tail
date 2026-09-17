from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import math
import re
import socket
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash


STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION = "static_source_evidence_v1"
STATIC_SOURCE_VERIFIER_CONTRACT_VERSION = "static_source_verifier_v1"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
EASTMONEY_GLOBAL_NEWS_URL = (
    "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
)
EASTMONEY_STOCK_NEWS_URL = (
    "https://search-api-web.eastmoney.com/search/jsonp"
)
CNINFO_ANNOUNCEMENT_URL = (
    "https://www.cninfo.com.cn/new/hisAnnouncement/query"
)

SOURCE_IDENTITIES = {
    "trading_calendar": (
        "tencent",
        "direct_http",
        "ifzq_fqkline_day_v2026-07-30",
    ),
    "daily_bar_qfq": (
        "tencent",
        "direct_http",
        "ifzq_fqkline_qfqday_v2026-07-30",
    ),
    "stock_news": (
        "eastmoney",
        "direct_http",
        "search_api_cms_old_v2026-07-30",
    ),
    "global_news": (
        "eastmoney",
        "direct_http",
        "np_weblist_724_v2026-07-30",
    ),
    "announcement": (
        "cninfo",
        "direct_http",
        "cninfo_query_v2026-07-30",
    ),
}

PROVIDER_KEYS = {
    "trading_calendar": (
        "static_source_providers.StaticSourceProviders."
        "collect_trading_calendar_records"
    ),
    "daily_bar_qfq": (
        "static_source_providers.StaticSourceProviders."
        "collect_qfq_daily_records"
    ),
    "stock_news": (
        "static_source_providers.StaticSourceProviders."
        "collect_stock_news_records"
    ),
    "global_news": (
        "static_source_providers.StaticSourceProviders."
        "collect_global_news_records"
    ),
    "announcement": (
        "static_source_providers.StaticSourceProviders."
        "collect_announcement_records"
    ),
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StaticSourceContractError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        response_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.response_evidence = (
            dict(response_evidence) if response_evidence is not None else None
        )
        super().__init__(self.code)


@dataclass(frozen=True)
class StaticHttpResponse:
    content: bytes
    status_code: int
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StaticProviderBatch:
    capability: str
    records: tuple[dict[str, Any], ...]
    responses: tuple[dict[str, Any], ...]
    status: str


class StaticTransport(Protocol):
    request_count: int

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        data: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> StaticHttpResponse: ...


class StaticUrllibTransport:
    """Single-attempt direct transport used only with explicit --network."""

    def __init__(self) -> None:
        self.request_count = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        data: Mapping[str, Any] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> StaticHttpResponse:
        self.request_count += 1
        query = _canonical_pairs(params)
        request_url = url + (("?" + urlencode(query)) if query else "")
        body = urlencode(_canonical_pairs(data)).encode("utf-8") if data else None
        request = Request(
            request_url,
            data=body,
            headers=dict(headers),
            method=method,
        )
        try:
            with urlopen(request, timeout=float(timeout_seconds)) as response:
                return StaticHttpResponse(
                    content=response.read(),
                    status_code=int(response.status),
                    url=str(response.geturl()),
                    headers=dict(response.headers.items()),
                )
        except (TimeoutError, socket.timeout) as exc:
            raise StaticSourceContractError("STATIC_SOURCE_TIMEOUT") from exc
        except HTTPError as exc:
            if url == CNINFO_ANNOUNCEMENT_URL:
                return StaticHttpResponse(
                    content=exc.read(),
                    status_code=int(exc.code),
                    url=str(exc.geturl()),
                    headers=dict(exc.headers.items()),
                )
            raise StaticSourceContractError(
                f"STATIC_SOURCE_HTTP_{int(exc.code)}"
            ) from exc
        except (URLError, OSError) as exc:
            raise StaticSourceContractError("STATIC_SOURCE_REQUEST_FAILED") from exc


class StaticSourceProviders:
    def __init__(
        self,
        codes: Iterable[str],
        *,
        target_trade_date: str,
        feature_cutoff: str | datetime,
        transport: StaticTransport,
        clock: Callable[[], datetime],
        timeout_seconds: float = 10.0,
    ) -> None:
        self.codes = _normalize_codes(codes)
        if not self.codes:
            raise StaticSourceContractError("STATIC_SOURCE_CODES_MISSING")
        self.target_trade_date = _parse_date(target_trade_date)
        self.feature_cutoff = _as_cn(feature_cutoff)
        if self.feature_cutoff.date() != self.target_trade_date:
            raise StaticSourceContractError("STATIC_SOURCE_CUTOFF_DATE_MISMATCH")
        if not hasattr(transport, "request") or not callable(clock):
            raise StaticSourceContractError("STATIC_SOURCE_DEPENDENCY_INVALID")
        if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise StaticSourceContractError("STATIC_SOURCE_TIMEOUT_INVALID")
        self.transport = transport
        self.clock = clock
        self.timeout_seconds = float(timeout_seconds)

    def collect_trading_calendar_records(self) -> list[dict[str, Any]]:
        return list(self.collect_trading_calendar_batch().records)

    def collect_trading_calendar_batch(self) -> StaticProviderBatch:
        response = self._request(
            "trading_calendar",
            "GET",
            TENCENT_KLINE_URL,
            params={"param": "sh000001,day,,,260,qfq"},
            data=None,
            headers={"User-Agent": USER_AGENT, "Referer": "https://gu.qq.com/"},
        )
        payload = _json_bytes(response["raw_bytes"])
        data = (payload.get("data") or {}).get("sh000001") or {}
        rows = data.get("qfqday") or data.get("day") or []
        dates = _calendar_dates(rows, self.target_trade_date)
        if len(dates) < 60:
            raise StaticSourceContractError(
                f"STATIC_CALENDAR_BELOW_60:{len(dates)}"
            )
        record = self._record(
            "trading_calendar",
            event_time=f"{dates[-1]}T15:00:00+08:00",
            observed_at=response["observed_at"],
            available_at=response["available_at"],
            request_hash=response["request_hash"],
            raw_hash=response["raw_hash"],
            payload={
                "calendar_kind": "benchmark_index_trade_dates",
                "calendar_name": "tencent_sh000001_daily_dates",
                "exchange_scope": ["SSE", "SZSE"],
                "benchmark": "sh000001",
                "trade_dates": dates,
                "latest_completed_trade_date": dates[-1],
            },
        )
        return _batch("trading_calendar", [record], [response])

    def collect_qfq_daily_records(
        self, calendar_trade_dates: Iterable[str]
    ) -> list[dict[str, Any]]:
        return list(self.collect_qfq_daily_batch(calendar_trade_dates).records)

    def collect_qfq_daily_batch(
        self, calendar_trade_dates: Iterable[str]
    ) -> StaticProviderBatch:
        trusted = tuple(sorted({_parse_date(value).isoformat() for value in calendar_trade_dates}))
        if len(trusted) < 60:
            raise StaticSourceContractError("STATIC_QFQ_CALENDAR_BELOW_60")
        trusted_set = set(trusted)
        records: list[dict[str, Any]] = []
        responses = []
        for code in self.codes:
            symbol = _tencent_symbol(code)
            response = self._request(
                "daily_bar_qfq",
                "GET",
                TENCENT_KLINE_URL,
                params={"param": f"{symbol},day,,,120,qfq"},
                data=None,
                headers={"User-Agent": USER_AGENT, "Referer": "https://gu.qq.com/"},
            )
            responses.append(response)
            payload = _json_bytes(response["raw_bytes"])
            source_data = (payload.get("data") or {}).get(symbol) or {}
            if "qfqday" not in source_data:
                raise StaticSourceContractError(f"STATIC_QFQ_NOT_PROVEN:{code}")
            rows = source_data.get("qfqday") or []
            normalized = _qfq_rows(rows, code, self.target_trade_date, trusted_set)
            if len(normalized) < 60:
                raise StaticSourceContractError(
                    f"STATIC_QFQ_BELOW_60:{code}:{len(normalized)}"
                )
            for item in normalized[-60:]:
                records.append(
                    self._record(
                        "daily_bar_qfq",
                        event_time=f"{item['date']}T15:00:00+08:00",
                        observed_at=response["observed_at"],
                        available_at=response["available_at"],
                        request_hash=response["request_hash"],
                        raw_hash=response["raw_hash"],
                        payload={
                            **item,
                            "adjustment": "qfq",
                            "adjustment_evidence": (
                                "request_param=qfq;response_key=qfqday"
                            ),
                            "field_units": {
                                "price": "CNY_per_share",
                                "volume": "vendor_lot",
                            },
                        },
                    )
                )
        return _batch("daily_bar_qfq", records, responses)

    def collect_global_news_records(self) -> list[dict[str, Any]]:
        return list(self.collect_global_news_batch().records)

    def collect_global_news_batch(self) -> StaticProviderBatch:
        response = self._request(
            "global_news",
            "GET",
            EASTMONEY_GLOBAL_NEWS_URL,
            params={
                "client": "web",
                "biz": "web_724",
                "fastColumn": "102",
                "sortEnd": "",
                "pageSize": "80",
                "req_trace": stable_hash(self.feature_cutoff.isoformat())[:32],
            },
            data=None,
            headers={"User-Agent": USER_AGENT, "Referer": "https://kuaixun.eastmoney.com/"},
        )
        body = _json_bytes(response["raw_bytes"])
        rows = ((body.get("data") or {}).get("fastNewsList") or [])
        records = self._news_records("global_news", rows, response, code="")
        return _batch("global_news", records, [response])

    def collect_stock_news_records(self) -> list[dict[str, Any]]:
        return list(self.collect_stock_news_batch().records)

    def collect_stock_news_batch(self) -> StaticProviderBatch:
        records: list[dict[str, Any]] = []
        responses = []
        for code in self.codes:
            callback = "jQuery_static_source"
            inner = {
                "uid": "",
                "keyword": code,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {"cmsArticleWebOld": {
                    "searchScope": "default", "sort": "default",
                    "pageIndex": 1, "pageSize": 10, "preTag": "", "postTag": "",
                }},
            }
            response = self._request(
                "stock_news",
                "GET",
                EASTMONEY_STOCK_NEWS_URL,
                params={
                    "cb": callback,
                    "param": json.dumps(inner, ensure_ascii=False, separators=(",", ":")),
                },
                data=None,
                headers={"User-Agent": USER_AGENT, "Referer": "https://so.eastmoney.com/"},
            )
            responses.append(response)
            text = response["raw_bytes"].decode("utf-8-sig").strip()
            if "(" in text and text.rfind(")") > text.index("("):
                text = text[text.index("(") + 1:text.rfind(")")]
            body = json.loads(text)
            found = (body.get("result") or {}).get("cmsArticleWebOld") or []
            rows = found.get("list") or [] if isinstance(found, dict) else found
            records.extend(self._news_records("stock_news", rows, response, code=code))
        return _batch("stock_news", records, responses)

    def collect_announcement_records(self) -> list[dict[str, Any]]:
        return list(self.collect_announcement_batch().records)

    def collect_announcement_batch(self) -> StaticProviderBatch:
        records: list[dict[str, Any]] = []
        responses = []
        for code in self.codes:
            response = self._request(
                "announcement",
                "POST",
                CNINFO_ANNOUNCEMENT_URL,
                params=None,
                data={
                    "stock": f"{code},{_cninfo_org_id(code)}",
                    "tabName": "fulltext", "pageSize": "30", "pageNum": "1",
                    "column": "", "category": "", "plate": "", "seDate": "",
                    "searchkey": "", "secid": "", "sortName": "",
                    "sortType": "", "isHLtitle": "true",
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": "https://www.cninfo.com.cn/new/disclosure",
                    "Origin": "https://www.cninfo.com.cn",
                },
            )
            responses.append(response)
            rows = _json_bytes(response["raw_bytes"]).get("announcements") or []
            records.extend(self._news_records("announcement", rows, response, code=code))
        return _batch("announcement", records, responses)

    def _news_records(
        self,
        capability: str,
        rows: Iterable[Mapping[str, Any]],
        response: Mapping[str, Any],
        *,
        code: str,
    ) -> list[dict[str, Any]]:
        records = []
        for item in rows:
            if not isinstance(item, Mapping):
                raise StaticSourceContractError(f"STATIC_{capability.upper()}_ROW_INVALID")
            published = _published_at(item)
            if published is None:
                raise StaticSourceContractError(
                    f"STATIC_{capability.upper()}_PUBLISHED_AT_MISSING"
                )
            if published > self.feature_cutoff:
                continue
            title = str(
                item.get("title") or item.get("brief") or item.get("content")
                or item.get("announcementTitle") or ""
            ).strip()
            if not title:
                continue
            announcement_id = str(
                item.get("announcementId") or item.get("id") or ""
            ).strip()
            raw_url = str(
                item.get("url") or item.get("shareurl") or item.get("adjunctUrl") or ""
            ).strip()
            if capability == "announcement" and raw_url and not raw_url.startswith("http"):
                raw_url = urljoin("https://static.cninfo.com.cn/", raw_url.lstrip("/"))
            payload = {
                "code": code,
                "title": title,
                "summary": str(item.get("summary") or item.get("digest") or item.get("content") or "").strip()[:500],
                "url": raw_url,
            }
            if capability == "announcement":
                payload.update({
                    "announcement_id": announcement_id,
                    "announcement_time": published.isoformat(timespec="seconds"),
                    "canonical_url": raw_url,
                })
            records.append(
                self._record(
                    capability,
                    event_time=published,
                    published_at=published,
                    observed_at=response["observed_at"],
                    available_at=response["available_at"],
                    request_hash=response["request_hash"],
                    raw_hash=response["raw_hash"],
                    payload=payload,
                )
            )
        return records

    def _request(
        self,
        capability: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        data: Mapping[str, Any] | None,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        observed = _as_cn(self.clock())
        request_material = {
            "method": method,
            "url": url,
            "params": _canonical_pairs(params),
            "data": _canonical_pairs(data),
        }
        response = self.transport.request(
            method, url, params=params, data=data,
            headers=headers, timeout_seconds=self.timeout_seconds,
        )
        available = _as_cn(self.clock())
        if available < observed:
            raise StaticSourceContractError("STATIC_SOURCE_TIME_ORDER_INVALID")
        if not response.url.startswith("https://"):
            raise StaticSourceContractError("STATIC_SOURCE_RESPONSE_URL_INVALID")
        raw_hash = hashlib.sha256(response.content).hexdigest()
        response_evidence = {
            "capability": capability,
            "method": method,
            "url": url,
            "params": _canonical_pairs(params),
            "data": _canonical_pairs(data),
            "request_hash": stable_hash(request_material),
            "raw_hash": raw_hash,
            "raw_bytes": response.content,
            "http_status_code": response.status_code,
            "response_url": response.url,
            "response_content_type": str(
                response.headers.get("Content-Type") or ""
            ),
            "response_server": str(response.headers.get("Server") or ""),
            "response_via": str(response.headers.get("Via") or ""),
            "observed_at": observed.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
        }
        if type(response.status_code) is not int or response.status_code != 200:
            code = (
                "CNINFO_DIRECT_ACCESS_UNAVAILABLE"
                if capability == "announcement" and response.status_code == 403
                else "STATIC_SOURCE_HTTP_STATUS_INVALID"
            )
            raise StaticSourceContractError(
                code,
                response_evidence=response_evidence,
            )
        return response_evidence

    def _record(
        self,
        capability: str,
        *,
        event_time: str | datetime,
        observed_at: str | datetime,
        available_at: str | datetime,
        request_hash: str,
        raw_hash: str,
        payload: Mapping[str, Any],
        published_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        origin, adapter, version = SOURCE_IDENTITIES[capability]
        event = _as_cn(event_time)
        observed = _as_cn(observed_at)
        available = _as_cn(available_at)
        if event > observed or observed > available:
            raise StaticSourceContractError("STATIC_SOURCE_TIME_ORDER_INVALID")
        record = {
            "capability": capability,
            "origin_source": origin,
            "adapter": adapter,
            "source_version": version,
            "event_time": event.isoformat(timespec="seconds"),
            "observed_at": observed.isoformat(timespec="microseconds"),
            "available_at": available.isoformat(timespec="microseconds"),
            "decision_cutoff": self.feature_cutoff.isoformat(timespec="seconds"),
            "request_hash": request_hash,
            "raw_hash": raw_hash,
            "payload": dict(payload),
        }
        if published_at is not None:
            record["published_at"] = _as_cn(published_at).isoformat(timespec="seconds")
        return record


def validate_static_provider_records(
    capability: str,
    records: Iterable[Mapping[str, Any]],
    *,
    target_trade_date: str,
    codes: Iterable[str],
    calendar_trade_dates: Iterable[str] = (),
) -> dict[str, Any]:
    rows = [dict(row) for row in records]
    expected_codes = set(_normalize_codes(codes))
    errors: list[str] = []
    identity = SOURCE_IDENTITIES.get(capability)
    if identity is None:
        errors.append("capability_unknown")
    for index, row in enumerate(rows):
        if row.get("capability") != capability:
            errors.append(f"{index}:capability_mismatch")
        if identity and tuple(row.get(field) for field in ("origin_source", "adapter", "source_version")) != identity:
            errors.append(f"{index}:source_identity_mismatch")
        for field in ("event_time", "observed_at", "available_at", "decision_cutoff"):
            if parse_cn_datetime(row.get(field)) is None:
                errors.append(f"{index}:{field}_invalid")
        for field in ("request_hash", "raw_hash"):
            if not isinstance(row.get(field), str) or _SHA256.fullmatch(row[field]) is None:
                errors.append(f"{index}:{field}_invalid")
        if capability in {"stock_news", "global_news", "announcement"} and parse_cn_datetime(row.get("published_at")) is None:
            errors.append(f"{index}:published_at_invalid")
    if capability == "trading_calendar" and len(rows) == 1:
        try:
            _validate_calendar_payload(rows[0].get("payload") or {}, _parse_date(target_trade_date))
        except StaticSourceContractError as exc:
            errors.append(exc.code)
    if capability == "daily_bar_qfq":
        trusted = set(str(value) for value in calendar_trade_dates)
        by_code: dict[str, set[str]] = {code: set() for code in expected_codes}
        for index, row in enumerate(rows):
            payload = row.get("payload") or {}
            code = str(payload.get("code") or "")
            day = str(payload.get("date") or "")
            if code not in expected_codes:
                errors.append(f"{index}:code_invalid")
                continue
            if day in by_code[code]:
                errors.append(f"{code}:duplicate_date")
            by_code[code].add(day)
            if day not in trusted:
                errors.append(f"{code}:date_not_in_calendar")
            if day >= target_trade_date:
                errors.append(f"{code}:unfinished_daily_bar")
            if payload.get("adjustment") != "qfq" or "response_key=qfqday" not in str(payload.get("adjustment_evidence") or ""):
                errors.append(f"{code}:qfq_not_proven")
            try:
                _validate_ohlcv(payload)
            except StaticSourceContractError as exc:
                errors.append(f"{code}:{exc.code}")
        for code, days in by_code.items():
            if len(days) < 60:
                errors.append(f"{code}:daily_bar_below_60")
    return {
        "status": "STATIC_SOURCE_RECORDS_VALID" if not errors else "STATIC_SOURCE_RECORDS_INVALID",
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "record_count": len(rows),
        "records_hash": stable_hash(sorted(rows, key=lambda row: stable_hash(row))),
    }


def compute_static_source_verifier_contract_hash() -> str:
    return stable_hash({
        "version": STATIC_SOURCE_VERIFIER_CONTRACT_VERSION,
        "identities": SOURCE_IDENTITIES,
        "provider_keys": PROVIDER_KEYS,
        "rules": [
            "calendar_weekday_unique_completed",
            "qfq_response_key_and_calendar_match",
            "published_at_required_and_cutoff",
            "sha256_and_time_order",
        ],
    })


def _batch(capability: str, records: list[dict[str, Any]], responses: list[dict[str, Any]]) -> StaticProviderBatch:
    normalized = sorted(records, key=lambda row: stable_hash(row))
    return StaticProviderBatch(
        capability=capability,
        records=tuple(normalized),
        responses=tuple(responses),
        status="SUCCESS" if normalized else "AVAILABLE_EMPTY",
    )


def _calendar_dates(rows: Iterable[Any], target: date) -> list[str]:
    dates = []
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        day = _parse_date(str(row[0])[:10])
        if day >= target:
            continue
        if day.weekday() >= 5:
            raise StaticSourceContractError("STATIC_CALENDAR_NON_TRADING_DATE")
        text = day.isoformat()
        if text in dates:
            raise StaticSourceContractError("STATIC_CALENDAR_DUPLICATE_DATE")
        dates.append(text)
    dates.sort()
    return dates


def _validate_calendar_payload(payload: Mapping[str, Any], target: date) -> None:
    dates = payload.get("trade_dates") or []
    normalized = _calendar_dates([[value] for value in dates], target)
    if normalized != list(dates):
        raise StaticSourceContractError("STATIC_CALENDAR_ORDER_INVALID")
    if len(normalized) < 60:
        raise StaticSourceContractError("STATIC_CALENDAR_BELOW_60")
    if payload.get("latest_completed_trade_date") != normalized[-1]:
        raise StaticSourceContractError("STATIC_CALENDAR_LATEST_DATE_INVALID")
    if payload.get("exchange_scope") != ["SSE", "SZSE"]:
        raise StaticSourceContractError("STATIC_CALENDAR_SCOPE_INVALID")


def _qfq_rows(rows: Iterable[Any], code: str, target: date, trusted: set[str]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        day = _parse_date(str(row[0])[:10]).isoformat()
        if day >= target.isoformat():
            continue
        if day not in trusted:
            raise StaticSourceContractError(f"STATIC_QFQ_DATE_NOT_IN_CALENDAR:{code}:{day}")
        if day in seen:
            raise StaticSourceContractError(f"STATIC_QFQ_DUPLICATE_DATE:{code}:{day}")
        seen.add(day)
        item = {
            "code": code, "date": day,
            "open": _positive(row[1]), "close": _positive(row[2]),
            "high": _positive(row[3]), "low": _positive(row[4]),
            "volume": _positive(row[5]),
        }
        _validate_ohlcv(item)
        result.append(item)
    return sorted(result, key=lambda item: item["date"])


def _validate_ohlcv(payload: Mapping[str, Any]) -> None:
    values = {key: _positive(payload.get(key)) for key in ("open", "close", "high", "low", "volume")}
    if values["high"] < max(values["open"], values["close"], values["low"]):
        raise StaticSourceContractError("STATIC_OHLCV_HIGH_INVALID")
    if values["low"] > min(values["open"], values["close"], values["high"]):
        raise StaticSourceContractError("STATIC_OHLCV_LOW_INVALID")


def _published_at(item: Mapping[str, Any]) -> datetime | None:
    value = (
        item.get("showTime") or item.get("time") or item.get("ctime")
        or item.get("date") or item.get("announcementTime")
        or item.get("publish_time") or item.get("pubDate")
    )
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, CN_TZ)
    return parse_cn_datetime(value)


def _json_bytes(content: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticSourceContractError("STATIC_SOURCE_JSON_INVALID") from exc
    if not isinstance(payload, dict):
        raise StaticSourceContractError("STATIC_SOURCE_JSON_OBJECT_REQUIRED")
    return payload


def _canonical_pairs(values: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    return sorted((str(key), str(value)) for key, value in (values or {}).items())


def _normalize_codes(codes: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    for value in codes:
        code = str(value or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise StaticSourceContractError(f"STATIC_SOURCE_CODE_INVALID:{code}")
        if code in normalized:
            raise StaticSourceContractError(f"STATIC_SOURCE_CODE_DUPLICATE:{code}")
        normalized.append(code)
    return tuple(sorted(normalized))


def _parse_date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise StaticSourceContractError(f"STATIC_SOURCE_DATE_INVALID:{value}") from exc


def _as_cn(value: str | datetime) -> datetime:
    parsed = parse_cn_datetime(value)
    if parsed is None:
        raise StaticSourceContractError(f"STATIC_SOURCE_TIME_INVALID:{value}")
    return parsed


def _positive(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise StaticSourceContractError("STATIC_OHLCV_VALUE_INVALID") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise StaticSourceContractError("STATIC_OHLCV_VALUE_INVALID")
    return parsed


def _tencent_symbol(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("8", "4")):
        return "bj" + code
    return "sz" + code


def _cninfo_org_id(code: str) -> str:
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


__all__ = [
    "PROVIDER_KEYS",
    "SOURCE_IDENTITIES",
    "STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION",
    "StaticHttpResponse",
    "StaticProviderBatch",
    "StaticSourceContractError",
    "StaticSourceProviders",
    "StaticUrllibTransport",
    "compute_static_source_verifier_contract_hash",
    "validate_static_provider_records",
]
