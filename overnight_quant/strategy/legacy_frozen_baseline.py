from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from overnight_quant.strategy.yang_yongxing_overnight import YangYongxingOvernightStrategy


LEGACY_BASELINE_NAME = "legacy_frozen_baseline"
LEGACY_SOURCE = Path(__file__).with_name("yang_yongxing_overnight.py")
FROZEN_LEGACY_SHA256 = "5738ffa2cf0cdf53200f6bfc4103a6d54ce6deba30c202ef75566428bf530378"


class LegacyFrozenBaseline(YangYongxingOvernightStrategy):
    """Compatibility wrapper for deterministic baseline replay only."""

    strategy_name = LEGACY_BASELINE_NAME
    formal_signal_enabled = False
    ticket_enabled = False

    def __init__(self, client, config: dict):
        baseline_config = copy.deepcopy(config)
        baseline_config.setdefault("strategy", {})["name"] = LEGACY_BASELINE_NAME
        super().__init__(client, baseline_config)

    def scan(self, trade_date: str | None = None, dry_run: bool = False) -> dict:
        if getattr(self.client, "mode", "") == "live" and not dry_run:
            return {
                "status": "LEGACY_FORMAL_ENTRY_DISABLED",
                "strategy_name": LEGACY_BASELINE_NAME,
                "strategy_phase": "frozen",
                "trade_date": trade_date or "",
                "market_gate": {
                    "pass": False,
                    "reasons": [],
                    "reject_reasons": ["legacy_formal_entry_disabled"],
                },
                "candidate_count": 0,
                "candidate_source": "disabled",
                "scored": [],
                "rejected": [],
                "selected": [],
                "dry_run_selected": [],
                "tickets": [],
                "orders": [],
                "demo_field_count": 0,
                "formal_signal_enabled": False,
                "ticket_enabled": False,
            }
        return super().scan(trade_date, dry_run=dry_run)


def legacy_baseline_fingerprint() -> str:
    return hashlib.sha256(LEGACY_SOURCE.read_bytes()).hexdigest()


def legacy_baseline_is_intact() -> bool:
    return legacy_baseline_fingerprint() == FROZEN_LEGACY_SHA256
