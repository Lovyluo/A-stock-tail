from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.execution.state_manager import config_for_mode
from overnight_quant.reports.news_briefing_report import write_news_briefing_report
from overnight_quant.strategy.auction_observation import load_trading_day_candidates
from overnight_quant.strategy.news_briefing import NewsBriefingAnalyzer, finalize_news_sources, load_news_config


def run_news_briefing(mode: str = "demo", trade_date: str | None = None, config: dict | None = None, now: datetime | None = None, source_fetchers=None, candidate_rows: list[dict] | None = None) -> dict:
    runtime = config_for_mode(config or load_news_config(), mode)
    paths = runtime.get("paths", {})
    candidates = candidate_rows if candidate_rows is not None else load_trading_day_candidates(paths["records_dir"])
    analyzer = NewsBriefingAnalyzer(runtime, mode, now, candidates, source_fetchers)
    if mode == "demo" and source_fetchers is None:
        analyzer.fetchers = _demo_fetchers()
    result = finalize_news_sources(analyzer, analyzer.analyze(trade_date))
    result["report_path"] = write_news_briefing_report(result, paths["reports_dir"])
    return result


def _demo_fetchers():
    stamp = datetime.now(CN_TZ).isoformat(timespec="seconds")
    broad = lambda **_: [{"title": "政策支持人工智能产业规范发展", "summary": "产业景气与风险并存", "published_at": stamp, "source": "demo"}]
    stock = lambda code="", **_: [{"title": f"{code} 发布经营进展公告", "published_at": stamp, "source": "demo"}]
    return {"eastmoney_global_news": broad, "cls_telegraph": broad, "eastmoney_stock_news": stock, "cninfo_announcements": stock}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate pre-market news briefing.")
    parser.add_argument("--date", default=None)
    parser.add_argument("--mode", choices=["live", "demo"], default="demo")
    args = parser.parse_args()
    result = run_news_briefing(args.mode, args.date)
    print(f"Status: {result['status']}")
    print(f"Report: {result['report_path']}")
    print("Risk Notice: extractive briefing only; not investment advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
