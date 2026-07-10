from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.demo_data import demo_quotes
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.execution.position_tracker import get_open_positions
from overnight_quant.execution.state_manager import config_for_mode
from overnight_quant.reports.intraday_report import write_intraday_report, write_intraday_signals_csv
from overnight_quant.strategy.intraday_observation import IntradayObservationAnalyzer, load_intraday_config


def run_intraday_observation(
    mode: str = "demo",
    trade_date: str | None = None,
    config: dict | None = None,
    client=None,
    now: datetime | None = None,
    watchlist_path: str | None = None,
) -> dict:
    runtime_config = config_for_mode(config or load_intraday_config(), mode)
    analyzer_now = _demo_default_now() if mode == "demo" and now is None else _normalize_now(now)
    paths = runtime_config.get("paths", {})
    records_dir = Path(paths.get("records_dir", "overnight_quant/records"))
    candidate_rows, candidate_source = _load_candidates(records_dir, watchlist_path)
    position_rows = _load_position_candidates(records_dir, runtime_config)
    if position_rows:
        candidate_rows = _merge_candidate_sources(candidate_rows, position_rows)
        candidate_source = _join_sources(candidate_source, "open_positions")
    if mode == "demo" and not candidate_rows:
        candidate_rows = _demo_candidates()
        candidate_source = "demo_synthetic_watchlist"
    runtime_client = client or AStockClient(mode, now=analyzer_now)
    result = IntradayObservationAnalyzer(
        runtime_client,
        runtime_config,
        mode,
        analyzer_now,
        candidate_rows=candidate_rows,
    ).analyze(trade_date)
    result["candidate_source"] = candidate_source or result.get("candidate_source", "")
    result["report_path"] = write_intraday_report(result, paths["reports_dir"])
    result["signals_csv"] = write_intraday_signals_csv(result, paths["records_dir"])
    return result


def _load_candidates(records_dir: Path, explicit_path: str | None = None) -> tuple[list[dict], str]:
    path = Path(explicit_path) if explicit_path else _latest_watchlist(records_dir)
    if not path or not path.exists():
        return [], ""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows = [row for row in rows if str(row.get("category", "")).upper() in {"A", "B"}]
    return rows, str(path)


def _load_position_candidates(records_dir: Path, config: dict) -> list[dict]:
    score = float(config.get("intraday", {}).get("position_candidate_score", 72) or 72)
    rows: list[dict] = []
    for position in get_open_positions(str(records_dir)):
        rows.append(
            {
                "trade_date": position.get("trade_date", ""),
                "code": position.get("code", ""),
                "name": position.get("name", ""),
                "category": "POSITION",
                "score": score,
                "risk_flags": "",
                "is_position": True,
                "open_qty": position.get("open_qty", ""),
                "avg_buy_price": position.get("avg_buy_price", ""),
                "stop_loss_price": position.get("stop_loss_price", ""),
            }
        )
    return rows


def _merge_candidate_sources(watchlist_rows: list[dict], position_rows: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in watchlist_rows:
        code = _normalize_code(row.get("code"))
        if code:
            merged[code] = dict(row)
    for row in position_rows:
        code = _normalize_code(row.get("code"))
        if not code:
            continue
        if code in merged:
            merged[code].update(
                {
                    "is_position": True,
                    "open_qty": row.get("open_qty", ""),
                    "avg_buy_price": row.get("avg_buy_price", ""),
                    "position_score": row.get("score", ""),
                }
            )
        else:
            merged[code] = dict(row)
    return list(merged.values())


def _join_sources(*values: str) -> str:
    return "+".join(value for value in values if value)


def _latest_watchlist(records_dir: Path) -> Path | None:
    patterns = ["morning_replay_watchlist_*.csv", "next_morning_watchlist_*.csv"]
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(path for path in records_dir.glob(pattern) if path.is_file())
    if not matches:
        return None
    return max(matches, key=lambda path: (path.stat().st_mtime, path.name))


def _demo_candidates() -> list[dict]:
    rows = []
    for category, score, quote in [("A", 88, demo_quotes()[0]), ("B", 74, demo_quotes()[4])]:
        rows.append(
            {
                "trade_date": "demo",
                "code": quote["code"],
                "name": quote["name"],
                "category": category,
                "score": score,
                "theme_tags": "|".join(quote.get("theme_tags") or []),
                "risk_flags": "",
            }
        )
    return rows


def _normalize_code(value) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _normalize_now(now: datetime | None) -> datetime:
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        return current.replace(tzinfo=CN_TZ)
    return current.astimezone(CN_TZ)


def _demo_default_now() -> datetime:
    current = datetime.now(CN_TZ)
    return datetime.combine(current.date(), time(10, 5), tzinfo=CN_TZ)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate intraday VWAP pullback observation reminders.")
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--date", default=None)
    parser.add_argument("--watchlist", default=None, help="Optional explicit A/B watchlist CSV path.")
    args = parser.parse_args()

    result = run_intraday_observation(mode=args.mode, trade_date=args.date, watchlist_path=args.watchlist)
    print("Mode: " + result["mode"])
    print("Status: " + result["status"])
    print("Session State: " + result["session_state"])
    print("Intraday Window: " + result["intraday_window"])
    print("Candidate Source: " + result["candidate_source"])
    print("Valid For Trading Observation: " + result["valid_for_trading_observation"])
    print(f"Signal Count: {result.get('signal_count', 0)}")
    print(f"Buy Point A Count: {result.get('buy_point_a_count', 0)}")
    print(f"Buy Point B Count: {result.get('buy_point_b_count', 0)}")
    print(f"Buy Watch Count: {result.get('buy_watch_count', 0)}")
    print("Report: " + result["report_path"])
    print("Signals CSV: " + result["signals_csv"])
    print("Risk Notice: observation only; no automated orders; not investment advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
