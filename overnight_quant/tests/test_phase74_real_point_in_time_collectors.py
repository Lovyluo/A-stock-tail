from __future__ import annotations

from datetime import date, datetime, timedelta
import inspect
import json

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import (
    REQUIRED_TEMPORAL_FIELDS,
)
from overnight_quant.data.real_point_in_time_collectors import (
    CNINFO_ANNOUNCEMENT_URL,
    EASTMONEY_BOARD_URL,
    EASTMONEY_FUND_URL,
    EASTMONEY_GLOBAL_NEWS_URL,
    EASTMONEY_INDUSTRY_MAP_URL,
    EASTMONEY_MARKET_URL,
    EASTMONEY_MINUTE_URL,
    EASTMONEY_STOCK_NEWS_URL,
    NEWSNOW_URL,
    SINA_FUND_URL,
    TENCENT_KLINE_URL,
    TENCENT_QUOTE_URL,
    RawHttpResponse,
    RealPointInTimeCollectors,
    SourceContractError,
    RealSourceError,
)
from overnight_quant.data.snapshot_store import (
    CloseWindowCollector,
    ImmutableSnapshotStore,
    ProviderBatch,
    ProviderSpec,
)
from overnight_quant.scripts.run_close_snapshot_collector import (
    run_snapshot_collection,
)


OBSERVED = datetime.fromisoformat("2026-07-30T14:49:00+08:00")
COMPLETED = datetime.fromisoformat("2026-07-30T14:50:00+08:00")


class FakeTransport:
    def __init__(self, handler=None):
        self.handler = handler or _source_handler
        self.calls = []

    def request(
        self,
        method,
        url,
        *,
        params=None,
        data=None,
        headers=None,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "data": data,
                "headers": headers,
            }
        )
        value = self.handler(method, url, params or {}, data or {})
        if isinstance(value, bytes):
            content = value
        elif isinstance(value, str):
            content = value.encode("utf-8")
        else:
            content = json.dumps(
                value,
                ensure_ascii=False,
            ).encode("utf-8")
        return RawHttpResponse(
            content=content,
            status_code=200,
            url=url,
            elapsed_ms=1.0,
        )


def test_all_scoreable_real_providers_emit_point_in_time_contracts():
    collectors = RealPointInTimeCollectors(
        ["000001"],
        transport=FakeTransport(),
        clock=lambda: COMPLETED,
    )

    batches = [
        collectors.collect_industry(OBSERVED),
        collectors.collect_quotes(OBSERVED),
        collectors.collect_market(OBSERVED),
        collectors.collect_trading_calendar(OBSERVED),
        collectors.collect_qfq_daily_bars(OBSERVED),
        collectors.collect_minute_bars(OBSERVED),
        collectors.collect_eastmoney_fund_flow(OBSERVED),
        collectors.collect_sina_fund_flow(OBSERVED),
        collectors.collect_global_news(OBSERVED),
        collectors.collect_stock_news(OBSERVED),
        collectors.collect_announcements(OBSERVED),
    ]

    records = [
        record for batch in batches for record in batch.records
    ]
    assert records
    for record in records:
        assert not [
            field
            for field in REQUIRED_TEMPORAL_FIELDS
            if field not in record
        ]
        assert len(record["request_hash"]) == 64
        assert len(record["raw_hash"]) == 64
        assert "demo" not in record["source"].lower()

    daily = [
        row for row in records if row["data_type"] == "daily_bar"
    ]
    minute = [
        row for row in records if row["data_type"] == "minute_bar"
    ]
    quote = next(
        row for row in records if row["data_type"] == "quote"
    )
    market = next(
        row for row in records if row["data_type"] == "market"
    )
    industry = next(
        row for row in records if row["data_type"] == "industry"
    )

    assert len(daily) == 60
    assert {
        row["payload"]["adjustment"] for row in daily
    } == {"qfq"}
    assert all(
        "response_key=qfqday"
        in row["payload"]["adjustment_evidence"]
        for row in daily
    )
    assert all(
        row["payload"]["date"] < OBSERVED.date().isoformat()
        for row in daily
    )
    assert any(
        row["event_time"][11:16] == "14:50" for row in minute
    )
    assert quote["payload"]["industry_name"] == "银行"
    assert quote["payload"]["amount_wan"] == 200000.0
    assert market["payload"]["breadth_ratio"] == pytest.approx(
        1800 / 4400
    )
    assert industry["payload"]["breadth_ratio"] == pytest.approx(
        30 / 50
    )
    assert industry["payload"]["relative_strength_pct"] == (
        pytest.approx(1.43)
    )


