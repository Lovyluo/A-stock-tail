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
    args = parser.parse_args()
    result = run_scheduled_minute_label_probe(
        [
            item.strip()
            for item in str(args.codes).split(",")
            if item.strip()
        ],
        trade_date=args.date,
        source=args.source,
    )
    if args.output:
        write_probe_json_atomic(result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        0
        if result.get("status") == "MINUTE_LABEL_VERIFIED"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
