from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.execution.position_tracker import get_open_positions
from overnight_quant.execution.state_manager import config_for_mode
from overnight_quant.execution.trade_recorder import read_manual_orders, save_sell_plan
from overnight_quant.reports.lifecycle_report import write_trade_lifecycle_report
from overnight_quant.strategy.sell_rules import decide_sell_action
from overnight_quant.strategy.yang_yongxing_overnight import load_config


ACTION_CN = {
    "TAKE_PROFIT": "止盈/分批兑现",
    "STOP_LOSS": "止损优先",
    "SELL_NOW": "卖出/减仓优先",
    "WAIT_10_MIN": "先观察再决定",
    "LIMIT_UP_WATCH": "涨停持有观察",
    "LIMIT_DOWN_RISK": "跌停风险处理",
    "PRICE_MISSING": "价格缺失，暂停判断",
}

REASON_CN = {
    "gap_or_profit_reached": "高开或浮盈已达到第一止盈线",
    "stop_loss_or_large_low_open": "浮亏触及止损线或低开幅度过大",
    "low_open_sell_on_failed_rebound": "低开后反弹不强，优先控制回撤",
    "moderate_high_open_watch_strength": "温和高开，先看承接强弱",
    "flat_open_watch_until_10": "平开附近，先观察 10 分钟方向选择",
    "default_next_day_exit": "默认次日纪律退出",
    "limit_up_watch_open_break": "涨停状态，观察是否开板回封",
    "limit_down_cannot_exit_normally": "跌停状态，可能无法正常卖出",
    "missing_price_data": "缺少有效价格数据",
    "price_missing": "缺少有效价格数据",
}