def test_qfq_daily_rejects_unproven_adjustment():
    def handler(method, url, params, data):
        if url == TENCENT_KLINE_URL:
            return {
                "code": 0,
                "data": {
                    "sz000001": {
                        "day": _daily_rows(70),
                        "version": "test",
                    }
                },
            }
        return _source_handler(method, url, params, data)

    collectors = RealPointInTimeCollectors(
        ["000001"],
        transport=FakeTransport(handler),
        clock=lambda: COMPLETED,
    )

    with pytest.raises(
        SourceContractError,
        match="qfq_response_not_proven",
    ):
        collectors.collect_qfq_daily_bars(OBSERVED)


def test_newsnow_without_item_publish_times_is_failed_contract():
    collectors = RealPointInTimeCollectors(
        ["000001"],
        transport=FakeTransport(),
        clock=lambda: COMPLETED,
    )

    with pytest.raises(
        SourceContractError,
        match="missing_published_at",
    ):
        collectors.collect_newsnow_cls(OBSERVED)


def test_returned_news_without_publish_time_is_not_available_empty():
    def handler(method, url, params, data):
        if url == EASTMONEY_GLOBAL_NEWS_URL:
            return {
                "data": {
                    "fastNewsList": [
                        {"title": "缺少发布时间的消息"}
                    ]
                }
            }
        return _source_handler(method, url, params, data)

    collectors = RealPointInTimeCollectors(
        ["000001"],
        transport=FakeTransport(handler),
        clock=lambda: COMPLETED,
    )

    with pytest.raises(
        SourceContractError,
        match="eastmoney_global_news_published_at_missing",
    ):
        collectors.collect_global_news(OBSERVED)


def test_selected_fund_flow_uses_backup_without_double_counting():
    def handler(method, url, params, data):
        if url == EASTMONEY_FUND_URL:
            raise RealSourceError("primary unavailable")
        return _source_handler(method, url, params, data)

    collectors = RealPointInTimeCollectors(
        ["000001"],
        transport=FakeTransport(handler),
        clock=lambda: COMPLETED,
    )

    batch = collectors.collect_selected_fund_flow(OBSERVED)

    assert len(batch.records) == 1
    assert batch.records[0]["source"] == "sina_money_flow_current"
    assert batch.records[0]["payload"]["fallback_from"] == (
        "eastmoney_fund_flow_minute"
    )
    assert "primary unavailable" in batch.records[0]["payload"][
        "fallback_reason"
    ]
    assert "eastmoney_fund_flow" not in collectors.provider_map()
    assert "sina_fund_flow_backup" not in collectors.provider_map()
    assert "selected_fund_flow" in collectors.provider_map()


