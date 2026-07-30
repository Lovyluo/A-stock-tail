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
    for heading in [
        "数据源清单和抓取时间",
        "宏观消息",
        "政策/监管消息",
        "市场/资金消息",
        "海外市场消息",
        "产业/题材消息",
        "个股公告/新闻",
        "今日关注方向",
        "分歧后的进攻方案",
        "分歧后的防御方案",
    ]:
        assert heading in text
    assert "必涨" not in text and "稳赚" not in text


def test_missing_news_source_degrades_safely(tmp_path):
    def failed(**_):
        raise RuntimeError("offline")
    result = run_news_briefing("live", "2026-07-22", _config(tmp_path), source_fetchers={"eastmoney_global_news": failed, "cls_telegraph": failed, "eastmoney_stock_news": failed, "cninfo_announcements": failed}, candidate_rows=[])
    assert result["status"] == "NEWS_BRIEFING_DEGRADED"
    assert any("来源不可用" in item for item in result["risk_notes"])


def test_multiple_broad_sources_are_deduplicated_and_partial_failure_is_visible(tmp_path):
    stamp = "2026-07-22T08:30:00+08:00"

    def healthy(**_):
        return [
            {"title": "A股成交额放大，人工智能板块活跃", "published_at": stamp, "source": "stub"},
            {"title": "A股成交额放大，人工智能板块活跃", "published_at": stamp, "source": "duplicate"},
            {"title": "美联储讨论利率路径", "published_at": stamp, "source": "stub"},
        ]

    def failed(**_):
        raise RuntimeError("offline")

    fetchers = {
        "eastmoney_global_news": healthy,
        "cls_telegraph": failed,
        "newsnow_cls_hot": healthy,
        "newsnow_wallstreetcn": healthy,
        "newsnow_jin10": healthy,
        "newsnow_xueqiu_hotstock": healthy,
        "eastmoney_stock_news": lambda **_: [],
        "cninfo_announcements": lambda **_: [],
    }
    result = run_news_briefing(
        "live",
        "2026-07-22",
        _config(tmp_path),
        datetime(2026, 7, 22, 9, 0, tzinfo=CN_TZ),
        fetchers,
        [],
    )

    assert result["status"] == "NEWS_BRIEFING_PARTIAL"
    assert result["news_count"] == 2
    assert len(result["market_news"]) == 1
    assert len(result["global_news"]) == 1
    assert any(item["source"] == "cls_telegraph" and not item["ok"] for item in result["sources"])


def test_newsnow_payload_is_normalized(monkeypatch):
    from overnight_quant.strategy.news_briefing import fetch_newsnow

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "title": "证监会发布市场信息",
                        "url": "https://example.test/a",
                        "pubDate": 1784827059000,
                        "extra": {"info": "市场保持平稳"},
                    }
                ]
            }

    monkeypatch.setattr("overnight_quant.strategy.news_briefing.requests.get", lambda *args, **kwargs: Response())

    rows = fetch_newsnow("cls-hot", "newsnow_cls_hot", max_items=5)

    assert rows[0]["title"] == "证监会发布市场信息"
    assert rows[0]["summary"] == "市场保持平稳"
    assert rows[0]["source"] == "newsnow_cls_hot"
