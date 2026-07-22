from __future__ import annotations

import copy
import csv
from datetime import datetime, time
from pathlib import Path
from typing import Any

from overnight_quant.data.market_calendar import CN_TZ, is_likely_cn_trade_day
from overnight_quant.execution.position_tracker import get_open_positions


DEFAULT_AUCTION_CONFIG = {
    "auction": {
        "enabled": True,
        "live_start": "09:25",
        "live_end": "09:30",
        "max_candidates": 50,
        "strong_gap_pct": 3.0,
        "weak_gap_pct": -2.0,
        "min_amount_wan": 1000,
    },
    "paths": {
        "records_dir": "overnight_quant/records",
        "reports_dir": "overnight_quant/reports",
        "examples_dir": "overnight_quant/examples",
    },
}


def load_auction_config(path: str | None = None) -> dict:
    config = copy.deepcopy(DEFAULT_AUCTION_CONFIG)
    config_path = Path(path) if path else Path(__file__).resolve().parents[1] / "config.yaml"
    if not config_path.exists():
        return config
    try:
        import yaml  # type: ignore

        _deep_update(config, yaml.safe_load(config_path.read_text(encoding="utf-8")) or {})
    except Exception:
        pass
    return config


def load_trading_day_candidates(records_dir: str | Path, include_auction: bool = False) -> list[dict]:
    records = Path(records_dir)
    merged: dict[str, dict] = {}

    for position in get_open_positions(str(records)):
        _merge_candidate(merged, position, "holding", priority=0)

    signals = records / "signals.csv"
    for row in _read_csv(signals):
        _merge_candidate(merged, row, "tail_pick", priority=2)

    watchlist = _latest(records, ["morning_replay_watchlist_*.csv", "next_morning_watchlist_*.csv"])
    if watchlist:
        for row in _read_csv(watchlist):
            if str(row.get("category", "")).upper() in {"A", "B"}:
                _merge_candidate(merged, row, "watchlist", priority=1)

    if include_auction:
        auction = _latest(records, ["auction_observation_*.csv"])
        if auction:
            for row in _read_csv(auction):
                _merge_candidate(merged, row, "auction_new", priority=3)

    rows = sorted(merged.values(), key=lambda row: (int(row.get("_priority", 9)), str(row.get("code", ""))))
    for row in rows:
        buckets = list(row.pop("_source_bucket_list", []))
        row["source_buckets"] = "|".join(buckets)
        row["source_bucket"] = "holding" if "holding" in buckets else (buckets[0] if buckets else "watchlist")
    return rows


class AuctionObservationAnalyzer:
    def __init__(self, client, config: dict, mode: str, now: datetime | None = None, candidate_rows: list[dict] | None = None):
        self.client = client
        self.config = config
        self.mode = mode
        self.now = _coerce_now(now)
        self.candidate_rows = [dict(row) for row in (candidate_rows or [])]

    def analyze(self, trade_date: str | None = None) -> dict:
        result = {
            "trade_date": trade_date or self.now.date().isoformat(),
            "run_time": self.now.isoformat(timespec="seconds"),
            "mode": self.mode,
            "status": "UNKNOWN",
            "market_auction_bias": "neutral",
            "market_indices": {},
            "rows": [],
            "source_errors": [],
            "valid_for_trading_observation": "DEMO_ONLY" if self.mode == "demo" else "NO",
        }
        if self.mode == "live" and not is_likely_cn_trade_day(self.now):
            result["status"] = "NON_TRADING_DAY"
            return result
        if self.mode == "live" and not _in_window(self.now, self.config.get("auction", {})):
            result["status"] = "NOT_AUCTION_WINDOW"
            return result

        fallback_offset = _fallback_message_count(self.client)
        try:
            market = self.client.get_market_snapshot()
        except Exception as exc:
            market = {}
            result["source_errors"].append(f"market_snapshot: {type(exc).__name__}: {exc}")
        market_fallbacks = _fallback_messages_since(self.client, fallback_offset)
        if self.mode == "live" and market_fallbacks:
            market = {}
            result["source_errors"].extend(f"market_snapshot_demo_fallback: {message}" for message in market_fallbacks)
        result["market_indices"] = market.get("indices") or {}
        result["market_auction_bias"] = market_auction_bias(market)

        limit = int(self.config.get("auction", {}).get("max_candidates", 50) or 50)
        for candidate in self.candidate_rows[:limit]:
            code = _normalize_code(candidate.get("code"))
            if not code:
                continue
            fallback_offset = _fallback_message_count(self.client)
            try:
                quote = self.client.get_current_price(code)
            except Exception as exc:
                quote = {}
                result["source_errors"].append(f"{code}: {type(exc).__name__}: {exc}")
            quote_fallbacks = _fallback_messages_since(self.client, fallback_offset)
            if self.mode == "live" and quote_fallbacks:
                quote = {}
                result["source_errors"].extend(f"{code}_demo_fallback: {message}" for message in quote_fallbacks)
            result["rows"].append(evaluate_auction_candidate(candidate, quote, result["market_auction_bias"], self.config))

        usable = [row for row in result["rows"] if float(row.get("auction_price") or 0) > 0]
        if self.mode == "live" and not usable:
            result["status"] = "AUCTION_DATA_UNAVAILABLE"
        elif self.mode == "demo":
            result["status"] = "DEMO_AUCTION_OBSERVATION"
        else:
            result["status"] = "AUCTION_OBSERVATION_READY"
            result["valid_for_trading_observation"] = "YES"
        return result


