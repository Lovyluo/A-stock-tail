from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.mootdx_shadow_providers import (
    MOOTDX_QUALIFIED_CODES,
    MOOTDX_SHADOW_RECORDS_READY,
    build_mootdx_shadow_records,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build one read-only fixed-endpoint mootdx shadow record batch."
        )
    )
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--codes", default=",".join(MOOTDX_QUALIFIED_CODES))
    args = parser.parse_args(argv)
    result = build_mootdx_shadow_records(
        [item.strip() for item in args.codes.split(",") if item.strip()],
        network=bool(args.network),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == MOOTDX_SHADOW_RECORDS_READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
