from datetime import datetime
from pathlib import Path

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.scripts.run_news_briefing import run_news_briefing
from overnight_quant.strategy.news_briefing import load_news_config


def _config(tmp_path):
    config = load_news_config()
    config["paths"] = {"records_dir": str(tmp_path / "records"), "reports_dir": str(tmp_path / "reports"), "examples_dir": str(tmp_path / "examples")}
    return config


def test_news_report_contains_required_sections(tmp_path):
    stamp = "2026-07-22T08:30:00+08:00"
    broad = lambda **_: [{"title": "央行政策支持人工智能产业", "published_at": stamp, "source": "stub"}]
    stock = lambda **_: [{"title": "公司发布经营进展公告", "published_at": stamp, "source": "stub"}]
    result = run_news_briefing("live", "2026-07-22", _config(tmp_path), datetime(2026, 7, 22, 9, 0, tzinfo=CN_TZ), {"eastmoney_global_news": broad, "cls_telegraph": broad, "eastmoney_stock_news": stock, "cninfo_announcements": stock}, [{"code": "000001", "name": "示例"}])
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    for heading in ["数据源清单和抓取时间", "宏观消息", "政策/监管消息", "产业/题材消息", "个股公告/新闻", "今日关注方向", "分歧后的进攻方案", "分歧后的防御方案"]:
        assert heading in text
    assert "必涨" not in text and "稳赚" not in text


def test_missing_news_source_degrades_safely(tmp_path):
    def failed(**_):
        raise RuntimeError("offline")
    result = run_news_briefing("live", "2026-07-22", _config(tmp_path), source_fetchers={"eastmoney_global_news": failed, "cls_telegraph": failed, "eastmoney_stock_news": failed, "cninfo_announcements": failed}, candidate_rows=[])
    assert result["status"] == "NEWS_BRIEFING_DEGRADED"
    assert any("缺失来源" in item for item in result["risk_notes"])