def market_auction_bias(market: dict) -> str:
    changes = []
    for item in (market.get("indices") or {}).values():
        value = _as_float(item.get("change_pct"), None)
        if value is not None:
            changes.append(value)
    if not changes:
        return "neutral"
    average = sum(changes) / len(changes)
    if average >= 0.35:
        return "strong"
    if average <= -0.35:
        return "weak"
    return "neutral"


def evaluate_auction_candidate(candidate: dict, quote: dict, market_bias: str, config: dict) -> dict:
    auction_price = _as_float(quote.get("price") or quote.get("auction_price") or quote.get("open_price") or quote.get("open"), 0.0)
    prev_close = _as_float(quote.get("last_close") or quote.get("prev_close") or candidate.get("close_price"), 0.0)
    gap = round((auction_price / prev_close - 1) * 100, 2) if auction_price and prev_close else 0.0
    amount = _as_float(quote.get("amount_wan") or quote.get("auction_amount_wan"), 0.0)
    volume_ratio = _as_float(quote.get("vol_ratio") or quote.get("volume_ratio"), 0.0)
    settings = config.get("auction", {})
    strong_gap = float(settings.get("strong_gap_pct", 3.0))
    weak_gap = float(settings.get("weak_gap_pct", -2.0))
    min_amount = float(settings.get("min_amount_wan", 1000))
    reasons: list[str] = []
    risks: list[str] = []
    source_bucket = str(candidate.get("source_bucket") or "watchlist")

    if not auction_price or not prev_close:
        action = "avoid"
        risks.append("auction_quote_missing")
    elif gap >= strong_gap and (amount < min_amount or volume_ratio < 1):
        action = "observe"
        reasons.append("high_gap_without_volume_confirmation")
        risks.append("auction_volume_insufficient")
    elif market_bias == "weak" and gap >= strong_gap:
        action = "defend"
        reasons.append("weak_index_with_large_positive_gap")
    elif market_bias == "strong" and 0 <= gap < strong_gap and (amount >= min_amount or volume_ratio >= 1.2):
        action = "attack"
        reasons.extend(["market_direction_supportive", "moderate_gap_with_volume"])
    elif gap <= weak_gap or market_bias == "weak" or source_bucket == "holding":
        action = "defend" if source_bucket == "holding" or gap <= weak_gap else "observe"
        reasons.append("defence_first_context")
    else:
        action = "observe"
        reasons.append("wait_for_intraday_confirmation")

    return {
        "code": _normalize_code(candidate.get("code")),
        "name": candidate.get("name") or quote.get("name", ""),
        "source_bucket": source_bucket,
        "source_buckets": candidate.get("source_buckets") or source_bucket,
        "auction_price": auction_price,
        "prev_close": prev_close,
        "auction_gap_pct": gap,
        "auction_amount_wan": amount,
        "volume_ratio": volume_ratio,
        "market_auction_bias": market_bias,
        "action_bias": action,
        "reasons": reasons,
        "risk_flags": risks,
    }


def _merge_candidate(target: dict[str, dict], raw: dict, bucket: str, priority: int) -> None:
    code = _normalize_code(raw.get("code"))
    if not code:
        return
    row = target.setdefault(code, {"code": code, "_source_bucket_list": [], "_priority": priority})
    row["_priority"] = min(int(row.get("_priority", priority)), priority)
    if bucket not in row["_source_bucket_list"]:
        row["_source_bucket_list"].append(bucket)
    row.update({key: value for key, value in raw.items() if value not in (None, "")})


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _latest(directory: Path, patterns: list[str]) -> Path | None:
    matches = [path for pattern in patterns for path in directory.glob(pattern) if path.is_file()]
    return max(matches, key=lambda path: (path.stat().st_mtime, path.name)) if matches else None


def _in_window(value: datetime, settings: dict) -> bool:
    start = _parse_time(settings.get("live_start", "09:25"))
    end = _parse_time(settings.get("live_end", "09:30"))
    return start <= value.time().replace(tzinfo=None) <= end


def _parse_time(value: str) -> time:
    hour, minute = (int(item) for item in str(value).split(":")[:2])
    return time(hour, minute)


def _coerce_now(value: datetime | None) -> datetime:
    current = value or datetime.now(CN_TZ)
    return (current.replace(tzinfo=CN_TZ) if current.tzinfo is None else current).astimezone(CN_TZ)


def _normalize_code(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _as_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fallback_message_count(client: Any) -> int:
    messages = getattr(client, "fallback_messages", None)
    return len(messages) if isinstance(messages, list) else 0


def _fallback_messages_since(client: Any, offset: int) -> list[str]:
    messages = getattr(client, "fallback_messages", None)
    if not isinstance(messages, list):
        return []
    return [str(message) for message in messages[offset:]]


def _deep_update(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