_INDEX_LABELS = {
    "000001": "上证指数",
    "000300": "沪深300",
    "399006": "创业板指",
    "sh000001": "上证指数",
    "sh000300": "沪深300",
    "sz399006": "创业板指",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate next-day sell plan.")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    args = parser.parse_args()

    result = run_sell_plan(mode=args.mode, trade_date=date.today().isoformat())
    print("[Next-Day Sell Plan]")
    if result["status"] == "NO_OPEN_POSITION":
        print("NO_OPEN_POSITION")
    for row in result["rows"]:
        print(f"{row['code']} {row['name']}: {row['action']} ({row['reason']}), pnl={row['pnl_pct']}%")
    print(f"Sell Plan: {result['path']}")
    print("Risk Notice: manual execution only; not investment advice.")
    return 0


def run_sell_plan(mode: str = "demo", trade_date: str | None = None, config: dict | None = None, client=None) -> dict:
    runtime_config = config_for_mode(config or load_config(), mode)
    runtime_client = client or AStockClient(mode)
    return generate_sell_plan(runtime_config, runtime_client, mode, trade_date)


def generate_sell_plan(config: dict, client, mode: str = "demo", trade_date: str | None = None) -> dict:
    trade_date = trade_date or date.today().isoformat()
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    positions = get_open_positions(records_dir)
    if not positions and mode == "demo":
        read_manual_orders(records_dir, mode)
        positions = get_open_positions(records_dir)
    rows = []
    for position in positions:
        try:
            current = client.get_current_price(position["code"])
        except Exception as exc:
            current = {"price": 0, "name": position.get("name", ""), "price_error": str(exc)}
        if not current or float(current.get("price", 0) or 0) <= 0:
            decision = {"action": "PRICE_MISSING", "level": "DATA_MISSING", "reason": "price_missing", "pnl_pct": 0.0}
        else:
            decision = decide_sell_action(
                {"buy_price": position.get("buy_price"), "suggested_price": position.get("buy_price")},
                current,
                config,
            )
        intraday_context = _intraday_context(client, position["code"], current)
        environment_context = _environment_context(client, position["code"], current)
        detail = _sell_plan_detail(position, current, decision, config, intraday_context, environment_context)
        rows.append(
            {
                "code": position["code"],
                "name": position.get("name", current.get("name", "")),
                "buy_price": position.get("buy_price", ""),
                "qty": position.get("open_qty", ""),
                "current_price": current.get("price", ""),
                "stop_loss_price": position.get("stop_loss_price", ""),
                "force_exit_before": config.get("sell", {}).get("force_exit_before", "10:30"),
                "past_force_exit_time": _past_force_exit_time(config),
                "risk_notice": "manual execution only; not investment advice.",
                **decision,
                **detail,
            }
        )
    status = "SELL_PLAN_READY" if rows else "NO_OPEN_POSITION"
    path = save_sell_plan(rows, reports_dir, trade_date, status=status)
    lifecycle = write_trade_lifecycle_report(config, trade_date, path if rows else "")
    return {"status": status, "rows": rows, "path": path, "lifecycle_report_path": lifecycle}


def _past_force_exit_time(config: dict) -> bool:
    force = str(config.get("sell", {}).get("force_exit_before", "10:30"))
    try:
        hour, minute = [int(part) for part in force.split(":", 1)]
    except ValueError:
        return False
    now = datetime.now().time()
    return (now.hour, now.minute) >= (hour, minute)


def _intraday_context(client, code: str, current: dict) -> dict:
    try:
        bars = client.get_intraday_bars(code)
    except Exception as exc:
        return {"intraday_source": "unavailable", "intraday_error": str(exc)}
    if not bars:
        return {
            "intraday_source": "quote_vwap_proxy",
            "vwap": _as_float(current.get("vwap")),
            "intraday_error": "intraday_bars_unavailable",
        }
    last = bars[-1]
    vwap = _as_float(last.get("vwap")) or _as_float(current.get("vwap"))
    last_price = _as_float(last.get("close")) or _as_float(current.get("price"))
    recent = bars[-6:] if len(bars) >= 6 else bars
    start_price = _as_float(recent[0].get("close")) if recent else 0.0
    recent_trend_pct = round((last_price / start_price - 1) * 100, 2) if last_price and start_price else ""
    recent_vwap_pass = sum(
        1
        for row in recent
        if _as_float(row.get("close")) and _as_float(row.get("vwap")) and _as_float(row.get("close")) >= _as_float(row.get("vwap"))
    )
    previous_low = min((_as_float(row.get("low")) for row in bars[-12:-1]), default=0.0) if len(bars) > 1 else 0.0
    low_break = bool(previous_low and _as_float(last.get("low")) < previous_low)
    if vwap and last_price >= vwap and not low_break and _as_float(recent_trend_pct) >= 0:
        trend_cn = f"分时承接偏强：现价在 VWAP 上方，近{len(recent)}分钟{_fmt_pct(recent_trend_pct)}"
    elif vwap and last_price < vwap:
        trend_cn = f"分时承接偏弱：现价低于 VWAP，近{len(recent)}分钟{_fmt_pct(recent_trend_pct)}"
    elif low_break:
        trend_cn = f"分时低点下移：近端低点跌破前低，近{len(recent)}分钟{_fmt_pct(recent_trend_pct)}"
    else:
        trend_cn = f"分时中性：近{len(recent)}分钟{_fmt_pct(recent_trend_pct)}"
    return {
        "intraday_source": "eastmoney_intraday_trends",
        "vwap": round(vwap, 4) if vwap else "",
        "intraday_last_time": last.get("time", ""),
        "intraday_low": min(_as_float(row.get("low")) for row in bars),
        "intraday_high": max(_as_float(row.get("high")) for row in bars),
        "intraday_last_price": round(last_price, 3) if last_price else "",
        "intraday_recent_trend_pct": recent_trend_pct,
        "intraday_recent_vwap_pass_count": recent_vwap_pass,
        "intraday_low_break": low_break,
        "intraday_trend_cn": trend_cn,
        "intraday_error": "",
    }


def _environment_context(client, code: str, current: dict) -> dict:
    context: dict = {}
    steps = [
        (_market_context, (client,)),
        (_theme_context, (client, code)),
        (_multi_day_fund_context, (client, code)),
        (_minute_fund_context, (client, code)),
        (_today_main_fund_context, (client, code)),
        (_volume_trend_context, (client, code, current)),
    ]
    for getter, args in steps:
        try:
            context.update(getter(*args))
        except Exception as exc:
            key = getter.__name__.strip("_").replace("_context", "")
            context[f"{key}_context_cn"] = f"{key} 数据计算失败，暂不纳入硬性卖出条件"
            context[f"{key}_error"] = str(exc)
            context[f"{key}_score"] = 0
    score_keys = [
        "market_score",
        "theme_score",
        "fund_score",
        "today_main_score",
        "volume_score",
    ]
    total = sum(_as_float(context.get(key)) for key in score_keys)
    context["context_score_total"] = round(total, 1)
    context["composite_action_cn"] = _composite_action_cn(total)
    context["context_summary_cn"] = _build_context_summary(context)
    return context


def _market_context(client) -> dict:
    try:
        if hasattr(client, "_tencent_quotes"):
            raw = client._tencent_quotes(["sh000001", "sh000300", "sz399006"])
            source = "tencent_indices"
        elif hasattr(client, "get_market_snapshot"):
            snapshot = client.get_market_snapshot()
            raw = snapshot.get("indices", {})
            source = "market_snapshot.indices"
        else:
            raise RuntimeError("client has no market index source")
    except Exception as exc:
        return {
            "market_context_cn": "大盘数据暂缺，不作为硬卖出条件",
            "market_score": 0,
            "market_source": "missing",
            "market_error": str(exc),
        }
    points = []
    for key, item in (raw or {}).items():
        normalized = str(key).replace("sh", "").replace("sz", "")
        if normalized not in {"000001", "000300", "399006"}:
            continue
        label = _INDEX_LABELS.get(str(key), _INDEX_LABELS.get(normalized, normalized))
        change = _as_float(item.get("change_pct"))
        points.append((label, change))
    if not points:
        return {
            "market_context_cn": "大盘指数返回为空，不作为硬卖出条件",
            "market_score": 0,
            "market_source": source,
            "market_error": "empty_indices",
        }
    changes = [point[1] for point in points]
    weak_count = sum(1 for change in changes if change <= -1.0)
    strong_count = sum(1 for change in changes if change >= 0.5)
    if weak_count >= 2 or min(changes) <= -2.0:
        score = -2
        label = "大盘偏弱"
    elif any(change <= -1.0 for change in changes):
        score = -1
        label = "大盘略弱"
    elif strong_count >= 2 and min(changes) >= 0:
        score = 1
        label = "大盘支持"
    else:
        score = 0
        label = "大盘中性"
    detail = "，".join(f"{name}{_fmt_pct(change)}" for name, change in points)
    return {
        "market_context_cn": f"{label}：{detail}",
        "market_score": score,
        "market_source": source,
        "market_error": "",
    }


def _theme_context(client, code: str) -> dict:
    if hasattr(client, "_safe_baidu_concept_blocks"):
        blocks = client._safe_baidu_concept_blocks(code)
        source = "baidu_concept_blocks"
    elif hasattr(client, "_baidu_concept_blocks"):
        try:
            blocks = client._baidu_concept_blocks(code)
            source = "baidu_concept_blocks"
        except Exception as exc:
            return {
                "theme_context_cn": "题材/概念数据暂缺，不作为硬卖出条件",
                "theme_score": 0,
                "theme_source": "missing",
                "theme_error": str(exc),
            }
    else:
        return {
            "theme_context_cn": "题材/概念数据暂缺，不作为硬卖出条件",
            "theme_score": 0,
            "theme_source": "missing",
            "theme_error": "client has no concept block source",
        }
    theme_error = ""
    if not ((blocks or {}).get("industry") or (blocks or {}).get("concept")) and hasattr(client, "_eastmoney_core_conception_blocks"):
        try:
            blocks = client._eastmoney_core_conception_blocks(code)
            source = "eastmoney_core_conception"
        except Exception as exc:
            blocks = blocks or {}
            source = source if "source" in locals() else "missing"
            theme_error = str(exc)
    items = list((blocks or {}).get("industry") or []) + list((blocks or {}).get("concept") or [])
    parsed = [(str(item.get("name", "")), _as_percent_float(item.get("change_pct"))) for item in items]
    parsed = [(name, change) for name, change in parsed if name and change is not None]
    if not parsed:
        tags = ", ".join((blocks or {}).get("concept_tags") or [])
        block_errors = []
        for item in items:
            if item.get("error"):
                block_errors.append(f"{item.get('board_code') or item.get('name')}:{item.get('error')}")
        if block_errors and not theme_error:
            theme_error = "; ".join(block_errors[:3])
        return {
            "theme_context_cn": f"题材标签可见但涨跌数据不足：{tags[:60] or '无'}",
            "theme_tags_cn": tags,
            "theme_score": 0,
            "theme_source": source,
            "theme_error": theme_error or "missing_theme_change_pct",
        }
    top_names = "、".join(name for name, _ in parsed[:5])
    avg_change = _average([change for _, change in parsed])
    min_change = min(change for _, change in parsed)
    max_change = max(change for _, change in parsed)
    if avg_change <= -1.5 or min_change <= -3:
        score = -1
        label = "题材偏弱"
    elif avg_change >= 1.0 or max_change >= 2.5:
        score = 1
        label = "题材支持"
    else:
        score = 0
        label = "题材中性"
    return {
        "theme_context_cn": f"{label}：{top_names}，平均{_fmt_pct(avg_change)}，最强{_fmt_pct(max_change)}，最弱{_fmt_pct(min_change)}",
        "theme_tags_cn": top_names,
        "theme_score": score,
        "theme_source": source,
        "theme_error": "",
    }


def _multi_day_fund_context(client, code: str) -> dict:
    if not hasattr(client, "_eastmoney_fund_flow_daily"):
        return {
            "fund_context_cn": "多日资金流数据暂缺，不作为硬卖出条件",
            "fund_score": 0,
            "fund_source": "missing",
            "fund_error": "client has no daily fund flow source",
        }
    rows = []
    source = "eastmoney_fund_flow_daily"
    errors: list[str] = []
    for source_name, fetcher_name in (
        ("eastmoney_fund_flow_kline_daily", "_eastmoney_fund_flow_kline_daily"),
        ("eastmoney_fund_flow_daily", "_eastmoney_fund_flow_daily"),
        ("sina_money_flow_history", "_sina_money_flow_history"),
    ):
        fetcher = getattr(client, fetcher_name, None)
        if fetcher is None:
            continue
        try:
            if fetcher_name == "_sina_money_flow_history":
                candidate_rows = _fresh_fund_rows(fetcher(code))
                if not candidate_rows:
                    errors.append(f"{source_name}:stale_or_empty")
                    continue
            else:
                try:
                    candidate_rows = fetcher(code, limit=10)
                except TypeError:
                    candidate_rows = fetcher(code)
            if candidate_rows:
                rows = candidate_rows
                source = source_name
                break
            errors.append(f"{source_name}:empty")
        except Exception as exc:
            errors.append(f"{source_name}:{exc}")
    if not rows:
        return {
            "fund_context_cn": "多日主力资金数据获取失败，不作为硬卖出条件",
            "fund_score": 0,
            "fund_source": source,
            "fund_error": "; ".join(errors) or "daily_fund_flow_unavailable",
        }
    rows = list(rows or [])[-10:]
    if not rows:
        return {
            "fund_context_cn": "多日主力资金返回为空，不作为硬卖出条件",
            "fund_score": 0,
            "fund_source": "eastmoney_fund_flow_daily",
            "fund_error": "empty_daily_fund_flow",
        }
    last5 = rows[-5:]
    sum5_wan = sum(_as_float(row.get("main_net")) for row in last5) / 10000
    sum10_wan = sum(_as_float(row.get("main_net")) for row in rows) / 10000
    positive5 = sum(1 for row in last5 if _as_float(row.get("main_net")) > 0)
    last_wan = _as_float(rows[-1].get("main_net")) / 10000
    if sum5_wan <= -5000 and positive5 <= 2:
        score = -2
        label = "多日主力持续流出"
    elif sum5_wan < 0 and last_wan < 0:
        score = -1
        label = "多日资金偏弱"
    elif sum5_wan >= 5000 and positive5 >= 3:
        score = 1
        label = "多日资金支持"
    else:
        score = 0
        label = "多日资金中性"
    return {
        "fund_context_cn": (
            f"{label}：5日主力{_fmt_wan(sum5_wan)}，10日主力{_fmt_wan(sum10_wan)}，"
            f"近5日{positive5}天净流入，最近一日{_fmt_wan(last_wan)}"
        ),
        "fund_5d_main_net_wan": round(sum5_wan, 1),
        "fund_10d_main_net_wan": round(sum10_wan, 1),
        "fund_5d_positive_days": positive5,
        "fund_score": score,
        "fund_source": source,
        "fund_error": "",
    }


def _today_main_fund_context(client, code: str) -> dict:
    source = "eastmoney_quote_fund_flow"
    try:
        if hasattr(client, "_safe_quote_fund_flow"):
            row = (client._safe_quote_fund_flow([code]) or {}).get(str(code).zfill(6))
        elif hasattr(client, "_eastmoney_quote_fund_flow"):
            row = (client._eastmoney_quote_fund_flow([code]) or {}).get(str(code).zfill(6))
        else:
            raise RuntimeError("client has no current fund flow source")
    except Exception as exc:
        return {
            "today_main_fund_cn": "当日主力资金暂缺，不作为硬卖出条件",
            "today_main_score": 0,
            "today_fund_source": source,
            "today_fund_error": str(exc),
        }
    if not row:
        fallback_errors = []
        if hasattr(client, "_safe_fund_flow"):
            try:
                flow_rows, fallback_source, fallback_error = client._safe_fund_flow(code)
                fallback_errors.append(fallback_error)
                row = _current_fund_row(flow_rows, fallback_source)
                if row:
                    source = fallback_source
            except Exception as exc:
                fallback_errors.append(str(exc))
        if not row and hasattr(client, "_sina_money_flow_current"):
            try:
                row = (client._sina_money_flow_current([code]) or {}).get(str(code).zfill(6))
                if row:
                    source = "sina_money_flow_current"
            except Exception as exc:
                fallback_errors.append(str(exc))
        if row:
            pass
        else:
            fallback_error = "; ".join(error for error in fallback_errors if error)
            return {
                "today_main_fund_cn": "当日主力资金返回为空，不作为硬卖出条件",
                "today_main_score": 0,
                "today_fund_source": source,
                "today_fund_error": fallback_error or "empty_quote_fund_flow",
            }
    if not row:
        return {
            "today_main_fund_cn": "当日主力资金返回为空，不作为硬卖出条件",
            "today_main_score": 0,
            "today_fund_source": source,
            "today_fund_error": "empty_quote_fund_flow",
        }
    main_wan = _as_float(row.get("main_net")) / 10000
    large_wan = _as_float(row.get("large_net")) / 10000
    super_wan = _as_float(row.get("super_net")) / 10000
    if main_wan <= -10000:
        score = -2
        label = "当日主力明显流出"
    elif main_wan <= -3000:
        score = -1
        label = "当日主力流出"
    elif main_wan >= 3000:
        score = 1
        label = "当日主力流入"
    else:
        score = 0
        label = "当日主力中性"
    return {
        "today_main_fund_cn": f"{label}：主力{_fmt_wan(main_wan)}，大单{_fmt_wan(large_wan)}，超大单{_fmt_wan(super_wan)}",
        "today_main_net_wan": round(main_wan, 1),
        "today_large_net_wan": round(large_wan, 1),
        "today_super_net_wan": round(super_wan, 1),
        "today_main_score": score,
        "today_fund_source": source,
        "today_fund_error": "",
    }


def _minute_fund_context(client, code: str) -> dict:
    if not hasattr(client, "_eastmoney_fund_flow_minute"):
        return {
            "minute_fund_cn": "分钟资金数据暂缺",
            "minute_fund_score": 0,
            "minute_fund_source": "missing",
            "minute_fund_error": "client has no minute fund flow source",
        }
    try:
        rows = client._eastmoney_fund_flow_minute(str(code).zfill(6))
    except Exception as exc:
        return {
            "minute_fund_cn": "分钟资金数据获取失败，使用当日主力兜底",
            "minute_fund_score": 0,
            "minute_fund_source": "eastmoney_fund_flow_minute",
            "minute_fund_error": str(exc),
        }
    rows = list(rows or [])
    if not rows:
        return {
            "minute_fund_cn": "分钟资金返回为空，使用当日主力兜底",
            "minute_fund_score": 0,
            "minute_fund_source": "eastmoney_fund_flow_minute",
            "minute_fund_error": "empty_minute_fund_flow",
        }
    recent = rows[-5:]
    total_main_wan = sum(_as_float(row.get("main_net")) for row in rows) / 10000
    recent_main_wan = sum(_as_float(row.get("main_net")) for row in recent) / 10000
    total_large_wan = sum(_as_float(row.get("large_net")) for row in rows) / 10000
    if total_main_wan <= -5000 or recent_main_wan <= -1000:
        score = -2
        label = "分钟主力持续流出"
    elif total_main_wan < 0 or recent_main_wan < 0:
        score = -1
        label = "分钟资金偏弱"
    elif total_main_wan >= 3000 and recent_main_wan >= 0:
        score = 1
        label = "分钟资金支持"
    else:
        score = 0
        label = "分钟资金中性"
    return {
        "minute_fund_cn": f"{label}：累计主力{_fmt_wan(total_main_wan)}，近{len(recent)}分钟{_fmt_wan(recent_main_wan)}，累计大单{_fmt_wan(total_large_wan)}",
        "minute_main_net_wan": round(total_main_wan, 1),
        "minute_recent_main_net_wan": round(recent_main_wan, 1),
        "minute_large_net_wan": round(total_large_wan, 1),
        "minute_fund_score": score,
        "minute_fund_source": "eastmoney_fund_flow_minute",
        "minute_fund_error": "",
    }


def _volume_trend_context(client, code: str, current: dict) -> dict:
    rows = []
    source = "eastmoney_daily_kline"
    try:
        if hasattr(client, "_eastmoney_daily_kline"):
            rows = client._eastmoney_daily_kline(code, 30)
        elif hasattr(client, "get_daily_kline"):
            rows = client.get_daily_kline(code, 30)
            source = "daily_kline"
        else:
            raise RuntimeError("client has no daily kline source")
    except Exception as exc:
        try:
            if hasattr(client, "get_daily_kline"):
                rows = client.get_daily_kline(code, 30)
                source = "daily_kline"
            else:
                raise
        except Exception as fallback_exc:
            return {
                "volume_context_cn": "多日量价数据获取失败，不作为硬卖出条件",
                "volume_score": 0,
                "volume_source": source,
                "volume_error": f"{exc}; fallback:{fallback_exc}",
            }
    rows = list(rows or [])
    if len(rows) < 6:
        return {
            "volume_context_cn": "多日量价样本不足，不作为硬卖出条件",
            "volume_score": 0,
            "volume_source": source,
            "volume_error": "not_enough_daily_bars",
        }
    last = rows[-1]
    prev = rows[-2]
    close = _as_float(current.get("price")) or _as_float(last.get("close"))
    prev_close = _as_float(prev.get("close"))
    volume = _as_float(last.get("volume"))
    prev5_volume = [_as_float(row.get("volume")) for row in rows[-6:-1] if _as_float(row.get("volume")) > 0]
    prev20_volume = [_as_float(row.get("volume")) for row in rows[-21:-1] if _as_float(row.get("volume")) > 0]
    vol_ratio_5d = volume / _average(prev5_volume) if prev5_volume and volume else 0
    vol_ratio_20d = volume / _average(prev20_volume) if prev20_volume and volume else 0
    ma5 = _as_float(last.get("ma5")) or _average([_as_float(row.get("close")) for row in rows[-5:]])
    ma20 = _as_float(last.get("ma20")) or _average([_as_float(row.get("close")) for row in rows[-20:]])
    day_change = (close / prev_close - 1) * 100 if close and prev_close else 0
    close_vs_ma5 = (close / ma5 - 1) * 100 if close and ma5 else 0
    close_vs_ma20 = (close / ma20 - 1) * 100 if close and ma20 else 0
    if day_change < 0 and vol_ratio_5d >= 1.2:
        score = -2
        label = "放量下跌，承接偏弱"
    elif close_vs_ma5 < 0 and close_vs_ma20 < 0:
        score = -1
        label = "趋势在均线下方"
    elif day_change > 0 and vol_ratio_5d >= 1.1 and close_vs_ma5 >= 0:
        score = 1
        label = "放量上涨，承接较好"
    else:
        score = 0
        label = "量价中性"
    return {
        "volume_context_cn": (
            f"{label}：较5日均量{vol_ratio_5d:.2f}倍，较20日均量{vol_ratio_20d:.2f}倍，"
            f"收盘/现价相对MA5{_fmt_pct(close_vs_ma5)}、MA20{_fmt_pct(close_vs_ma20)}"
        ),
        "volume_vs_5d": round(vol_ratio_5d, 2),
        "volume_vs_20d": round(vol_ratio_20d, 2),
        "close_vs_ma5_pct": round(close_vs_ma5, 2),
        "close_vs_ma20_pct": round(close_vs_ma20, 2),
        "volume_score": score,
        "volume_source": source,
        "volume_error": "",
    }


def _sell_plan_detail(
    position: dict,
    current: dict,
    decision: dict,
    config: dict,
    intraday: dict,
    environment: dict | None = None,
) -> dict:
    buy_price = _as_float(position.get("buy_price"))
    price = _as_float(current.get("price"))
    high = _as_float(current.get("high"))
    low = _as_float(current.get("low"))
    stop_loss_price = _effective_stop_loss(position, config)
    sell_cfg = config.get("sell", {})
    take_profit_pct_1 = _as_float(sell_cfg.get("take_profit_pct_1", 3))
    take_profit_pct_2 = _as_float(sell_cfg.get("take_profit_pct_2", 6))
    take_profit_price_1 = round(buy_price * (1 + take_profit_pct_1 / 100), 3) if buy_price else ""
    take_profit_price_2 = round(buy_price * (1 + take_profit_pct_2 / 100), 3) if buy_price else ""
    vwap = _as_float(intraday.get("vwap") or current.get("vwap"))
    vwap_gap = round((price / vwap - 1) * 100, 2) if price and vwap else ""
    range_position = round((price - low) / (high - low) * 100, 1) if price and high > low else ""
    pullback = round((high - price) / high * 100, 2) if price and high else ""
    open_change = _as_float(current.get("open_change_pct"))
    current_change = _as_float(current.get("current_change_pct", current.get("change_pct")))
    action = str(decision.get("action", ""))
    reason = str(decision.get("reason", ""))
    detail = {
        "action_cn": ACTION_CN.get(action, action),
        "reason_cn": REASON_CN.get(reason, reason),
        "current_price": current.get("price", ""),
        "open_price": current.get("open_price", ""),
        "open_change_pct": round(open_change, 2),
        "current_change_pct": round(current_change, 2),
        "day_high": high,
        "day_low": low,
        "range_position_pct": range_position,
        "pullback_from_high_pct": pullback,
        "amount_wan": current.get("amount_wan", ""),
        "turnover_pct": current.get("turnover_pct", ""),
        "vol_ratio": current.get("vol_ratio", ""),
        "vwap": round(vwap, 3) if vwap else "",
        "vwap_source": intraday.get("intraday_source", "missing"),
        "vwap_gap_pct": vwap_gap,
        "take_profit_price_1": take_profit_price_1,
        "take_profit_price_2": take_profit_price_2,
        "effective_stop_loss_price": stop_loss_price,
        "force_exit_before": sell_cfg.get("force_exit_before", "10:30"),
        "past_force_exit_time": _past_force_exit_time(config),
        "last_update_time": datetime.now().strftime("%H:%M:%S"),
        "intraday_last_time": intraday.get("intraday_last_time", ""),
        "intraday_last_price": intraday.get("intraday_last_price", ""),
        "intraday_recent_trend_pct": intraday.get("intraday_recent_trend_pct", ""),
        "intraday_recent_vwap_pass_count": intraday.get("intraday_recent_vwap_pass_count", ""),
        "intraday_low_break": intraday.get("intraday_low_break", ""),
        "intraday_trend_cn": intraday.get("intraday_trend_cn", ""),
        "intraday_error": intraday.get("intraday_error", ""),
    }
    detail.update(environment or {})
    detail["realtime_alert_cn"] = _realtime_alert_cn(action, detail)
    detail["realtime_trigger_cn"] = _realtime_trigger_cn(action, detail)
    detail["plan_cn"] = _execution_plan_cn(action, detail)
    detail["hold_condition_cn"] = _hold_condition_cn(action, detail)
    detail["sell_trigger_cn"] = _sell_trigger_cn(action, detail)
    detail["logic_cn"] = _logic_cn(action, reason, detail, sell_cfg)
    return detail


def _effective_stop_loss(position: dict, config: dict):
    explicit = _as_float(position.get("stop_loss_price"))
    if explicit:
        return round(explicit, 3)
    buy_price = _as_float(position.get("buy_price"))
    stop_pct = _as_float(config.get("sell", {}).get("stop_loss_pct", config.get("risk", {}).get("hard_stop_loss_pct", -3)))
    return round(buy_price * (1 + stop_pct / 100), 3) if buy_price else ""


def _execution_plan_cn(action: str, detail: dict) -> str:
    force_note = "超过强制退出时间仍走弱时，不再恋战。"
    if action == "TAKE_PROFIT":
        base = "先锁定利润；若冲高回落跌破 VWAP 或回落超过 2%，分批兑现。若持续在 VWAP 上方且高点继续抬升，可保留观察仓。"
    elif action == "STOP_LOSS":
        base = "止损优先；反抽不能重新站回 VWAP 或买入价附近时，按纪律卖出。"
    elif action == "WAIT_10_MIN":
        base = f"先观察 10 分钟承接：站稳 VWAP 且不破开盘低点可继续看；跌破 VWAP 或低点下移则减仓/卖出。{force_note}"
    elif action == "LIMIT_UP_WATCH":
        base = "涨停不主动卖；若开板后不能快速回封，或放量开板跌破分时均价，先兑现。"
    elif action == "LIMIT_DOWN_RISK":
        base = "跌停风险，优先等待可成交窗口；能打开且反抽无力时控制风险，不做加仓摊薄。"
    elif action == "PRICE_MISSING":
        base = "价格数据缺失，暂不执行卖出判断，先刷新行情。"
    else:
        base = f"卖出/减仓优先；如果临近 VWAP 反抽失败或继续低于开盘价，执行纪律退出。{force_note}"
    composite = detail.get("composite_action_cn")
    return f"{base} 综合判断：{composite}" if composite else base


def _hold_condition_cn(action: str, detail: dict) -> str:
    base = (
        "价格持续在 VWAP 上方，分时低点抬高，量能不过度萎缩，且未跌破有效止损线。"
        if action in {"TAKE_PROFIT", "WAIT_10_MIN", "LIMIT_UP_WATCH"}
        else "只有快速收回 VWAP 并重新站上买入价/开盘价，才考虑从立即卖出降级为观察。"
    )
    score = _as_float(detail.get("context_score_total"))
    if score <= -2:
        return f"{base} 额外要求：大盘/题材不能继续走弱，且主力资金不能继续明显流出。"
    return base


def _sell_trigger_cn(action: str, detail: dict) -> str:
    stop = detail.get("effective_stop_loss_price", "")
    vwap = detail.get("vwap", "")
    triggers = []
    if stop:
        triggers.append(f"跌破有效止损价 {stop}")
    if vwap:
        triggers.append(f"跌破 VWAP {vwap} 且反抽不过")
    force_time = detail.get("force_exit_before", "10:30")
    if detail.get("past_force_exit_time"):
        triggers.append(f"已过 {force_time} 时间纪律点，若仍低于 VWAP、低点下移或资金继续流出，不再等待")
    else:
        triggers.append(f"{force_time} 前仍未走强或分时低点持续下移")
    if action == "TAKE_PROFIT":
        triggers.append("冲高回落超过 2% 或打开涨停后不能回封")
    if _as_float(detail.get("context_score_total")) <= -2:
        triggers.append("大盘/题材同步走弱且个股主力资金继续流出")
    return "；".join(triggers) + "。"


def _logic_cn(action: str, reason: str, detail: dict, sell_cfg: dict) -> str:
    return (
        f"规则触发：{REASON_CN.get(reason, reason)}。"
        f"价格纪律：开盘涨跌幅 {detail.get('open_change_pct')}%，当前涨跌幅 {detail.get('current_change_pct')}%，"
        f"第一止盈阈值 {sell_cfg.get('take_profit_pct_1', 3)}%，止损阈值 {sell_cfg.get('stop_loss_pct', -3)}%。"
        f"盘中承接：VWAP 偏离 {detail.get('vwap_gap_pct', '')}%，日内位置 {detail.get('range_position_pct', '')}%，"
        f"高点回落 {detail.get('pullback_from_high_pct', '')}%。"
        f"综合环境：{detail.get('context_summary_cn', '暂缺')}。"
        "VWAP 用于判断盘中承接，时间止损用于避免弱势票拖到午后；市场、题材、多日资金、分钟资金、量价用于决定是否要提高卖出纪律。"
    )


def _realtime_alert_cn(action: str, detail: dict) -> str:
    price = _as_float(detail.get("current_price") or detail.get("intraday_last_price"))
    vwap = _as_float(detail.get("vwap"))
    stop = _as_float(detail.get("effective_stop_loss_price"))
    vwap_gap = _as_float(detail.get("vwap_gap_pct"))
    minute_score = _as_float(detail.get("minute_fund_score"))
    context_score = _as_float(detail.get("context_score_total"))
    past_force = bool(detail.get("past_force_exit_time"))
    force_time = detail.get("force_exit_before", "10:30")
    weak_reasons = []
    strong_reasons = []
    if stop and price and price <= stop:
        weak_reasons.append(f"现价已触及/跌破止损价 {stop}")
    if vwap and price and price < vwap:
        weak_reasons.append(f"现价低于 VWAP {vwap}")
    elif vwap and price and price >= vwap:
        strong_reasons.append(f"现价在 VWAP {vwap} 上方")
    if detail.get("intraday_low_break") in {True, "True", "true", "1"}:
        weak_reasons.append("分时低点下移")
    if minute_score <= -1:
        weak_reasons.append(detail.get("minute_fund_cn", "分钟资金偏弱"))
    elif minute_score >= 1:
        strong_reasons.append(detail.get("minute_fund_cn", "分钟资金支持"))
    if past_force and weak_reasons:
        prefix = f"已过 {force_time} 时间纪律点，减仓/卖出优先"
    elif weak_reasons and (vwap_gap < -0.3 or context_score <= -2):
        prefix = "实时承接转弱，减仓优先"
    elif weak_reasons:
        prefix = "实时偏弱，先观察反抽能否收回 VWAP"
    elif action == "LIMIT_UP_WATCH":
        prefix = "涨停/强势观察，未开板前不主动卖"
    elif strong_reasons:
        prefix = "实时承接尚可，继续按 VWAP 和止损线盯盘"
    else:
        prefix = "实时中性，按原计划盯盘"
    reasons = weak_reasons or strong_reasons
    source_note = detail.get("intraday_trend_cn") or ""
    parts = [prefix]
    if reasons:
        parts.append("；".join(str(item) for item in reasons[:3]))
    if source_note:
        parts.append(str(source_note))
    parts.append(f"更新时间 {detail.get('last_update_time', '')}")
    return "。".join(part for part in parts if part) + "。"


def _realtime_trigger_cn(action: str, detail: dict) -> str:
    price = _as_float(detail.get("current_price") or detail.get("intraday_last_price"))
    minute_score = _as_float(detail.get("minute_fund_score"))
    stop = detail.get("effective_stop_loss_price", "")
    vwap = detail.get("vwap", "")
    force_time = detail.get("force_exit_before", "10:30")
    triggers = []
    watches = []
    stop_value = _as_float(stop)
    vwap_value = _as_float(vwap)
    if stop_value and price:
        if price <= stop_value:
            triggers.append(f"现价 {price:g} 已跌破止损价 {stop}")
        elif price <= stop_value * 1.005:
            triggers.append(f"现价 {price:g} 贴近止损价 {stop}，反抽失败即减仓")
        else:
            watches.append(f"止损价 {stop}，跌破才触发硬止损")
    elif stop:
        watches.append(f"止损价 {stop}，等待实时价格确认")
    if vwap_value and price:
        if price < vwap_value:
            triggers.append(f"现价 {price:g} 低于 VWAP {vwap}，3-5 分钟不能收回则减仓")
        else:
            watches.append(f"VWAP {vwap}，跌破后 3-5 分钟不能收回才触发")
    elif vwap:
        watches.append(f"VWAP {vwap}，等待实时价格确认")
    if detail.get("intraday_low_break") in {True, "True", "true", "1"}:
        triggers.append("分时低点正在下移")
    elif detail.get("intraday_trend_cn"):
        watches.append("继续看分时低点是否下移")
    else:
        watches.append("分时数据暂缺，低点下移不作硬触发")
    if minute_score <= -1:
        triggers.append("分钟主力资金继续流出")
    elif minute_score >= 1:
        watches.append("分钟资金暂时支持，不作为卖出触发")
    else:
        watches.append("分钟资金未明显给出方向")
    if detail.get("past_force_exit_time"):
        if triggers:
            triggers.append(f"已过 {force_time}，上述弱势项成立时不再等")
        else:
            watches.append(f"已过 {force_time}，只有 VWAP 失守/低点下移/资金转弱时执行")
    else:
        watches.append(f"{force_time} 前仍未走强才触发时间纪律")
    if action == "LIMIT_UP_WATCH":
        triggers.append("涨停打开后不能快速回封")
    if triggers:
        text = f"当前触发：{'；'.join(triggers)}"
        if watches:
            text += f"。继续盯：{'；'.join(watches)}"
        return text + "。"
    return f"当前未触发硬卖点；继续盯：{'；'.join(watches)}。"


def _build_context_summary(context: dict) -> str:
    parts = [
        context.get("market_context_cn", ""),
        context.get("theme_context_cn", ""),
        context.get("fund_context_cn", ""),
        context.get("minute_fund_cn", ""),
        context.get("today_main_fund_cn", ""),
        context.get("volume_context_cn", ""),
    ]
    summary = "；".join(part for part in parts if part)
    score = context.get("context_score_total", "")
    return f"综合环境分 {score}。{summary}" if summary else f"综合环境分 {score}，上下文数据不足。"


def _composite_action_cn(score: float) -> str:
    if score <= -4:
        return "综合环境偏弱，反弹优先减仓，跌破 VWAP 不等待"
    if score <= -2:
        return "环境不支持恋战，VWAP/止损任一失守即减仓"
    if score >= 3:
        return "环境支持持有观察，但仍按止损线和 VWAP 执行"
    if score >= 1:
        return "环境略有支持，可观察承接，但不放宽止损"
    return "环境中性，按原卖出纪律执行"


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_percent_float(value):
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace("+", "").replace(",", "")
    if text in {"", "-", "--", "None", "nan"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _average(values: list[float]) -> float:
    cleaned = [float(value) for value in values if value is not None]
    return sum(cleaned) / len(cleaned) if cleaned else 0.0


def _fresh_fund_rows(rows: list[dict], max_stale_days: int = 20) -> list[dict]:
    cleaned = list(rows or [])
    if not cleaned:
        return []
    dated = []
    for row in cleaned:
        row_date = _parse_date(str(row.get("time", "")))
        if row_date:
            dated.append((row_date, row))
    if not dated:
        return []
    latest = max(item[0] for item in dated)
    if (date.today() - latest).days > max_stale_days:
        return []
    recent = [(row_date, row) for row_date, row in dated if (latest - row_date).days <= max_stale_days]
    return [row for row_date, row in sorted(recent, key=lambda item: item[0])]


def _current_fund_row(rows: list[dict], source: str) -> dict:
    cleaned = list(rows or [])
    if not cleaned:
        return {}
    if source == "eastmoney_fund_flow_minute":
        return {
            "time": cleaned[-1].get("time", ""),
            "main_net": sum(_as_float(row.get("main_net")) for row in cleaned),
            "large_net": sum(_as_float(row.get("large_net")) for row in cleaned),
            "super_net": sum(_as_float(row.get("super_net")) for row in cleaned),
        }
    return cleaned[-1]


def _parse_date(value: str):
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) >= 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _fmt_pct(value) -> str:
    return f"{_as_float(value):+.2f}%"


def _fmt_wan(value) -> str:
    return f"{_as_float(value):+.0f}万"


if __name__ == "__main__":
    raise SystemExit(main())
