from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from overnight_quant.backtest.preparation_sources import SourceBatch, SourceError


LIVE_PREP_HARD_MAX_CODES = 10
ASTOCK_DEFAULT_MAX_CODES = 10
ASTOCK_MIN_SLEEP_SECONDS = 0.2
ASTOCK_ENDPOINT_VERSION = "mock_contract_v1"
REAL_REQUEST_HARD_MAX_CODES = 3
REAL_REQUEST_MAX_DAYS = 31
ASTOCK_FAKE_REAL_ENDPOINT_VERSION = "fake_real_contract_v1"
REAL_FIRST_RUN_MAX_CODES = 1
REAL_FIRST_RUN_MAX_DAYS = 10
ASTOCK_REAL_ENDPOINT_VERSION = "real_historical_first_run_v1"
REAL_SCOPE_MINIMAL = "minimal"
REAL_SCOPE_EXPANDED = "expanded"
REAL_EXPANDED_REQUEST_CONTRACT = "real_expanded_validation"

_REQUIRED_DAILY_FIELDS = ("trade_date", "open", "high", "low", "close", "volume", "amount")
_REQUIRED_BENCHMARK_FIELDS = ("trade_date", "open", "high", "low", "close")


class AStockHistoricalClientProtocol(Protocol):
    def fetch_daily_bars(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_stock_metadata(self, code: str) -> dict:
        raise NotImplementedError

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError


class AStockRealHistoricalClientProtocol(Protocol):
    def fetch_daily_bars(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_stock_metadata(self, code: str) -> dict:
        raise NotImplementedError

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        raise NotImplementedError


@dataclass
class SourceAudit:
    requested_codes_count: int
    effective_max_codes: int
    cache_enabled: bool = False
    network_requests_made: int = 0
    mock_client_calls: int = 0
    cache_hits: int = 0
    cache_writes: int = 0
    cache_read_failures: int = 0
    benchmark_source: str = ""
    field_truth: dict[str, str] = field(default_factory=dict)
    request_contract: str = "mocked_contract"
    real_request_enabled: bool = False
    fake_real_client_prepared: bool = False
    effective_real_request_max_codes: int | None = None
    requested_date_range_days: int | None = None
    max_allowed_days: int | None = None
    real_first_run_prepared: bool = False
    endpoint_attempts: dict[str, int] = field(default_factory=dict)
    endpoint_successes: dict[str, int] = field(default_factory=dict)
    endpoint_failures: dict[str, int] = field(default_factory=dict)
    real_request_scope: str = REAL_SCOPE_MINIMAL
    partial_success: bool = False
    failed_codes: list[str] = field(default_factory=list)
    per_code_source_status: dict[str, dict[str, str]] = field(default_factory=dict)


def build_cache_path(
    cache_root: Path,
    source: str,
    endpoint_version: str,
    symbol: str,
    start: str,
    end: str,
    request_params: dict[str, str],
) -> Path:
    canonical = json.dumps(request_params, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return (
        Path(cache_root)
        / source
        / endpoint_version
        / symbol
        / f"{start}_{end}"
        / f"{digest}.json"
    )


class AStockHistoricalSource:
    name = "a-stock-data"

    def __init__(
        self,
        client: AStockHistoricalClientProtocol | AStockRealHistoricalClientProtocol,
        cache_dir: Path,
        effective_max_codes: int = ASTOCK_DEFAULT_MAX_CODES,
        benchmark_symbol: str = "sh000300",
        use_cache: bool = True,
        endpoint_version: str = ASTOCK_ENDPOINT_VERSION,
        request_contract: str = "mocked_contract",
        real_request_enabled: bool = False,
        effective_real_request_max_codes: int | None = None,
        requested_date_range_days: int | None = None,
        max_allowed_days: int | None = None,
        real_request_scope: str = REAL_SCOPE_MINIMAL,
    ):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.effective_max_codes = effective_max_codes
        self.benchmark_symbol = benchmark_symbol
        self.use_cache = use_cache
        self.endpoint_version = endpoint_version
        self.request_contract = request_contract
        self.real_request_enabled = real_request_enabled
        self.effective_real_request_max_codes = effective_real_request_max_codes
        self.requested_date_range_days = requested_date_range_days
        self.max_allowed_days = max_allowed_days
        self.real_request_scope = real_request_scope

    def load(self, codes: list[str], start: str, end: str) -> SourceBatch:
        errors: list[SourceError] = []
        rows: list[dict] = []
        audit = SourceAudit(
            requested_codes_count=len(codes),
            effective_max_codes=self.effective_max_codes,
            cache_enabled=self.use_cache,
            request_contract=self.request_contract,
            real_request_enabled=self.real_request_enabled,
            fake_real_client_prepared=self.request_contract == "fake_real_validation",
            effective_real_request_max_codes=self.effective_real_request_max_codes,
            requested_date_range_days=self.requested_date_range_days,
            max_allowed_days=self.max_allowed_days,
            real_first_run_prepared=self.request_contract == "real_first_run",
            real_request_scope=self.real_request_scope,
            field_truth={
                "daily_bars": "REAL_HISTORICAL",
                "vol_ratio": "DAILY_PROXY",
                "range_position": "DAILY_PROXY",
                "market_gate": "DAILY_PROXY",
                "tail_pullback_pct": "UNAVAILABLE",
                "theme_tags": "UNAVAILABLE",
                "main_net": "UNAVAILABLE",
            },
        )
        for code in codes:
            audit.per_code_source_status[code] = {
                "daily_bars": "NOT_REQUESTED",
                "metadata": "NOT_REQUESTED",
            }
            bars = self._cached_or_fetch(
                "daily_bars",
                code,
                start,
                end,
                {"code": code, "start": start, "end": end},
                lambda code=code: self.client.fetch_daily_bars(code, start, end),
                "daily_bars_primary",
                "HISTORICAL_DAILY_BAR_SOURCE_FAILED",
                errors,
                audit,
            )
            self._merge_client_diagnostics(errors, audit)
            accepted = _accepted_rows(bars, code, start, end, _REQUIRED_DAILY_FIELDS)
            if not accepted:
                audit.per_code_source_status[code]["daily_bars"] = "FAILED"
                audit.failed_codes.append(code)
                if not _has_error_code(
                    errors,
                    code,
                    "daily_bars_primary",
                    "HISTORICAL_DAILY_BAR_SOURCE_FAILED",
                ):
                    errors.append(
                        _error(
                            code,
                            "daily_bars_primary",
                            "HISTORICAL_DAILY_BAR_SOURCE_FAILED",
                            "no accepted historical daily bars",
                        )
                    )
                continue
            audit.per_code_source_status[code]["daily_bars"] = "SUCCESS"
            metadata = self._cached_or_fetch(
                "metadata",
                code,
                "metadata",
                "metadata",
                {"code": code},
                lambda code=code: self.client.fetch_stock_metadata(code),
                "metadata",
                "HISTORICAL_METADATA_FAILED",
                errors,
                audit,
            )
            self._merge_client_diagnostics(errors, audit)
            metadata = metadata if isinstance(metadata, dict) else {}
            if (
                self._is_real_network_request()
                and not (metadata.get("name") or metadata.get("list_date"))
                and not _has_error_code(errors, code, "metadata", "HISTORICAL_METADATA_FAILED")
            ):
                errors.append(
                    _error(
                        code,
                        "metadata",
                        "HISTORICAL_METADATA_FAILED",
                        "metadata unavailable; list_date remains unknown",
                    )
                )
            audit.per_code_source_status[code]["metadata"] = (
                "SUCCESS"
                if metadata.get("name") and metadata.get("list_date")
                else "PARTIAL"
                if metadata.get("name") or metadata.get("list_date")
                else "FAILED"
            )
            for row in accepted:
                normalized = dict(row)
                normalized["code"] = code
                normalized["name"] = normalized.get("name") or metadata.get("name", "") or code
                normalized["list_date"] = normalized.get("list_date") or metadata.get("list_date", "")
                rows.append(normalized)

        benchmark = self._cached_or_fetch(
            "benchmark_bars",
            self.benchmark_symbol,
            start,
            end,
            {"symbol": self.benchmark_symbol, "start": start, "end": end},
            lambda: self.client.fetch_benchmark_bars(self.benchmark_symbol, start, end),
            "benchmark",
            "BENCHMARK_UNAVAILABLE",
            errors,
            audit,
        )
        self._merge_client_diagnostics(errors, audit)
        benchmark_rows = _accepted_rows(
            benchmark, self.benchmark_symbol, start, end, _REQUIRED_BENCHMARK_FIELDS
        )
        if not benchmark_rows and not _has_stage_error(errors, self.benchmark_symbol, "benchmark"):
            errors.append(
                _error(self.benchmark_symbol, "benchmark", "BENCHMARK_UNAVAILABLE", "no benchmark rows")
            )
        elif benchmark_rows:
            audit.benchmark_source = (
                "fake_real_historical_client"
                if self.request_contract == "fake_real_validation"
                else "real_historical_client"
                if self._is_real_network_request()
                else "mock_historical_client"
            )
        audit.partial_success = bool(rows) and any(
            status["daily_bars"] != "SUCCESS" or status["metadata"] != "SUCCESS"
            for status in audit.per_code_source_status.values()
        )

        return SourceBatch(
            daily_rows=rows,
            benchmark_rows=benchmark_rows,
            errors=errors,
            audit=asdict(audit),
        )

    def _cached_or_fetch(
        self,
        source: str,
        symbol: str,
        start: str,
        end: str,
        params: dict[str, str],
        loader: Callable[[], object],
        stage: str,
        error_code: str,
        errors: list[SourceError],
        audit: SourceAudit,
    ) -> object:
        cache_path = build_cache_path(
            self.cache_dir,
            source,
            self.endpoint_version,
            symbol,
            start,
            end,
            params,
        )
        if self.use_cache and cache_path.exists():
            try:
                value = json.loads(cache_path.read_text(encoding="utf-8"))
                audit.cache_hits += 1
                return value
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                errors.append(_error(symbol, "cache_read", "CACHE_READ_FAILED", str(exc)))
        try:
            if not self._is_real_network_request():
                audit.mock_client_calls += 1
            value = loader()
        except Exception as exc:
            errors.append(_error(symbol, stage, error_code, str(exc)))
            return [] if source != "metadata" else {}
        if self.use_cache:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(value, ensure_ascii=True, sort_keys=True),
                    encoding="utf-8",
                )
                audit.cache_writes += 1
            except OSError as exc:
                errors.append(_error(symbol, "cache_write", "CACHE_WRITE_FAILED", str(exc)))
        return value

    def _merge_client_diagnostics(self, errors: list[SourceError], audit: SourceAudit) -> None:
        drain = getattr(self.client, "drain_errors", None)
        if callable(drain):
            errors.extend(drain())
        snapshot = getattr(self.client, "audit_snapshot", None)
        if callable(snapshot):
            values = snapshot()
            audit.cache_enabled = bool(values.get("cache_enabled", audit.cache_enabled))
            audit.network_requests_made = int(values.get("network_requests_made", 0))
            audit.cache_hits = int(values.get("cache_hits", 0))
            audit.cache_writes = int(values.get("cache_writes", 0))
            audit.cache_read_failures = int(values.get("cache_read_failures", 0))
            audit.endpoint_attempts = dict(values.get("endpoint_attempts", {}))
            audit.endpoint_successes = dict(values.get("endpoint_successes", {}))
            audit.endpoint_failures = dict(values.get("endpoint_failures", {}))

    def _is_real_network_request(self) -> bool:
        return self.request_contract in {"real_first_run", REAL_EXPANDED_REQUEST_CONTRACT}


def _accepted_rows(
    value: object, code: str, start: str, end: str, required_fields: tuple[str, ...]
) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows: list[dict] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        trade_date = str(raw.get("trade_date") or "")
        if not (start <= trade_date <= end):
            continue
        if any(raw.get(field) in (None, "", "-") for field in required_fields):
            continue
        rows.append(dict(raw))
    return rows


def _error(code: str, stage: str, error_code: str, message: str) -> SourceError:
    return SourceError(
        source="a-stock-data",
        code=code,
        stage=stage,
        error_code=error_code,
        error_message=message,
        detail=message,
        retry_count=0,
    )


def _has_stage_error(errors: list[SourceError], code: str, stage: str) -> bool:
    return any(error.code == code and error.stage == stage for error in errors)


def _has_error_code(
    errors: list[SourceError], code: str, stage: str, error_code: str
) -> bool:
    return any(
        error.code == code and error.stage == stage and error.error_code == error_code
        for error in errors
    )
