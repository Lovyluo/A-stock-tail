from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.tencent_shadow_snapshot import (
    TENCENT_SHADOW_SNAPSHOT_READY,
    build_tencent_shadow_snapshot,
)


DEFAULT_CODES = "000001,000333,600000,600519,601318"
CACHE_ROOT = (ROOT / "overnight_quant" / "data" / "cache").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build one read-only Tencent quote/valuation shadow snapshot."
    )
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--codes", default=DEFAULT_CODES)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    codes = [item.strip() for item in args.codes.split(",") if item.strip()]
    result = build_tencent_shadow_snapshot(codes, network=bool(args.network))
    if args.output:
        _write_cache_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == TENCENT_SHADOW_SNAPSHOT_READY else 2


def _write_cache_json(output: str, payload: dict) -> Path:
    target = Path(output)
    if not target.is_absolute():
        target = (ROOT / target).resolve()
    else:
        target = target.resolve()
    try:
        target.relative_to(CACHE_ROOT)
    except ValueError as exc:
        raise ValueError("shadow_snapshot_output_must_be_in_ignored_cache") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"shadow_snapshot_output_exists:{target}")
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


if __name__ == "__main__":
    raise SystemExit(main())
