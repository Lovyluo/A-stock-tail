"""Four equally weighted, uncalibrated research factors; no selection threshold."""
from __future__ import annotations

import math
from statistics import mean

from overnight_quant.data.point_in_time import stable_hash
from .contracts import GROUPS, PARAMETERS, safety
from .readiness import _SourceBridgeAdmission, validate_research_input


def _clip(value: float) -> float:
    return min(1.0, max(0.0, value))


def build_features(stock: dict, market: dict) -> dict:
    bars = [r["payload"] for r in stock["minute_bar"]]
    quote, industry = stock["quote"]["payload"], stock["industry"]["payload"]
    vwap = sum(b["amount"] for b in bars) / sum(b["volume"] for b in bars)
    initial_vwap = sum(b["amount"] for b in bars[:7]) / sum(b["volume"] for b in bars[:7])
    position = (bars[-1]["close"] / vwap - 1) * 100
    slope = (vwap / initial_vwap - 1) * 100
    volume_change = sum(b["volume"] for b in bars[-5:]) / sum(b["volume"] for b in bars[-10:-5]) - 1
    tail_return = (bars[-1]["close"] / bars[-5]["open"] - 1) * 100
    market_relative = quote["change_pct"] - market["index_change_pct"]
    industry_relative = quote["change_pct"] - industry["change_pct"]
    if not all(math.isfinite(value) for value in (
        vwap, initial_vwap, position, slope, volume_change, tail_return, market_relative, industry_relative,
    )):
        raise ValueError("derived_feature_nonfinite")
    components = {
        "market_state": mean((
            _clip(0.5 + market["index_change_pct"] / PARAMETERS["index_scale_pp"]), market["breadth_ratio"],
        )),
        "industry_resonance": mean((
            _clip(0.5 + industry["change_pct"] / PARAMETERS["industry_scale_pp"]), industry["breadth_ratio"],
        )),
        "relative_strength": mean((
            _clip(0.5 + market_relative / PARAMETERS["relative_scale_pp"]),
            _clip(0.5 + industry_relative / PARAMETERS["relative_scale_pp"]),
        )),
        "price_volume": mean((
            _clip(0.5 + position / PARAMETERS["vwap_position_scale_pp"]),
            _clip(0.5 + slope / PARAMETERS["vwap_slope_scale_pp"]),
            _clip(0.5 + volume_change / PARAMETERS["volume_change_scale"]),
            _clip(0.5 + tail_return / PARAMETERS["tail_return_scale_pp"]),
        )),
    }
    return {
        "benchmark_identity": market["benchmark_identity"], "board_code": industry["board_code"],
        "classification_version": industry["classification_version"],
        "market_change_pct": market["index_change_pct"], "market_breadth_ratio": market["breadth_ratio"],
        "industry_change_pct": industry["change_pct"], "industry_breadth_ratio": industry["breadth_ratio"],
        "industry_invalid_count": industry["invalid_count"],
        "stock_vs_market_pp": market_relative, "stock_vs_industry_pp": industry_relative,
        "window_vwap_cny": vwap, "vwap_scope": "last_12_contiguous_minutes_not_full_day",
        "window_start": stock["minute_bar"][0]["event_time"],
        "window_end": stock["minute_bar"][-1]["event_time"],
        "vwap_position_pct": position, "window_vwap_slope_pct": slope,
        "last5_vs_previous5_volume_ratio_change": volume_change,
        "last5_open_to_close_return_pct": tail_return,
        "components": components,
    }


class NoFundFlowResearchStrategy:
    def evaluate_snapshot(self, snapshot: dict, *, test_only: bool = False,
                          _bridge: _SourceBridgeAdmission | None = None) -> dict:
        readiness = validate_research_input(snapshot, test_only=test_only, _bridge=_bridge)
        base = {
            **safety(), "test_only": test_only, "execution_ok": True,
            "research_ready": readiness["research_ready"], "missing_reasons": readiness["errors"],
            "research_ranking": [], "research_selection": [],
            "notice": "Uncalibrated research ranking, not probability, advice or a trading signal.",
        }
        # Raw audit identity is deliberately separate from all decision semantics.
        base["ingest_hash"] = stable_hash(snapshot)
        base["audit_source_index"] = [
            {key: row.get(key) for key in (
                "data_type", "capability", "source", "origin_source", "source_version", "batch_id",
                "event_time", "observed_at", "available_at", "request_hash", "raw_hash", "payload_hash",
            )} for row in readiness["rows"]
        ]
        ranking = []
        try:
            if readiness["research_ready"]:
                state = readiness["state"]
                for code, stock in state["stocks"].items():
                    features = build_features(stock, state["market"])
                    components = features["components"]
                    ranking.append({
                        "code": code, "features": features,
                        "score": 100 * PARAMETERS["group_weight"] * sum(components[g] for g in GROUPS),
                        "score_explanation": {g: {"input": components[g], "weight": PARAMETERS["group_weight"],
                                                  "contribution": 25 * components[g]} for g in GROUPS},
                    })
                ranking.sort(key=lambda row: (-row["score"], row["code"]))
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            ranking = []
            readiness.update(research_ready=False, errors=[str(exc)])
            base.update(research_ready=False, missing_reasons=readiness["errors"])
        # No raw/ingest hash, fund-flow values, chip proxy or book fields in this digest.
        decision = {
            "strategy": base["strategy_id"], "requirements": base["requirements_hash"],
            "parameters": base["parameter_hash"], "test_only": test_only,
            "trade_date": snapshot.get("trade_date") if isinstance(snapshot, dict) else None,
            "time_contract": snapshot.get("time_contract") if isinstance(snapshot, dict) else None,
            "ready": readiness["research_ready"], "errors": readiness["errors"], "ranking": ranking,
        }
        base.update({
            "status": "SYNTHETIC_RESEARCH_RANKING" if ranking else "POINT_IN_TIME_DATA_INCOMPLETE",
            "research_ranking": ranking, "decision_hash": stable_hash(decision),
            "snapshot_hash": stable_hash({"namespace": base["strategy_id"], "decision": decision}),
        })
        return base
