from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.minute_probe_sources import (
    SUPPORTED_MINUTE_PROBE_SOURCES,
)
from overnight_quant.data.probe_evidence import (
    verify_probe_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Independently recompute source-specific minute, "
            "transaction and combined evidence hashes."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--source",
        required=True,
        choices=SUPPORTED_MINUTE_PROBE_SOURCES,
    )
    args = parser.parse_args()

    path = Path(args.input)
    raw = path.read_bytes()
    encoding_errors = []
    if raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        encoding_errors.append("probe_json_must_be_utf8_without_bom")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result = {
            "status": "PROBE_EVIDENCE_INVALID",
            "execution_ok": False,
            "data_ready": False,
            "source": args.source,
            "errors": encoding_errors + [
                f"probe_json_invalid:{type(exc).__name__}"
            ],
            "candidates": [],
            "tickets": [],
            "orders": [],
        }
    else:
        result = verify_probe_evidence(payload, source=args.source)
        if encoding_errors:
            result["status"] = "PROBE_EVIDENCE_INVALID"
            result["errors"] = sorted(
                set(result.get("errors") or [])
                | set(encoding_errors)
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PROBE_EVIDENCE_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
