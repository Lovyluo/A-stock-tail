from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.market_calendar import CN_TZ, get_session_state, is_likely_cn_trade_day
from overnight_quant.reports.scan_reports import write_preflight_report
from overnight_quant.strategy.yang_yongxing_overnight import load_config
from overnight_quant.strategy.news_briefing import fetch_cls_telegraph, fetch_eastmoney_global_news
from overnight_quant.ui.result_parser import (
    find_latest_file,
    parse_after_close_report,
    parse_intraday_report,
    parse_key_value_md,
)


def run_preflight(config: dict | None = None, client: AStockClient | None = None, now: datetime | None = None, trade_date: str | None = None, check_network: bool = False) -> dict:
    current = _coerce_now(now)
    trade_date = trade_date or current.date().isoformat()
    config_ok = _config_readable()
    config = config or load_config()
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    explicit_client = client is not None
    client = client or AStockClient("live", now=current)

    session_state = get_session_state(current)
    is_trade_day = is_likely_cn_trade_day(current)
    records_writable = _is_writable_dir(records_dir)
    reports_writable = _is_writable_dir(reports_dir)
    backtest_writable = _is_writable_dir(config.get("backtest", {}).get("output_dir", "overnight_quant/backtest_outputs"))
    cache_writable = _is_writable_dir("overnight_quant/data/cache")
    sources = _check_sources(client) if check_network else []
    workflows = [] if explicit_client else _check_demo_workflows()
    parser_check = _check_dashboard_parser(Path(reports_dir))
    infrastructure_ok = all([config_ok, records_writable, reports_writable, backtest_writable, cache_writable])
    workflows_execution_ok = all(item.get("execution_ok", item.get("ok", False)) for item in workflows)
    execution_ok = infrastructure_ok and workflows_execution_ok and parser_check["ok"]
    readiness_checks = [
        item.get("data_ready")
        for item in workflows
        if item.get("data_ready") is not None
    ]
    if parser_check.get("data_ready") is not None:
        readiness_checks.append(parser_check["data_ready"])
    if check_network:
        readiness_checks.extend(bool(item.get("ok")) for item in sources)
    data_ready = execution_ok and all(readiness_checks)

    if not execution_ok:
        status = "PROJECT_HEALTH_FAILED"
    elif not data_ready:
        status = "PROJECT_HEALTH_DEGRADED"
    else:
        status = "PROJECT_HEALTHY"

    result = {
        "status": status,
        "execution_ok": execution_ok,
        "data_ready": data_ready,
        "trade_date": trade_date,
        "run_time": current.isoformat(),
        "session_state": session_state,
        "is_trade_day": is_trade_day,
        "config_ok": config_ok,
        "records_writable": records_writable,
        "reports_writable": reports_writable,
        "backtest_outputs_writable": backtest_writable,
        "cache_writable": cache_writable,
        "workflow_checks": workflows,
        "dashboard_parser": parser_check,
        "network_check_enabled": check_network,
        "sources": sources,
    }
    result["report_path"] = write_preflight_report(result, reports_dir, trade_date)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run project path and workflow health checks.")
    parser.add_argument("--date", default=None)
    parser.add_argument("--network", action="store_true", help="Also check optional external data sources.")
    args = parser.parse_args()
    config = load_config()
    result = run_preflight(config=config, trade_date=args.date, check_network=args.network)
    print(result["status"])
    print(f"execution_ok: {str(bool(result.get('execution_ok'))).lower()}")
    print(f"data_ready: {str(bool(result.get('data_ready'))).lower()}")
    print(f"session_state: {result['session_state']}")
    print(f"Health Check Report: {result['report_path']}")
    return 0


def _check_sources(client: AStockClient) -> list[dict]:
    checks = [
        ("tencent_quote", lambda: client._tencent_quotes(["300001"])),
        ("eastmoney_push2", lambda: client._eastmoney_fund_flow_minute("300001")),
        ("cls_telegraph", lambda: fetch_cls_telegraph(max_items=3)),
        ("eastmoney_global_news", lambda: fetch_eastmoney_global_news(max_items=3)),
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


def _check_demo_workflows() -> list[dict]:
    commands = [
        ("demo_scan", [sys.executable, "overnight_quant/scripts/run_scan.py", "--mode", "demo", "--dry-run"], False),
        ("after_close_demo", [sys.executable, "overnight_quant/scripts/run_after_close_analysis.py", "--mode", "demo"], False),
        ("intraday_demo", [sys.executable, "overnight_quant/scripts/run_intraday_observation.py", "--mode", "demo"], False),
        (
            "close_confirmation_shadow",
            [sys.executable, "overnight_quant/scripts/run_close_confirmation.py", "--mode", "shadow"],
            True,
        ),
    ]
    rows = []
    for name, command, requires_data in commands:
        try:
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=90, shell=False)
            execution_ok = _stdout_bool(completed.stdout, "execution_ok")
            data_ready = _stdout_bool(completed.stdout, "data_ready")
            if execution_ok is None:
                execution_ok = completed.returncode == 0
            if data_ready is None:
                data_ready = execution_ok if not requires_data else False
            rows.append(
                {
                    "name": name,
                    "ok": execution_ok,
                    "execution_ok": execution_ok,
                    "data_ready": data_ready,
                    "returncode": completed.returncode,
                    "error": completed.stderr[-300:] if not execution_ok else "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "name": name,
                    "ok": False,
                    "execution_ok": False,
                    "data_ready": False,
                    "returncode": -1,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return rows


def _check_dashboard_parser(reports_dir: Path) -> dict:
    try:
        after_path = find_latest_file("after_close_analysis_*.md", reports_dir)
        intraday_path = find_latest_file("intraday_observation_*.md", reports_dir)
        close_confirmation_path = find_latest_file("close_confirmation_shadow_*.md", reports_dir)
        parsed = []
        if after_path:
            parsed.append(parse_after_close_report(after_path).get("status"))
        if intraday_path:
            parsed.append(parse_intraday_report(intraday_path).get("status"))
        data_ready = None
        if close_confirmation_path:
            close_confirmation = parse_key_value_md(close_confirmation_path)
            parsed.append(close_confirmation.get("status"))
            data_ready = _value_bool(close_confirmation.get("data_ready"))
        return {
            "ok": True,
            "parsed_reports": len(parsed),
            "statuses": [item for item in parsed if item],
            "data_ready": data_ready,
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "parsed_reports": 0,
            "statuses": [],
            "data_ready": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _stdout_bool(stdout: str, label: str) -> bool | None:
    prefix = f"{label}:"
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return _value_bool(line.split(":", 1)[1].strip())
    return None


def _value_bool(value: object) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    return None


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
