from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import hashlib
import json
import math
import threading
import time as time_module
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import requests

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.close_time_contract import (
    CloseTimeContract,
    build_close_time_contract,
    normalize_close_time_contract,
)
from overnight_quant.data.point_in_time import (
    parse_cn_datetime,
    stable_hash,
)
from overnight_quant.data.snapshot_store import (
    ProviderBatch,
    ProviderSpec,
)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
)
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_KLINE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
)
EASTMONEY_MARKET_URL = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get"
)
EASTMONEY_MINUTE_URL = (
    "https://push2.eastmoney.com/api/qt/stock/trends2/get"
)
EASTMONEY_FUND_URL = (
    "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
)
EASTMONEY_INDUSTRY_MAP_URL = (
    "https://emweb.securities.eastmoney.com/"
    "PC_HSF10/CoreConception/PageAjax"
)
EASTMONEY_BOARD_URL = (
    "https://push2.eastmoney.com/api/qt/stock/get"
)
EASTMONEY_INDUSTRY_LIST_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
)
SINA_FUND_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/"
    "api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj"
)
EASTMONEY_GLOBAL_NEWS_URL = (
    "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
)
EASTMONEY_STOCK_NEWS_URL = (
    "https://search-api-web.eastmoney.com/search/jsonp"
)
CNINFO_ANNOUNCEMENT_URL = (
    "https://www.cninfo.com.cn/new/hisAnnouncement/query"
)
NEWSNOW_URL = "https://newsnow.busiyi.world/api/s"


SOURCE_VERSIONS = {
    "tencent_quote": (
        "qt.gtimg.cn~88_fields+eastmoney_industry_map_v2026-07-30"
    ),
    "tencent_calendar": "ifzq_fqkline_day_v2026-07-30",
    "tencent_qfq_daily": "ifzq_fqkline_qfqday_v2026-07-30",
    "eastmoney_market": "push2_index_breadth_v2026-07-30",
    "eastmoney_minute": "push2_trends2_fields_v2026-07-30",
    "eastmoney_industry": "emweb_core+push2_board_v2026-07-30",
    "eastmoney_industry_list": (
        "push2_industry_clist_breadth_v2026-07-30"
    ),
    "eastmoney_fund": "push2_fflow_kline_v2026-07-30",
    "sina_fund": "sina_moneyflow_current_v2026-07-30",
    "selected_fund": "eastmoney_primary_sina_backup_v2026-07-30",
    "eastmoney_global_news": "np_weblist_724_v2026-07-30",
    "eastmoney_stock_news": "search_api_cms_old_v2026-07-30",
    "cninfo_announcements": "cninfo_query_v2026-07-30",
    "newsnow_cls": "newsnow_cls_hot_v2026-07-30",
}


class RealSourceError(RuntimeError):
    pass


class SourceContractError(RealSourceError):
    pass


class GlobalDeadlineExceeded(RealSourceError):
    pass


