from __future__ import annotations

import base64
from datetime import datetime
import json
import sys
from typing import Any

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EastmoneyMarketSourceProviders,
    MarketSourceContractError,
    MarketUrllibTransport,
)


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    provider = EastmoneyMarketSourceProviders(
        task.get("codes") or [],
        trade_date=str(task.get("trade_date") or ""),
        feature_cutoff=str(task.get("feature_cutoff") or ""),
        collection_deadline=str(task.get("collection_deadline") or ""),
        transport=MarketUrllibTransport(),
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
    batch = methods[capability]()
    return {
        "capability": capability,
        "records": list(batch.records),
        "responses": [_serialize_response(item) for item in batch.responses],
        "batch_status": batch.status,
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
