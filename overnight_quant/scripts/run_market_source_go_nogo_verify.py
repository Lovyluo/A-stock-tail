from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_source_go_nogo import (
    file_sha256,
    verify_market_source_go_nogo_evidence,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify S2 Go/No-Go evidence")
    parser.add_argument("path")
    parser.add_argument("--expected-file-sha256", required=True)
    args = parser.parse_args(argv)
    path = Path(args.path).resolve()
    actual = file_sha256(path)
    evidence = json.loads(path.read_bytes().decode("utf-8"))
    result = verify_market_source_go_nogo_evidence(
        evidence,
        expected_file_sha256=args.expected_file_sha256,
        actual_file_sha256=actual,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["evidence_integrity_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
