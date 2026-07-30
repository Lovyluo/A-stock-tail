from __future__ import annotations

from typing import Any

from overnight_quant.strategy.close_confirmation_v1.features import SCORE_WEIGHTS


def score_close_confirmation(features: dict[str, Any]) -> dict[str, Any]:
    inputs = features.get("component_inputs") or {}
    components = {}
    total = 0.0
    for name, weight in SCORE_WEIGHTS.items():
        raw = max(0.0, min(1.0, float(inputs.get(name, 0.0) or 0.0)))
        points = round(raw * weight, 4)
        components[name] = {
            "raw": round(raw, 4),
            "weight": weight,
            "points": points,
        }
        total += points
    return {
        "total_score": round(total, 2),
        "components": components,
        "weights": dict(SCORE_WEIGHTS),
        "score_reasons": list(features.get("feature_reasons") or []),
    }
