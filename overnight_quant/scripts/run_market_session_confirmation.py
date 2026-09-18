from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_session_confirmation import (
    SESSION_CONFIRMED,
    build_market_session_confirmation,
    write_market_session_confirmation_json_atomic,
)
from overnight_quant.data.source_capability_adapters import (
    SourceProviderEnvelope,
    execute_source_adapter,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_ADAPTER,
    TENCENT_ORIGIN_SOURCE,
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_SOURCE_VERSION,
    TencentDirectHttpProviders,
    TencentUrllibTransport,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create immutable Tencent current-session confirmation evidence"
    )
    parser.add_argument("--date", required=True)
    parser.add_argument("--codes", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not args.network:
        parser.error("--network is required; offline mode never fabricates confirmation")

    started = datetime.now(CN_TZ)
    provider = TencentDirectHttpProviders(
        args.codes.split(","),
        transport=TencentUrllibTransport(),
        clock=lambda: datetime.now(CN_TZ),
        timeout_seconds=args.timeout_seconds,
    )
    captured: dict[str, list[dict]] = {"records": []}

    def collect() -> list[dict]:
        records = provider.collect_quote_records()
        captured["records"] = records
        return records

    adapter_result = execute_source_adapter(
        "quote",
        origin_source=TENCENT_ORIGIN_SOURCE,
        adapter=TENCENT_ADAPTER,
        source_version=TENCENT_SOURCE_VERSION,
        provider_envelope=SourceProviderEnvelope(
            provider_key=TENCENT_QUOTE_PROVIDER_KEY,
            provider_callable=collect,
        ),
        environ={},
    )
    completed = datetime.now(CN_TZ)
    result = build_market_session_confirmation(
        trade_date=args.date,
        records=captured["records"],
        provider_key=TENCENT_QUOTE_PROVIDER_KEY,
        adapter_execution=adapter_result,
        started_at=started.isoformat(timespec="microseconds"),
        completed_at=completed.isoformat(timespec="microseconds"),
    )
    try:
        write_market_session_confirmation_json_atomic(args.output, result)
    except FileExistsError:
        print(
            json.dumps(
                {
                    "status": "MARKET_SESSION_CONFIRMATION_WRITE_REJECTED",
                    "reason": "immutable_result_exists",
                    "data_ready": False,
                    "hard_gate_authorized": False,
                    "candidates": [],
                    "tickets": [],
                    "orders": [],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == SESSION_CONFIRMED else 2


if __name__ == "__main__":
    raise SystemExit(main())