def test_successful_empty_news_and_failed_news_are_distinct(tmp_path):
    moments = iter([
        datetime.fromisoformat("2026-07-30T14:49:00+08:00"),
        datetime.fromisoformat("2026-07-30T14:49:10+08:00"),
    ])
    empty_collector = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path / "empty"),
        {
            "empty.news": ProviderSpec(
                lambda _: ProviderBatch(
                    records=[],
                    data_types=["news"],
                    source_version="unit_news_v1",
                    raw_hash="a" * 64,
                ),
                ["news"],
                "unit_news_v1",
            )
        },
        clock=lambda: next(moments),
    )

    empty_result = empty_collector.collect(OBSERVED)

    assert empty_result["status"] == "NO_VALID_RECORDS"
    assert empty_result["critical_source_status"]["news"]["status"] == (
        "AVAILABLE_EMPTY"
    )
    assert empty_result["source_status"][0]["source_version"] == (
        "unit_news_v1"
    )
    assert empty_result["source_status"][0]["raw_hash"] == "a" * 64

    failed_moments = iter([
        datetime.fromisoformat("2026-07-30T14:49:00+08:00"),
        datetime.fromisoformat("2026-07-30T14:49:10+08:00"),
    ])

    def fail(_):
        raise RuntimeError("network down")

    failed_collector = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path / "failed"),
        {
            "failed.news": ProviderSpec(
                fail,
                ["news"],
                "unit_news_v1",
            )
        },
        clock=lambda: next(failed_moments),
    )

    failed_result = failed_collector.collect(OBSERVED)

    assert failed_result["status"] == "NO_VALID_RECORDS"
    assert failed_result["critical_source_status"]["news"]["status"] == (
        "FAILED"
    )
    assert failed_result["source_status"][0]["ok"] is False
    assert failed_result["source_status"][0]["data_types"] == ["news"]


def test_source_validation_never_claims_decision_ready_or_outputs():
    collectors = RealPointInTimeCollectors(
        ["000001"],
        transport=FakeTransport(),
        clock=lambda: COMPLETED,
    )

    result = collectors.validate_sources(OBSERVED)

    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    assert result["status"] == "SOURCE_VALIDATION_COMPLETED_WITH_GAPS"
    assert result["failed_sources"] == ["newsnow_cls_audit"]
    assert result["sources"]["newsnow_cls_audit"]["data_types"] == [
        "news"
    ]
    assert result["sources"]["newsnow_cls_audit"][
        "source_version"
    ]
    assert result["sources"]["tencent_qfq_daily"][
        "qfq_proven"
    ] is True
    assert result["sources"]["eastmoney_minute_bar"][
        "contains_1450_minute"
    ] is True
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def test_live_freeze_requires_previously_collected_input(tmp_path):
    class EmptyCollectors:
        @staticmethod
        def provider_map():
            return {}

    result = run_snapshot_collection(
        snapshot_root=tmp_path,
        trade_date="2026-07-30",
        freeze=True,
        live=True,
        codes=["000001"],
        collectors=EmptyCollectors(),
    )

    assert result["status"] == "LIVE_FREEZE_REQUIRES_COLLECTED_INPUT"
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []
    assert not any(tmp_path.rglob("*"))


def test_real_collector_module_has_no_demo_or_execution_integration():
    import overnight_quant.data.real_point_in_time_collectors as module

    source = inspect.getsource(module).lower()

    assert "demo_data" not in source
    for forbidden in (
        "pyautogui",
        "selenium",
        "broker api",
        "auto_order",
        "place_order",
        "自动下单",
        "自动点击",
    ):
        assert forbidden not in source


