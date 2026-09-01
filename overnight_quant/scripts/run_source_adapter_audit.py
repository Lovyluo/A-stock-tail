from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.source_capability_adapters import (
    audit_source_adapters,
)


def run_source_adapter_audit() -> dict:
    # The command intentionally uses an empty environment contract. It reports
    # optional secret-backed adapters as unconfigured without reading secrets.
    return audit_source_adapters(environ={})


def main() -> int:
    result = run_source_adapter_audit()
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
