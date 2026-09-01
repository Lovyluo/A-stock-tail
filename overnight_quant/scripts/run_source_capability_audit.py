from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.source_capability_registry import (
    audit_source_capabilities,
    route_source_capability,
)


def run_source_capability_audit(
    *,
    capability: str = "",
    origin_source: str = "",
    adapter: str = "",
    require_hard_gate: bool = False,
) -> dict:
    result = audit_source_capabilities()
    if capability:
        result["route_audit"] = route_source_capability(
            capability,
            origin_source=origin_source,
            adapter=adapter,
            require_hard_gate=require_hard_gate,
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the static source capability registry without network "
            "requests or configuration changes."
        )
    )
    parser.add_argument("--capability", default="")
    parser.add_argument("--origin-source", default="")
    parser.add_argument("--adapter", default="")
    parser.add_argument("--require-hard-gate", action="store_true")
    args = parser.parse_args()

    result = run_source_capability_audit(
        capability=args.capability,
        origin_source=args.origin_source,
        adapter=args.adapter,
        require_hard_gate=args.require_hard_gate,
    )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
