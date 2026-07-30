from __future__ import annotations

from copy import deepcopy
from typing import Any

from overnight_quant.data.close_confirmation_readiness import (
    materialize_point_in_time_records,
    validate_close_confirmation_readiness,
)
from overnight_quant.data.point_in_time import (
    demo_field_paths,
    enforce_formal_no_demo,
    stable_hash,
)
from overnight_quant.strategy.close_confirmation_v1.features import (
    build_close_confirmation_features,
)
from overnight_quant.strategy.close_confirmation_v1.gates import evaluate_hard_gates
from overnight_quant.strategy.close_confirmation_v1.scoring import (
    score_close_confirmation,
)


STRATEGY_NAME = "close_confirmation_v1"
STRATEGY_PHASE = "research_shadow"
FORMAL_MODES = {"live", "shadow", "paper", "replay"}


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

        working_snapshot = deepcopy(snapshot)
        if not working_snapshot.get("stocks") and working_snapshot.get("records"):
            working_snapshot.update(
                materialize_point_in_time_records(
                    working_snapshot.get("records") or []
                )
            )
        readiness = validate_close_confirmation_readiness(
            working_snapshot,
            decision_time=decision_time,
        )
        working_snapshot = readiness["normalized_snapshot"]
        base_result = _base_result(readiness, mode, decision_time)
        input_demo_paths = (
            demo_field_paths(working_snapshot)
            if str(mode).lower() in FORMAL_MODES
            else []
        )
        if input_demo_paths:
            base_result["demo_input_paths"] = input_demo_paths
            return enforce_formal_no_demo(
                {
                    **base_result,
                    "status": "FORMAL_DATA_REJECTED",
                    "data_ready": False,
                    "scored": [],
                    "shadow_candidates": [],
                },
                mode,
            )

        if not readiness["data_ready"]:
            return enforce_formal_no_demo(
                {
                    **base_result,
                    "status": "POINT_IN_TIME_DATA_INCOMPLETE",
                    "scored": [],
                    "shadow_candidates": [],
                },
                mode,
            )

        eligible_codes = set(readiness["eligible_stock_codes"])
        news_source_ready = (
            readiness["critical_source_status"]["news"]["status"]
            in {"AVAILABLE", "AVAILABLE_EMPTY"}
        )
        rows = []
        for stock in working_snapshot.get("stocks") or []:
            code = str(stock.get("code") or "").zfill(6)
            if code not in eligible_codes:
                continue
            scoring_stock = {**stock, "_news_source_ready": news_source_ready}
            features = build_close_confirmation_features(
                scoring_stock,
                decision_time=decision_time,
            )
            gates = evaluate_hard_gates(
                scoring_stock,
                features,
                decision_time=decision_time,
                mode=mode,
                config=self.config.get("gates"),
            )
            score = score_close_confirmation(features)
            row = {
                "code": code,
                "name": stock.get("name", ""),
                "features": features,
                "hard_gates": gates,
                **score,
                "decision": (
                    "SHADOW_CONFIRMATION"
                    if gates["all_pass"]
                    and score["total_score"]
                    >= float(self.config.get("min_shadow_score", 70))
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

        shadow_candidates = [
            row for row in rows if row["decision"] == "SHADOW_CONFIRMATION"
        ]
        result = {
            **base_result,
            "status": (
                "SHADOW_SIMULATION_READY"
                if shadow_candidates
                else "NO_SHADOW_CONFIRMATION"
            ),
            "scored": sorted(
                rows,
                key=lambda row: row["total_score"],
                reverse=True,
            ),
            "shadow_candidates": sorted(
                shadow_candidates,
                key=lambda row: row["total_score"],
                reverse=True,
            ),
        }
        return enforce_formal_no_demo(result, mode)


def _base_result(
    readiness: dict[str, Any],
    mode: str,
    decision_time: Any,
) -> dict[str, Any]:
    return {
        "strategy_name": STRATEGY_NAME,
        "strategy_phase": STRATEGY_PHASE,
        "mode": mode,
        "decision_time": str(decision_time),
        "execution_ok": True,
        "data_ready": bool(readiness["data_ready"]),
        "coverage_by_type": dict(readiness.get("coverage_by_type") or {}),
        "readiness_errors": list(readiness.get("readiness_errors") or []),
        "critical_source_status": dict(
            readiness.get("critical_source_status") or {}
        ),
        "eligible_stock_codes": list(
            readiness.get("eligible_stock_codes") or []
        ),
        "stock_readiness": dict(readiness.get("stock_readiness") or {}),
        "selected": [],
        "tickets": [],
        "orders": [],
        "formal_signal_enabled": False,
        "ticket_enabled": False,
        "notice": (
            "策略研发/影子模拟中；仅供研究观察，"
            "不生成正式交易信号、票据或外部执行动作。"
        ),
    }
