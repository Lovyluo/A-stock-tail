from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.source_qualification import (
    evaluate_minute_source_qualification,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate source-specific minute probe evidence. "
            "This never changes formal configuration."
        )
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=("eastmoney", "mootdx"),
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="Source-specific UTF-8 probe JSON; repeat per day.",
    )
    parser.add_argument(
        "--trading-calendar",
        required=True,
        help=(
            "UTF-8 JSON object containing trade_dates, source, "
            "source_version and raw_hash from a trusted calendar."
        ),
    )
    parser.add_argument(
        "--codes",
        default="000001,000333,600000,600519,601318",
    )
    args = parser.parse_args()

    probe_results = [
        json.loads(Path(value).read_text(encoding="utf-8"))
        for value in args.input
    ]
    calendar_payload = json.loads(
        Path(args.trading_calendar).read_text(encoding="utf-8")
    )
    result = evaluate_minute_source_qualification(
        probe_results,
        source=args.source,
        trading_calendar=calendar_payload,
        expected_codes=[
            value.strip()
            for value in str(args.codes).split(",")
            if value.strip()
        ],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        0
        if result["qualified_for_configuration_review"]
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
