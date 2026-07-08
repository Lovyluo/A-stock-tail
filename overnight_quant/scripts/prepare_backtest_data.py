from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.backtest.data_preparation import (
    DataPreparationError,
    PreparationRequest,
    prepare_dataset,
    result_to_dict,
    write_failed_prepare_report,
)
from overnight_quant.backtest.astock_historical_source import (
    ASTOCK_DEFAULT_MAX_CODES,
    ASTOCK_FAKE_REAL_ENDPOINT_VERSION,
    ASTOCK_REAL_ENDPOINT_VERSION,
    AStockHistoricalSource,
    LIVE_PREP_HARD_MAX_CODES,
    REAL_EXPANDED_REQUEST_CONTRACT,
    REAL_FIRST_RUN_MAX_CODES,
    REAL_REQUEST_HARD_MAX_CODES,
    REAL_SCOPE_EXPANDED,
    REAL_SCOPE_MINIMAL,
)
from overnight_quant.backtest.astock_real_historical_client import (
    AStockRealHistoricalClient,
    UrllibJsonTransport,
)
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def run_prepare(
    source: str,
    sample_profile: str = "neutral",
    codes: list[str] | None = None,
    codes_file: str | None = None,
    start: str = "",
    end: str = "",
    out_dir: str | None = None,
    raw_dir: str | None = None,
    max_codes: int | None = None,
    sleep: float = 0.2,
    overwrite: bool = False,
    dry_run: bool = False,
    config: dict | None = None,
    historical_client=None,
    enable_real_astock_request: bool = False,
    real_request_scope: str = REAL_SCOPE_MINIMAL,
    real_historical_client=None,
    real_transport=None,
) -> dict:
    active_config = config or load_config()
    backtest_config = active_config.get("backtest", {})
    resolved_codes = list(codes or [])
    if codes_file:
        resolved_codes.extend(_read_codes_file(_resolve_path(codes_file)))
    sample_dir_setting = (
        backtest_config.get(
            "preparation_positive_sample_dir",
            "overnight_quant/examples/historical_prepare_positive_raw",
        )
        if source == "sample" and sample_profile == "positive"
        else backtest_config.get("preparation_sample_dir", "overnight_quant/examples/historical_prepare_raw")
    )
    resolved_max_codes = (
        REAL_REQUEST_HARD_MAX_CODES
        if source == "a-stock-data" and enable_real_astock_request and max_codes is None
        else ASTOCK_DEFAULT_MAX_CODES
        if source == "a-stock-data" and max_codes is None
        else (50 if max_codes is None else max_codes)
    )
    actual_real_request = (
        source == "a-stock-data"
        and enable_real_astock_request
        and real_historical_client is None
    )
    request = PreparationRequest(
        source=source,
        codes=resolved_codes,
        start=start,
        end=end,
        out_dir=_resolve_path(out_dir or backtest_config.get("local_data_dir", "overnight_quant/backtest_data/processed")),
        raw_dir=_resolve_path(raw_dir or backtest_config.get("raw_data_dir", "overnight_quant/backtest_data/raw")),
        manifest_dir=_resolve_path(backtest_config.get("manifest_dir", "overnight_quant/backtest_data/manifests")),
        sample_dir=_resolve_path(sample_dir_setting),
        sample_profile=sample_profile if source == "sample" else "neutral",
        cache_dir=_resolve_path(
            backtest_config.get(
                "historical_cache_dir", "overnight_quant/backtest_data/cache/a_stock_data"
            )
        ),
        max_codes=resolved_max_codes,
        sleep=sleep,
        overwrite=overwrite,
        dry_run=dry_run,
        real_request_enabled=source == "a-stock-data" and enable_real_astock_request,
        real_first_run_enabled=(
            actual_real_request
            and real_request_scope == REAL_SCOPE_MINIMAL
        ),
        request_contract=(
            "fake_real_validation"
            if source == "a-stock-data" and enable_real_astock_request and real_historical_client is not None
            else "real_first_run"
            if actual_real_request and real_request_scope == REAL_SCOPE_MINIMAL
            else REAL_EXPANDED_REQUEST_CONTRACT
            if actual_real_request and real_request_scope == REAL_SCOPE_EXPANDED
            else (
                "mocked_contract"
                if source == "a-stock-data" and (historical_client is not None or dry_run)
                else "real_cli_guard"
                if source == "a-stock-data"
                else "offline"
            )
        ),
        real_request_scope=real_request_scope if source == "a-stock-data" else REAL_SCOPE_MINIMAL,
    )
    try:
        source_override = None
        source_factory = None
        if (
            source == "a-stock-data"
            and enable_real_astock_request
            and real_historical_client is not None
            and not dry_run
        ):
            source_factory = lambda validated: AStockHistoricalSource(
                real_historical_client,
                validated.cache_dir,
                effective_max_codes=validated.effective_real_request_max_codes
                or REAL_REQUEST_HARD_MAX_CODES,
                endpoint_version=ASTOCK_FAKE_REAL_ENDPOINT_VERSION,
                request_contract=validated.request_contract,
                real_request_enabled=validated.real_request_enabled,
                effective_real_request_max_codes=validated.effective_real_request_max_codes,
                requested_date_range_days=validated.requested_date_range_days,
                max_allowed_days=validated.max_allowed_days,
                real_request_scope=validated.real_request_scope,
            )
        elif (
            source == "a-stock-data"
            and enable_real_astock_request
            and real_historical_client is None
            and not dry_run
        ):
            def build_real_source(validated):
                client = AStockRealHistoricalClient(
                    transport=real_transport or UrllibJsonTransport(),
                    cache_dir=validated.cache_dir,
                    sleep_seconds=validated.sleep,
                )
                return AStockHistoricalSource(
                    client,
                    validated.cache_dir,
                    effective_max_codes=validated.effective_real_request_max_codes
                    or REAL_FIRST_RUN_MAX_CODES,
                    endpoint_version=ASTOCK_REAL_ENDPOINT_VERSION,
                    request_contract=validated.request_contract,
                    real_request_enabled=True,
                    effective_real_request_max_codes=validated.effective_real_request_max_codes,
                    requested_date_range_days=validated.requested_date_range_days,
                    max_allowed_days=validated.max_allowed_days,
                    real_request_scope=validated.real_request_scope,
                    use_cache=False,
                )

            source_factory = build_real_source
        elif source == "a-stock-data" and historical_client is not None and not dry_run:
            source_override = AStockHistoricalSource(
                historical_client,
                request.cache_dir,
                effective_max_codes=min(resolved_max_codes, LIVE_PREP_HARD_MAX_CODES),
            )
        return result_to_dict(
            prepare_dataset(
                request,
                source_override=source_override,
                source_factory=source_factory,
            )
        )
    except DataPreparationError as exc:
        audit_files = (
            write_failed_prepare_report(request, exc)
            if not dry_run or source == "a-stock-data"
            else {}
        )
        return {
            "error": exc.code,
            "detail": exc.detail,
            "source": source,
            "out_dir": str(request.out_dir),
            "audit_files": audit_files,
            "requested_code_count": len(resolved_codes),
            "effective_max_codes": request.effective_max_codes
            if request.effective_max_codes is not None
            else (min(resolved_max_codes, LIVE_PREP_HARD_MAX_CODES) if source == "a-stock-data" else None),
            "network_requests_made": int(exc.source_audit.get("network_requests_made", 0)),
            "mock_client_calls": int(exc.source_audit.get("mock_client_calls", 0)),
            "cache_enabled": bool(exc.source_audit.get("cache_enabled", False)),
            "cache_hits": int(exc.source_audit.get("cache_hits", 0)),
            "cache_writes": int(exc.source_audit.get("cache_writes", 0)),
            "cache_read_failures": int(exc.source_audit.get("cache_read_failures", 0)),
            "real_request_enabled": request.real_request_enabled,
            "real_request_scope": request.real_request_scope,
            "request_contract": request.request_contract,
            "effective_real_request_max_codes": request.effective_real_request_max_codes,
            "requested_date_range_days": request.requested_date_range_days,
            "max_allowed_days": request.max_allowed_days,
            "real_first_run_enabled": request.real_first_run_enabled,
            "real_first_run_max_codes": request.real_first_run_max_codes,
            "real_first_run_max_days": request.real_first_run_max_days,
            "partial_success": bool(exc.source_audit.get("partial_success", False)),
            "failed_codes": list(exc.source_audit.get("failed_codes", [])),
            "per_code_source_status": dict(exc.source_audit.get("per_code_source_status", {})),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare offline historical data for daily-proxy research.")
    parser.add_argument("--source", required=True, choices=["sample", "local-raw", "a-stock-data"])
    parser.add_argument("--sample-profile", default="neutral", choices=["neutral", "positive"])
    code_group = parser.add_mutually_exclusive_group()
    code_group.add_argument("--codes")
    code_group.add_argument("--codes-file")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--max-codes", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enable-real-astock-request", action="store_true")
    parser.add_argument(
        "--real-request-scope",
        default=REAL_SCOPE_MINIMAL,
        choices=[REAL_SCOPE_MINIMAL, REAL_SCOPE_EXPANDED],
    )
    args = parser.parse_args()
    result = run_prepare(
        source=args.source,
        sample_profile=args.sample_profile,
        codes=_parse_inline_codes(args.codes),
        codes_file=args.codes_file,
        start=args.start,
        end=args.end,
        out_dir=args.out_dir,
        raw_dir=args.raw_dir,
        max_codes=args.max_codes,
        sleep=args.sleep,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        enable_real_astock_request=args.enable_real_astock_request,
        real_request_scope=args.real_request_scope,
    )
    if result.get("error"):
        print(f"{result['error']}: {result.get('detail', '')}".rstrip())
        if args.source == "a-stock-data":
            print(f"real_request_enabled: {str(result.get('real_request_enabled', False)).lower()}")
            print(f"real_request_scope: {result.get('real_request_scope', REAL_SCOPE_MINIMAL)}")
            print(f"requested_codes_count: {result.get('requested_code_count', 0)}")
            print(
                f"effective_real_request_max_codes: {result.get('effective_real_request_max_codes', 'not_applicable')}"
            )
            print(f"requested_date_range_days: {result.get('requested_date_range_days', 'not_applicable')}")
            print(f"max_allowed_days: {result.get('max_allowed_days', 'not_applicable')}")
            print(f"cache_enabled: {str(result.get('cache_enabled', False)).lower()}")
            print(f"cache_hits: {result.get('cache_hits', 0)}")
            print(f"cache_writes: {result.get('cache_writes', 0)}")
            print(f"cache_read_failures: {result.get('cache_read_failures', 0)}")
            print(f"network_requests_made: {result.get('network_requests_made', 0)}")
            print(f"partial_success: {str(result.get('partial_success', False)).lower()}")
            print(f"failed_codes: {','.join(result.get('failed_codes', [])) or 'none'}")
        for name, path in result.get("audit_files", {}).items():
            print(f"{name}: {path}")
        return 2
    print(result["status"])
    print(f"Output Directory: {result.get('out_dir', '')}")
    if args.source == "sample":
        print(f"Sample Profile: {args.sample_profile}")
    if args.source == "a-stock-data":
        print(f"real_request_enabled: {str(result.get('real_request_enabled', False)).lower()}")
        print(f"real_request_scope: {result.get('real_request_scope', REAL_SCOPE_MINIMAL)}")
        print(f"requested_codes_count: {result.get('requested_code_count', 0)}")
        print(
            f"effective_real_request_max_codes: {result.get('effective_real_request_max_codes', 'not_applicable')}"
        )
        print(f"requested_date_range_days: {result.get('requested_date_range_days', 'not_applicable')}")
        print(f"max_allowed_days: {result.get('max_allowed_days', 'not_applicable')}")
        print(f"cache_enabled: {str(result.get('cache_enabled', False)).lower()}")
        print(f"cache_hits: {result.get('cache_hits', 0)}")
        print(f"cache_writes: {result.get('cache_writes', 0)}")
        print(f"cache_read_failures: {result.get('cache_read_failures', 0)}")
        print(f"network_requests_made: {result.get('network_requests_made', 0)}")
        print(f"partial_success: {str(result.get('partial_success', False)).lower()}")
        print(f"failed_codes: {','.join(result.get('failed_codes', [])) or 'none'}")
    if result["status"] == "DRY_RUN":
        print(f"Codes: {','.join(result.get('capped_codes', []))}")
        print(f"Date Range: {args.start} to {args.end}")
    for name, path in result.get("audit_files", {}).items():
        print(f"{name}: {path}")
    return 0


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _parse_inline_codes(value: str | None) -> list[str]:
    return [token.strip() for token in (value or "").replace("\n", ",").split(",") if token.strip()]


def _read_codes_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    tokens: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.split("#", 1)[0].strip()
        tokens.extend(_parse_inline_codes(cleaned))
    return tokens


if __name__ == "__main__":
    raise SystemExit(main())
