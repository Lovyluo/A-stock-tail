from __future__ import annotations

from datetime import datetime, timedelta
import json

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_SOURCE_VERSION,
    TENCENT_VALUATION_PROVIDER_KEY,
    TencentHttpResponse,
)
from overnight_quant.data.tencent_shadow_snapshot import (
    TENCENT_SHADOW_SNAPSHOT_INCOMPLETE,
    TENCENT_SHADOW_SNAPSHOT_NETWORK_NOT_REQUESTED,
    TENCENT_SHADOW_SNAPSHOT_READY,
    build_tencent_shadow_snapshot,
)
from overnight_quant.scripts import run_tencent_shadow_snapshot
from overnight_quant.ui import dashboard


CODES = ("000001", "000333", "600000", "600519", "601318")


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 2, 14, 50, 10, tzinfo=CN_TZ)

    def __call__(self):
        current = self.value
        self.value += timedelta(milliseconds=10)
        return current


class Transport:
    def __init__(self, *, fail_on_request: int | None = None):
        self.request_count = 0
        self.fail_on_request = fail_on_request

    def request(self, method, url, *, headers, timeout_seconds):
        self.request_count += 1
        if self.request_count == self.fail_on_request:
            raise TimeoutError("timeout")
        return TencentHttpResponse(
            content=_response_bytes(CODES),
            status_code=200,
            url=url,
        )


def _response_bytes(codes):
    lines = []
    for index, code in enumerate(codes):
        values = [""] * 88
        values[1] = f"股票{index + 1}"
        values[2] = code
        values[3] = str(10 + index)
        values[4] = str(9.8 + index)
        values[5] = str(9.9 + index)
        values[30] = "20260902144959"
        values[32] = "1.25"
        values[33] = str(10.2 + index)
        values[34] = str(9.7 + index)
        values[36] = "10000"
        values[37] = "12345"
        values[38] = "2.5"
        values[39] = str(12.5 + index)
        values[43] = "3.2"
        values[44] = str(100 + index)
        values[45] = str(80 + index)
        values[46] = str(1.5 + index / 10)
        values[47] = str(11 + index)
        values[48] = str(9 + index)
        values[52] = str(13 + index)
        prefix = "sh" if code.startswith("6") else "sz"
        lines.append(f'v_{prefix}{code}="{"~".join(values)}";')
    return "\n".join(lines).encode("gbk")


