"""Explicit synthetic subprocess fixture, with no network transport."""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import MARKET_DOTNET_TRANSPORT_VERSION


def main() -> int:
    task = json.loads(sys.stdin.read())
    if task.get("test_only") is not True:
        raise ValueError("TEST_ONLY_REQUIRED")
    scenario = task["test_response"]
    started = datetime.now(CN_TZ).isoformat(timespec="microseconds")
    time.sleep(float(scenario.get("sleep_seconds", 0)))
    if scenario.get("late_marker"):
        Path(scenario["late_marker"]).write_text("late write", encoding="utf-8")
    if scenario.get("error_code"):
        print(json.dumps({"ok": False, "error_code": scenario["error_code"]}))
        return 2
    raw = json.dumps(scenario.get("body", {}), sort_keys=True, ensure_ascii=False).encode("utf-8")
    print(json.dumps({"ok": True, "transport_version": MARKET_DOTNET_TRANSPORT_VERSION,
        "response": {"status_code": scenario.get("status_code", 200),
            "url": scenario.get("url", task["url"]), "redirect_chain": [],
            "request_started_at": started,
            "request_completed_at": datetime.now(CN_TZ).isoformat(timespec="microseconds"),
            "byte_count": len(raw), "raw_hash": hashlib.sha256(raw).hexdigest(),
            "content_base64": base64.b64encode(raw).decode("ascii")}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
