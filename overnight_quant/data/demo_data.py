from __future__ import annotations

from datetime import date, timedelta


def demo_market_snapshot() -> dict:
    return {
        "date": date.today().isoformat(),
        "indices": {
            "sh000001": {"name": "SSE Composite", "change_pct": 0.36},
            "sz399001": {"name": "SZSE Component", "change_pct": 0.58},
            "sz399006": {"name": "ChiNext", "change_pct": 0.82},
            "sh000300": {"name": "CSI 300", "change_pct": 0.28},
        },
        "tail_30m_stable": True,
        "hot_theme_count": 5,
        "northbound_net_yi": 8.6,
        "limit_down_count": 8,
        "limit_up_count": 54,
        "industry_top": ["Robotics", "AI", "Semiconductors", "Power Equipment"],
    }


def demo_quotes() -> list[dict]:
    base = {
        "is_st": False,
        "is_suspended": False,
        "is_new_stock": False,
        "is_limit_up": False,
        "is_limit_down": False,
        "is_bj": False,
        "listed_days": 800,
        "limit_down": 0.0,
    }
    rows = [
        {
            **base,
            "code": "300001",
            "name": "Demo Robotics",
            "price": 18.50,
            "open": 17.65,
            "high": 18.72,
            "low": 17.42,
            "limit_up": 20.31,
            "change_pct": 4.80,
            "vol_ratio": 1.62,
            "turnover_pct": 8.40,
            "amount_wan": 36500,
            "float_mcap_yi": 118,
            "tail_pullback_pct": 0.45,
            "upper_shadow_ratio": 0.17,
            "range_position": 0.83,
            "theme_tags": ["Robotics", "AI"],
            "theme_rank": 1,
            "same_theme_strong_count": 6,
            "big_order_net": 1850,
            "main_net": 3260,
        },
        {
            **base,
            "code": "002002",
            "name": "Demo Limit Up",
            "price": 12.10,
            "open": 11.00,
            "high": 12.10,
            "low": 10.92,
            "limit_up": 12.10,
            "change_pct": 10.00,
            "vol_ratio": 2.20,
            "turnover_pct": 6.30,
            "amount_wan": 41000,
            "float_mcap_yi": 96,
            "tail_pullback_pct": 0.0,
            "upper_shadow_ratio": 0.0,
            "range_position": 1.0,
            "theme_tags": ["AI"],
            "theme_rank": 2,
            "same_theme_strong_count": 5,
            "big_order_net": 2400,
            "main_net": 3800,
            "is_limit_up": True,
        },
        {
            **base,
            "code": "600003",
            "name": "Demo Tail Dive",
            "price": 9.82,
            "open": 9.42,
            "high": 10.25,
            "low": 9.35,
            "limit_up": 10.36,
            "change_pct": 4.10,
            "vol_ratio": 1.34,
            "turnover_pct": 7.20,
            "amount_wan": 23500,
            "float_mcap_yi": 72,
            "tail_pullback_pct": 3.15,
            "upper_shadow_ratio": 0.48,
            "range_position": 0.52,
            "theme_tags": ["Power Equipment"],
            "theme_rank": 4,
            "same_theme_strong_count": 3,
            "big_order_net": 260,
            "main_net": 410,
        },
        {
            **base,
            "code": "300004",
            "name": "Demo No Theme",
            "price": 22.40,
            "open": 21.32,
            "high": 22.60,
            "low": 21.20,
            "limit_up": 24.64,
            "change_pct": 4.55,
            "vol_ratio": 1.28,
            "turnover_pct": 7.90,
            "amount_wan": 28400,
            "float_mcap_yi": 144,
            "tail_pullback_pct": 0.70,
            "upper_shadow_ratio": 0.14,
            "range_position": 0.86,
            "theme_tags": [],
            "theme_rank": None,
            "same_theme_strong_count": 0,
            "big_order_net": 320,
            "main_net": 520,
        },
        {
            **base,
            "code": "002005",
            "name": "Demo Outflow",
            "price": 15.30,
            "open": 14.70,
            "high": 15.56,
            "low": 14.62,
            "limit_up": 16.83,
            "change_pct": 4.25,
            "vol_ratio": 1.42,
            "turnover_pct": 8.10,
            "amount_wan": 31200,
            "float_mcap_yi": 106,
            "tail_pullback_pct": 0.90,
            "upper_shadow_ratio": 0.28,
            "range_position": 0.72,
            "theme_tags": ["Robotics"],
            "theme_rank": 2,
            "same_theme_strong_count": 4,
            "big_order_net": -1260,
            "main_net": -2180,
        },
        {
            **base,
            "code": "600006",
            "name": "ST Demo",
            "price": 5.80,
            "open": 5.61,
            "high": 5.90,
            "low": 5.56,
            "limit_up": 6.09,
            "change_pct": 3.80,
            "vol_ratio": 1.18,
            "turnover_pct": 6.00,
            "amount_wan": 18800,
            "float_mcap_yi": 45,
            "tail_pullback_pct": 0.40,
            "upper_shadow_ratio": 0.20,
            "range_position": 0.71,
            "theme_tags": ["Restructuring"],
            "theme_rank": 5,
            "same_theme_strong_count": 2,
            "big_order_net": 100,
            "main_net": 130,
            "is_st": True,
        },
        {
            **base,
            "code": "300007",
            "name": "Demo Suspended",
            "price": 0.0,
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "limit_up": 0.0,
            "change_pct": 0.0,
            "vol_ratio": 0.0,
            "turnover_pct": 0.0,
            "amount_wan": 0,
            "float_mcap_yi": 65,
            "tail_pullback_pct": 0.0,
            "upper_shadow_ratio": 0.0,
            "range_position": 0.0,
            "theme_tags": ["AI"],
            "theme_rank": 1,
            "same_theme_strong_count": 3,
            "big_order_net": 0,
            "main_net": 0,
            "is_suspended": True,
        },
        {
            **base,
            "code": "301008",
            "name": "Demo New Stock",
            "price": 33.20,
            "open": 31.80,
            "high": 34.10,
            "low": 31.50,
            "limit_up": 39.84,
            "change_pct": 4.60,
            "vol_ratio": 1.90,
            "turnover_pct": 28.00,
            "amount_wan": 52000,
            "float_mcap_yi": 36,
            "tail_pullback_pct": 1.20,
            "upper_shadow_ratio": 0.35,
            "range_position": 0.65,
            "theme_tags": ["New Energy"],
            "theme_rank": 6,
            "same_theme_strong_count": 1,
            "big_order_net": 600,
            "main_net": 900,
            "is_new_stock": True,
            "listed_days": 18,
        },
        {
            **base,
            "code": "830009",
            "name": "Demo BJ Stock",
            "price": 7.20,
            "open": 6.95,
            "high": 7.35,
            "low": 6.90,
            "limit_up": 8.64,
            "change_pct": 3.30,
            "vol_ratio": 1.10,
            "turnover_pct": 6.50,
            "amount_wan": 17000,
            "float_mcap_yi": 32,
            "tail_pullback_pct": 0.50,
            "upper_shadow_ratio": 0.28,
            "range_position": 0.67,
            "theme_tags": ["Specialized SME"],
            "theme_rank": 8,
            "same_theme_strong_count": 1,
            "big_order_net": 80,
            "main_net": 120,
            "is_bj": True,
        },
    ]
    for row in rows:
        row["limit_down"] = round(row["price"] * 0.9, 2) if row["price"] else 0.0
    return rows