@dataclass(frozen=True)
class RawHttpResponse:
    content: bytes
    status_code: int
    url: str
    elapsed_ms: float

    @property
    def raw_hash(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8-sig"))


class RequestsTransport:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 12.0,
        max_attempts: int = 2,
        backoff_seconds: float = 0.4,
        min_host_interval_seconds: float = 0.25,
        sleep: Callable[[float], None] = time_module.sleep,
        monotonic: Callable[[], float] = time_module.monotonic,
    ):
        self.session = session or requests.Session()
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.min_host_interval_seconds = max(
            0.0,
            float(min_host_interval_seconds),
        )
        self.sleep = sleep
        self.monotonic = monotonic
        self._last_host_request: dict[str, float] = {}
        self._state_lock = threading.Lock()
        self._global_deadline: float | None = None
        self._collection_cancelled = False
        self._metrics = {
            "request_count": 0,
            "retry_count": 0,
            "failure_count": 0,
            "rate_limit_wait_count": 0,
            "deadline_trigger_count": 0,
        }

    def set_global_deadline(self, deadline: float | None) -> None:
        with self._state_lock:
            self._global_deadline = deadline
            self._collection_cancelled = False

    def cancel_current_collection(self) -> None:
        with self._state_lock:
            self._collection_cancelled = True

    def reset_metrics(self) -> None:
        with self._state_lock:
            self._metrics = {
                "request_count": 0,
                "retry_count": 0,
                "failure_count": 0,
                "rate_limit_wait_count": 0,
                "deadline_trigger_count": 0,
            }

    def metrics_snapshot(self) -> dict[str, int]:
        with self._state_lock:
            return dict(self._metrics)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> RawHttpResponse:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            self.raise_if_cancelled()
            self._respect_host_interval(url)
            started = self.monotonic()
            remaining = self._deadline_remaining()
            if remaining is not None and remaining <= 0:
                self._increment_metric("deadline_trigger_count")
                raise GlobalDeadlineExceeded(
                    "global_collection_deadline_exceeded"
                )
            read_timeout = self.timeout_seconds
            connect_timeout = 4.0
            if remaining is not None:
                read_timeout = max(0.1, min(read_timeout, remaining))
                connect_timeout = max(
                    0.1,
                    min(connect_timeout, remaining),
                )
            self._increment_metric("request_count")
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=(connect_timeout, read_timeout),
                )
                response.raise_for_status()
                self.raise_if_cancelled()
                return RawHttpResponse(
                    content=bytes(response.content),
                    status_code=int(response.status_code),
                    url=str(response.url),
                    elapsed_ms=round(
                        (self.monotonic() - started) * 1000,
                        3,
                    ),
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    self.raise_if_cancelled()
                    self._increment_metric("retry_count")
                    self.sleep(
                        self.backoff_seconds * (2**attempt)
                    )
        self._increment_metric("failure_count")
        raise RealSourceError(
            f"http_request_failed:{type(last_error).__name__}:"
            f"{last_error}"
        )

    def _respect_host_interval(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        current = self.monotonic()
        with self._state_lock:
            previous = self._last_host_request.get(host)
            reserved = max(
                current,
                (
                    previous + self.min_host_interval_seconds
                    if previous is not None
                    else current
                ),
            )
            self._last_host_request[host] = reserved
        wait_seconds = reserved - current
        if wait_seconds > 0:
            self._increment_metric("rate_limit_wait_count")
            remaining = self._deadline_remaining()
            if remaining is not None and wait_seconds >= remaining:
                self._increment_metric("deadline_trigger_count")
                raise GlobalDeadlineExceeded(
                    "global_collection_deadline_exceeded"
                )
            self.sleep(wait_seconds)
            self.raise_if_cancelled()

    def _deadline_remaining(self) -> float | None:
        with self._state_lock:
            deadline = self._global_deadline
        return (
            None
            if deadline is None
            else deadline - self.monotonic()
        )

    def raise_if_cancelled(self) -> None:
        with self._state_lock:
            cancelled = self._collection_cancelled
            deadline = self._global_deadline
        if cancelled or (
            deadline is not None
            and deadline - self.monotonic() <= 0
        ):
            self._increment_metric("deadline_trigger_count")
            raise GlobalDeadlineExceeded(
                "global_collection_deadline_exceeded"
            )

    def _increment_metric(self, name: str) -> None:
        with self._state_lock:
            self._metrics[name] = int(self._metrics.get(name, 0)) + 1


class RealPointInTimeCollectors:
    def __init__(
        self,
        codes: Iterable[str],
        *,
        transport: RequestsTransport | Any | None = None,
        clock: Callable[[], datetime] | None = None,
        time_contract: dict[str, Any] | CloseTimeContract | None = None,
        minute_label_semantics: str = "unverified",
        minute_label_verified: bool = False,
        probe_evidence_hash: str = "",
    ):
        self.codes = _normalize_codes(codes)
        self.transport = transport or RequestsTransport()
        self.clock = clock or (lambda: datetime.now(CN_TZ))
        self._industry_mapping_cache: dict[str, dict[str, Any]] = {}
        self._industry_list_cache: dict[str, Any] | None = None
        self.time_contract = normalize_close_time_contract(time_contract)
        self.minute_label_semantics = minute_label_semantics
        self.minute_label_verified = bool(minute_label_verified)
        self.probe_evidence_hash = str(probe_evidence_hash or "")

    def provider_map(
        self,
    ) -> dict[str, ProviderSpec]:
        return {
            "eastmoney_industry": ProviderSpec(
                self.collect_industry,
                ["industry"],
                SOURCE_VERSIONS["eastmoney_industry"],
                priority=1,
                stage="tail",
            ),
            "tencent_quote": ProviderSpec(
                self.collect_quotes,
                ["quote"],
                SOURCE_VERSIONS["tencent_quote"],
                priority=1,
                stage="tail",
            ),
            "eastmoney_market": ProviderSpec(
                self.collect_market,
                ["market"],
                SOURCE_VERSIONS["eastmoney_market"],
                priority=1,
                stage="tail",
            ),
            "tencent_trading_calendar": ProviderSpec(
                self.collect_trading_calendar,
                ["trading_calendar"],
                SOURCE_VERSIONS["tencent_calendar"],
                priority=2,
                stage="prewarm",
            ),
            "tencent_qfq_daily": ProviderSpec(
                self.collect_qfq_daily_bars,
                ["daily_bar"],
                SOURCE_VERSIONS["tencent_qfq_daily"],
                priority=2,
                stage="prewarm",
            ),
            "eastmoney_minute_bar": ProviderSpec(
                self.collect_minute_bars,
                ["minute_bar"],
                SOURCE_VERSIONS["eastmoney_minute"],
                priority=1,
                stage="tail",
            ),
            "selected_fund_flow": ProviderSpec(
                self.collect_selected_fund_flow,
                ["fund_flow"],
                SOURCE_VERSIONS["selected_fund"],
                priority=1,
                stage="tail",
            ),
            "eastmoney_global_news": ProviderSpec(
                self.collect_global_news,
                ["news"],
                SOURCE_VERSIONS["eastmoney_global_news"],
                priority=3,
                stage="news",
            ),
            "eastmoney_stock_news": ProviderSpec(
                self.collect_stock_news,
                ["news"],
                SOURCE_VERSIONS["eastmoney_stock_news"],
                priority=3,
                stage="news",
            ),
            "cninfo_announcements": ProviderSpec(
                self.collect_announcements,
                ["news"],
                SOURCE_VERSIONS["cninfo_announcements"],
                priority=3,
                stage="news",
            ),
            "newsnow_cls_audit": ProviderSpec(
                self.collect_newsnow_cls,
                ["news"],
                SOURCE_VERSIONS["newsnow_cls"],
                priority=4,
                stage="audit",
            ),
        }

    def tail_provider_map(self) -> dict[str, ProviderSpec]:
        return {
            source: provider
            for source, provider in self.provider_map().items()
            if provider.stage == "tail"
        }

    def prewarm_provider_map(self) -> dict[str, ProviderSpec]:
        providers = self.provider_map()
        providers["eastmoney_industry_mapping"] = ProviderSpec(
            self.collect_industry_mappings,
            ["industry_mapping"],
            SOURCE_VERSIONS["eastmoney_industry"],
            priority=2,
            stage="prewarm",
        )
        return {
            source: provider
            for source, provider in providers.items()
            if provider.stage in {"prewarm", "news"}
        }

    def validation_provider_map(self) -> dict[str, ProviderSpec]:
        providers = self.provider_map()
        providers.pop("selected_fund_flow")
        providers["eastmoney_fund_flow"] = ProviderSpec(
            self.collect_eastmoney_fund_flow,
            ["fund_flow"],
            SOURCE_VERSIONS["eastmoney_fund"],
            priority=1,
            stage="tail",
        )
        providers["sina_fund_flow_backup"] = ProviderSpec(
            self.collect_sina_fund_flow,
            ["fund_flow"],
            SOURCE_VERSIONS["sina_fund"],
            priority=4,
            stage="audit",
        )
        return providers

    def collect_quotes(self, observed_at: datetime) -> ProviderBatch:
        self._require_codes()
        self._check_cancelled()
        symbols = [_tencent_symbol(code) for code in self.codes]
        response = self.transport.request(
            "GET",
            TENCENT_QUOTE_URL + ",".join(symbols),
            headers={"User-Agent": USER_AGENT},
        )
        completed = self._now()
        text = response.content.decode("gbk", errors="strict")
        records = []
        for line in text.split(";"):
            parsed = _parse_tencent_quote_line(line)
            if parsed is None:
                continue
            code, values, event_time = parsed
            if code not in self.codes:
                continue
            industry = self._get_industry_mapping(
                code,
                observed_at=observed_at,
            )
            price = _number(values[3])
            limit_up = _number(values[47])
            limit_down = _number(values[48])
            payload = {
                "code": code,
                "name": values[1],
                "price": price,
                "prev_close": _number(values[4]),
                "open": _number(values[5]),
                "change_pct": _number(values[32]),
                "high": _number(values[33]),
                "low": _number(values[34]),
                "volume": _number(values[36]),
                "amount_wan": _number(values[37]),
                "turnover_pct": _number(values[38]),
                "limit_up": limit_up,
                "limit_down": limit_down,
                "is_limit_up": bool(
                    price > 0
                    and limit_up > 0
                    and price >= limit_up
                ),
                "is_limit_down": bool(
                    price > 0
                    and limit_down > 0
                    and price <= limit_down
                ),
                "suspended": bool(price <= 0),
                "industry_name": industry["name"],
                "bid_volume": sum(
                    _number(values[index])
                    for index in (10, 12, 14, 16, 18)
                    if index < len(values)
                ),
                "ask_volume": sum(
                    _number(values[index])
                    for index in (20, 22, 24, 26, 28)
                    if index < len(values)
                ),
                "field_units": {
                    "amount_wan": "CNY_10k",
                    "volume": "vendor_lot",
                    "bid_volume": "vendor_lot",
                    "ask_volume": "vendor_lot",
                    "turnover_pct": "percent",
                },
            }
            records.append(
                self._record(
                    payload,
                    data_type="quote",
                    event_time=event_time,
                    observed_at=observed_at,
                    available_at=completed,
                    source=(
                        "tencent_quote+eastmoney_industry_mapping"
                    ),
                    source_version=SOURCE_VERSIONS["tencent_quote"],
                    request={
                        "symbols": symbols,
                        "industry_mapping": (
                            "eastmoney_core_conception_rank_1"
                        ),
                    },
                    raw_hash=stable_hash(
                        [
                            response.raw_hash,
                            industry["raw_hash"],
                        ]
                    ),
                )
            )
        missing = sorted(set(self.codes) - {
            row["payload"]["code"] for row in records
        })
        if missing:
            raise SourceContractError(
                "tencent_quote_missing_codes:" + ",".join(missing)
            )
        return _batch(
            records,
            ["quote"],
            SOURCE_VERSIONS["tencent_quote"],
        )

    def collect_market(self, observed_at: datetime) -> ProviderBatch:
        quote_response = self.transport.request(
            "GET",
            TENCENT_QUOTE_URL
            + "sh000001,sh000300,sz399006",
            headers={"User-Agent": USER_AGENT},
        )
        breadth_response = self.transport.request(
            "GET",
            EASTMONEY_MARKET_URL,
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f12,f14,f2,f3,f104,f105,f106",
                "secids": "1.000001,0.399001,0.399006",
            },
            headers=_eastmoney_headers(),
        )
        completed = self._now()
        index_rows = {}
        event_times = []
        for line in quote_response.content.decode(
            "gbk",
            errors="strict",
        ).split(";"):
            parsed = _parse_tencent_quote_line(line)
            if parsed is None:
                continue
            code, values, event_time = parsed
            index_rows[code] = {
                "name": values[1],
                "price": _number(values[3]),
                "change_pct": _number(values[32]),
            }
            event_times.append(event_time)
        breadth_json = breadth_response.json()
        breadth_rows = (
            (breadth_json.get("data") or {}).get("diff") or []
        )
        by_code = {
            str(row.get("f12") or ""): row for row in breadth_rows
        }
        market_rows = [
            by_code.get("000001") or {},
            by_code.get("399001") or {},
        ]
        up_count = sum(int(row.get("f104") or 0) for row in market_rows)
        down_count = sum(
            int(row.get("f105") or 0) for row in market_rows
        )
        flat_count = sum(
            int(row.get("f106") or 0) for row in market_rows
        )
        total = up_count + down_count + flat_count
        changes = [
            float(row["change_pct"])
            for row in index_rows.values()
            if _finite(row.get("change_pct"))
        ]
        if len(index_rows) < 3 or total <= 0 or not changes:
            raise SourceContractError(
                "market_index_or_breadth_fields_missing"
            )
        payload = {
            "index_change_pct": round(
                sum(changes) / len(changes),
                6,
            ),
            "breadth_ratio": round(up_count / total, 8),
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "indices": index_rows,
            "field_units": {
                "index_change_pct": "percent",
                "breadth_ratio": "ratio_0_1",
                "up_count": "security_count",
                "down_count": "security_count",
                "flat_count": "security_count",
            },
        }
        event_time = max(
            [*event_times, completed],
        )
        record = self._record(
            payload,
            data_type="market",
            event_time=event_time,
            observed_at=observed_at,
            available_at=completed,
            source="tencent_indices+eastmoney_index_breadth",
            source_version=SOURCE_VERSIONS["eastmoney_market"],
            request={
                "tencent_symbols": [
                    "sh000001",
                    "sh000300",
                    "sz399006",
                ],
                "eastmoney_secids": [
                    "1.000001",
                    "0.399001",
                    "0.399006",
                ],
            },
            raw_hash=stable_hash(
                [quote_response.raw_hash, breadth_response.raw_hash]
            ),
        )
        return _batch(
            [record],
            ["market"],
            SOURCE_VERSIONS["eastmoney_market"],
        )

    def collect_trading_calendar(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        response, payload = self._tencent_daily_response(
            "sh000001",
            adjustment="qfq",
            limit=180,
        )
        completed = self._now()
        data = (payload.get("data") or {}).get("sh000001") or {}
        rows = data.get("day") or []
        dates = sorted({
            str(row[0])[:10]
            for row in rows
            if isinstance(row, list)
            and row
            and str(row[0])[:10] < observed_at.date().isoformat()
        })
        if len(dates) < 60:
            raise SourceContractError(
                f"trading_calendar_below_60:{len(dates)}"
            )
        calendar_payload = {
            "calendar_kind": "benchmark_index_trade_dates",
            "calendar_name": "tencent_sh000001_daily_dates",
            "trade_dates": dates,
            "latest_completed_trade_date": dates[-1],
            "benchmark": "sh000001",
            "field_units": {"trade_dates": "date"},
        }
        record = self._record(
            calendar_payload,
            data_type="trading_calendar",
            event_time=f"{dates[-1]}T15:00:00+08:00",
            observed_at=observed_at,
            available_at=completed,
            source="tencent_index_daily_calendar",
            source_version=SOURCE_VERSIONS["tencent_calendar"],
            request={
                "symbol": "sh000001",
                "period": "day",
                "limit": 180,
            },
            raw_hash=response.raw_hash,
        )
        return _batch(
            [record],
            ["trading_calendar"],
            SOURCE_VERSIONS["tencent_calendar"],
        )

    def collect_qfq_daily_bars(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        self._require_codes()
        completed_cutoff = observed_at.date().isoformat()
        records = []
        raw_hashes = []
        for code in self.codes:
            self._check_cancelled()
            symbol = _tencent_symbol(code)
            response, payload = self._tencent_daily_response(
                symbol,
                adjustment="qfq",
                limit=100,
            )
            raw_hashes.append(response.raw_hash)
            data = (payload.get("data") or {}).get(symbol) or {}
            if "qfqday" not in data:
                raise SourceContractError(
                    f"qfq_response_not_proven:{code}"
                )
            completed = self._now()
            rows = [
                row
                for row in data.get("qfqday") or []
                if isinstance(row, list)
                and len(row) >= 6
                and str(row[0])[:10] < completed_cutoff
            ]
            if len({str(row[0])[:10] for row in rows}) < 60:
                raise SourceContractError(
                    f"qfq_daily_below_60:{code}:{len(rows)}"
                )
            request = {
                "symbol": symbol,
                "period": "day",
                "limit": 100,
                "adjustment": "qfq",
            }
            for row in rows[-60:]:
                day = str(row[0])[:10]
                records.append(
                    self._record(
                        {
                            "code": code,
                            "date": day,
                            "open": _number(row[1]),
                            "close": _number(row[2]),
                            "high": _number(row[3]),
                            "low": _number(row[4]),
                            "volume": _number(row[5]),
                            "adjustment": "qfq",
                            "adjustment_evidence": (
                                "request_param=qfq;"
                                "response_key=qfqday"
                            ),
                            "field_units": {
                                "price": "CNY_per_share",
                                "volume": "vendor_lot",
                            },
                        },
                        data_type="daily_bar",
                        event_time=f"{day}T15:00:00+08:00",
                        observed_at=observed_at,
                        available_at=completed,
                        source="tencent_qfq_daily",
                        source_version=SOURCE_VERSIONS[
                            "tencent_qfq_daily"
                        ],
                        request=request,
                        raw_hash=response.raw_hash,
                    )
                )
        return _batch(
            records,
            ["daily_bar"],
            SOURCE_VERSIONS["tencent_qfq_daily"],
            raw_hashes=raw_hashes,
        )

    def collect_minute_bars(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        self._require_codes()
        records = []
        raw_hashes = []
        for code in self.codes:
            self._check_cancelled()
            response = self.transport.request(
                "GET",
                EASTMONEY_MINUTE_URL,
                params={
                    "secid": _eastmoney_secid(code),
                    "fields1": (
                        "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11"
                    ),
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                    "iscr": "0",
                    "iscca": "0",
                    "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                },
                headers=_eastmoney_headers(),
            )
            completed = self._now()
            raw_hashes.append(response.raw_hash)
            rows = (
                (response.json().get("data") or {}).get("trends")
                or []
            )
            for line in rows:
                parts = str(line).split(",")
                if len(parts) < 8:
                    continue
                event = parse_cn_datetime(parts[0])
                if event is None:
                    continue
                records.append(
                    self._record(
                        {
                            "code": code,
                            "open": _number(parts[1]),
                            "close": _number(parts[2]),
                            "high": _number(parts[3]),
                            "low": _number(parts[4]),
                            "volume": _number(parts[5]),
                            "amount": _number(parts[6]),
                            "vwap": _number(parts[7]),
                            "field_units": {
                                "price": "CNY_per_share",
                                "volume": "vendor_lot",
                                "amount": "CNY",
                            },
                            "minute_label_semantics": (
                                self.minute_label_semantics
                            ),
                            "minute_label_verified": (
                                self.minute_label_verified
                            ),
                        },
                        data_type="minute_bar",
                        event_time=event,
                        observed_at=observed_at,
                        available_at=completed,
                        source="eastmoney_intraday_trends",
                        source_version=SOURCE_VERSIONS[
                            "eastmoney_minute"
                        ],
                        request={
                            "secid": _eastmoney_secid(code),
                            "klt": 1,
                        },
                        raw_hash=response.raw_hash,
                    )
                )
        if not records:
            raise SourceContractError("minute_bar_empty")
        return _batch(
            records,
            ["minute_bar"],
            SOURCE_VERSIONS["eastmoney_minute"],
            raw_hashes=raw_hashes,
        )

    def collect_industry(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        self._require_codes()
        records = []
        raw_hashes = []
        market_change, market_raw_hash = (
            self._fetch_market_change_pct()
        )
        raw_hashes.append(market_raw_hash)
        for code in self.codes:
            self._check_cancelled()
            mapping = self._get_industry_mapping(
                code,
                observed_at=observed_at,
            )
            raw_hashes.append(mapping["raw_hash"])
            (
                item,
                completed,
                board_raw_hash,
                board_source,
                board_source_version,
                primary_error,
            ) = self._fetch_industry_board(mapping)
            raw_hashes.append(board_raw_hash)
            change_pct = _number(
                item.get("change_pct")
            )
            up_count = int(item.get("f104") or 0)
            down_count = int(item.get("f105") or 0)
            flat_count = int(item.get("f106") or 0)
            total = up_count + down_count + flat_count
            if total <= 0 or not _finite(change_pct):
                raise SourceContractError(
                    f"industry_breadth_missing:{code}"
                )
            payload = {
                "name": mapping["name"],
                "board_code": mapping["board_code"],
                "change_pct": change_pct,
                "relative_strength_pct": (
                    change_pct - market_change
                ),
                "breadth_ratio": up_count / total,
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": flat_count,
                "industry_source_role": (
                    "backup"
                    if board_source == "eastmoney_industry_clist"
                    else "primary"
                ),
                "primary_error": primary_error,
                "field_units": {
                    "change_pct": "percent",
                    "relative_strength_pct": "percent",
                    "breadth_ratio": "ratio_0_1",
                },
            }
            records.append(
                self._record(
                    payload,
                    data_type="industry",
                    event_time=completed,
                    observed_at=observed_at,
                    available_at=completed,
                    source=f"{board_source}+tencent_market",
                    source_version=board_source_version,
                    request={
                        "stock_code": code,
                        "board_code": mapping["board_code"],
                    },
                    raw_hash=stable_hash(
                        [
                            mapping["raw_hash"],
                            board_raw_hash,
                            market_raw_hash,
                        ]
                    ),
                )
            )
        return _batch(
            records,
            ["industry"],
            SOURCE_VERSIONS["eastmoney_industry"],
            raw_hashes=raw_hashes,
        )

    def collect_industry_mappings(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        self._require_codes()
        records = []
        raw_hashes = []
        for code in self.codes:
            self._check_cancelled()
            mapping = self._get_industry_mapping(
                code,
                observed_at=observed_at,
            )
            raw_hashes.append(mapping["raw_hash"])
            completed = (
                parse_cn_datetime(mapping.get("available_at"))
                or self._now()
            )
            records.append(
                self._record(
                    {
                        "code": code,
                        "name": mapping["name"],
                        "board_code": mapping["board_code"],
                        "prewarm_only": True,
                        "eligible_for_hard_gate": False,
                    },
                    data_type="industry_mapping",
                    event_time=completed,
                    observed_at=observed_at,
                    available_at=completed,
                    source="eastmoney_industry_mapping",
                    source_version=SOURCE_VERSIONS[
                        "eastmoney_industry"
                    ],
                    request={"stock_code": code},
                    raw_hash=mapping["raw_hash"],
                )
            )
        return _batch(
            records,
            ["industry_mapping"],
            SOURCE_VERSIONS["eastmoney_industry"],
            raw_hashes=raw_hashes,
        )

    def collect_eastmoney_fund_flow(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        self._require_codes()
        records = []
        raw_hashes = []
        for code in self.codes:
            self._check_cancelled()
            response = self.transport.request(
                "GET",
                EASTMONEY_FUND_URL,
                params={
                    "secid": _eastmoney_secid(code),
                    "klt": "1",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                },
                headers=_eastmoney_headers(),
            )
            completed = self._now()
            raw_hashes.append(response.raw_hash)
            rows = (
                (response.json().get("data") or {}).get("klines")
                or []
            )
            for line in rows:
                parts = str(line).split(",")
                if len(parts) < 6:
                    continue
                event = parse_cn_datetime(parts[0])
                if event is None:
                    continue
                records.append(
                    self._record(
                        {
                            "code": code,
                            "main_net": _number(parts[1]),
                            "small_net": _number(parts[2]),
                            "mid_net": _number(parts[3]),
                            "large_net": _number(parts[4]),
                            "super_net": _number(parts[5]),
                            "field_units": {
                                "main_net": "CNY",
                                "small_net": "CNY",
                                "mid_net": "CNY",
                                "large_net": "CNY",
                                "super_net": "CNY",
                            },
                            "semantic_class": (
                                "formal_minute_main_force_flow"
                            ),
                            "timestamp_quality": (
                                "source_minute_event_time"
                            ),
                            "is_proxy": False,
                            "eligible_for_hard_gate": True,
                            "field_definition_version": (
                                "eastmoney_fflow_f51_f56_v1"
                            ),
                        },
                        data_type="fund_flow",
                        event_time=event,
                        observed_at=observed_at,
                        available_at=completed,
                        source="eastmoney_fund_flow_minute",
                        source_version=SOURCE_VERSIONS[
                            "eastmoney_fund"
                        ],
                        request={
                            "secid": _eastmoney_secid(code),
                            "klt": 1,
                        },
                        raw_hash=response.raw_hash,
                    )
                )
        if not records:
            raise SourceContractError("eastmoney_fund_flow_empty")
        return _batch(
            records,
            ["fund_flow"],
            SOURCE_VERSIONS["eastmoney_fund"],
            raw_hashes=raw_hashes,
        )

    def collect_sina_fund_flow(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        self._require_codes()
        response = self.transport.request(
            "GET",
            SINA_FUND_URL,
            params={
                "page": 1,
                "num": 6000,
                "sort": "symbol",
                "asc": 1,
                "bankuai": "",
                "shichang": "",
            },
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://finance.sina.com.cn/",
            },
        )
        completed = self._now()
        rows = response.json()
        by_code = {
            str(row.get("symbol") or "")[-6:]: row
            for row in rows
            if isinstance(row, dict)
        }
        records = []
        for code in self.codes:
            self._check_cancelled()
            item = by_code.get(code)
            if not item:
                continue
            records.append(
                self._record(
                    {
                        "code": code,
                        "main_net": _number(item.get("r0_net")),
                        "large_net": _number(item.get("netamount")),
                        "field_units": {
                            "main_net": "CNY",
                            "large_net": "CNY",
                        },
                        "semantic_class": (
                            "current_snapshot_money_flow_proxy"
                        ),
                        "timestamp_quality": (
                            "collector_completion_only"
                        ),
                        "is_proxy": True,
                        "eligible_for_hard_gate": False,
                        "field_definition_version": (
                            "sina_r0_net_netamount_unverified_v1"
                        ),
                        "timestamp_quality": (
                            "project_observed_snapshot_only"
                        ),
                    },
                    data_type="fund_flow",
                    event_time=completed,
                    observed_at=observed_at,
                    available_at=completed,
                    source="sina_money_flow_current",
                    source_version=SOURCE_VERSIONS["sina_fund"],
                    request={"market_rows": 6000},
                    raw_hash=response.raw_hash,
                )
            )
        if not records:
            raise SourceContractError("sina_fund_flow_empty")
        return _batch(
            records,
            ["fund_flow"],
            SOURCE_VERSIONS["sina_fund"],
            raw_hashes=[response.raw_hash],
        )

    def collect_selected_fund_flow(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        try:
            return self.collect_eastmoney_fund_flow(observed_at)
        except RealSourceError as primary_error:
            backup = self.collect_sina_fund_flow(observed_at)
            records = []
            for row in backup.records:
                payload = dict(row.get("payload") or {})
                payload["fallback_from"] = (
                    "eastmoney_fund_flow_minute"
                )
                payload["fallback_reason"] = (
                    f"{type(primary_error).__name__}:"
                    f"{primary_error}"
                )
                records.append({**row, "payload": payload})
            return ProviderBatch(
                records=records,
                data_types=["fund_flow"],
                source_version=SOURCE_VERSIONS["selected_fund"],
                raw_hash=stable_hash(
                    [
                        "eastmoney_primary_failed",
                        backup.raw_hash,
                    ]
                ),
            )

    def collect_global_news(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        response = self.transport.request(
            "GET",
            EASTMONEY_GLOBAL_NEWS_URL,
            params={
                "client": "web",
                "biz": "web_724",
                "fastColumn": "102",
                "sortEnd": "",
                "pageSize": "80",
                "req_trace": stable_hash(
                    observed_at.isoformat()
                )[:32],
            },
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://kuaixun.eastmoney.com/",
            },
        )
        completed = self._now()
        rows = (
            (response.json().get("data") or {}).get("fastNewsList")
            or []
        )
        records = self._news_records(
            rows,
            observed_at=observed_at,
            available_at=completed,
            source="eastmoney_global_news",
            source_version=SOURCE_VERSIONS[
                "eastmoney_global_news"
            ],
            raw_hash=response.raw_hash,
            code="",
        )
        return _batch(
            records,
            ["news"],
            SOURCE_VERSIONS["eastmoney_global_news"],
            raw_hashes=[response.raw_hash],
        )

    def collect_stock_news(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        self._require_codes()
        records = []
        raw_hashes = []
        for code in self.codes:
            self._check_cancelled()
            callback = "jQuery_pit_news"
            inner = {
                "uid": "",
                "keyword": code,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default",
                        "sort": "default",
                        "pageIndex": 1,
                        "pageSize": 10,
                        "preTag": "",
                        "postTag": "",
                    }
                },
            }
            response = self.transport.request(
                "GET",
                EASTMONEY_STOCK_NEWS_URL,
                params={
                    "cb": callback,
                    "param": json.dumps(
                        inner,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "https://so.eastmoney.com/",
                },
            )
            completed = self._now()
            raw_hashes.append(response.raw_hash)
            text = response.content.decode("utf-8-sig").strip()
            if "(" in text and text.rfind(")") > text.index("("):
                text = text[text.index("(") + 1:text.rfind(")")]
            body = json.loads(text)
            payload = (
                (body.get("result") or {}).get("cmsArticleWebOld")
                or []
            )
            rows = (
                payload.get("list") or []
                if isinstance(payload, dict)
                else payload
            )
            records.extend(
                self._news_records(
                    rows,
                    observed_at=observed_at,
                    available_at=completed,
                    source="eastmoney_stock_news",
                    source_version=SOURCE_VERSIONS[
                        "eastmoney_stock_news"
                    ],
                    raw_hash=response.raw_hash,
                    code=code,
                )
            )
        return _batch(
            records,
            ["news"],
            SOURCE_VERSIONS["eastmoney_stock_news"],
            raw_hashes=raw_hashes,
        )

    def collect_announcements(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        self._require_codes()
        records = []
        raw_hashes = []
        for code in self.codes:
            self._check_cancelled()
            response = self.transport.request(
                "POST",
                CNINFO_ANNOUNCEMENT_URL,
                data={
                    "stock": f"{code},{_cninfo_org_id(code)}",
                    "tabName": "fulltext",
                    "pageSize": "10",
                    "pageNum": "1",
                    "column": "",
                    "category": "",
                    "plate": "",
                    "seDate": "",
                    "searchkey": "",
                    "secid": "",
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                },
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": (
                        "https://www.cninfo.com.cn/new/disclosure"
                    ),
                },
            )
            completed = self._now()
            raw_hashes.append(response.raw_hash)
            rows = response.json().get("announcements") or []
            records.extend(
                self._news_records(
                    rows,
                    observed_at=observed_at,
                    available_at=completed,
                    source="cninfo_announcements",
                    source_version=SOURCE_VERSIONS[
                        "cninfo_announcements"
                    ],
                    raw_hash=response.raw_hash,
                    code=code,
                )
            )
        return _batch(
            records,
            ["news"],
            SOURCE_VERSIONS["cninfo_announcements"],
            raw_hashes=raw_hashes,
        )

    def collect_newsnow_cls(
        self,
        observed_at: datetime,
    ) -> ProviderBatch:
        response = self.transport.request(
            "GET",
            NEWSNOW_URL,
            params={"id": "cls-hot"},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        rows = response.json().get("items") or []
        if any(
            not (
                row.get("pubDate")
                or (row.get("extra") or {}).get("date")
            )
            for row in rows
            if isinstance(row, dict)
        ):
            raise SourceContractError(
                "newsnow_cls_items_missing_published_at"
            )
        completed = self._now()
        records = self._news_records(
            rows,
            observed_at=observed_at,
            available_at=completed,
            source="newsnow_cls_hot",
            source_version=SOURCE_VERSIONS["newsnow_cls"],
            raw_hash=response.raw_hash,
            code="",
        )
        return _batch(
            records,
            ["news"],
            SOURCE_VERSIONS["newsnow_cls"],
            raw_hashes=[response.raw_hash],
        )

    def validate_sources(
        self,
        observed_at: datetime | None = None,
    ) -> dict[str, Any]:
        observed = _as_cn(observed_at or self._now())
        cutoff = datetime.combine(
            observed.date(),
            time(14, 50),
            tzinfo=CN_TZ,
        )
        sources = {}
        for name, provider in self.validation_provider_map().items():
            started = self._now()
            try:
                batch = provider(started)
                completed = self._now()
                records = list(batch.records)
                sources[name] = {
                    "status": (
                        "SUCCESS" if records else "AVAILABLE_EMPTY"
                    ),
                    "record_count": len(records),
                    "data_types": list(batch.data_types),
                    "source_version": batch.source_version,
                    "started_at": started.isoformat(
                        timespec="seconds"
                    ),
                    "completed_at": completed.isoformat(
                        timespec="seconds"
                    ),
                    "elapsed_ms": round(
                        (completed - started).total_seconds() * 1000,
                        3,
                    ),
                    "completed_before_1450": completed <= cutoff,
                    "event_time_min": _record_time_bound(
                        records,
                        minimum=True,
                    ),
                    "event_time_max": _record_time_bound(
                        records,
                        minimum=False,
                    ),
                    "raw_hash": batch.raw_hash,
                    "qfq_proven": _qfq_proven(records),
                    "contains_1450_minute": _contains_1450(records),
                }
            except Exception as exc:
                completed = self._now()
                sources[name] = {
                    "status": "FAILED",
                    "record_count": 0,
                    "data_types": list(provider.data_types),
                    "source_version": provider.source_version,
                    "started_at": started.isoformat(
                        timespec="seconds"
                    ),
                    "completed_at": completed.isoformat(
                        timespec="seconds"
                    ),
                    "elapsed_ms": round(
                        (completed - started).total_seconds() * 1000,
                        3,
                    ),
                    "completed_before_1450": completed <= cutoff,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        failures = [
            name
            for name, item in sources.items()
            if item["status"] == "FAILED"
        ]
        return {
            "status": (
                "SOURCE_VALIDATION_COMPLETED"
                if not failures
                else "SOURCE_VALIDATION_COMPLETED_WITH_GAPS"
            ),
            "execution_ok": True,
            "data_ready": False,
            "observed_at": observed.isoformat(timespec="seconds"),
            "source_count": len(sources),
            "failed_sources": failures,
            "sources": sources,
            "notes": [
                "source validation never creates strategy outputs",
                "completion outside 14:50 is audit evidence only",
            ],
            "candidates": [],
            "tickets": [],
            "orders": [],
        }

    def _get_industry_mapping(
        self,
        code: str,
        *,
        observed_at: datetime,
    ) -> dict[str, Any]:
        cached = self._industry_mapping_cache.get(code)
        if cached:
            return cached
        response = self.transport.request(
            "GET",
            EASTMONEY_INDUSTRY_MAP_URL,
            params={"code": _eastmoney_web_code(code)},
            headers={
                "User-Agent": USER_AGENT,
                "Referer": (
                    "https://emweb.securities.eastmoney.com/"
                ),
            },
        )
        completed = self._now()
        rows = response.json().get("ssbk") or []
        ranked = sorted(
            (
                row
                for row in rows
                if str(row.get("BOARD_NAME") or "").strip()
                and str(row.get("BOARD_CODE") or "").strip()
            ),
            key=lambda row: int(row.get("BOARD_RANK") or 999),
        )
        if not ranked:
            raise SourceContractError(
                f"industry_mapping_missing:{code}"
            )
        selected = ranked[0]
        result = {
            "name": str(selected["BOARD_NAME"]).strip(),
            "board_code": (
                "BK"
                + str(selected["BOARD_CODE"]).strip().zfill(4)
            ),
            "raw_hash": response.raw_hash,
            "observed_at": observed_at.isoformat(
                timespec="seconds"
            ),
            "available_at": completed.isoformat(
                timespec="seconds"
            ),
        }
        self._industry_mapping_cache[code] = result
        return result

    def _fetch_industry_board(
        self,
        mapping: dict[str, Any],
    ) -> tuple[
        dict[str, Any],
        datetime,
        str,
        str,
        str,
        str,
    ]:
        primary_error = ""
        try:
            response = self.transport.request(
                "GET",
                EASTMONEY_BOARD_URL,
                params={
                    "secid": f"90.{mapping['board_code']}",
                    "fields": "f57,f58,f170,f104,f105,f106",
                },
                headers=_eastmoney_headers(),
            )
            completed = self._now()
            item = response.json().get("data") or {}
            total = sum(
                int(item.get(field) or 0)
                for field in ("f104", "f105", "f106")
            )
            if total <= 0 or item.get("f170") in (None, ""):
                raise SourceContractError(
                    "industry_primary_fields_missing"
                )
            return (
                {
                    **item,
                    "change_pct": _number(item.get("f170")) / 100.0,
                },
                completed,
                response.raw_hash,
                "eastmoney_industry_board",
                SOURCE_VERSIONS["eastmoney_industry"],
                "",
            )
        except RealSourceError as exc:
            primary_error = f"{type(exc).__name__}: {exc}"

        backup = self._get_industry_list()
        item = (
            backup["items"].get(mapping["board_code"])
            or backup["items"].get(mapping["name"])
        )
        if not item:
            raise SourceContractError(
                f"industry_backup_mapping_missing:"
                f"{mapping['board_code']}"
            )
        return (
            item,
            backup["completed_at"],
            backup["raw_hash"],
            "eastmoney_industry_clist",
            SOURCE_VERSIONS["eastmoney_industry_list"],
            primary_error,
        )

    def _get_industry_list(self) -> dict[str, Any]:
        if self._industry_list_cache is not None:
            return self._industry_list_cache
        response = self.transport.request(
            "GET",
            EASTMONEY_INDUSTRY_LIST_URL,
            params={
                "pn": "1",
                "pz": "100",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": "m:90+t:2",
                "fields": (
                    "f3,f12,f14,f104,f105,f106"
                ),
            },
            headers=_eastmoney_headers(),
        )
        completed = self._now()
        rows = (
            (response.json().get("data") or {}).get("diff")
            or []
        )
        items: dict[str, dict[str, Any]] = {}
        for row in rows:
            board_code = str(row.get("f12") or "").strip()
            name = str(row.get("f14") or "").strip()
            total = sum(
                int(row.get(field) or 0)
                for field in ("f104", "f105", "f106")
            )
            if (
                not board_code
                or not name
                or row.get("f3") in (None, "")
                or total <= 0
            ):
                continue
            normalized = {
                **row,
                "change_pct": _number(row.get("f3")),
            }
            items[board_code] = normalized
            items[name] = normalized
        if not items:
            raise SourceContractError(
                "industry_backup_fields_missing"
            )
        self._industry_list_cache = {
            "items": items,
            "completed_at": completed,
            "raw_hash": response.raw_hash,
        }
        return self._industry_list_cache

    def _fetch_market_change_pct(self) -> tuple[float, str]:
        response = self.transport.request(
            "GET",
            TENCENT_QUOTE_URL + "sh000001",
            headers={"User-Agent": USER_AGENT},
        )
        parsed_rows = [
            _parse_tencent_quote_line(line)
            for line in response.content.decode(
                "gbk",
                errors="strict",
            ).split(";")
        ]
        valid = [item for item in parsed_rows if item is not None]
        if not valid:
            raise SourceContractError("market_change_missing")
        return _number(valid[0][1][32]), response.raw_hash

    def _tencent_daily_response(
        self,
        symbol: str,
        *,
        adjustment: str,
        limit: int,
    ) -> tuple[RawHttpResponse, dict[str, Any]]:
        response = self.transport.request(
            "GET",
            TENCENT_KLINE_URL,
            params={
                "param": (
                    f"{symbol},day,,,{int(limit)},{adjustment}"
                )
            },
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://gu.qq.com/",
            },
        )
        payload = response.json()
        if int(payload.get("code") or 0) != 0:
            raise RealSourceError(
                f"tencent_kline_code:{payload.get('code')}"
            )
        return response, payload

    def _news_records(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        observed_at: datetime,
        available_at: datetime,
        source: str,
        source_version: str,
        raw_hash: str,
        code: str,
    ) -> list[dict[str, Any]]:
        records = []
        missing_published_at = 0
        for item in rows:
            title = str(
                item.get("title")
                or item.get("brief")
                or item.get("content")
                or item.get("announcementTitle")
                or ""
            ).strip()
            if not title:
                continue
            published = _news_timestamp(item)
            if published is None:
                missing_published_at += 1
                continue
            payload = {
                "code": code,
                "title": title,
                "summary": str(
                    item.get("summary")
                    or item.get("digest")
                    or item.get("content")
                    or ""
                ).strip()[:500],
                "url": str(
                    item.get("url")
                    or item.get("shareurl")
                    or item.get("adjunctUrl")
                    or ""
                ),
            }
            records.append(
                self._record(
                    payload,
                    data_type="news",
                    event_time=published,
                    published_at=published,
                    observed_at=observed_at,
                    available_at=available_at,
                    source=source,
                    source_version=source_version,
                    request={"code": code, "limit": 80},
                    raw_hash=raw_hash,
                )
            )
        if missing_published_at:
            raise SourceContractError(
                f"{source}_published_at_missing:"
                f"{missing_published_at}"
            )
        return records

    def _record(
        self,
        payload: dict[str, Any],
        *,
        data_type: str,
        event_time: str | datetime,
        observed_at: str | datetime,
        available_at: str | datetime,
        source: str,
        source_version: str,
        request: dict[str, Any],
        raw_hash: str,
        published_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        observed = _as_cn(observed_at)
        available = _as_cn(available_at)
        contract = self.time_contract or build_close_time_contract(
            observed.date(),
            minute_label_semantics=self.minute_label_semantics,
            verified=self.minute_label_verified,
        )
        event = _as_cn(event_time)
        published = (
            _as_cn(published_at).isoformat(timespec="seconds")
            if published_at not in (None, "")
            else ""
        )
        return {
            "event_time": event.isoformat(timespec="seconds"),
            "published_at": published,
            "observed_at": observed.isoformat(timespec="seconds"),
            "available_at": available.isoformat(timespec="seconds"),
            "decision_cutoff": contract.decision_time,
            "feature_event_cutoff": contract.feature_event_cutoff,
            "collection_deadline": contract.collection_deadline,
            "decision_time": contract.decision_time,
            "execution_not_before": contract.execution_not_before,
            "time_contract_version": contract.contract_version,
            "minute_label_semantics": (
                contract.minute_label_semantics
            ),
            "minute_label_validation_status": (
                contract.minute_label_validation_status
            ),
            "source": source,
            "source_version": source_version,
            "request_hash": stable_hash(request),
            "raw_hash": str(raw_hash),
            "data_type": data_type,
            "payload": payload,
        }

    def _require_codes(self) -> None:
        if not self.codes:
            raise SourceContractError("target_codes_missing")

    def _check_cancelled(self) -> None:
        checker = getattr(self.transport, "raise_if_cancelled", None)
        if callable(checker):
            checker()

    def _now(self) -> datetime:
        return _as_cn(self.clock())


def _batch(
    records: list[dict[str, Any]],
    data_types: list[str],
    source_version: str,
    *,
    raw_hashes: list[str] | None = None,
) -> ProviderBatch:
    hashes = list(raw_hashes or [])
    if not hashes:
        hashes = sorted({
            str(row.get("raw_hash") or "")
            for row in records
            if row.get("raw_hash")
        })
    return ProviderBatch(
        records=records,
        data_types=data_types,
        source_version=source_version,
        raw_hash=stable_hash(hashes),
    )


def _parse_tencent_quote_line(
    line: str,
) -> tuple[str, list[str], datetime] | None:
    if "=" not in line or '"' not in line:
        return None
    key = line.split("=", 1)[0].split("_")[-1].strip()
    values = line.split('"', 2)[1].split("~")
    if len(values) < 53 or len(key) < 8:
        return None
    event_time = _parse_tencent_timestamp(values[30])
    if event_time is None:
        raise SourceContractError(
            f"tencent_quote_timestamp_missing:{key}"
        )
    return key[2:].zfill(6), values, event_time


def _parse_tencent_timestamp(value: Any) -> datetime | None:
    text = "".join(
        character for character in str(value or "") if character.isdigit()
    )
    if len(text) < 12:
        return None
    try:
        parsed = datetime.strptime(
            text[:14] if len(text) >= 14 else text[:12],
            "%Y%m%d%H%M%S" if len(text) >= 14 else "%Y%m%d%H%M",
        )
    except ValueError:
        return None
    return parsed.replace(tzinfo=CN_TZ)


def _news_timestamp(item: dict[str, Any]) -> datetime | None:
    extra = (
        item.get("extra")
        if isinstance(item.get("extra"), dict)
        else {}
    )
    value = (
        item.get("showTime")
        or item.get("time")
        or item.get("ctime")
        or item.get("date")
        or item.get("announcementTime")
        or item.get("publish_time")
        or item.get("pubDate")
        or extra.get("date")
    )
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, CN_TZ)
    parsed = parse_cn_datetime(value)
    if parsed is not None:
        return parsed
    text = str(value or "").strip()
    if len(text) == 10 and text.isdigit():
        return datetime.fromtimestamp(int(text), CN_TZ)
    return None


def _number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _as_cn(value: str | datetime) -> datetime:
    parsed = parse_cn_datetime(value)
    if parsed is None:
        raise SourceContractError(f"datetime_invalid:{value}")
    return parsed


def _normalize_codes(codes: Iterable[str]) -> list[str]:
    normalized = []
    for value in codes:
        text = str(value or "").strip().lower()
        if text.startswith(("sh", "sz", "bj")):
            text = text[2:]
        if "." in text:
            text = text.split(".", 1)[0]
        if text.isdigit():
            code = text.zfill(6)
            if code not in normalized:
                normalized.append(code)
    return normalized


def _tencent_symbol(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("8", "4")):
        return f"bj{code}"
    return f"sz{code}"


def _eastmoney_secid(code: str) -> str:
    market = "1" if code.startswith(("6", "9")) else "0"
    return f"{market}.{code}"


def _eastmoney_web_code(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"SH{code}"
    if code.startswith(("8", "4")):
        return f"BJ{code}"
    return f"SZ{code}"


def _cninfo_org_id(code: str) -> str:
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def _eastmoney_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Referer": "https://quote.eastmoney.com/",
    }


def _record_time_bound(
    records: list[dict[str, Any]],
    *,
    minimum: bool,
) -> str:
    values = sorted(
        str(row.get("event_time") or "")
        for row in records
        if row.get("event_time")
    )
    if not values:
        return ""
    return values[0] if minimum else values[-1]


def _qfq_proven(records: list[dict[str, Any]]) -> bool | None:
    daily = [
        row
        for row in records
        if row.get("data_type") == "daily_bar"
    ]
    if not daily:
        return None
    return all(
        (row.get("payload") or {}).get("adjustment") == "qfq"
        and "response_key=qfqday"
        in str(
            (row.get("payload") or {}).get(
                "adjustment_evidence"
            )
        )
        for row in daily
    )


def _contains_1450(records: list[dict[str, Any]]) -> bool | None:
    minute = [
        row
        for row in records
        if row.get("data_type") == "minute_bar"
    ]
    if not minute:
        return None
    return any(
        str(row.get("event_time") or "")[11:16] == "14:50"
        for row in minute
    )
