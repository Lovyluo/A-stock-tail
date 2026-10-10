"""Bounded research plan and observations, not a fund or strength parser."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import json

from overnight_quant.data.market_source_providers import FIXED_CODES, EASTMONEY_STOCK_URL, EASTMONEY_FUND_FLOW_URL
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash
from overnight_quant.data.t25_bounded_executor import safe


RESEARCH_VERSION = "t25_native_source_research_plan_v1"
OBSERVATION_CLOCKS = ("14:39:55", "14:40:05", "14:40:35")


def native_research_plan() -> dict:
    boards = ["BK0474", "BK0475", "BK1239", "BK1277"]
    requests = [{"request_id": f"native-{board}", "method": "GET", "url": EASTMONEY_STOCK_URL,
                 "params": {"secid": f"90.{board}", "fltt": "2", "invt": "2",
                            "fields": "f57,f58,f86,f170,f43,f60,f169"},
                 "board_code": board, "observation_clock": None}
                for board in boards]
    for clock in OBSERVATION_CLOCKS:
        for code in FIXED_CODES:
            requests.append({"request_id": f"fund-{clock}-{code}", "method": "GET",
                "url": EASTMONEY_FUND_FLOW_URL, "code": code, "observation_clock": clock,
                "params": {"secid": ("1." if code.startswith("6") else "0.") + code,
                    "klt": "1", "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57"}})
    material = {"version": RESEARCH_VERSION, "requests": requests,
        "planned_source_http_request_count": 19, "source_http_request_limit": 20,
        "unused_budget_not_available_for_retry": 1,
        "fund_requires_completed_calendar_and_current_session": True,
        "trade_date": None, "target_row_label": "14:40",
        "request_deadline_ms": 2000, "max_workers": 2, "host_start_interval_ms": 500,
        "retry_count": 0, "fallback_count": 0,
        "semantic_authority": "upstream_original_documentation_and_captured_response_only",
        "industry_semantics": "INDUSTRY_STRENGTH_NATIVE_SCHEMA_UNPROVEN",
        "fund_semantics": "FUND_FLOW_SEMANTICS_UNPROVEN"}
    return safe({**material, "research_plan_hash": stable_hash(material), "network_requests_made": 0})


def summarize_fund_observations(captures: list[dict], *, trade_date: str) -> dict:
    """Read labels and availability only, never infer value semantics or units."""
    target = datetime.fromisoformat(f"{trade_date}T14:40:00+08:00")
    seen, rows, first_seen = set(), [], {}
    for capture in captures:
        code, slot = capture["code"], capture["observation_clock"]
        if code not in FIXED_CODES or slot not in OBSERVATION_CLOCKS or (code, slot) in seen:
            raise ValueError("FUND_OBSERVATION_PLAN_MISMATCH")
        seen.add((code, slot))
        raw = base64.b64decode(capture["content_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != capture["raw_hash"]:
            raise ValueError("FUND_OBSERVATION_RAW_HASH_INVALID")
        started, available = map(parse_cn_datetime, (capture["request_started_at"], capture["available_at"]))
        scheduled = datetime.fromisoformat(f"{trade_date}T{slot}+08:00")
        if (not started or not available or not scheduled <= started <= available
                or started.utcoffset() != timedelta(hours=8) or available.date() != target.date()):
            raise ValueError("FUND_OBSERVATION_TIME_INVALID")
        data = json.loads(raw.decode("utf-8", errors="strict"))["data"]
        if str(data.get("code")) != code or not isinstance(data.get("klines"), list):
            raise ValueError("FUND_OBSERVATION_IDENTITY_INVALID")
        labels = [parse_cn_datetime(line.split(",")[0]) for line in data["klines"]]
        if any(label is None or label.date() != target.date() for label in labels) or len(set(labels)) != len(labels):
            raise ValueError("FUND_OBSERVATION_LABEL_INVALID")
        if labels and max(labels) > available:
            raise ValueError("FUND_OBSERVATION_FUTURE_LABEL")
        has_target = target in labels
        if has_target:
            first_seen[code] = min(first_seen.get(code, available), available)
        rows.append({"code": code, "observation_clock": slot, "raw_hash": capture["raw_hash"],
                     "request_started_at": started.isoformat(), "available_at": available.isoformat(),
                     "latest_row_label": max(labels).isoformat() if labels else None,
                     "target_row_present": has_target, "row_count": len(labels)})
    return safe({"status": "FUND_LABEL_OBSERVATIONS_AUDIT_ONLY", "rows": sorted(rows, key=lambda row: (row["observation_clock"], row["code"])),
        "first_observed_target_available_at": {code: value.isoformat() for code, value in sorted(first_seen.items())},
        "worst_publication_delay_proven": False, "fund_semantics": "FUND_FLOW_SEMANTICS_UNPROVEN",
        "physical_http_request_count": None, "captured_response_count": len(rows),
        "five_minute_window_requirement": "five_continuous_incremental_intervals_or_six_cumulative_rows_with_baseline"})