def _assert_safe(result):
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["strategy_integration"] is False
    assert result["readiness_integration"] is False
    assert result["decision_hash_integration"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def test_network_must_be_explicit_and_makes_no_request():
    transport = Transport()
    result = build_tencent_shadow_snapshot(
        CODES, network=False, transport=transport, clock=Clock()
    )

    assert result["status"] == TENCENT_SHADOW_SNAPSHOT_NETWORK_NOT_REQUESTED
    assert transport.request_count == 0
    _assert_safe(result)


def test_fixed_five_snapshot_uses_production_adapters_and_merges_rows():
    transport = Transport()
    result = build_tencent_shadow_snapshot(
        reversed(CODES), network=True, transport=transport, clock=Clock()
    )

    assert result["status"] == TENCENT_SHADOW_SNAPSHOT_READY
    assert result["snapshot_available"] is True
    assert result["covered_codes"] == sorted(CODES)
    assert len(result["rows"]) == 5
    assert transport.request_count == 2
    assert result["adapter_statuses"] == {
        "quote": "SOURCE_ADAPTER_BOUND",
        "valuation": "SOURCE_ADAPTER_BOUND",
    }
    assert result["provider_keys"] == {
        "quote": TENCENT_QUOTE_PROVIDER_KEY,
        "valuation": TENCENT_VALUATION_PROVIDER_KEY,
    }
    assert result["source"]["source_version"] == TENCENT_SOURCE_VERSION
    assert result["rows"][0]["price"] == 10.0
    assert result["rows"][0]["pe_ttm"] == 12.5
    assert result["rows"][0]["pb"] == 1.5
    assert result["rows"][0]["market_cap_yi"] == 100.0
    _assert_safe(result)


def test_input_order_does_not_change_snapshot_output():
    first = build_tencent_shadow_snapshot(
        CODES, network=True, transport=Transport(), clock=Clock()
    )
    second = build_tencent_shadow_snapshot(
        reversed(CODES), network=True, transport=Transport(), clock=Clock()
    )

    assert first["rows"] == second["rows"]
    assert first["snapshot_hash"] == second["snapshot_hash"]


def test_one_capability_failure_has_no_partial_display_or_fallback():
    result = build_tencent_shadow_snapshot(
        CODES,
        network=True,
        transport=Transport(fail_on_request=2),
        clock=Clock(),
    )

    assert result["status"] == TENCENT_SHADOW_SNAPSHOT_INCOMPLETE
    assert result["snapshot_available"] is False
    assert result["rows"] == []
    assert result["adapter_statuses"]["valuation"] == (
        "SOURCE_ADAPTER_PROVIDER_FAILED"
    )
    _assert_safe(result)


def test_cli_requires_network_and_writes_only_ignored_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_tencent_shadow_snapshot, "CACHE_ROOT", tmp_path.resolve())
    monkeypatch.setattr(run_tencent_shadow_snapshot, "ROOT", tmp_path.resolve())
    target = tmp_path / "snapshot.json"

    exit_code = run_tencent_shadow_snapshot.main(
        ["--codes", ",".join(CODES), "--output", str(target)]
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert payload["status"] == TENCENT_SHADOW_SNAPSHOT_NETWORK_NOT_REQUESTED
    assert "TENCENT_SHADOW_SNAPSHOT_NETWORK_NOT_REQUESTED" in capsys.readouterr().out
    _assert_safe(payload)


class FakeColumn:
    def __init__(self, owner, *, clicked=False):
        self.owner = owner
        self.clicked = clicked

    def text_input(self, label, value, key=None):
        return value

    def button(self, label, use_container_width=False, key=None):
        return self.clicked


class FakeStreamlit:
    def __init__(self, *, clicked=False):
        self.clicked = clicked
        self.session_state = {}
        self.warnings = []
        self.infos = []
        self.captions = []
        self.frames = []

    def markdown(self, *_args, **_kwargs):
        pass

    def columns(self, _spec):
        return [FakeColumn(self), FakeColumn(self, clicked=self.clicked)]

    def warning(self, text):
        self.warnings.append(text)

    def info(self, text):
        self.infos.append(text)

    def caption(self, text):
        self.captions.append(text)

    def dataframe(self, rows, use_container_width=False):
        self.frames.append(rows)


def test_dashboard_does_not_call_network_until_user_clicks():
    calls = []

    def runner(codes, *, network):
        calls.append((codes, network))
        return {"status": TENCENT_SHADOW_SNAPSHOT_INCOMPLETE}

    dashboard._render_tencent_shadow_snapshot_panel(
        FakeStreamlit(clicked=False), "zh", runner=runner
    )

    assert calls == []


def test_dashboard_clicks_once_and_failure_is_yellow():
    calls = []

    def runner(codes, *, network):
        calls.append((codes, network))
        return {"status": TENCENT_SHADOW_SNAPSHOT_INCOMPLETE}

    fake = FakeStreamlit(clicked=True)
    dashboard._render_tencent_shadow_snapshot_panel(fake, "zh", runner=runner)

    assert calls == [(list(CODES), True)]
    assert fake.warnings == [
        "数据未就绪：TENCENT_SHADOW_SNAPSHOT_INCOMPLETE"
    ]
    assert fake.frames == []


def test_dashboard_ready_table_has_required_read_only_fields():
    result = build_tencent_shadow_snapshot(
        CODES, network=True, transport=Transport(), clock=Clock()
    )
    fake = FakeStreamlit(clicked=False)
    fake.session_state["tencent_shadow_snapshot"] = result

    dashboard._render_tencent_shadow_snapshot_panel(fake, "zh")

    assert fake.warnings == []
    assert len(fake.frames) == 1
    assert set(fake.frames[0][0]) == {
        "代码",
        "名称",
        "现价",
        "PE(TTM)",
        "PB",
        "总市值(亿元)",
        "流通市值(亿元)",
        "来源时间",
    }
    assert any("data_ready=false" in text for text in fake.captions)
