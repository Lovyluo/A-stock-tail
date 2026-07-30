from __future__ import annotations

from datetime import datetime
from typing import Any

from overnight_quant.data.point_in_time import enforce_formal_no_demo, stable_hash
from overnight_quant.strategy.close_confirmation_v1.features import build_close_confirmation_features
from overnight_quant.strategy.close_confirmation_v1.gates import evaluate_hard_gates
from overnight_quant.strategy.close_confirmation_v1.scoring import score_close_confirmation


STRATEGY_NAME = "close_confirmation_v1"
STRATEGY_PHASE = "research_shadow"


class CloseConfirmationStrategy:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def evaluate_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        mode: str = "shadow",
    ) -> dict[str, Any]:
        decision_time = snapshot.get("decision_time")
        if not decision_time:
            raise ValueError("decision_time_missing")
        rows = []
        for stock in snapshot.get("stocks") or []:
            features = build_close_confirmation_features(stock, decision_time=decision_time)
            gates = evaluate_hard_gates(
                stock,
                features,
                decision_time=decision_time,
                mode=mode,
                config=self.config.get("gates"),
            )
            score = score_close_confirmation(features)
            row = {
                "code": str(stock.get("code") or "").zfill(6),
                "name": stock.get("name", ""),
                "features": features,
                "hard_gates": gates,
                **score,
                "decision": (
                    "SHADOW_CONFIRMATION"
                    if gates["all_pass"]
                    and score["total_score"] >= float(self.config.get("min_shadow_score", 70))
                    else "REJECT"
                ),
            }
            row["decision_hash"] = stable_hash(
                {
                    "strategy": STRATEGY_NAME,
                    "decision_time": decision_time,
                    "code": row["code"],
                    "features": features,
                    "hard_gates": gates,
                    "score": score,
                }
            )
            rows.append(row)
        shadow_candidates = [row for row in rows if row["decision"] == "SHADOW_CONFIRMATION"]
        result = {
            "strategy_name": STRATEGY_NAME,
            "strategy_phase": STRATEGY_PHASE,
            "status": "SHADOW_SIMULATION_READY" if shadow_candidates else "NO_SHADOW_CONFIRMATION",
            "mode": mode,
            "decision_time": str(decision_time),
            "scored": sorted(rows, key=lambda row: row["total_score"], reverse=True),
            "shadow_candidates": sorted(
                shadow_candidates,
                key=lambda row: row["total_score"],
                reverse=True,
            ),
            "selected": [],
            "tickets": [],
            "orders": [],
            "formal_signal_enabled": False,
            "ticket_enabled": False,
            "notice": "策略研发/影子模拟中；仅供研究观察，不生成正式交易票据。",
        }
        return enforce_formal_no_demo(result, mode)
