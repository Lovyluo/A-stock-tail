from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.stock_catalog import catalog_path_from_config, update_stock_catalog
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the local A-share stock code and name catalog.")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    config = load_config()
    output = Path(args.output) if args.output else catalog_path_from_config(config)
    result = update_stock_catalog(output)
    print(f"Status: {result['status']}")
    print(f"Stock Count: {result['count']}")
    print(f"Catalog Path: {result['path']}")
    if result.get("source"):
        print(f"Source: {result['source']}")
    for source in result.get("sources") or []:
        print(
            f"- {source.get('source')}: {'OK' if source.get('ok') else 'FAILED'}, "
            f"rows={source.get('rows', 0)}, error={source.get('error', '')}"
        )
    return 0 if result["status"] == "STOCK_CATALOG_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
