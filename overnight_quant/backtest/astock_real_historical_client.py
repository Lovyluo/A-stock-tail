from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from time import sleep
from typing import Callable, Protocol

from overnight_quant.backtest.astock_historical_source import (
    ASTOCK_REAL_ENDPOINT_VERSION,
    build_cache_path,
)
from overnight_quant.backtest.preparation_sources import SourceError


BAIDU_DAILY_URL = "https://finance.pae.baidu.com/selfselect/getstockquotation"
EASTMONEY_METADATA_URL = "https://push2.eastmoney.com/api/qt/stock/get"
UA = "Mozilla/5.0"


class JsonTransportProtocol(Protocol):
    def get_text(
        self,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> str: ...


class UrllibJsonTransport:
    def get_text(
        self,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> str:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}", headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")


class AStockRealHistoricalClient:
    def __init__(
        self,
        transport: JsonTransportProtocol,
        cache_dir: Path,
        sleep_seconds: float,
        timeout: float = 10.0,
        sleep_fn: Callable[[float], None] = sleep,
    ):
        self.transport = transport
        self.cache_dir = Path(cache_dir)
        self.sleep_seconds = sleep_seconds
        self.timeout = timeout
        self.sleep_fn = sleep_fn
        self._errors: list[SourceError] = []
        self._cache_enabled = True
        self._network_requests_made = 0
        self._cache_hits = 0
        self._cache_writes = 0
        self._cache_read_failures = 0
        self._has_requested = False
        self._endpoint_attempts: dict[str, int] = {}
        self._endpoint_successes: dict[str, int] = {}
        self._endpoint_failures: dict[str, int] = {}

    def fetch_daily_bars(self, code: str, start: str, end: str) -> list[dict]:
        params = {
            "all": "1",
            "isIndex": "false",
            "isBk": "false",
            "isBlock": "false",
            "isFutures": "false",
            "isStock": "true",
            "newFormat": "1",
            "group": "quotation_kline_ab",
            "finClientType": "pc",
            "code": code,
            "start_time": start.replace("-", ""),
            "ktype": "1",
        }
        payload = self._request_json(
            "baidu_daily_kline",
            code,
            start,
            end,
            BAIDU_DAILY_URL,
            params,
            {
                "User-Agent": UA,
                "Accept": "application/vnd.finance-web.v1+json",
                "Origin": "https://gushitong.baidu.com",
                "Referer": "https://gushitong.baidu.com/",
            },
            "daily_bars_primary",
        )
        if not payload:
            return []
        market_data = ((payload.get("Result") or {}).get("newMarketData") or {})
        keys = market_data.get("keys") or []
        rows_text = market_data.get("marketData") or ""
        if not keys or not isinstance(rows_text, str):
            self._add_error(
                code,
                "daily_bars_primary",
                "BAIDU_DAILY_KLINE_PARSE_FAILED",
                "newMarketData keys or marketData missing",
            )
            return []

        accepted: list[dict] = []
        out_of_range: list[str] = []
        invalid_rows = 0
        for row_text in rows_text.split(";"):
            if not row_text:
                continue
            raw = dict(zip(keys, row_text.split(",")))
            trade_date = _iso_date(raw.get("time") or raw.get("timestamp"))
            if not trade_date:
                invalid_rows += 1
                continue
            if not (start <= trade_date <= end):
                out_of_range.append(trade_date)
                continue
            accepted.append(
                {
                    "trade_date": trade_date,
                    "open": raw.get("open", ""),
                    "high": raw.get("high", ""),
                    "low": raw.get("low", ""),
                    "close": raw.get("close", ""),
                    "volume": raw.get("volume", ""),
                    "amount": raw.get("amount", ""),
                }
            )
        if out_of_range:
            self._add_error(
                code,
                "daily_bars_primary",
                "HISTORICAL_ROW_OUT_OF_REQUEST_RANGE",
                f"discarded_rows={len(out_of_range)}",
            )
        if invalid_rows:
            self._add_error(
                code,
                "daily_bars_primary",
                "BAIDU_DAILY_KLINE_PARSE_FAILED",
                f"invalid_dated_rows={invalid_rows}",
            )
        return accepted

    def fetch_stock_metadata(self, code: str) -> dict:
        market_code = "1" if code.startswith(("6", "9")) else "0"
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f57,f58,f189",
            "secid": f"{market_code}.{code}",
        }
        payload = self._request_json(
            "eastmoney_stock_metadata",
            code,
            "metadata",
            "metadata",
            EASTMONEY_METADATA_URL,
            params,
            {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
            "metadata",
        )
        if not payload:
            return {}
        raw = payload.get("data") or {}
        if not isinstance(raw, dict):
            self._add_error(
                code,
                "metadata",
                "HISTORICAL_METADATA_FAILED",
                "metadata payload is not an object",
            )
            return {}
        raw_date = raw.get("f189")
        list_date = _iso_date(raw_date)
        if raw_date not in (None, "") and not list_date:
            self._add_error(
                code,
                "metadata",
                "HISTORICAL_METADATA_FAILED",
                "list_date parse failed",
            )
        return {"name": str(raw.get("f58") or ""), "list_date": list_date}

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        self._add_error(
            symbol,
            "benchmark",
            "BENCHMARK_UNAVAILABLE",
            "not implemented in real historical preparation client",
        )
        return []

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        return []

    def drain_errors(self) -> list[SourceError]:
        errors = list(self._errors)
        self._errors.clear()
        return errors

    def audit_snapshot(self) -> dict:
        return {
            "cache_enabled": self._cache_enabled,
            "network_requests_made": self._network_requests_made,
            "cache_hits": self._cache_hits,
            "cache_writes": self._cache_writes,
            "cache_read_failures": self._cache_read_failures,
            "endpoint_attempts": dict(self._endpoint_attempts),
            "endpoint_successes": dict(self._endpoint_successes),
            "endpoint_failures": dict(self._endpoint_failures),
        }

    def _request_json(
        self,
        source: str,
        symbol: str,
        start: str,
        end: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        stage: str,
    ) -> dict:
        cache_path = build_cache_path(
            self.cache_dir,
            source,
            ASTOCK_REAL_ENDPOINT_VERSION,
            symbol,
            start,
            end,
            params,
        )
        if cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("cached response is not an object")
                self._cache_hits += 1
                return payload
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                self._cache_read_failures += 1
                self._add_error(symbol, "cache_read", "CACHE_READ_FAILED", str(exc))
        if self._has_requested:
            self.sleep_fn(self.sleep_seconds)
        self._has_requested = True
        self._network_requests_made += 1
        _increment(self._endpoint_attempts, source)
        try:
            text = self.transport.get_text(url, params, headers, self.timeout)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            _increment(self._endpoint_failures, source)
            self._add_error(symbol, stage, "HTTP_REQUEST_FAILED", str(exc))
            return {}
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text, encoding="utf-8")
            self._cache_writes += 1
        except OSError as exc:
            self._add_error(symbol, "cache_write", "CACHE_WRITE_FAILED", str(exc))
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            _increment(self._endpoint_failures, source)
            self._add_error(symbol, stage, "JSON_PARSE_FAILED", str(exc))
            return {}
        if not isinstance(payload, dict):
            _increment(self._endpoint_failures, source)
            self._add_error(symbol, stage, "JSON_PARSE_FAILED", "response is not an object")
            return {}
        _increment(self._endpoint_successes, source)
        return payload

    def _add_error(self, code: str, stage: str, error_code: str, message: str) -> None:
        self._errors.append(
            SourceError(
                source="a-stock-data",
                code=code,
                stage=stage,
                error_code=error_code,
                error_message=message,
                detail=message,
                retry_count=0,
            )
        )


def _iso_date(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) < 8:
        return ""
    candidate = digits[:8]
    try:
        return date(int(candidate[:4]), int(candidate[4:6]), int(candidate[6:8])).isoformat()
    except ValueError:
        return ""


def _increment(values: dict[str, int], key: str) -> None:
    values[key] = values.get(key, 0) + 1
