from __future__ import annotations

import base64
from datetime import datetime
import json
import sys
from typing import Any

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EastmoneyMarketSourceProviders,
    MarketDotNetHttpClientTransport,
    MarketSourceContractError,
)


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    transport = MarketDotNetHttpClientTransport()
    provider = EastmoneyMarketSourceProviders(
        task.get("codes") or [],
        trade_date=str(task.get("trade_date") or ""),
        feature_cutoff=str(task.get("feature_cutoff") or ""),
        collection_deadline=str(task.get("collection_deadline") or ""),
        transport=transport,
        clock=lambda: datetime.now(CN_TZ),
        timeout_seconds=float(task.get("request_timeout_seconds") or 2.0),
    )
    capability = str(task.get("capability") or "")
    methods = {
        "market_breadth": provider.collect_market_breadth_batch,
        "industry_snapshot": provider.collect_industry_batch,
        "fund_flow": provider.collect_fund_flow_batch,
    }
    if capability not in methods:
        raise MarketSourceContractError("MARKET_SOURCE_CAPABILITY_UNKNOWN")
    try:
        batch = methods[capability]()
    except MarketSourceContractError as exc:
        transport_qualified = bool(transport.attempts) and all(
            item.get("ok") is True for item in transport.attempts
        )
        return {
            "capability": capability,
            "records": [],
            "responses": [
                _serialize_response(item) for item in provider.captured_responses
            ],
            "transport_attempts": [_attempt_summary(item) for item in transport.attempts],
            "physical_request_count": transport.request_count,
            "logical_endpoint_count": _logical_endpoint_count(capability),
            "transport_qualified": transport_qualified,
            "provider_contract_complete": False,
            "error_code": (
                "EASTMONEY_PROVIDER_CONTRACT_INCOMPLETE"
                if transport_qualified
                else exc.code
            ),
            "provider_error_code": exc.code,
            "batch_status": "EASTMONEY_PROVIDER_CONTRACT_INCOMPLETE",
        }
    return {
        "capability": capability,
        "records": list(batch.records),
        "responses": [_serialize_response(item) for item in batch.responses],
        "transport_attempts": [_attempt_summary(item) for item in transport.attempts],
        "physical_request_count": transport.request_count,
        "logical_endpoint_count": _logical_endpoint_count(capability),
        "transport_qualified": all(
            item.get("ok") is True for item in transport.attempts
        ),
        "provider_contract_complete": True,
        "batch_status": batch.status,
    }


def _logical_endpoint_count(capability: str) -> int:
    return {
        "market_breadth": 2,
        "industry_snapshot": 6,
        "fund_flow": 5,
    }[capability]


def _attempt_summary(item: dict[str, Any]) -> dict[str, Any]:
    response = item.get("response") or {}
    return {
        "ok": item.get("ok") is True,
        "error_code": str(item.get("error_code") or ""),
        "process_started_at": str(item.get("process_started_at") or ""),
        "process_completed_at": str(item.get("process_completed_at") or ""),
        "process_elapsed_ms": item.get("process_elapsed_ms"),
        "worker_terminated": item.get("worker_terminated") is True,
        "http_status_code": response.get("status_code"),
        "response_url": str(response.get("url") or ""),
        "raw_hash": str(response.get("raw_hash") or ""),
        "byte_count": response.get("byte_count"),
    }


def _serialize_response(item: dict[str, Any]) -> dict[str, Any]:
    raw = bytes(item["raw_bytes"])
    return {
        **{key: value for key, value in item.items() if key != "raw_bytes"},
        "encoding": "base64",
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "byte_count": len(raw),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        task = json.loads(sys.stdin.read())
        payload = run_task(task)
        print(json.dumps({"ok": True, "payload": payload}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error_code": getattr(exc, "code", type(exc).__name__),
            "error": str(exc)[:500],
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