def demo_daily_kline(code: str, lookback: int = 120) -> list[dict]:
    final_price = _price_for_code(code)
    slope = -0.02 if code == "600003" else 0.025
    if code == "300004":
        slope = 0.012
    if code == "002005":
        slope = 0.018
    start_price = max(2.0, final_price * (1 - slope * min(lookback, 60) / 2))
    rows: list[dict] = []
    start_day = date.today() - timedelta(days=lookback)
    for i in range(lookback):
        progress = i / max(lookback - 1, 1)
        close = start_price + (final_price - start_price) * progress
        if code == "600003" and i == lookback - 1:
            high = close * 1.045
            open_price = close * 0.985
        else:
            high = close * 1.018
            open_price = close * 0.992
        low = close * 0.982
        rows.append(
            {
                "date": (start_day + timedelta(days=i)).isoformat(),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": 1000000 + i * 5000,
                "amount": round(close * (1000000 + i * 5000), 2),
            }
        )
    _add_moving_averages(rows)
    return rows


def demo_manual_order() -> dict:
    return {
        "strategy": "yang_yongxing_overnight_v1",
        "trade_date": date.today().isoformat(),
        "code": "300001",
        "name": "Demo Robotics",
        "buy_price": 18.50,
        "quantity": 200,
        "stop_loss": 17.95,
    }


