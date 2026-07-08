from __future__ import annotations

from datetime import date


SAFETY_UNKNOWN_FIELDS = {
    "limit_up": "limit_price_unknown",
    "limit_down": "limit_price_unknown",
    "is_st": "st_status_unknown",
    "is_suspended": "suspended_status_unknown",
    "list_date": "list_date_missing",
    "is_bj_stock": "bj_status_unknown",
}


class DailyProxyPolicy:
    selection_as_of = "daily_close_proxy"

    def candidate_view(self, daily_bar: dict, selection_row: dict | None, trade_date: str) -> dict:
        raw = {key: value for key, value in daily_bar.items() if not key.startswith("next_day_")}
        if selection_row:
            raw.update({key: value for key, value in selection_row.items() if not key.startswith("next_day_")})
        candidate = dict(raw)
        candidate["price"] = candidate.get("close")
        amount = candidate.get("amount")
        candidate["amount_wan"] = round(float(amount) / 10000, 4) if amount not in (None, "") else None
        candidate["selection_as_of"] = self.selection_as_of
        candidate["data_fidelity"] = "daily_proxy"

        proxy_fields = ["price_from_daily_close", "amount_wan_from_amount"]
        unavailable: list[str] = []
        missing_input_fields: list[str] = []

        if candidate.get("vol_ratio") in (None, ""):
            candidate["vol_ratio"] = 1.0
            proxy_fields.append("vol_ratio_neutral_proxy")
            unavailable.append("vol_ratio_unavailable")
            missing_input_fields.append("vol_ratio")
        if candidate.get("range_position") in (None, ""):
            candidate["range_position"] = 0.0
            proxy_fields.append("range_position_conservative_proxy")
            unavailable.append("range_position_unavailable")
            missing_input_fields.append("range_position")
        if candidate.get("tail_pullback_pct") in (None, ""):
            candidate["tail_pullback_pct"] = 2.0
            proxy_fields.append("tail_pullback_neutral_proxy")
            unavailable.append("tail_pullback_unavailable")
            missing_input_fields.append("tail_pullback_pct")
        if not candidate.get("theme_tags") or candidate.get("theme_rank") in (None, ""):
            unavailable.append("theme_unavailable")
            missing_input_fields.extend(["theme_tags", "theme_rank"])
            candidate["_allow_missing_theme"] = True
        if candidate.get("main_net") in (None, "") or candidate.get("big_order_net") in (None, ""):
            unavailable.append("capital_unavailable")
            missing_input_fields.extend(["main_net", "big_order_net"])

        risk_unknown: list[str] = []
        for field, reason in SAFETY_UNKNOWN_FIELDS.items():
            if candidate.get(field) in (None, "") and reason not in risk_unknown:
                risk_unknown.append(reason)

        candidate["is_bj"] = candidate.get("is_bj_stock")
        candidate["is_limit_up"] = _is_at_limit(candidate.get("close"), candidate.get("limit_up"))
        list_date = candidate.get("list_date")
        if list_date not in (None, ""):
            try:
                listed_days = (date.fromisoformat(trade_date) - date.fromisoformat(str(list_date)[:10])).days
                candidate["listed_days"] = listed_days
                candidate["is_new_stock"] = listed_days < 90
                proxy_fields.append("new_stock_age_from_list_date")
            except ValueError:
                if "list_date_missing" not in risk_unknown:
                    risk_unknown.append("list_date_missing")
        candidate["_risk_unknown_reasons"] = risk_unknown
        candidate["_unavailable_reasons"] = list(dict.fromkeys(unavailable))
        candidate["_missing_input_fields"] = list(dict.fromkeys(missing_input_fields))
        candidate["proxy_fields"] = list(dict.fromkeys(proxy_fields))
        return candidate

    def market_view(self, trade_date: str, market_row: dict | None, benchmark_bar: dict | None) -> dict:
        if market_row and str(market_row.get("market_gate", "")).upper() in {"PASS", "FAIL"}:
            passed = str(market_row["market_gate"]).upper() == "PASS"
            reason = str(market_row.get("market_reason") or "market_snapshot")
            proxy_used = str(market_row.get("market_proxy_used", "")).lower() in {"true", "1", "yes"}
            return {
                "date": trade_date,
                "_gate_override": {
                    "pass": passed,
                    "score": 65 if passed else 0,
                    "reasons": [reason] if passed else [],
                    "reject_reasons": [] if passed else [reason],
                },
                "_market_proxy_used": proxy_used,
                "_market_source": "benchmark_direction_proxy" if proxy_used else "market_snapshots",
            }
        if benchmark_bar:
            open_price = float(benchmark_bar.get("open", 0) or 0)
            close_price = float(benchmark_bar.get("close", 0) or 0)
            direction = ((close_price / open_price) - 1) * 100 if open_price else 0.0
            passed = direction > 0
            return {
                "date": trade_date,
                "_gate_override": {
                    "pass": passed,
                    "score": 55 if passed else 0,
                    "reasons": ["benchmark_direction_proxy"] if passed else [],
                    "reject_reasons": [] if passed else ["benchmark_direction_proxy_non_positive"],
                },
                "_market_proxy_used": True,
                "_market_source": "benchmark_direction_proxy",
            }
        return {
            "date": trade_date,
            "_gate_override": {
                "pass": False,
                "score": 0,
                "reasons": [],
                "reject_reasons": ["market_data_unavailable"],
            },
            "_market_proxy_used": False,
            "_market_source": "unavailable",
        }


def _is_at_limit(price: object, limit_up: object) -> bool:
    if price in (None, "") or limit_up in (None, ""):
        return False
    return abs(float(price) - float(limit_up)) <= 0.005
