from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.execution.state_manager import reset_real_state
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear generated files from the real trading state only.")
    parser.add_argument("--yes", action="store_true", help="Delete listed real-state generated files without prompting.")
    parser.add_argument("--dry-run", action="store_true", help="List real-state generated files without deleting them.")
    args = parser.parse_args()

    config = load_config()
    preview = reset_real_state(config, dry_run=True)
    print("[Reset Real State]")
    print("Examples are preserved; source, tests, README and config are never reset.")
    if preview["targets"]:
        print("Generated real-state files:")
        for target in preview["targets"]:
            print(f"- {target}")
    else:
        print("Generated real-state files: none")
    if args.dry_run:
        print("Status: DRY_RUN_ONLY")
        return 0
    confirmed = args.yes
    if not confirmed:
        confirmed = input("Type RESET to delete these real-state files: ").strip() == "RESET"
    if not confirmed:
        print("Status: CANCELLED")
        return 1
    result = reset_real_state(config, confirmed=True)
    print(f"Status: {result['status']}")
    print(f"Deleted: {len(result['deleted'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