def demo_current_price(code: str) -> dict:
    if code == "300001":
        return {
            "code": code,
            "name": "Demo Robotics",
            "price": 19.02,
            "open_price": 18.92,
            "open_change_pct": 2.27,
            "last_close": 18.50,
            "high": 19.18,
            "low": 18.72,
            "amount_wan": 18500,
            "volume": 98500,
            "turnover_pct": 5.2,
            "vol_ratio": 1.45,
            "limit_up": 20.35,
            "limit_up_gap_pct": 7.0,
            "vwap": 18.90,
            "is_limit_up": False,
            "is_limit_down": False,
        }
    quote = next((row for row in demo_quotes() if row["code"] == code), None)
    if quote is None:
        return {"code": code, "name": "", "price": 0.0, "open_change_pct": 0.0}
    return {
        "code": code,
        "name": quote["name"],
        "price": quote["price"],
        "open_price": quote["open"],
        "open_change_pct": quote["change_pct"],
        "last_close": round(quote["price"] / (1 + quote["change_pct"] / 100), 2) if quote.get("change_pct") else quote["price"],
        "high": quote.get("high", quote["price"]),
        "low": quote.get("low", quote["price"]),
        "amount_wan": quote.get("amount_wan", 0),
        "volume": 100000,
        "turnover_pct": quote.get("turnover_pct", 0),
        "vol_ratio": quote.get("vol_ratio", 0),
        "limit_up": quote.get("limit_up", 0),
        "limit_up_gap_pct": _pct_change(quote.get("limit_up", 0), quote["price"]) if quote.get("limit_up") else 0,
        "vwap": round((quote.get("open", quote["price"]) + quote["price"]) / 2, 2),
        "is_limit_up": quote.get("is_limit_up", False),
        "is_limit_down": quote.get("is_limit_down", False),
    }


def demo_intraday_bars(code: str) -> list[dict]:
    quote = demo_current_price(code)
    price = float(quote.get("price") or 10.0)
    vwap = float(quote.get("vwap") or price)
    base_time = "2026-05-22"
    if code == "300001":
        closes = [18.72, 18.82, 18.94, 18.88, 18.91, 18.97, 19.02]
        vols = [8800, 7600, 9400, 6200, 7000, 10800, 13200]
    elif code == "002005":
        closes = [15.22, 15.17, 15.08, 15.04, 15.10, 15.08, 15.03]
        vols = [6800, 6200, 5400, 5200, 5600, 5100, 5000]
    else:
        closes = [round(price * factor, 2) for factor in (0.985, 0.992, 1.0, 0.996, 1.002, 1.005, 1.0)]
        vols = [5000, 5200, 5400, 5100, 5300, 5600, 5500]
    rows: list[dict] = []
    for index, close in enumerate(closes):
        avg_price = round(vwap + (index - len(closes) + 1) * 0.01, 3)
        rows.append(
            {
                "time": f"{base_time} 10:{index + 1:02d}",
                "open": closes[index - 1] if index else close,
                "close": close,
                "high": round(max(close, avg_price) * 1.003, 2),
                "low": round(min(close, avg_price) * 0.997, 2),
                "volume": vols[index],
                "amount": round(close * vols[index] * 100, 2),
                "vwap": avg_price,
            }
        )
    return rows


def _price_for_code(code: str) -> float:
    quote = next((row for row in demo_quotes() if row["code"] == code), None)
    return float(quote["price"]) if quote and quote["price"] else 10.0


def _pct_change(target: float, base: float) -> float:
    if not target or not base:
        return 0.0
    return round((float(target) - float(base)) / float(base) * 100, 2)


def _add_moving_averages(rows: list[dict]) -> None:
    for i, row in enumerate(rows):
        closes = [float(item["close"]) for item in rows[: i + 1]]
        for window in (5, 10, 20, 60):
            sample = closes[-window:]
            row[f"ma{window}"] = round(sum(sample) / len(sample), 2)
