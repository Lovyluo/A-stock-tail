from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from overnight_quant.backtest.astock_historical_source import (
    ASTOCK_DEFAULT_MAX_CODES,
    ASTOCK_MIN_SLEEP_SECONDS,
    LIVE_PREP_HARD_MAX_CODES,
    REAL_EXPANDED_REQUEST_CONTRACT,
    REAL_FIRST_RUN_MAX_CODES,
    REAL_FIRST_RUN_MAX_DAYS,
    REAL_SCOPE_EXPANDED,
    REAL_SCOPE_MINIMAL,
    REAL_REQUEST_HARD_MAX_CODES,
    REAL_REQUEST_MAX_DAYS,
)
from overnight_quant.backtest.preparation_sources import (
    LocalRawSource,
    SamplePreparationSource,
    SourceBatch,
    SourceError,
)


DAILY_BAR_FIELDS = [
    "trade_date",
    "code",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turnover_pct",
    "change_pct",
    "float_mcap_yi",
    "limit_up",
    "limit_down",
    "is_st",
    "is_suspended",
    "list_date",
    "is_bj_stock",
    "ma5",
    "ma10",
    "ma20",
    "is_limit_down",
]
SELECTION_FIELDS = [
    "trade_date",
    "code",
    "vol_ratio",
    "range_position",
    "tail_pullback_pct",
    "theme_tags",
    "theme_rank",
    "same_theme_strong_count",
    "main_net",
    "big_order_net",
    "theme_source",
    "capital_source",
    "source_quality",
]
MARKET_FIELDS = [
    "trade_date",
    "market_gate",
    "index_change_pct",
    "market_reason",
    "market_proxy_used",
]
BENCHMARK_FIELDS = ["trade_date", "open", "high", "low", "close"]
PROCESSED_FILENAMES = [
    "daily_bars.csv",
    "selection_snapshots.csv",
    "market_snapshots.csv",
    "benchmark_bars.csv",
    "dataset_manifest.yaml",
]
COVERAGE_FIELDS = [
    "table",
    "field",
    "present_rows",
    "total_rows",
    "coverage_pct",
    "classification",
    "source_or_formula",
]
SOURCE_ERROR_FIELDS = ["source", "code", "trade_date", "error_code", "detail", "recoverable"]
ASTOCK_SOURCE_ERROR_FIELDS = ["code", "source", "stage", "error_code", "error_message", "retry_count"]
SAFETY_FIELDS = ["limit_up", "limit_down", "is_st", "is_suspended", "list_date"]
OPTIONAL_UNAVAILABLE_FIELDS = [
    "tail_pullback_pct",
    "theme_tags",
    "theme_rank",
    "main_net",
    "big_order_net",
]
WARNING_VALUES = [
    "DAILY_PROXY_ONLY",
    "NOT_STRICT_HISTORICAL",
    "NO_INTRADAY_TAIL_DATA_IF_APPLICABLE",
    "NO_HISTORICAL_THEME_IF_APPLICABLE",
    "NO_HISTORICAL_FUND_FLOW_IF_APPLICABLE",
]


