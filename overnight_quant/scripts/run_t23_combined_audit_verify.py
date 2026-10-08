from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.t23_combined_audit import verify_combined_audit_file
from overnight_quant.scripts.run_market_source_validation import write_json_atomic


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Offline anchored T2.3 combined audit replay")
    parser.add_argument("path")
    parser.add_argument("--expected-file-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = verify_combined_audit_file(args.path, expected_file_sha256=args.expected_file_sha256)
    write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["evidence_integrity_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
