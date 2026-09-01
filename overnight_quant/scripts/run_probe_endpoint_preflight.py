from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.probe_worker_process import run_probe_worker_process


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one fail-closed mootdx endpoint preflight."
    )
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--endpoint-id", default="")
    parser.add_argument("--codes", required=True)
    parser.add_argument("--deadline-ms", type=int, default=2000)
    args = parser.parse_args()
    if args.deadline_ms != 2000:
        raise SystemExit("endpoint_preflight_deadline_must_be_2000ms")

    endpoint = _endpoint(args.endpoint, args.endpoint_id)
    codes = sorted(
        item.strip().zfill(6)
        for item in str(args.codes).split(",")
        if item.strip()
    )
    result = run_probe_worker_process(
        {
            "operation": "preflight",
            "source": "mootdx",
            "codes": codes,
            "observed_at": datetime.now(CN_TZ).isoformat(),
            "endpoint": endpoint,
            "provider_timeout_seconds": 2.0,
        },
        2000,
    )
    payload = result.get("payload") or {}
    summary = {
        "status": "ENDPOINT_PREFLIGHT_READY",
        "execution_ok": True,
        "data_ready": False,
        "ok": result.get("ok") is True,
        "error_code": str(result.get("error_code") or ""),
        "elapsed_ms": result.get("elapsed_ms"),
        "request_timed_out": bool(result.get("request_timed_out")),
        "worker_terminated": bool(result.get("worker_terminated")),
        "requested_codes": codes,
        "covered_codes": sorted(payload.get("covered_codes") or []),
        "endpoint_id": str(payload.get("endpoint_id") or ""),
        "returned_record_count": int(payload.get("returned_record_count") or 0),
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    ready = (
        summary["ok"]
        and summary["covered_codes"] == codes
        and summary["endpoint_id"] == endpoint["id"]
        and float(summary["elapsed_ms"] or 999999) <= 2000
        and not summary["request_timed_out"]
        and not summary["worker_terminated"]
    )
    if not ready:
        summary["status"] = "ENDPOINT_PREFLIGHT_FAILED"
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if ready else 2


def _endpoint(value: str, endpoint_id: str) -> dict[str, object]:
    host, separator, raw_port = str(value).strip().rpartition(":")
    if not separator or not host:
        raise SystemExit("endpoint_invalid")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit("endpoint_invalid") from exc
    if port <= 0:
        raise SystemExit("endpoint_invalid")
    return {
        "id": endpoint_id or f"mootdx_benchmark@{host}:{port}",
        "name": "mootdx_benchmark",
        "host": host,
        "port": port,
    }


if __name__ == "__main__":
    raise SystemExit(main())
