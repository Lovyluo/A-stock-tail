from __future__ import annotations

import copy
from pathlib import Path


REAL_STATE = "real"
EXAMPLE_STATE = "example"
VALID_STATES = {REAL_STATE, EXAMPLE_STATE}

REPORT_OUTPUT_PATTERNS = (
    "dry_run_scan_*.md",
    "intraday_observation_*.md",
    "live_data_quality_*.md",
    "live_scan_summary_*.md",
    "manual_order_record_*.md",
    "manual_order_ticket_*.md",
    "preflight_*.md",
    "sell_plan_*.md",
    "trade_lifecycle_*.md",
    "trade_review_*.md",
)


def config_for_mode(config: dict, mode: str) -> dict:
    return config_for_state(config, EXAMPLE_STATE if mode == "demo" else REAL_STATE)


def config_for_state(config: dict, state: str = REAL_STATE) -> dict:
    if state not in VALID_STATES:
        raise ValueError(f"unsupported state: {state}")
    resolved = copy.deepcopy(config)
    paths = resolved.setdefault("paths", {})
    if state == EXAMPLE_STATE:
        examples_dir = Path(paths.get("examples_dir", "overnight_quant/examples"))
        paths["records_dir"] = str(examples_dir / "records")
        paths["reports_dir"] = str(examples_dir / "reports")
    resolved["runtime_state"] = state
    return resolved


def collect_real_state_outputs(config: dict) -> list[Path]:
    real_config = config_for_state(config, REAL_STATE)
    paths = real_config.get("paths", {})
    records_dir = Path(paths.get("records_dir", "overnight_quant/records"))
    reports_dir = Path(paths.get("reports_dir", "overnight_quant/reports"))
    outputs = sorted(records_dir.glob("*.csv")) if records_dir.exists() else []
    if reports_dir.exists():
        for pattern in REPORT_OUTPUT_PATTERNS:
            outputs.extend(sorted(reports_dir.glob(pattern)))
    return sorted(set(outputs), key=lambda path: str(path))


def reset_real_state(config: dict, dry_run: bool = False, confirmed: bool = False) -> dict:
    targets = collect_real_state_outputs(config)
    if dry_run:
        return {"status": "DRY_RUN", "targets": [str(path) for path in targets], "deleted": []}
    if not confirmed:
        return {"status": "CONFIRMATION_REQUIRED", "targets": [str(path) for path in targets], "deleted": []}
    for path in targets:
        path.unlink(missing_ok=True)
    return {
        "status": "RESET_COMPLETE",
        "targets": [str(path) for path in targets],
        "deleted": [str(path) for path in targets],
    }