class DataPreparationError(Exception):
    def __init__(
        self,
        code: str,
        detail: str = "",
        source_errors: list[SourceError] | None = None,
        source_audit: dict | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.source_errors = source_errors or []
        self.source_audit = source_audit or {}


@dataclass
class PreparationRequest:
    source: str
    codes: list[str]
    start: str
    end: str
    out_dir: Path
    raw_dir: Path
    manifest_dir: Path
    sample_dir: Path | None = None
    sample_profile: str = "neutral"
    cache_dir: Path | None = None
    max_codes: int | None = 50
    sleep: float = 0.2
    overwrite: bool = False
    dry_run: bool = False
    effective_max_codes: int | None = None
    real_request_enabled: bool = False
    request_contract: str = "offline"
    effective_real_request_max_codes: int | None = None
    requested_date_range_days: int | None = None
    max_allowed_days: int | None = None
    real_first_run_enabled: bool = False
    real_first_run_max_codes: int | None = None
    real_first_run_max_days: int | None = None
    real_request_scope: str = REAL_SCOPE_MINIMAL


@dataclass
class PreparationResult:
    status: str
    out_dir: Path
    processed_files: dict[str, str] = field(default_factory=dict)
    audit_files: dict[str, str] = field(default_factory=dict)
    requested_code_count: int = 0
    capped_codes: list[str] = field(default_factory=list)
    code_count: int = 0
    trade_date_count: int = 0
    errors: list[SourceError] = field(default_factory=list)
    effective_max_codes: int | None = None
    network_requests_made: int = 0
    mock_client_calls: int = 0
    real_request_enabled: bool = False
    request_contract: str = "offline"
    effective_real_request_max_codes: int | None = None
    requested_date_range_days: int | None = None
    max_allowed_days: int | None = None
    cache_enabled: bool = False
    cache_hits: int = 0
    cache_writes: int = 0
    cache_read_failures: int = 0
    real_first_run_enabled: bool = False
    real_first_run_max_codes: int | None = None
    real_first_run_max_days: int | None = None
    real_request_scope: str = REAL_SCOPE_MINIMAL
    partial_success: bool = False
    failed_codes: list[str] = field(default_factory=list)
    per_code_source_status: dict[str, dict[str, str]] = field(default_factory=dict)


def prepare_dataset(
    request: PreparationRequest,
    now: datetime | None = None,
    source_override: object | None = None,
    source_factory: Callable[[PreparationRequest], object] | None = None,
) -> PreparationResult:
    normalized_codes = _validate_request(request)
    if request.dry_run:
        return PreparationResult(
            status="DRY_RUN",
            out_dir=request.out_dir,
            requested_code_count=len(request.codes),
            capped_codes=normalized_codes,
            effective_max_codes=request.effective_max_codes,
            network_requests_made=0,
            real_request_enabled=request.real_request_enabled,
            request_contract=request.request_contract,
            effective_real_request_max_codes=request.effective_real_request_max_codes,
            requested_date_range_days=request.requested_date_range_days,
            max_allowed_days=request.max_allowed_days,
            cache_enabled=_uses_raw_real_cache(request),
            real_first_run_enabled=request.real_first_run_enabled,
            real_first_run_max_codes=request.real_first_run_max_codes,
            real_first_run_max_days=request.real_first_run_max_days,
            real_request_scope=request.real_request_scope,
        )

    selected_source = source_override or (source_factory(request) if source_factory else None)
    if request.source == "a-stock-data" and selected_source is None:
        raise DataPreparationError(
            (
                "REAL_CLIENT_NOT_IMPLEMENTED_IN_PHASE_3_2C"
                if request.real_request_enabled
                else "REAL_NETWORK_NOT_ENABLED"
            ),
            source_audit=_empty_astock_audit(request, len(normalized_codes)),
        )
    source = selected_source or _source_for_request(request)
    batch = source.load(normalized_codes, request.start, request.end)
    daily_rows, selection_rows = _normalize_rows(batch.daily_rows, request.source, request.sample_profile)
    if not daily_rows:
        raise DataPreparationError(
            "NO_DAILY_BARS_FETCHED",
            _source_error_detail(batch.errors),
            source_errors=batch.errors,
            source_audit=batch.audit,
        )
    benchmark_rows = _normalize_benchmark_rows(batch.benchmark_rows)
    market_rows = _build_market_rows(benchmark_rows)
    created_at = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    coverage = _build_coverage(
        daily_rows, selection_rows, market_rows, benchmark_rows, request.source
    )
    manifest = _build_manifest(
        request, daily_rows, selection_rows, market_rows, benchmark_rows, created_at, batch.audit
    )

    request.out_dir.mkdir(parents=True, exist_ok=True)
    processed_files = {
        "daily_bars": _write_csv(request.out_dir / "daily_bars.csv", DAILY_BAR_FIELDS, daily_rows),
        "selection_snapshots": _write_csv(
            request.out_dir / "selection_snapshots.csv", SELECTION_FIELDS, selection_rows
        ),
        "market_snapshots": _write_csv(request.out_dir / "market_snapshots.csv", MARKET_FIELDS, market_rows),
        "benchmark_bars": _write_csv(
            request.out_dir / "benchmark_bars.csv", BENCHMARK_FIELDS, benchmark_rows
        ),
        "dataset_manifest": _write_yaml(request.out_dir / "dataset_manifest.yaml", manifest),
    }
    loadable = _validate_provider_loadable(request.out_dir)
    status = "PARTIAL_DATA_PREPARED" if batch.errors else "PREPARE_COMPLETED"
    audit_files = _write_audit_files(
        request,
        status,
        created_at,
        daily_rows,
        selection_rows,
        coverage,
        batch.errors,
        manifest,
        loadable,
        now,
    )
    return PreparationResult(
        status=status,
        out_dir=request.out_dir,
        processed_files=processed_files,
        audit_files=audit_files,
        requested_code_count=len(request.codes),
        capped_codes=normalized_codes,
        code_count=len({row["code"] for row in daily_rows}),
        trade_date_count=len({row["trade_date"] for row in daily_rows}),
        errors=batch.errors,
        effective_max_codes=request.effective_max_codes,
        network_requests_made=int(batch.audit.get("network_requests_made", 0)),
        mock_client_calls=int(batch.audit.get("mock_client_calls", 0)),
        real_request_enabled=request.real_request_enabled,
        request_contract=request.request_contract,
        effective_real_request_max_codes=request.effective_real_request_max_codes,
        requested_date_range_days=request.requested_date_range_days,
        max_allowed_days=request.max_allowed_days,
        cache_enabled=bool(batch.audit.get("cache_enabled", False)),
        cache_hits=int(batch.audit.get("cache_hits", 0)),
        cache_writes=int(batch.audit.get("cache_writes", 0)),
        cache_read_failures=int(batch.audit.get("cache_read_failures", 0)),
        real_first_run_enabled=request.real_first_run_enabled,
        real_first_run_max_codes=request.real_first_run_max_codes,
        real_first_run_max_days=request.real_first_run_max_days,
        real_request_scope=request.real_request_scope,
        partial_success=bool(batch.audit.get("partial_success", False)),
        failed_codes=list(batch.audit.get("failed_codes", [])),
        per_code_source_status=dict(batch.audit.get("per_code_source_status", {})),
    )


def write_failed_prepare_report(
    request: PreparationRequest, error: DataPreparationError, now: datetime | None = None
) -> dict[str, str]:
    created = now or datetime.now().astimezone()
    request.manifest_dir.mkdir(parents=True, exist_ok=True)
    token = _unique_audit_token(request.manifest_dir, created.strftime("%Y%m%d_%H%M%S"))
    path = request.manifest_dir / f"prepare_report_{token}.md"
    lines = [
        "# Historical Data Preparation Report",
        "",
        f"status: {error.code}",
        f"source: {request.source}",
        f"requested_codes: {','.join(request.codes)}",
        f"requested_codes_count: {len([code for code in request.codes if _normalize_code(code)])}",
        f"effective_max_codes: {_astock_effective_max(request) if request.source == 'a-stock-data' else 'not_applicable'}",
        f"real_request_enabled: {str(request.real_request_enabled).lower()}",
        f"real_request_scope: {request.real_request_scope if request.source == 'a-stock-data' else 'not_applicable'}",
        f"request_contract: {request.request_contract}",
        f"real_request_hard_max_codes: {REAL_REQUEST_HARD_MAX_CODES if request.real_request_enabled else 'not_applicable'}",
        f"effective_real_request_max_codes: {request.effective_real_request_max_codes if request.effective_real_request_max_codes is not None else 'not_applicable'}",
        f"requested_date_range_days: {request.requested_date_range_days if request.requested_date_range_days is not None else 'not_applicable'}",
        f"max_allowed_days: {request.max_allowed_days if request.max_allowed_days is not None else 'not_applicable'}",
        f"real_first_run_max_codes: {request.real_first_run_max_codes if request.real_first_run_max_codes is not None else 'not_applicable'}",
        f"real_first_run_max_days: {request.real_first_run_max_days if request.real_first_run_max_days is not None else 'not_applicable'}",
        f"first_run_scope_limit: {REAL_FIRST_RUN_MAX_CODES} code / {REAL_FIRST_RUN_MAX_DAYS} days"
        if request.real_first_run_enabled
        else "first_run_scope_limit: not_applicable",
        f"date_range: {request.start} to {request.end}",
        f"out_dir: {request.out_dir}",
        f"detail: {error.detail}",
        f"cache_enabled: {str(bool(error.source_audit.get('cache_enabled', False))).lower()}",
        f"cache_hits: {int(error.source_audit.get('cache_hits', 0))}",
        f"cache_writes: {int(error.source_audit.get('cache_writes', 0))}",
        f"cache_read_failures: {int(error.source_audit.get('cache_read_failures', 0))}",
        f"network_requests_made: {int(error.source_audit.get('network_requests_made', 0))}",
        f"partial_success: {str(bool(error.source_audit.get('partial_success', False))).lower()}",
        f"failed_codes: {','.join(error.source_audit.get('failed_codes', [])) or 'none'}",
        f"mock_client_calls: {int(error.source_audit.get('mock_client_calls', 0))}",
        f"endpoint_attempts: {error.source_audit.get('endpoint_attempts', {})}",
        f"endpoint_successes: {error.source_audit.get('endpoint_successes', {})}",
        f"endpoint_failures: {error.source_audit.get('endpoint_failures', {})}",
        "backtest_network_access: false",
        "daily_proxy_loadable: false",
        "strict_historical_supported: false",
        "",
        "Research Limitation: This preparation attempt produced no validated DAILY_PROXY dataset and is not suitable for strict_historical validation.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = {"prepare_report": str(path)}
    if request.source == "a-stock-data":
        error_path = request.manifest_dir / f"source_errors_{token}.csv"
        errors = error.source_errors or [
            SourceError(
                source=request.source,
                code="",
                stage="validation",
                error_code=error.code,
                error_message=error.detail,
                detail=error.detail,
            )
        ]
        result["source_errors"] = _write_csv(
            error_path,
            ASTOCK_SOURCE_ERROR_FIELDS,
            [_astock_error_row(item) for item in errors],
        )
    return result


def result_to_dict(result: PreparationResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["out_dir"] = str(result.out_dir)
    payload["errors"] = [asdict(error) for error in result.errors]
    return payload


def _validate_request(request: PreparationRequest) -> list[str]:
    normalized = list(dict.fromkeys(_normalize_code(code) for code in request.codes if _normalize_code(code)))
    if not normalized:
        raise DataPreparationError("CODES_REQUIRED")
    _validate_dates(request.start, request.end)
    if request.source == "a-stock-data":
        user_max = request.max_codes if request.max_codes is not None else ASTOCK_DEFAULT_MAX_CODES
        if user_max <= 0:
            raise DataPreparationError("CODES_REQUIRED", "max_codes_must_be_positive")
        if request.sleep < ASTOCK_MIN_SLEEP_SECONDS:
            raise DataPreparationError(
                "SLEEP_BELOW_MINIMUM",
                f"minimum={ASTOCK_MIN_SLEEP_SECONDS}",
                source_audit=_empty_astock_audit(request, len(normalized)),
            )
        if request.real_request_scope == REAL_SCOPE_EXPANDED and not request.real_request_enabled:
            raise DataPreparationError(
                "REAL_NETWORK_NOT_ENABLED",
                "expanded_scope_requires_enable_real_astock_request",
                source_audit=_empty_astock_audit(request, len(normalized)),
            )
        if request.real_request_enabled:
            request.effective_real_request_max_codes = min(user_max, REAL_REQUEST_HARD_MAX_CODES)
            request.effective_max_codes = request.effective_real_request_max_codes
            request.requested_date_range_days = _requested_date_range_days(request.start, request.end)
            request.max_allowed_days = REAL_REQUEST_MAX_DAYS
            if len(normalized) > request.effective_real_request_max_codes:
                raise DataPreparationError(
                    "MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT",
                    (
                        f"requested_codes_count={len(normalized)} "
                        f"effective_real_request_max_codes={request.effective_real_request_max_codes}"
                    ),
                    source_audit=_empty_astock_audit(request, len(normalized)),
                )
            if request.requested_date_range_days > REAL_REQUEST_MAX_DAYS:
                raise DataPreparationError(
                    "DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT",
                    (
                        f"requested_date_range_days={request.requested_date_range_days} "
                        f"max_allowed_days={REAL_REQUEST_MAX_DAYS}"
                    ),
                    source_audit=_empty_astock_audit(request, len(normalized)),
                )
            if request.real_request_scope == REAL_SCOPE_MINIMAL and request.real_first_run_enabled:
                request.real_first_run_max_codes = REAL_FIRST_RUN_MAX_CODES
                request.real_first_run_max_days = REAL_FIRST_RUN_MAX_DAYS
                request.effective_real_request_max_codes = min(user_max, REAL_FIRST_RUN_MAX_CODES)
                request.effective_max_codes = request.effective_real_request_max_codes
                request.max_allowed_days = REAL_FIRST_RUN_MAX_DAYS
                if (
                    len(normalized) > REAL_FIRST_RUN_MAX_CODES
                    or request.requested_date_range_days > REAL_FIRST_RUN_MAX_DAYS
                ):
                    raise DataPreparationError(
                        "REAL_FIRST_RUN_SCOPE_TOO_LARGE",
                        (
                            f"requested_codes_count={len(normalized)} "
                            f"real_first_run_max_codes={REAL_FIRST_RUN_MAX_CODES} "
                            f"requested_date_range_days={request.requested_date_range_days} "
                            f"real_first_run_max_days={REAL_FIRST_RUN_MAX_DAYS}"
                        ),
                        source_audit=_empty_astock_audit(request, len(normalized)),
                    )
        else:
            request.effective_max_codes = min(user_max, LIVE_PREP_HARD_MAX_CODES)
            if len(normalized) > request.effective_max_codes:
                raise DataPreparationError(
                    "MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT",
                    f"requested_codes_count={len(normalized)} effective_max_codes={request.effective_max_codes}",
                    source_audit=_empty_astock_audit(request, len(normalized)),
                )
        if not request.dry_run and not request.overwrite and _directory_has_user_output(request.out_dir):
            raise DataPreparationError(
                "DATA_DIR_EXISTS_WITHOUT_OVERWRITE",
                str(request.out_dir),
                source_audit=_empty_astock_audit(request, len(normalized)),
            )
        return normalized
    if request.source not in {"sample", "local-raw"}:
        raise DataPreparationError("SOURCE_NOT_IMPLEMENTED", request.source)
    if request.source == "sample" and request.sample_profile not in {"neutral", "positive"}:
        raise DataPreparationError("SAMPLE_PROFILE_INVALID", request.sample_profile)
    if request.max_codes is None or request.max_codes <= 0:
        raise DataPreparationError("CODES_REQUIRED", "max_codes_must_be_positive")
    if not request.dry_run and not request.overwrite and _directory_has_user_output(request.out_dir):
        raise DataPreparationError("DATA_DIR_EXISTS_WITHOUT_OVERWRITE", str(request.out_dir))
    return normalized[: request.max_codes]


def _validate_dates(start: str, end: str) -> None:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise DataPreparationError("DATE_RANGE_REQUIRED", str(exc)) from exc
    if start_date > end_date:
        raise DataPreparationError("DATE_RANGE_REQUIRED", "start_after_end")


def _requested_date_range_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def _source_for_request(request: PreparationRequest) -> LocalRawSource:
    if request.source == "sample":
        if request.sample_dir is None:
            raise DataPreparationError("NO_DAILY_BARS_FETCHED", "sample_dir_missing")
        return SamplePreparationSource(request.sample_dir)
    if request.source == "local-raw":
        return LocalRawSource(request.raw_dir)
    raise DataPreparationError("REAL_NETWORK_NOT_ENABLED")


def _normalize_rows(raw_rows: list[dict], source: str, sample_profile: str) -> tuple[list[dict], list[dict]]:
    daily_rows: list[dict] = []
    selection_rows: list[dict] = []
    by_code: dict[str, list[dict]] = {}
    for raw in sorted(raw_rows, key=lambda row: (str(row.get("code", "")), str(row.get("trade_date", "")))):
        code = str(raw["code"]).zfill(6)
        history = by_code.setdefault(code, [])
        close = _safe_float(raw.get("close"))
        high = _safe_float(raw.get("high"))
        low = _safe_float(raw.get("low"))
        volume = _safe_float(raw.get("volume"))
        prior_closes = [_safe_float(item.get("close")) for item in history]
        closes = [value for value in prior_closes if value is not None] + ([close] if close is not None else [])
        prior_volumes = [_safe_float(item.get("volume")) for item in history]
        prior_volumes = [value for value in prior_volumes if value is not None]
        raw_change = _safe_float(raw.get("change_pct"))
        prior_close = prior_closes[-1] if prior_closes else None
        daily = {
            "trade_date": raw.get("trade_date", ""),
            "code": code,
            "name": raw.get("name", ""),
            "open": _display_number(raw.get("open")),
            "high": _display_number(raw.get("high")),
            "low": _display_number(raw.get("low")),
            "close": _display_number(raw.get("close")),
            "volume": _display_number(raw.get("volume")),
            "amount": _display_number(raw.get("amount")),
            "turnover_pct": _display_number(raw.get("turnover_pct")),
            "change_pct": _display_number(raw_change if raw_change is not None else _calculate_change_pct(close, prior_close)),
            "float_mcap_yi": _display_number(raw.get("float_mcap_yi")),
            "limit_up": _display_number(raw.get("limit_up")),
            "limit_down": _display_number(raw.get("limit_down")),
            "is_st": _display_bool(raw.get("is_st")),
            "is_suspended": _display_bool(raw.get("is_suspended")),
            "list_date": raw.get("list_date", "") or "",
            "is_bj_stock": str(code.startswith(("4", "8"))).lower(),
            "ma5": _display_number(_rolling_close_indicator(closes, 5)),
            "ma10": _display_number(_rolling_close_indicator(closes, 10)),
            "ma20": _display_number(_rolling_close_indicator(closes, 20)),
            "is_limit_down": _display_bool(raw.get("is_limit_down")),
        }
        daily_rows.append(daily)
        use_sample_fixture = source == "sample" and sample_profile == "positive"
        selection_rows.append(
            {
                "trade_date": daily["trade_date"],
                "code": code,
                "vol_ratio": _display_number(_calculate_vol_ratio(volume, prior_volumes)),
                "range_position": _display_number(_calculate_range_position(close, high, low)),
                "tail_pullback_pct": "",
                "theme_tags": raw.get("theme_tags", "") if use_sample_fixture else "",
                "theme_rank": raw.get("theme_rank", "") if use_sample_fixture else "",
                "same_theme_strong_count": raw.get("same_theme_strong_count", "") if use_sample_fixture else "",
                "main_net": raw.get("main_net", "") if use_sample_fixture else "",
                "big_order_net": raw.get("big_order_net", "") if use_sample_fixture else "",
                "theme_source": raw.get("theme_source", "") if use_sample_fixture else "",
                "capital_source": raw.get("capital_source", "") if use_sample_fixture else "",
                "source_quality": "sample_fixture" if use_sample_fixture and raw.get("source_quality") == "sample_fixture" else f"{source}_daily_proxy",
            }
        )
        history.append(raw)
    return daily_rows, selection_rows


def _normalize_benchmark_rows(raw_rows: list[dict]) -> list[dict]:
    return [
        {field: _display_number(row.get(field)) if field != "trade_date" else str(row.get(field, "")) for field in BENCHMARK_FIELDS}
        for row in sorted(raw_rows, key=lambda item: str(item.get("trade_date", "")))
    ]


def _build_market_rows(benchmark_rows: list[dict]) -> list[dict]:
    result = []
    for row in benchmark_rows:
        open_price = _safe_float(row.get("open"))
        close_price = _safe_float(row.get("close"))
        direction = _calculate_change_pct(close_price, open_price)
        passed = direction is not None and direction > 0
        result.append(
            {
                "trade_date": row["trade_date"],
                "market_gate": "PASS" if passed else "FAIL",
                "index_change_pct": _display_number(direction),
                "market_reason": "benchmark_direction_proxy" if passed else "benchmark_direction_proxy_non_positive",
                "market_proxy_used": "true",
            }
        )
    return result


def _build_manifest(
    request: PreparationRequest,
    daily_rows: list[dict],
    selection_rows: list[dict],
    market_rows: list[dict],
    benchmark_rows: list[dict],
    created_at: str,
    source_audit: dict | None = None,
) -> dict:
    safety_unknown = [
        field for field in SAFETY_FIELDS if any(row.get(field, "") in ("", None) for row in daily_rows)
    ]
    notes = ["first_observation_change_pct_unavailable"] if any(row.get("change_pct") == "" for row in daily_rows) else []
    positive_profile = request.source == "sample" and request.sample_profile == "positive"
    source_audit = source_audit or {}
    manifest = {
        "dataset": f"{request.source}_daily_proxy_{request.start}_{request.end}",
        "created_at": created_at,
        "start": request.start,
        "end": request.end,
        "codes_count": len({row["code"] for row in daily_rows}),
        "trade_dates_count": len({row["trade_date"] for row in daily_rows}),
        "source": request.source,
        "sample_profile": request.sample_profile if request.source == "sample" else "not_applicable",
        "fidelity": "daily_proxy",
        "data_fidelity": "daily_proxy",
        "selection_as_of": "daily_close_proxy",
        "strict_historical_supported": False,
        "proxy_fields": [
            "change_pct_from_prior_close",
            "code_prefix_proxy",
            "daily_close_rolling_indicator",
            "volume_ratio_proxy",
            "daily_range_position_proxy",
            "benchmark_direction_proxy",
        ],
        "unavailable_fields": OPTIONAL_UNAVAILABLE_FIELDS + safety_unknown,
        "notes": notes,
        "sources": {
            "daily_bars": {
                "source": f"{request.source}_daily_bars",
                "fields": DAILY_BAR_FIELDS,
                "proxy_fields": ["change_pct", "is_bj_stock", "ma5", "ma10", "ma20"],
                "unavailable_fields": safety_unknown,
            },
            "selection_snapshots": {
                "source": "daily_bar_derivations_with_sample_fixture" if positive_profile else "daily_bar_derivations",
                "fields": SELECTION_FIELDS,
                "proxy_fields": ["vol_ratio", "range_position"],
                "unavailable_fields": OPTIONAL_UNAVAILABLE_FIELDS,
            },
            "market_snapshots": {
                "source": "benchmark_direction_proxy",
                "fields": MARKET_FIELDS,
                "proxy_fields": ["market_gate", "index_change_pct"],
                "unavailable_fields": [] if market_rows else ["market_gate"],
            },
            "benchmark_bars": {
                "source": f"{request.source}_benchmark",
                "fields": BENCHMARK_FIELDS,
            },
        },
        "warnings": WARNING_VALUES,
    }
    if positive_profile:
        manifest["fixture_fields"] = ["theme_tags", "theme_rank", "main_net", "big_order_net"]
        manifest["simulated_or_fixture_fields"] = ["theme_tags", "theme_rank", "main_net", "big_order_net"]
        manifest["fixture_field_source"] = "sample_fixture"
        manifest["disclosures"] = _positive_profile_disclosures()
    if request.source == "a-stock-data":
        fake_real = request.request_contract == "fake_real_validation"
        real_first_run = request.request_contract == "real_first_run"
        real_expanded = request.real_request_scope == REAL_SCOPE_EXPANDED and not fake_real
        real_network_prepared = real_first_run or real_expanded
        manifest.update(
            {
                "requested_codes_count": len(request.codes),
                "effective_max_codes": request.effective_max_codes,
                "real_request_enabled": request.real_request_enabled,
                "real_request_scope": request.real_request_scope,
                "request_contract": request.request_contract,
                "real_request_hard_max_codes": (
                    REAL_REQUEST_HARD_MAX_CODES if request.real_request_enabled else "not_applicable"
                ),
                "effective_real_request_max_codes": request.effective_real_request_max_codes,
                "requested_date_range_days": request.requested_date_range_days,
                "max_allowed_days": request.max_allowed_days,
                "real_first_run_max_codes": request.real_first_run_max_codes,
                "real_first_run_max_days": request.real_first_run_max_days,
                "network_prepared": real_network_prepared,
                "mock_client_prepared": not fake_real and not real_network_prepared,
                "fake_real_client_prepared": fake_real,
                "real_first_run_prepared": real_first_run,
                "real_expanded_prepared": real_expanded,
                "backtest_network_access": False,
                "cache_enabled": bool(source_audit.get("cache_enabled", False)),
                "network_requests_made": int(source_audit.get("network_requests_made", 0)),
                "mock_client_calls": int(source_audit.get("mock_client_calls", 0)),
                "cache_hits": int(source_audit.get("cache_hits", 0)),
                "cache_writes": int(source_audit.get("cache_writes", 0)),
                "cache_read_failures": int(source_audit.get("cache_read_failures", 0)),
                "endpoint_attempts": source_audit.get("endpoint_attempts", {}),
                "endpoint_successes": source_audit.get("endpoint_successes", {}),
                "endpoint_failures": source_audit.get("endpoint_failures", {}),
                "partial_success": bool(source_audit.get("partial_success", False)),
                "failed_codes": list(source_audit.get("failed_codes", [])),
                "per_code_source_status": source_audit.get("per_code_source_status", {}),
                "truth_levels": {
                    "REAL_HISTORICAL": (
                        "dated Baidu daily rows and disclosed Eastmoney stable metadata"
                        if real_network_prepared
                        else "dated rows supplied by injected historical client contract"
                    ),
                    "DAILY_PROXY": "daily-row derivations",
                    "SAMPLE_FIXTURE": "prohibited_for_a_stock_data",
                    "UNAVAILABLE": (
                        "not reconstructed in real historical preparation client"
                        if real_network_prepared
                        else "not reconstructed in mocked skeleton"
                    ),
                    "UNKNOWN": "unconfirmed required safety value",
                },
            }
        )
        manifest["warnings"] = WARNING_VALUES + (
            [
                "REAL_FIRST_RUN_EXPERIMENTAL_ONE_CODE_TEN_DAYS",
                "BENCHMARK_UNAVAILABLE_IN_REAL_FIRST_RUN",
                "NO_CURRENT_LIVE_BACKFILL",
            ]
            if real_first_run
            else [
                "REAL_EXPANDED_EXPERIMENTAL_THREE_CODES_THIRTY_ONE_DAYS",
                "BENCHMARK_UNAVAILABLE_IN_REAL_PREPARATION",
                "NO_CURRENT_LIVE_BACKFILL",
            ]
            if real_expanded
            else
            [
                "FAKE_REAL_CLIENT_ONLY_NO_NETWORK",
                "EXPERIMENTAL_SMALL_SCALE_REAL_HISTORICAL_PREPARATION_NOT_YET_ENABLED",
                "CURRENT_LIVE_VALUES_NOT_USED_FOR_HISTORICAL_BACKFILL",
            ]
            if fake_real
            else [
                "MOCK_CLIENT_ONLY_NO_REAL_NETWORK",
                "CURRENT_LIVE_VALUES_NOT_USED_FOR_HISTORICAL_BACKFILL",
            ]
        )
    return manifest


def _build_coverage(
    daily_rows: list[dict],
    selection_rows: list[dict],
    market_rows: list[dict],
    benchmark_rows: list[dict],
    source: str = "",
) -> list[dict]:
    specs = [
        ("daily_bars", daily_rows, DAILY_BAR_FIELDS),
        ("selection_snapshots", selection_rows, SELECTION_FIELDS),
        ("market_snapshots", market_rows, MARKET_FIELDS),
        ("benchmark_bars", benchmark_rows, BENCHMARK_FIELDS),
    ]
    coverage = []
    for table, rows, fields in specs:
        for field_name in fields:
            present = sum(1 for row in rows if row.get(field_name) not in ("", None, []))
            classification, origin = _coverage_classification(
                table, field_name, present, len(rows), rows, source
            )
            coverage.append(
                {
                    "table": table,
                    "field": field_name,
                    "present_rows": present,
                    "total_rows": len(rows),
                    "coverage_pct": round(present / len(rows) * 100, 2) if rows else 0.0,
                    "classification": classification,
                    "source_or_formula": origin,
                }
            )
    return coverage


def _coverage_classification(
    table: str, field_name: str, present: int, total: int, rows: list[dict], source: str = ""
) -> tuple[str, str]:
    if source == "a-stock-data":
        if table == "selection_snapshots" and field_name in OPTIONAL_UNAVAILABLE_FIELDS + [
            "same_theme_strong_count",
            "theme_source",
            "capital_source",
        ]:
            return "UNAVAILABLE", "not_reconstructed"
        if field_name in {"change_pct", "is_bj_stock", "ma5", "ma10", "ma20", "vol_ratio", "range_position", "market_gate", "index_change_pct", "market_proxy_used"}:
            return "DAILY_PROXY", "daily_historical_derivation"
        if table == "daily_bars" and field_name in SAFETY_FIELDS:
            return ("REAL_HISTORICAL" if total and present == total else "UNKNOWN"), "historical_source_contract"
        return "REAL_HISTORICAL", "historical_source_contract"
    fixture_source_field = (
        "theme_source"
        if field_name in {"theme_tags", "theme_rank", "same_theme_strong_count"}
        else "capital_source"
    )
    if (
        table == "selection_snapshots"
        and present
        and field_name in {"theme_tags", "theme_rank", "same_theme_strong_count", "main_net", "big_order_net"}
        and any(row.get(fixture_source_field) == "sample_fixture" for row in rows)
    ):
        return "sample_fixture", "sample_fixture"
    if table == "selection_snapshots" and field_name in OPTIONAL_UNAVAILABLE_FIELDS + ["same_theme_strong_count"]:
        return "unavailable", "not_reconstructed"
    if field_name in {"change_pct", "is_bj_stock", "ma5", "ma10", "ma20", "vol_ratio", "range_position", "market_gate", "index_change_pct", "market_proxy_used"}:
        return "proxy", "daily_historical_derivation"
    if table == "daily_bars" and field_name in SAFETY_FIELDS:
        return ("safety_source" if total and present == total else "safety_unknown"), "raw_source_only"
    return "source", "raw_input"


def _positive_profile_disclosures() -> list[str]:
    return [
        "positive profile uses deterministic sample_fixture theme/capital fields for pipeline validation",
        "not live-filled",
        "not strict historical",
        "not evidence of strategy profitability",
        "DAILY_PROXY only",
    ]


def _write_audit_files(
    request: PreparationRequest,
    status: str,
    created_at: str,
    daily_rows: list[dict],
    selection_rows: list[dict],
    coverage: list[dict],
    errors: list[SourceError],
    manifest: dict,
    loadable: bool,
    now: datetime | None,
) -> dict[str, str]:
    current = now or datetime.now().astimezone()
    stamp = current.strftime("%Y%m%d_%H%M%S")
    request.manifest_dir.mkdir(parents=True, exist_ok=True)
    token = _unique_audit_token(request.manifest_dir, stamp)
    report_path = request.manifest_dir / f"prepare_report_{token}.md"
    coverage_path = request.manifest_dir / f"field_coverage_{token}.csv"
    errors_path = request.manifest_dir / f"source_errors_{token}.csv"
    processed_dates = sorted({row["trade_date"] for row in daily_rows})
    lines = [
        "# Historical Data Preparation Report",
        "",
        f"status: {status}",
        f"source: {request.source}",
        f"sample_profile: {request.sample_profile if request.source == 'sample' else 'not_applicable'}",
        f"requested_codes: {','.join(request.codes)}",
        f"processed_codes: {','.join(sorted({row['code'] for row in daily_rows}))}",
        f"date_range: {request.start} to {request.end}",
        f"processed_date_range: {processed_dates[0]} to {processed_dates[-1]}",
        f"daily_proxy_loadable: {str(loadable).lower()}",
        "strict_historical_supported: false",
        f"proxy_fields: {', '.join(manifest['proxy_fields'])}",
        f"unavailable_fields: {', '.join(manifest['unavailable_fields'])}",
        f"safety_unknown_fields: {', '.join(value for value in SAFETY_FIELDS if value in manifest['unavailable_fields'])}",
        f"source_error_count: {len(errors)}",
        "",
        "Research Limitation: This dataset is suitable only for DAILY_PROXY research and is not suitable for strict_historical validation.",
    ]
    if request.source == "a-stock-data":
        insertion = lines.index("")
        lines[insertion:insertion] = [
            f"requested_codes_count: {len(request.codes)}",
            f"effective_max_codes: {request.effective_max_codes}",
            f"real_request_enabled: {str(request.real_request_enabled).lower()}",
            f"real_request_scope: {request.real_request_scope}",
            f"request_contract: {request.request_contract}",
            f"real_request_hard_max_codes: {REAL_REQUEST_HARD_MAX_CODES if request.real_request_enabled else 'not_applicable'}",
            f"effective_real_request_max_codes: {request.effective_real_request_max_codes if request.effective_real_request_max_codes is not None else 'not_applicable'}",
            f"requested_date_range_days: {request.requested_date_range_days if request.requested_date_range_days is not None else 'not_applicable'}",
            f"max_allowed_days: {request.max_allowed_days if request.max_allowed_days is not None else 'not_applicable'}",
            f"real_first_run_max_codes: {request.real_first_run_max_codes if request.real_first_run_max_codes is not None else 'not_applicable'}",
            f"real_first_run_max_days: {request.real_first_run_max_days if request.real_first_run_max_days is not None else 'not_applicable'}",
            f"first_run_scope_limit: {REAL_FIRST_RUN_MAX_CODES} code / {REAL_FIRST_RUN_MAX_DAYS} days"
            if request.real_first_run_enabled
            else "first_run_scope_limit: not_applicable",
            f"cache_enabled: {str(bool(manifest.get('cache_enabled', False))).lower()}",
            f"cache_hits: {int(manifest.get('cache_hits', 0))}",
            f"cache_writes: {int(manifest.get('cache_writes', 0))}",
            f"cache_read_failures: {int(manifest.get('cache_read_failures', 0))}",
            f"network_requests_made: {int(manifest.get('network_requests_made', 0))}",
            f"partial_success: {str(bool(manifest.get('partial_success', False))).lower()}",
            f"failed_codes: {','.join(manifest.get('failed_codes', [])) or 'none'}",
            f"mock_client_calls: {int(manifest.get('mock_client_calls', 0))}",
            f"endpoint_attempts: {manifest.get('endpoint_attempts', {})}",
            f"endpoint_successes: {manifest.get('endpoint_successes', {})}",
            f"endpoint_failures: {manifest.get('endpoint_failures', {})}",
            "backtest_network_access: false",
            "benchmark_status: unavailable_in_real_preparation"
            if _uses_raw_real_cache(request)
            else "benchmark_status: as_supplied_by_source",
        ]
        if manifest.get("per_code_source_status"):
            lines[insertion:insertion] = [
                "per_code_source_status:",
                *[
                    (
                        f"  {code}: daily_bars={status.get('daily_bars', 'UNKNOWN')}, "
                        f"metadata={status.get('metadata', 'UNKNOWN')}"
                    )
                    for code, status in manifest["per_code_source_status"].items()
                ],
            ]
    if request.source == "sample" and request.sample_profile == "positive":
        lines.extend(["", *_positive_profile_disclosures()])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_csv(coverage_path, COVERAGE_FIELDS, coverage)
    if request.source == "a-stock-data":
        _write_csv(errors_path, ASTOCK_SOURCE_ERROR_FIELDS, [_astock_error_row(error) for error in errors])
    else:
        _write_csv(errors_path, SOURCE_ERROR_FIELDS, [asdict(error) for error in errors])
    return {
        "prepare_report": str(report_path),
        "field_coverage": str(coverage_path),
        "source_errors": str(errors_path),
    }


def _unique_audit_token(manifest_dir: Path, stamp: str) -> str:
    index = 0
    while True:
        token = stamp if index == 0 else f"{stamp}_{index:02d}"
        candidate_paths = (
            manifest_dir / f"prepare_report_{token}.md",
            manifest_dir / f"field_coverage_{token}.csv",
            manifest_dir / f"source_errors_{token}.csv",
        )
        if not any(path.exists() for path in candidate_paths):
            return token
        index += 1


def _validate_provider_loadable(out_dir: Path) -> bool:
    from overnight_quant.backtest.historical_data import LocalCsvHistoricalDataProvider

    LocalCsvHistoricalDataProvider(out_dir)
    return True


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def _write_yaml(path: Path, value: dict) -> str:
    path.write_text(_yaml_lines(value), encoding="utf-8")
    return str(path)


def _yaml_lines(value: dict, indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    for key, item in value.items():
        if isinstance(item, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_yaml_lines(item, indent + 2).rstrip("\n"))
        elif isinstance(item, list):
            if not item:
                lines.append(f"{prefix}{key}: []")
            else:
                lines.append(f"{prefix}{key}:")
                for member in item:
                    lines.append(f"{prefix}  - {_yaml_scalar(member)}")
        else:
            lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
    return "\n".join(lines) + "\n"


def _yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return '""'
    text = str(value)
    if not text or any(char in text for char in ":#[]{}"):
        return f'"{text}"'
    return text


def _normalize_code(value: object) -> str:
    text = str(value or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if text.startswith(prefix):
            text = text[2:]
    if "." in text:
        text = text.split(".", 1)[0]
    digits = "".join(char for char in text if char.isdigit())
    return digits.zfill(6) if digits else ""


def _directory_has_user_output(path: Path) -> bool:
    if not path.exists():
        return False
    return any(item.name not in {".gitignore", ".gitkeep"} for item in path.iterdir())


def _source_error_detail(errors: list[SourceError]) -> str:
    return "; ".join(f"{error.error_code}:{error.error_message or error.detail}" for error in errors)


def _astock_effective_max(request: PreparationRequest) -> int:
    user_max = request.max_codes if request.max_codes is not None else ASTOCK_DEFAULT_MAX_CODES
    return min(user_max, LIVE_PREP_HARD_MAX_CODES)


def _empty_astock_audit(request: PreparationRequest, requested_count: int) -> dict:
    return {
        "requested_codes_count": requested_count,
        "effective_max_codes": request.effective_max_codes
        if request.effective_max_codes is not None
        else _astock_effective_max(request),
        "network_requests_made": 0,
        "mock_client_calls": 0,
        "real_request_enabled": request.real_request_enabled,
        "real_request_scope": request.real_request_scope,
        "request_contract": request.request_contract,
        "effective_real_request_max_codes": request.effective_real_request_max_codes,
        "requested_date_range_days": request.requested_date_range_days,
        "max_allowed_days": request.max_allowed_days,
        "real_first_run_enabled": request.real_first_run_enabled,
        "real_first_run_max_codes": request.real_first_run_max_codes,
        "real_first_run_max_days": request.real_first_run_max_days,
        "cache_enabled": _uses_raw_real_cache(request),
        "cache_hits": 0,
        "cache_writes": 0,
        "cache_read_failures": 0,
        "partial_success": False,
        "failed_codes": [],
        "per_code_source_status": {},
        "endpoint_attempts": {},
        "endpoint_successes": {},
        "endpoint_failures": {},
    }


def _astock_error_row(error: SourceError) -> dict:
    return {
        "code": error.code,
        "source": error.source,
        "stage": error.stage or "source",
        "error_code": error.error_code,
        "error_message": error.error_message or error.detail,
        "retry_count": error.retry_count,
    }


def _uses_raw_real_cache(request: PreparationRequest) -> bool:
    return request.request_contract in {"real_first_run", REAL_EXPANDED_REQUEST_CONTRACT}


def _safe_float(value: object) -> float | None:
    try:
        return None if value in (None, "", "-") else float(value)
    except (TypeError, ValueError):
        return None


def _calculate_change_pct(close: float | None, previous_close: float | None) -> float | None:
    if close is None or previous_close in (None, 0):
        return None
    return round((close / previous_close - 1) * 100, 4)


def _calculate_range_position(close: float | None, high: float | None, low: float | None) -> float | None:
    if close is None or high is None or low is None or high <= low:
        return None
    return round((close - low) / (high - low), 4)


def _calculate_vol_ratio(volume: float | None, earlier_volumes: list[float]) -> float | None:
    prior = earlier_volumes[-5:]
    if volume is None or not prior or sum(prior) <= 0:
        return None
    return round(volume / (sum(prior) / len(prior)), 4)


def _rolling_close_indicator(closes: list[float], window: int) -> float | None:
    if not closes:
        return None
    sample = closes[-window:]
    return round(sum(sample) / len(sample), 4)


def _display_number(value: object) -> str:
    number = _safe_float(value)
    return "" if number is None else str(number).rstrip("0").rstrip(".") if "." in str(number) else str(number)


def _display_bool(value: object) -> str:
    if value in (None, "", "-"):
        return ""
    return str(str(value).lower() in {"true", "1", "yes"}).lower()