def _source_handler(method, url, params, data):
    if url.startswith(TENCENT_QUOTE_URL):
        symbols = url.removeprefix(TENCENT_QUOTE_URL).split(",")
        rows = []
        for symbol in symbols:
            names = {
                "sz000001": "平安银行",
                "sh000001": "上证指数",
                "sh000300": "沪深300",
                "sz399006": "创业板指",
            }
            changes = {
                "sz000001": 2.0,
                "sh000001": 1.0,
                "sh000300": 0.5,
                "sz399006": -0.5,
            }
            rows.append(
                _tencent_line(
                    symbol,
                    names[symbol],
                    changes[symbol],
                )
            )
        return ";".join(rows).encode("gbk")
    if url == EASTMONEY_INDUSTRY_MAP_URL:
        return {
            "ssbk": [
                {
                    "BOARD_CODE": "1283",
                    "BOARD_NAME": "银行",
                    "BOARD_RANK": 1,
                }
            ]
        }
    if url == EASTMONEY_BOARD_URL:
        return {
            "data": {
                "f57": "BK1283",
                "f58": "银行",
                "f170": 243,
                "f104": 30,
                "f105": 18,
                "f106": 2,
            }
        }
    if url == EASTMONEY_MARKET_URL:
        return {
            "data": {
                "diff": [
                    {
                        "f12": "000001",
                        "f104": 1000,
                        "f105": 1200,
                        "f106": 100,
                    },
                    {
                        "f12": "399001",
                        "f104": 800,
                        "f105": 1200,
                        "f106": 100,
                    },
                    {
                        "f12": "399006",
                        "f104": 300,
                        "f105": 900,
                        "f106": 50,
                    },
                ]
            }
        }
    if url == TENCENT_KLINE_URL:
        symbol = str(params["param"]).split(",", 1)[0]
        key = "day" if symbol == "sh000001" else "qfqday"
        return {
            "code": 0,
            "data": {
                symbol: {
                    key: _daily_rows(70),
                    "version": "test",
                }
            },
        }
    if url == EASTMONEY_MINUTE_URL:
        rows = []
        for minute in range(39, 51):
            rows.append(
                f"2026-07-30 14:{minute:02d},10,10.1,10.2,"
                f"9.9,{1000 + minute},10000,10.05"
            )
        return {"data": {"trends": rows}}
    if url == EASTMONEY_FUND_URL:
        return {
            "data": {
                "klines": [
                    (
                        "2026-07-30 14:50,100000,-20000,"
                        "10000,50000,50000"
                    )
                ]
            }
        }
    if url == SINA_FUND_URL:
        return [
            {
                "symbol": "sz000001",
                "r0_net": "100000",
                "netamount": "80000",
            }
        ]
    if url == EASTMONEY_GLOBAL_NEWS_URL:
        return {
            "data": {
                "fastNewsList": [
                    {
                        "title": "政策信息",
                        "showTime": "2026-07-30 14:40:00",
                    }
                ]
            }
        }
    if url == EASTMONEY_STOCK_NEWS_URL:
        payload = {
            "result": {
                "cmsArticleWebOld": {
                    "list": [
                        {
                            "title": "公司新闻",
                            "date": "2026-07-30 14:30:00",
                        }
                    ]
                }
            }
        }
        return (
            "jQuery_pit_news("
            + json.dumps(payload, ensure_ascii=False)
            + ")"
        )
    if url == CNINFO_ANNOUNCEMENT_URL:
        return {
            "announcements": [
                {
                    "announcementTitle": "董事会公告",
                    "announcementTime": 1785392400000,
                }
            ]
        }
    if url == NEWSNOW_URL:
        return {
            "status": "success",
            "items": [
                {
                    "title": "没有逐条发布时间",
                    "url": "https://www.cls.cn/detail/1",
                }
            ],
        }
    raise AssertionError(f"unexpected request:{method}:{url}")


def _tencent_line(symbol: str, name: str, change_pct: float) -> str:
    values = [""] * 60
    values[1] = name
    values[3] = "11.62"
    values[4] = "11.29"
    values[5] = "11.30"
    for index in (10, 12, 14, 16, 18):
        values[index] = "100"
    for index in (20, 22, 24, 26, 28):
        values[index] = "80"
    values[30] = "20260730145000"
    values[32] = str(change_pct)
    values[33] = "11.70"
    values[34] = "11.20"
    values[36] = "1000000"
    values[37] = "200000"
    values[38] = "1.43"
    values[47] = "12.42"
    values[48] = "10.16"
    return f'v_{symbol}="' + "~".join(values) + '"'


def _daily_rows(count: int) -> list[list[str]]:
    current = date(2026, 7, 29)
    days = []
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return [
        [
            day.isoformat(),
            "10.0",
            "10.1",
            "10.2",
            "9.9",
            str(1000 + index),
        ]
        for index, day in enumerate(reversed(days))
    ]
