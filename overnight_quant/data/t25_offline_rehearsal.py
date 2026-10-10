"""Execute local response processes before the real test-only integration chain."""
from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.t24_offline_rehearsal import (
    FIXTURE_DATE, FIXTURE_BOARDS, run_offline_rehearsal, synthetic_fixture,
)
from overnight_quant.data.t25_bounded_executor import (
    AuditTimeConfiguration, BoundedAuditExecutor, integration_plan, safe, schedule_budget,
)


def run_local_chain(*, lock_path: Path) -> dict:
    configuration = AuditTimeConfiguration(FIXTURE_DATE)
    plan = integration_plan(configuration)
    fixtures = synthetic_fixture()
    groups = {job.request_id: [] for job in plan}
    for row in fixtures:
        cap = row["capability"]
        code = row["payload"].get("code")
        if cap == "fund_flow":
            key = f"40-fund-{code}"
        elif cap == "industry_snapshot":
            key = f"30-native-board-{FIXTURE_BOARDS[code][0]}"
        else:
            key = "00-market-counts"
        groups[key].append(row)
    local_start = datetime.now(CN_TZ)
    # Only the local test clock is moved; source records retain their fixture contract.
    jobs = [replace(job, release_at=local_start + timedelta(seconds=1) if job.release_at else None) for job in plan]
    payloads = {key: {"body": {"test_only": True, "records": rows}} for key, rows in groups.items()}
    executor = BoundedAuditExecutor()._run_for_test(
        jobs, deadline=local_start + timedelta(seconds=40),
        command=[sys.executable, "-m", "overnight_quant.data.t25_local_response_worker"],
        payloads=payloads, lock_path=lock_path,
    )
    records = []
    if executor["execution_ok"]:
        for result in executor["results"]:
            raw = base64.b64decode(result["response"]["response"]["content_base64"], validate=True)
            body = json.loads(raw.decode("utf-8", errors="strict"))
            if body.get("test_only") is not True:
                raise ValueError("LOCAL_PROVIDER_SCOPE_INVALID")
            records.extend(body["records"])
    chain = run_offline_rehearsal(records) if records else {"status": "LOCAL_PROVIDER_FAILED"}
    success = executor["execution_ok"] and chain["status"] == "OFFLINE_CHAIN_EXECUTABLE"
    return safe({"status": "T25_OFFLINE_CHAIN_EXECUTABLE" if success else "T25_OFFLINE_CHAIN_REJECTED",
        "execution_ok": success, "test_only": True, "network_requests_made": 0,
        "audit_configuration": configuration.material(),
        "synthetic_feature_contract": "unchanged_formal_1450_test_only",
        "request_plan": [job.material() for job in plan],
        "request_plan_hash": stable_hash([job.material() for job in plan]),
        "executor": executor, "chain": chain,
        "deterministic_result_hash": stable_hash({"plan": [job.material() for job in plan],
            "snapshot_hash": chain.get("snapshot_hash"), "feature_hash": chain.get("feature_hash")}),
        "budget": schedule_budget(integration_plan(configuration, industry_pages=2),
            started_at=configuration.at("14:39:30"), deadline=configuration.at("14:41:05")),
        "provider_contract_complete": False,
        "blockers": ["INDUSTRY_STRENGTH_NATIVE_SCHEMA_UNPROVEN", "FUND_FLOW_SEMANTICS_UNPROVEN"]})
