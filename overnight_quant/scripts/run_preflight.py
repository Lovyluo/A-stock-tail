from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.market_calendar import CN_TZ, TAIL_SESSION, get_session_state, is_likely_cn_trade_day
from overnight_quant.reports.scan_reports import write_preflight_report
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def run_preflight(config: dict | None = None, client: AStockClient | None = None, now: datetime | None = None, trade_date: str | None = None) -> dict:
    current = _coerce_now(now)
    trade_date = trade_date or current.date().isoformat()
    config_ok = _config_readable()
    config = config or load_config()
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    client = client or AStockClient("live", now=current)

    session_state = get_session_state(current)
    is_trade_day = is_likely_cn_trade_day(current)
    records_writable = _is_writable_dir(records_dir)
    reports_writable = _is_writable_dir(reports_dir)
    sources = _check_sources(client)
    degraded = any(not item.get("ok") for item in sources)

    if not config_ok or not records_writable or not reports_writable:
        status = "CONFIG_ERROR"
    elif not is_trade_day:
        status = "NON_TRADING_DAY"
    elif session_state != TAIL_SESSION:
        status = "OUTSIDE_TAIL_SESSION"
    elif degraded:
        status = "DATA_SOURCE_DEGRADED"
    else:
        status = "READY_FOR_LIVE_SCAN"

    result = {
        "status": status,
        "trade_date": trade_date,
        "run_time": current.isoformat(),
        "session_state": session_state,
        "is_trade_day": is_trade_day,
        "config_ok": config_ok,
        "records_writable": records_writable,
        "reports_writable": reports_writable,
        "sources": sources,
    }
    result["report_path"] = write_preflight_report(result, reports_dir, trade_date)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live scan preflight checks.")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    config = load_config()
    result = run_preflight(config=config, trade_date=args.date)
    print(result["status"])
    print(f"session_state: {result['session_state']}")
    print(f"Preflight Report: {result['report_path']}")
    return 0


def _check_sources(client: AStockClient) -> list[dict]:
    checks = [
        ("tencent_quote", lambda: client._tencent_quotes(["300001"])),
        ("ths_hot", lambda: client._ths_hot_reason_candidates()),
        ("baidu_kline", lambda: client._baidu_daily_kline("300001", 5)),
        ("eastmoney_meta", lambda: client._eastmoney_quote_meta(["300001"])),
        ("fund_flow", lambda: client._eastmoney_fund_flow_minute("300001")),
    ]
    rows: list[dict] = []
    for source, fn in checks:
        try:
            data = fn()
            count = len(data) if hasattr(data, "__len__") else 1
            rows.append({"source": source, "ok": count > 0, "rows": count, "error": "" if count > 0 else "empty"})
        except Exception as exc:
            rows.append({"source": source, "ok": False, "rows": 0, "error": f"{type(exc).__name__}: {exc}"})
    return rows


def _config_readable() -> bool:
    return (Path(__file__).resolve().parents[1] / "config.yaml").is_file()


def _is_writable_dir(path_value: str) -> bool:
    path = Path(path_value)
    try:
        path.mkdir(parents=True, exist_ok=True)
        marker = path / ".preflight_write_test"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _coerce_now(now: datetime | None) -> datetime:
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CN_TZ)
    return current.astimezone(CN_TZ)


if __name__ == "__main__":
    raise SystemExit(main())
