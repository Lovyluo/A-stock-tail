from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.minute_label_probe import (
    run_scheduled_minute_label_probe,
    write_probe_json_atomic,
)
from overnight_quant.data.minute_probe_sources import (
    SUPPORTED_MINUTE_PROBE_SOURCES,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sample the real minute endpoint at 14:49:55, 14:50:05, "
            "14:50:30 and 14:51:05. No strategy outputs are created."
        )
    )
    parser.add_argument(
        "--codes",
        default="000001,600000,600519",
        help="Comma-separated liquid A-share codes.",
    )
    parser.add_argument("--date", default=None)
    parser.add_argument(
        "--source",
        choices=SUPPORTED_MINUTE_PROBE_SOURCES,
        default="eastmoney",
        help=(
            "Run one source-specific probe. Evidence from different "
            "sources is never combined."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional JSON path. Python writes UTF-8 atomically; "
            "do not pipe through Tee-Object."
        ),
    )
    parser.add_argument(
        "--endpoint",
        default="",
        help=(
            "Optional fixed mootdx host:port selected before the formal "
            "sampling window. A supplied endpoint is the only endpoint tried."
        ),
    )
    parser.add_argument(
        "--endpoint-id",
        default="",
        help="Optional stable identifier for the fixed mootdx endpoint.",
    )
    args = parser.parse_args()
    endpoint_candidates = None
    if args.endpoint:
        if args.source != "mootdx":
            parser.error("--endpoint is only valid with --source mootdx")
        endpoint_candidates = [
            _parse_fixed_endpoint(args.endpoint, args.endpoint_id)
        ]
    probe_kwargs = {
        "trade_date": args.date,
        "source": args.source,
    }
    if endpoint_candidates is not None:
        probe_kwargs["endpoint_candidates"] = endpoint_candidates
    result = run_scheduled_minute_label_probe(
        [
            item.strip()
            for item in str(args.codes).split(",")
            if item.strip()
        ],
        **probe_kwargs,
    )
    if args.output:
        write_probe_json_atomic(result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        0
        if result.get("status")
        in {"MINUTE_LABEL_VERIFIED", "MINUTE_LABEL_PROVISIONAL"}
        else 2
    )


def _parse_fixed_endpoint(value: str, endpoint_id: str = "") -> dict:
    host, separator, raw_port = str(value or "").strip().rpartition(":")
    if not separator or not host.strip():
        raise argparse.ArgumentTypeError("endpoint must use host:port")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("endpoint port must be an integer") from exc
    if port <= 0 or port > 65535:
        raise argparse.ArgumentTypeError("endpoint port is out of range")
    normalized_host = host.strip()
    identifier = str(endpoint_id or "").strip()
    if not identifier:
        identifier = f"mootdx_fixed@{normalized_host}:{port}"
    return {
        "id": identifier,
        "name": "mootdx_fixed",
        "host": normalized_host,
        "port": port,
    }


if __name__ == "__main__":
    raise SystemExit(main())
