from __future__ import annotations

import json
import sys
from typing import Any

from overnight_quant.data.minute_probe_sources import (
    build_minute_probe_collector,
)
from overnight_quant.data.point_in_time import parse_cn_datetime


def execute_boundary_audit_worker_task(
    task: dict[str, Any],
    *,
    collector_factory=build_minute_probe_collector,
) -> dict[str, Any]:
    if str(task.get("operation") or "") != "boundary_audit_transaction":
        raise ValueError("boundary_audit_worker_operation_invalid")
    codes = [str(code).strip().zfill(6) for code in task.get("codes") or []]
    if codes != ["600000"]:
        raise ValueError("boundary_audit_worker_code_invalid")
    observed_at = parse_cn_datetime(task.get("observed_at"))
    if observed_at is None:
        raise ValueError("boundary_audit_worker_observed_at_invalid")
    collector = collector_factory(
        "mootdx",
        codes,
        endpoint=task.get("endpoint") or None,
        request_timeout_seconds=float(
            task.get("provider_timeout_seconds") or 2.0
        ),
    )
    try:
        collect = getattr(collector, "collect_transaction_evidence", None)
        if not callable(collect):
            raise ValueError("boundary_audit_transaction_unavailable")
        evidence = collect(
            observed_at,
            page_size=800,
            max_pages=1,
        )
        return {"transaction_evidence": evidence}
    finally:
        close = getattr(collector, "close", None)
        if callable(close):
            close()


def main() -> int:
    try:
        task = json.loads(sys.stdin.read())
        payload = execute_boundary_audit_worker_task(task)
        result = {"ok": True, "payload": payload}
        exit_code = 0
    except Exception as exc:
        result = {
            "ok": False,
            "error_code": "BOUNDARY_AUDIT_WORKER_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 2
    sys.stdout.write(
        json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
    )
    sys.stdout.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
