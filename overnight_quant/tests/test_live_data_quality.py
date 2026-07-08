import http.client
from pathlib import Path

from overnight_quant.data.astock_client import AStockClient, LIVE_SCAN_DEFAULT_MAX_CANDIDATES
from overnight_quant.data.demo_data import demo_quotes
from overnight_quant.data.live_data_quality import normalize_live_stock, validate_stock_fields
from overnight_quant.risk.risk_manager import RiskManager
from overnight_quant.strategy.yang_yongxing_overnight import YangYongxingOvernightStrategy, load_config


def test_demo_mode_is_deterministic_and_does_not_call_live(monkeypatch):
    client = AStockClient("demo")

    def fail_live_call(*args, **kwargs):
        raise AssertionError("demo mode must not call live adapters")

    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", fail_live_call)
    first = client.get_candidate_quotes()
    second = client.get_candidate_quotes()

    assert first == second == demo_quotes()
    assert client.fallback_messages == []


def test_get_json_retries_transient_remote_disconnect(monkeypatch):
    calls = []

    class FakeResponse:
        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=10):
        calls.append(req)
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("temporary disconnect")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    data = AStockClient("live")._get_json("https://example.test/api", timeout=1)

    assert data == {"ok": True}
    assert len(calls) == 2


def test_live_partial_data_does_not_crash_and_records_missing_fields(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")

    monkeypatch.setattr(
        client,
        "_fetch_live_candidate_seeds",
        lambda: [
            {
                "code": "300001",
                "name": "Partial Live",
                "theme_tags": ["AI"],
                "_sources": {"code": "test.seed", "name": "test.seed", "theme_tags": "test.seed"},
            }
        ],
    )
    monkeypatch.setattr(
        client,
        "_tencent_quotes",
        lambda codes: {
            "300001": {
                "code": "300001",
                "name": "Partial Live",
                "price": 10.5,
                "change_pct": 4.2,
                "vol_ratio": 1.3,
                "turnover_pct": 6.5,
                "amount_wan": 18000,
                "float_mcap_yi": 90,
                "_sources": {
                    "price": "test.tencent",
                    "change_pct": "test.tencent",
                    "vol_ratio": "test.tencent",
                    "turnover_pct": "test.tencent",
                    "amount_wan": "test.tencent",
                    "float_mcap_yi": "test.tencent",
                },
            }
        },
    )
    monkeypatch.setattr(client, "_eastmoney_stock_info", lambda code: {})
    monkeypatch.setattr(client, "_eastmoney_quote_meta", lambda codes: {})
    monkeypatch.setattr(client, "_eastmoney_fund_flow_minute", lambda code: [])
    monkeypatch.setattr(client, "_eastmoney_quote_fund_flow", lambda codes: {})
    monkeypatch.setattr(client, "_eastmoney_fund_flow_daily", lambda code: [])

    rows = client.get_candidate_quotes()

    assert len(rows) == 1
    assert rows[0]["code"] == "300001"
    assert "limit_up" in rows[0]["_missing_fields"]
    assert "is_new_stock" in rows[0]["_missing_fields"]
    assert client.quality_report.field_coverage["limit_up"]["missing"] == 1


def test_live_total_failure_falls_back_to_demo(monkeypatch):
    client = AStockClient("live")

    def fail_source():
        raise RuntimeError("source down")

    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", fail_source)

    rows = client.get_candidate_quotes()

    assert rows == demo_quotes()
    assert client.quality_report.fallback_to_demo is True
    assert any("fallback to demo" in message for message in client.fallback_messages)


def test_after_close_universe_filters_to_00_and_60_before_enrichment(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    seeds_seen = []

    def fake_json(url, params=None, headers=None, timeout=10):
        return {
            "data": {
                "diff": [
                    {"f12": "000001", "f14": "Allowed 00", "f2": 10, "f3": 4.1, "f6": 250000000, "f8": 6.0, "f10": 1.4, "f15": 10.2, "f16": 9.8, "f17": 9.9, "f21": 9000000000, "f124": 1800000000},
                    {"f12": "600001", "f14": "Allowed 60", "f2": 12, "f3": 4.8, "f6": 260000000, "f8": 7.0, "f10": 1.5, "f15": 12.3, "f16": 11.7, "f17": 11.9, "f21": 8000000000, "f124": 1800000000},
                    {"f12": "300001", "f14": "Rejected 30", "f2": 18, "f3": 5.0, "f6": 300000000, "f8": 8.0, "f10": 1.6, "f15": 18.5, "f16": 17.2, "f17": 17.5, "f21": 7000000000, "f124": 1800000000},
                    {"f12": "688001", "f14": "Rejected 68", "f2": 30, "f3": 5.5, "f6": 310000000, "f8": 8.2, "f10": 1.7, "f15": 31, "f16": 29, "f17": 30, "f21": 7000000000, "f124": 1800000000},
                    {"f12": "510300", "f14": "Rejected ETF", "f2": 4, "f3": 3.2, "f6": 500000000, "f8": 4.0, "f10": 1.1, "f15": 4.1, "f16": 3.9, "f17": 4.0, "f21": 5000000000, "f124": 1800000000},
                ]
            }
        }

    def fake_build(seeds):
        seeds_seen.extend(seeds)
        return seeds

    monkeypatch.setattr(client, "_get_json", fake_json)
    monkeypatch.setattr(client, "_build_after_close_candidates", fake_build)

    rows = client.get_after_close_universe_quotes()

    assert [row["code"] for row in seeds_seen] == ["000001", "600001"]
    assert [row["code"] for row in rows] == ["000001", "600001"]
    assert any(item["source"] == "eastmoney_after_close_universe" for item in client.quality_report.source_status)


def test_after_close_universe_seed_includes_eastmoney_clist_fund_flow_fields(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")

    monkeypatch.setattr(
        client,
        "_get_json",
        lambda *args, **kwargs: {
            "data": {
                "diff": [
                    {
                        "f12": "000001",
                        "f14": "Allowed 00",
                        "f2": 10,
                        "f3": 4.1,
                        "f6": 250000000,
                        "f8": 6.0,
                        "f10": 1.4,
                        "f15": 10.2,
                        "f16": 9.8,
                        "f17": 9.9,
                        "f21": 9000000000,
                        "f62": 6200,
                        "f66": 3500,
                        "f72": 2700,
                        "f124": 1800000000,
                    }
                ]
            }
        },
    )

    rows = client._eastmoney_after_close_universe_seeds()

    assert rows[0]["main_net"] == 6200.0
    assert rows[0]["big_order_net"] == 2700.0
    assert rows[0]["super_order_net"] == 3500.0
    assert rows[0]["_sources"]["main_net"] == "eastmoney_after_close_universe.f62"
    assert rows[0]["_sources"]["big_order_net"] == "eastmoney_after_close_universe.f72"


def test_after_close_universe_does_not_apply_hot_top_30_truncation(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    seeds_seen = []
    fake_rows = []
    for idx in range(35):
        code = f"00{idx + 1:04d}"
        fake_rows.append(
            {
                "f12": code,
                "f14": f"Allowed {idx}",
                "f2": 10 + idx / 100,
                "f3": 4.0,
                "f6": 250000000,
                "f8": 6.0,
                "f10": 1.4,
                "f15": 10.5,
                "f16": 9.8,
                "f17": 9.9,
                "f21": 9000000000,
                "f124": 1800000000,
            }
        )

    monkeypatch.setattr(client, "_get_json", lambda *args, **kwargs: {"data": {"diff": fake_rows}})

    def fake_build(seeds):
        seeds_seen.extend(seeds)
        return seeds

    monkeypatch.setattr(client, "_build_after_close_candidates", fake_build)

    rows = client.get_after_close_universe_quotes()

    assert len(seeds_seen) == 35
    assert len(rows) == 35


def test_after_close_candidates_enrich_fund_flow_after_prefilter(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    seed = {
        "code": "000001",
        "name": "Allowed 00",
        "price": 10.0,
        "change_pct": 4.1,
        "amount_wan": 25000,
        "turnover_pct": 6.0,
        "vol_ratio": 1.4,
        "high": 10.2,
        "low": 9.8,
        "open": 9.9,
        "float_mcap_yi": 90,
        "list_date": "20200102",
    }
    calls = []

    monkeypatch.setattr(
        client,
        "_tencent_quotes_batched",
        lambda codes: {
            "000001": {
                "code": "000001",
                "name": "Allowed 00",
                "price": 10.0,
                "change_pct": 4.1,
                "amount_wan": 25000,
                "turnover_pct": 6.0,
                "vol_ratio": 1.4,
                "high": 10.2,
                "low": 9.8,
                "open": 9.9,
                "float_mcap_yi": 90,
                "limit_up": 11.0,
                "limit_down": 9.0,
            }
        },
    )
    monkeypatch.setattr(client, "_safe_quote_meta", lambda codes: {})
    monkeypatch.setattr(client, "_safe_baidu_concept_blocks", lambda code: {})

    def fake_safe_fund_flow(code):
        calls.append(code)
        return [{"time": "2026-05-22 15:00", "main_net": 1230000, "large_net": 456000}], "eastmoney_fund_flow_minute", ""

    monkeypatch.setattr(client, "_safe_fund_flow", fake_safe_fund_flow)

    rows = client._build_after_close_candidates([seed])

    assert calls == ["000001"]
    assert rows[0]["main_net"] == 1230000
    assert rows[0]["big_order_net"] == 456000
    assert rows[0]["fund_flow_source"] == "eastmoney_fund_flow_minute"
    assert "main_net" not in rows[0]["_missing_fields"]
    assert "big_order_net" not in rows[0]["_missing_fields"]


def test_merge_fund_flow_preserves_seed_fund_values_when_remote_fallbacks_are_empty():
    stock = {
        **demo_quotes()[0],
        "main_net": 6200.0,
        "big_order_net": 2700.0,
        "_sources": {
            "main_net": "eastmoney_after_close_universe.f62",
            "big_order_net": "eastmoney_after_close_universe.f72",
        },
    }

    merged = AStockClient("live")._merge_fund_flow(stock, [], "missing", "fund flow down")

    assert merged["main_net"] == 6200.0
    assert merged["big_order_net"] == 2700.0
    assert merged["fund_flow_source"] == "eastmoney_after_close_universe"
    assert merged["_sources"]["main_net"] == "eastmoney_after_close_universe.f62"


def test_safe_fund_flow_uses_eastmoney_quote_fields_before_daily_fallback(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")

    monkeypatch.setattr(client, "_eastmoney_fund_flow_minute", lambda code: [])
    monkeypatch.setattr(
        client,
        "_eastmoney_quote_fund_flow",
        lambda codes: {
            "000001": {
                "time": "quote",
                "main_net": 6200.0,
                "large_net": 2700.0,
                "super_net": 3500.0,
            }
        },
    )
    monkeypatch.setattr(
        client,
        "_eastmoney_fund_flow_daily",
        lambda code: (_ for _ in ()).throw(AssertionError("daily fallback should not be called when quote fund flow is available")),
    )

    rows, source, error = client._safe_fund_flow("000001")

    assert source == "eastmoney_quote_fund_flow"
    assert error == ""
    assert rows == [{"time": "quote", "main_net": 6200.0, "large_net": 2700.0, "super_net": 3500.0}]


def test_eastmoney_quote_fund_flow_parses_main_large_and_super_order_fields(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")

    def fake_json(url, params=None, headers=None, timeout=10):
        assert "ulist.np/get" in url
        assert params["fields"] == "f12,f14,f62,f66,f72"
        assert params["secids"] == "0.000001,1.600489"
        return {
            "data": {
                "diff": [
                    {"f12": "000001", "f14": "平安银行", "f62": 6200, "f66": 3500, "f72": 2700},
                    {"f12": "600489", "f14": "中金黄金", "f62": 417890288, "f66": 146556352, "f72": 271333936},
                ]
            }
        }

    monkeypatch.setattr(client, "_get_json", fake_json)

    rows = client._eastmoney_quote_fund_flow(["000001", "600489"])

    assert rows["000001"]["main_net"] == 6200.0
    assert rows["000001"]["large_net"] == 2700.0
    assert rows["000001"]["super_net"] == 3500.0
    assert rows["600489"]["main_net"] == rows["600489"]["large_net"] + rows["600489"]["super_net"]
    assert rows["600489"]["_sources"]["main_net"] == "eastmoney_quote_fund_flow.f62"


def test_sina_money_flow_current_parses_verified_fallback_fields(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")

    def fake_json(url, params=None, headers=None, timeout=10):
        assert "MoneyFlow.ssl_bkzj_ssggzj" in url
        assert params["num"] == 6000
        return [
            {
                "symbol": "sz000908",
                "name": "石药景峰",
                "trade": "7.3000",
                "netamount": "40745572.0900",
                "r0_net": "17773741.0800",
            }
        ]

    monkeypatch.setattr(client, "_get_json", fake_json)

    rows = client._sina_money_flow_current(["000908"])

    assert rows["000908"]["main_net"] == 17773741.08
    assert rows["000908"]["large_net"] == 40745572.09
    assert rows["000908"]["_sources"]["main_net"] == "sina_money_flow_current.r0_net"


def test_safe_fund_flow_uses_sina_current_before_daily_when_eastmoney_is_empty(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")

    monkeypatch.setattr(client, "_eastmoney_fund_flow_minute", lambda code: [])
    monkeypatch.setattr(client, "_eastmoney_quote_fund_flow", lambda codes: {})
    monkeypatch.setattr(
        client,
        "_sina_money_flow_current",
        lambda codes: {
            "000908": {
                "time": "sina_current",
                "main_net": 17773741.08,
                "large_net": 40745572.09,
                "_sources": {
                    "main_net": "sina_money_flow_current.r0_net",
                    "large_net": "sina_money_flow_current.netamount",
                },
            }
        },
    )
    monkeypatch.setattr(
        client,
        "_eastmoney_fund_flow_daily",
        lambda code: (_ for _ in ()).throw(AssertionError("daily fallback should not be called when sina current is available")),
    )

    rows, source, error = client._safe_fund_flow("000908")

    assert source == "sina_money_flow_current"
    assert error == ""
    assert rows[0]["main_net"] == 17773741.08


def test_daily_kline_falls_back_to_eastmoney_when_baidu_unavailable(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")

    monkeypatch.setattr(client, "_baidu_daily_kline", lambda code, lookback: [])
    monkeypatch.setattr(
        client,
        "_eastmoney_daily_kline",
        lambda code, lookback: [
            {
                "date": client.now.date().isoformat(),
                "open": 6.63,
                "close": 7.30,
                "high": 7.56,
                "low": 6.33,
                "volume": 701790,
                "amount": 494985772.9,
            }
        ],
    )
    monkeypatch.setattr(
        client,
        "_mootdx_daily_kline",
        lambda code, lookback: (_ for _ in ()).throw(AssertionError("mootdx should not be called when eastmoney kline is available")),
    )

    rows = client.get_daily_kline("000908")

    assert rows[0]["close"] == 7.30
    assert any(item["source"] == "eastmoney_daily_kline" and item["ok"] for item in client.quality_report.source_status)


def test_after_close_candidates_fill_theme_from_baidu_concepts_before_industry_fallback(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    seed = {
        "code": "600489",
        "name": "Theme Missing",
        "price": 19.0,
        "change_pct": 4.1,
        "amount_wan": 25000,
        "turnover_pct": 6.0,
        "vol_ratio": 1.4,
        "high": 19.2,
        "low": 18.6,
        "open": 18.8,
        "float_mcap_yi": 90,
        "list_date": "20200102",
    }
    monkeypatch.setattr(
        client,
        "_tencent_quotes_batched",
        lambda codes: {
            "600489": {
                **seed,
                "limit_up": 20.9,
                "limit_down": 17.1,
            }
        },
    )
    monkeypatch.setattr(client, "_safe_quote_meta", lambda codes: {})
    monkeypatch.setattr(
        client,
        "_safe_fund_flow",
        lambda code: ([{"time": "2026-05-22 15:00", "main_net": 1000, "large_net": 500}], "eastmoney_fund_flow_minute", ""),
    )
    monkeypatch.setattr(
        client,
        "_safe_baidu_concept_blocks",
        lambda code: {
            "concept_tags": ["黄金概念", "央企改革"],
            "industry": [{"name": "贵金属"}],
            "concept": [{"name": "黄金概念", "change_pct": "-2.4"}, {"name": "央企改革", "change_pct": "-0.8"}],
            "region": [],
        },
    )

    rows = client._build_after_close_candidates([seed])

    assert rows[0]["theme_tags"] == ["黄金概念", "央企改革"]
    assert rows[0]["theme_source"] == "baidu_concept_blocks"
    assert rows[0]["theme_block_change_pct"] == -2.4
    assert "theme_tags" not in rows[0]["_missing_fields"]


def test_after_close_universe_merges_hot_theme_tags_before_enrichment(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    seeds_seen = []
    base_seed = {
        "code": "000001",
        "name": "Allowed 00",
        "price": 10,
        "change_pct": 4.1,
        "amount_wan": 25000,
        "turnover_pct": 6.0,
        "vol_ratio": 1.4,
        "high": 10.2,
        "low": 9.8,
        "open": 9.9,
        "float_mcap_yi": 90,
    }

    monkeypatch.setattr(client, "_after_close_universe_seeds", lambda: ("eastmoney_after_close_universe", [base_seed]))
    hot_row = {
        "code": "000001",
        "theme_tags": ["AI", "Robotics"],
        "theme_rank": 1,
        "same_theme_strong_count": 2,
        "_sources": {
            "theme_tags": "ths_hot_reason.reason",
            "theme_rank": "ths_hot_reason.reason",
            "same_theme_strong_count": "ths_hot_reason.reason",
        },
    }

    def fake_recent_context():
        client._recent_hot_theme_context_cache = {
            "by_code": {"000001": hot_row},
            "themes": {"AI": {"active_days": 2, "count": 4, "latest_date": "2026-05-22", "first_date": "2026-05-21"}},
            "latest_date": "2026-05-22",
        }
        return client._recent_hot_theme_context_cache

    monkeypatch.setattr(client, "_recent_hot_theme_context", fake_recent_context)

    def fake_build(seeds):
        seeds_seen.extend(seeds)
        return seeds

    monkeypatch.setattr(client, "_build_after_close_candidates", fake_build)

    rows = client.get_after_close_universe_quotes()

    assert rows[0]["theme_tags"] == ["AI", "Robotics"]
    assert rows[0]["theme_rank"] == 1
    assert rows[0]["same_theme_strong_count"] == 2
    assert seeds_seen[0]["_sources"]["theme_tags"] == "ths_hot_reason.reason"


def test_after_close_universe_falls_back_to_easyquotation_full_market(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    seeds_seen = []

    monkeypatch.setattr(client, "_eastmoney_after_close_universe_seeds", lambda: (_ for _ in ()).throw(RuntimeError("eastmoney down")))
    monkeypatch.setattr(client, "_recent_hot_theme_context", lambda: {"by_code": {}, "themes": {}, "latest_date": ""})
    monkeypatch.setattr(
        client,
        "_easyquotation_after_close_universe_seeds",
        lambda: [
            {"code": "000001", "name": "Allowed 00", "price": 10, "change_pct": 4.1, "amount_wan": 25000, "turnover_pct": 6.0, "vol_ratio": 1.4, "high": 10.2, "low": 9.8, "open": 9.9, "float_mcap_yi": 90},
            {"code": "600001", "name": "Allowed 60", "price": 12, "change_pct": 4.8, "amount_wan": 26000, "turnover_pct": 7.0, "vol_ratio": 1.5, "high": 12.3, "low": 11.7, "open": 11.9, "float_mcap_yi": 80},
            {"code": "300001", "name": "Rejected 30", "price": 18, "change_pct": 5.0, "amount_wan": 30000, "turnover_pct": 8.0, "vol_ratio": 1.6, "high": 18.5, "low": 17.2, "open": 17.5, "float_mcap_yi": 70},
        ],
    )

    def fake_build(seeds):
        seeds_seen.extend(seeds)
        return seeds

    monkeypatch.setattr(client, "_build_after_close_candidates", fake_build)

    rows = client.get_after_close_universe_quotes()

    assert [row["code"] for row in seeds_seen] == ["000001", "600001"]
    assert [row["code"] for row in rows] == ["000001", "600001"]
    assert client.quality_report.fallback_to_demo is False
    assert any(item["source"] == "easyquotation_sina_full_market" and item["ok"] for item in client.quality_report.source_status)
    assert any(item["source"] == "eastmoney_after_close_universe" and not item["ok"] for item in client.quality_report.source_status)


def test_after_close_universe_tries_secondary_source_when_primary_enrichment_has_no_base_rows(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    primary = {
        "code": "000001",
        "name": "Primary Weak",
        "price": 10,
        "change_pct": 4.1,
        "amount_wan": 25000,
        "turnover_pct": 6.0,
        "vol_ratio": 1.4,
    }
    secondary = {
        "code": "600001",
        "name": "Secondary Good",
        "price": 12,
        "change_pct": 4.8,
        "amount_wan": 26000,
        "turnover_pct": 7.0,
        "vol_ratio": 1.5,
    }

    monkeypatch.setattr(client, "_after_close_universe_seeds", lambda: ("eastmoney_after_close_universe", [primary]))
    monkeypatch.setattr(client, "_easyquotation_after_close_universe_seeds", lambda: [secondary])
    monkeypatch.setattr(client, "_recent_hot_theme_context", lambda: {"by_code": {}, "themes": {}, "latest_date": ""})

    def fake_build(seeds):
        if seeds[0]["code"] == "000001":
            row = dict(seeds[0])
            row["turnover_pct"] = 0
            return [row]
        return list(seeds)

    monkeypatch.setattr(client, "_build_after_close_candidates", fake_build)

    rows = client.get_after_close_universe_quotes()

    assert [row["code"] for row in rows] == ["600001"]
    assert client.after_close_candidate_source == "easyquotation_sina_full_market"
    assert client.quality_report.fallback_to_demo is False


def test_live_candidate_seeds_prefer_tradeable_full_market_rows(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    calls = []
    hot_limit_up = {
        "code": "600001",
        "name": "Hot Limit Up",
        "price": 11,
        "change_pct": 10.0,
        "theme_tags": ["AI"],
    }
    tradeable = {
        "code": "002001",
        "name": "Tradeable Breakout",
        "price": 18,
        "change_pct": 5.2,
        "amount_wan": 30000,
        "turnover_pct": 7.0,
        "vol_ratio": 1.4,
    }

    def fake_hot():
        calls.append("ths_hot_reason")
        return [hot_limit_up]

    def fake_full_market():
        calls.append("eastmoney_clist")
        return [tradeable]

    monkeypatch.setattr(client, "_ths_hot_reason_candidates", fake_hot)
    monkeypatch.setattr(client, "_eastmoney_clist_candidates", fake_full_market)

    seeds = client._fetch_live_candidate_seeds()

    assert calls == ["eastmoney_clist", "ths_hot_reason"]
    assert [row["code"] for row in seeds] == ["002001", "600001"]
    assert seeds[0]["_allow_missing_theme"] is True
    assert client.quality_report.source_status[-1]["source"] == "eastmoney_clist"


def test_live_candidate_seeds_fill_broad_active_rows_to_scan_default(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    tradeable = {
        "code": "000001",
        "name": "Tradeable Breakout",
        "price": 18,
        "change_pct": 5.2,
        "amount_wan": 30000,
        "turnover_pct": 7.0,
        "vol_ratio": 1.4,
    }
    broad_rows = [
        {
            "code": f"60{idx:04d}",
            "name": f"Broad Active {idx}",
            "price": 10 + idx / 100,
            "change_pct": 1.2,
            "amount_wan": 25000,
            "turnover_pct": 3.5,
            "vol_ratio": 0.9,
        }
        for idx in range(1, LIVE_SCAN_DEFAULT_MAX_CANDIDATES)
    ]
    ignored = {
        "code": "300001",
        "name": "Rejected Prefix",
        "price": 12,
        "change_pct": 1.5,
        "amount_wan": 50000,
        "turnover_pct": 5.0,
        "vol_ratio": 1.1,
    }

    monkeypatch.setattr(client, "_ths_hot_reason_candidates", lambda: [])
    monkeypatch.setattr(client, "_eastmoney_clist_candidates", lambda: [tradeable, ignored] + broad_rows)

    seeds = client._fetch_live_candidate_seeds()

    assert len(seeds) == LIVE_SCAN_DEFAULT_MAX_CANDIDATES
    assert seeds[0]["code"] == "000001"
    assert all(row["code"].startswith(("00", "60")) for row in seeds)
    assert "300001" not in {row["code"] for row in seeds}


def test_live_candidate_seeds_use_sina_fallback_when_eastmoney_clist_fails(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    hot = {
        "code": "600001",
        "name": "Hot Candidate",
        "price": 11,
        "change_pct": 5.0,
        "theme_tags": ["AI"],
    }
    fallback_rows = [
        {
            "code": f"00{idx:04d}",
            "name": f"Sina Flow {idx}",
            "main_net": 1000000 + idx,
            "big_order_net": 2000000 + idx,
            "_quote_deferred": True,
        }
        for idx in range(1, LIVE_SCAN_DEFAULT_MAX_CANDIDATES)
    ]

    monkeypatch.setattr(client, "_eastmoney_clist_candidates", lambda: (_ for _ in ()).throw(RuntimeError("eastmoney down")))
    monkeypatch.setattr(client, "_ths_hot_reason_candidates", lambda: [hot])
    monkeypatch.setattr(client, "_sina_money_flow_candidate_seeds", lambda: fallback_rows)

    seeds = client._fetch_live_candidate_seeds()
    codes = {row["code"] for row in seeds}

    assert len(seeds) == LIVE_SCAN_DEFAULT_MAX_CANDIDATES
    assert "600001" in codes
    assert "000001" in codes
    assert any(item["source"] == "eastmoney_clist" and not item["ok"] for item in client.quality_report.source_status)
    assert any(item["source"] == "sina_money_flow_candidate_seeds" and item["ok"] for item in client.quality_report.source_status)


def test_live_full_market_candidate_request_uses_paginated_deep_universe(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    seen_params = {}

    def fake_json(url, params=None, headers=None, timeout=10):
        seen_params.update(params or {})
        return {"data": {"diff": []}}

    monkeypatch.setattr(client, "_get_json", fake_json)

    assert client._eastmoney_clist_candidates() == []
    assert seen_params["pn"] == "1"
    assert seen_params["pz"] == "100"
    assert "f6" in seen_params["fields"]
    assert "f10" in seen_params["fields"]


def test_live_full_market_candidate_request_reads_multiple_pages(tmp_path, monkeypatch):
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    pages_seen = []

    def fake_json(url, params=None, headers=None, timeout=10):
        page = int((params or {})["pn"])
        pages_seen.append(page)
        if page == 1:
            return {
                "data": {
                    "diff": [
                        {
                            "f12": "000001",
                            "f14": "Page One",
                            "f2": 10,
                            "f3": 3.5,
                            "f6": 200000000,
                            "f8": 4.0,
                            "f10": 1.2,
                            "f15": 10.2,
                            "f16": 9.8,
                            "f17": 9.9,
                            "f21": 5000000000,
                            "f124": 1800000000,
                        }
                    ]
                    * 100
                }
            }
        if page == 2:
            return {
                "data": {
                    "diff": [
                        {
                            "f12": "600001",
                            "f14": "Page Two",
                            "f2": 12,
                            "f3": 2.5,
                            "f6": 210000000,
                            "f8": 4.5,
                            "f10": 1.1,
                            "f15": 12.2,
                            "f16": 11.8,
                            "f17": 11.9,
                            "f21": 6000000000,
                            "f124": 1800000000,
                        }
                    ]
                }
            }
        return {"data": {"diff": []}}

    monkeypatch.setattr(client, "_get_json", fake_json)

    rows = client._eastmoney_clist_candidates()

    assert pages_seen == [1, 2]
    assert rows[0]["code"] == "000001"
    assert rows[-1]["code"] == "600001"


def test_missing_safety_fields_reject_ticket():
    config = load_config()
    stock = {
        "code": "300001",
        "name": "Unsafe Live",
        "total_score": 99,
        "risk_flags": [],
        "_missing_fields": ["limit_up", "limit_down", "is_st", "is_suspended", "is_new_stock", "is_bj_stock"],
    }

    result = RiskManager(config).evaluate_buy(stock, {"pass": True}, planned_amount=4000)

    assert result["allow"] is False
    assert "limit_price_unknown" in result["reasons"]
    assert "st_status_unknown" in result["reasons"]
    assert "suspended_status_unknown" in result["reasons"]
    assert "list_date_missing" in result["reasons"]
    assert "bj_status_unknown" in result["reasons"]


def test_quality_report_file_is_generated(tmp_path, monkeypatch):
    config = load_config()
    config["paths"]["reports_dir"] = str(tmp_path)
    config["paths"]["records_dir"] = str(tmp_path)
    client = AStockClient("live", cache_dir=tmp_path / "cache")
    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [])

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-23")

    report_path = Path(result["quality_report_path"])
    assert report_path.exists()
    assert report_path.name == "live_data_quality_2026-05-23.md"
    assert "Fallback to demo: YES" in report_path.read_text(encoding="utf-8")


def test_source_map_and_missing_fields_are_populated():
    stock = normalize_live_stock(
        {
            "code": "300001",
            "name": "Mapped",
            "price": 10.0,
            "_sources": {"code": "seed", "name": "seed", "price": "quote"},
        },
        default_source="unit",
    )
    validated = validate_stock_fields(stock)

    assert validated["_sources"]["price"] == "quote"
    assert validated["_sources"]["code"] == "seed"
    assert "limit_up" in validated["_missing_fields"]
    assert "is_new_stock" in validated["_missing_fields"]
