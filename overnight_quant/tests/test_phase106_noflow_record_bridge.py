"""Provider-format synthetic replay only; no source requests or qualification."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import base64
import hashlib
import json
from pathlib import Path
import random
import socket
import subprocess
import sys

import pytest
import requests

from overnight_quant.data import complete_industry_candidate as industry
from overnight_quant.data import market_source_providers as market
from overnight_quant.data import static_source_providers as static
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import ASSOCIATION_SCHEMA, CLASSIFICATION as SYSTEM
from overnight_quant.data.source_capability_adapters import get_source_adapter_registry, compute_source_adapter_registry_hash
from overnight_quant.data.source_capability_registry import compute_source_capability_registry_hash
from overnight_quant.data.tencent_direct_http_providers import TencentDirectHttpProviders
from overnight_quant.scripts.run_noflow_research import run
from overnight_quant.strategy.close_confirmation_noflow_v1 import record_bridge as bridge
from overnight_quant.strategy.close_confirmation_noflow_v1.contracts import CAPABILITIES, STRATEGY_ID
from overnight_quant.strategy.close_confirmation_noflow_v1.strategy import NoFundFlowResearchStrategy
from overnight_quant.tests.test_phase105_noflow_research import synthetic_input, safe, DAY
from overnight_quant.tests.test_phase82_tencent_provider_candidates import FakeTransport as QuoteTransport, _line, _values, _response_bytes
from overnight_quant.tests.test_phase88_static_source_contracts import FakeTransport as StaticTransport, response as static_response
from overnight_quant.tests.test_phase89_market_source_qualification import FakeTransport as MarketTransport, response as market_response

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Source network forbidden in record bridge tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)


def captured_input():
    seed = synthetic_input()
    capture = {"schema_version": bridge.CAPTURE_SCHEMA, "strategy_id": STRATEGY_ID, "test_only": True,
        "input_scope": "synthetic_provider_capture_test_only",
        "batch_id": seed["batch_id"], "trade_date": DAY, "time_contract": deepcopy(seed["time_contract"]), "batches": []}
    bindings = get_source_adapter_registry()
    for kind in bridge.REQUIRED_TYPES:
        binding = next(r for r in bindings if r["capability"] == CAPABILITIES[kind]
                       and r["implementation_status"] in {"bound", "candidate_not_activated"}
                       and r["origin_source"] == ("tongdaxin" if kind == "minute_bar" else "tencent" if kind in {"quote", "trading_calendar"} else "cninfo" if kind == "announcement" else "eastmoney"))
        identity = {k: binding[k] for k in ("origin_source", "adapter", "source_version")}
        key = binding["provider_key"] or binding["candidate_provider_key"]
        if kind == "industry":
            identity["source_version"], key = industry.SOURCE_VERSION, industry.PROVIDER_KEY
        rows = deepcopy([r for r in seed["records"] if r["data_type"] == kind])
        for row in rows:
            row.update(identity, source="synthetic_test_only.provider_format", test_only=True)
            for field in ("record_schema_version", "requirements_hash", "time_contract_hash", "payload_hash"):
                row.pop(field)
            if bridge._identity(row) in bridge.FILTER_CUTOFF_IDENTITIES:
                row["decision_cutoff"] = seed["time_contract"]["feature_event_cutoff"]
            p = row["payload"]
            if kind == "industry":
                p.update(name="Synthetic BK", industry="Synthetic BK", field_units={"change_pct": "percent"})
                p["association_contract"].update(schema_version=ASSOCIATION_SCHEMA, classification_system=SYSTEM,
                    source_version=industry.SOURCE_VERSION, origin_source="eastmoney", board_name="Synthetic BK")
                p.update(strength_raw_fields={"f57": p["board_code"], "f58": industry.strength.BOARD_NAMES[p["board_code"]],
                    "f170": 80, "f152": 2, "f86": int(datetime.fromisoformat(row["event_time"]).timestamp())},
                    strength_event_time=row["event_time"], strength_source_version=industry.strength.SOURCE_VERSION)
            if kind == "minute_bar":
                p["field_units"] = {"price": "CNY", "volume": "shares", "amount": "CNY"}
        capture["batches"].append({**identity, "data_type": kind, "capability": CAPABILITIES[kind],
            "provider_key": key, "batch_id": seed["batch_id"], "trade_date": DAY, "test_only": True, "records": rows})

    # Actual Tencent Provider parsing, using an in-memory response, not noflow rows.
    quotes = batch(capture, "quote")
    codes = tuple(r["payload"]["code"] for r in quotes["records"])
    raw = _response_bytes(rows=[_line(code, _values(code, **{"3": "10.10", "32": "1.00", "30": DAY.replace("-", "") + "145000"})) for code in codes])
    transport = QuoteTransport(raw)
    ticks = iter([datetime.fromisoformat(f"{DAY}T14:50:00.100+08:00"), datetime.fromisoformat(f"{DAY}T14:50:00.200+08:00")])
    provider_rows = TencentDirectHttpProviders(codes, transport=transport, clock=lambda: next(ticks)).collect_quote_records()
    assert transport.request_count == 1
    for row in provider_rows:
        row.update(test_only=True, source="synthetic_test_only.native_tencent_provider")
        row["payload"].update(suspended=False, is_limit_up=False, is_limit_down=False,
                              tradability_status="VERIFIED_TRADABLE")  # Synthetic review only.
    quotes["records"] = provider_rows

    # Actual calendar and empty-news Provider methods; all responses are local.
    days = []
    day = datetime.fromisoformat(DAY).date() - timedelta(days=1)
    while len(days) < 70:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    days.reverse()  # Fabricated fixture dates, never asserted to be a live calendar.
    body = {"data": {"sh000001": {"day": [[d] for d in days]}}}
    available = datetime.fromisoformat(f"{DAY}T14:49:56+08:00")
    calendar_provider = static.StaticSourceProviders(codes, target_trade_date=DAY,
        feature_cutoff=capture["time_contract"]["feature_event_cutoff"],
        transport=StaticTransport([static_response(body, url=static.TENCENT_KLINE_URL)]), clock=lambda: available)
    calendar_rows = calendar_provider.collect_trading_calendar_records()
    for row in calendar_rows:
        row.update(test_only=True, source="synthetic_test_only.native_calendar_provider")
    batch(capture, "trading_calendar")["records"] = calendar_rows
    for kind, empty_body, url in (
        ("stock_news", {"result": {"cmsArticleWebOld": []}}, static.EASTMONEY_STOCK_NEWS_URL),
        ("global_news", {"data": {"fastNewsList": []}}, static.EASTMONEY_GLOBAL_NEWS_URL),
        ("announcement", {"announcements": []}, static.CNINFO_ANNOUNCEMENT_URL),
    ):
        count = 1 if kind == "global_news" else len(codes)
        p = static.StaticSourceProviders(codes, target_trade_date=DAY,
            feature_cutoff=capture["time_contract"]["feature_event_cutoff"],
            transport=StaticTransport([static_response(empty_body, url=url) for _ in range(count)]), clock=lambda: available)
        native = getattr(p, "collect_" + kind + "_batch")()
        target = batch(capture, kind)
        target["records"] = list(native.records)
        target["risk_review_status"] = "CLEAR"  # Only test-only branch consumes this.
        target["responses"] = [{**{k: v for k, v in r.items() if k != "raw_bytes"},
            "content_base64": base64.b64encode(r["raw_bytes"]).decode(),
            "scope_code": "" if kind == "global_news" else code}
            for r, code in zip(native.responses, [""] if kind == "global_news" else codes)]

    native_time = int(datetime.fromisoformat(capture["time_contract"]["feature_event_cutoff"]).timestamp())
    breadth_body = {"data": {"diff": [{"f12": c, "f14": c, "f104": 6, "f105": 3, "f106": 1, "f124": native_time}
                                      for c in ("000001", "399001", "899050")]}}
    index_body = {"data": {"f57": "000001", "f58": "Synthetic SSE", "f86": native_time, "f170": 0.5}}
    class LocalMarketTransport(MarketTransport):
        def request(self, *args, **kwargs):
            value = super().request(*args, **kwargs)
            start = datetime.fromisoformat(f"{DAY}T14:50:00+08:00") + timedelta(milliseconds=110 + 200 * (self.request_count - 1))
            return replace(value, request_started_at=start.isoformat(),
                           request_completed_at=(start + timedelta(milliseconds=40)).isoformat())
    ticks = iter(datetime.fromisoformat(f"{DAY}T14:50:00+08:00") + timedelta(milliseconds=i) for i in (100, 200, 300, 400))
    m = market.EastmoneyMarketSourceProviders(codes, trade_date=DAY,
        feature_cutoff=capture["time_contract"]["feature_event_cutoff"], collection_deadline=capture["time_contract"]["collection_deadline"],
        transport=LocalMarketTransport([market_response(breadth_body, market.EASTMONEY_ULIST_URL), market_response(index_body, market.EASTMONEY_STOCK_URL)]),
        clock=lambda: next(ticks))
    market_rows = m.collect_market_breadth_records()
    for row in market_rows:
        row.update(test_only=True, source="synthetic_test_only.native_market_provider")
    batch(capture, "market")["records"] = market_rows
    return capture


def batch(value, kind):
    return next(b for b in value["batches"] if b["data_type"] == kind)


def evaluate(value, test_only=True):
    return bridge.bridge_captured_records(value, test_only=test_only)


def test_native_provider_adapter_pit_readiness_features_chain(monkeypatch):
    def no_worker(*args, **kwargs):
        raise AssertionError("Offline bridge must not launch source workers")
    monkeypatch.setattr(subprocess, "Popen", no_worker)
    value = captured_input()
    original = deepcopy(value)
    result = evaluate(value)
    assert result["research_ready"], result["missing_reasons"]
    assert len(result["research_ranking"]) == 5
    assert all(m["conversion_ok"] for m in result["module_results"])
    assert all(m["adapter_result"]["selection_registry_scope"] == "test_only" for m in result["module_results"])
    assert value == original
    assert next(r for r in result["audit_records"] if r["data_type"] == "trading_calendar")["event_time"].startswith("2026-10-16")
    quote = next(r for r in result["audit_records"] if r["data_type"] == "quote")
    assert "industry" not in quote["bridge_trace"]["provider_payload"]
    assert quote["bridge_trace"]["provider_payload"]["field_units"]["price"] == "CNY_per_share"
    assert result["research_ranking"][0]["features"]["stock_vs_market_pp"] == 0.5
    assert next(r for r in result["research_ranking"] if r["code"] == "000333")["features"]["industry_invalid_count"] == 2
    safe(result)


def test_known_quote_limits_do_not_fabricate_suspension_or_tradability():
    row = captured_input()["batches"]
    quote = next(b for b in row if b["data_type"] == "quote")["records"][0]
    quote["payload"]["price"] = quote["payload"]["limit_up"]
    projected = bridge.project_payload("quote", quote, test_only=False)
    assert projected["is_limit_up"] is True and projected["is_limit_down"] is False
    assert projected["suspended"] is None and projected["tradability_status"] == "UNPROVEN"
    quote["payload"]["price"] = quote["payload"]["limit_down"]
    assert bridge.project_payload("quote", quote, test_only=False)["is_limit_down"] is True


@pytest.mark.parametrize("mutate", [
    lambda v: batch(v, "quote").update(source_version="forged"),
    lambda v: batch(v, "quote").update(provider_key="other.Provider.collect"),
    lambda v: batch(v, "quote")["records"][0].update(origin_source="eastmoney"),
    lambda v: batch(v, "quote")["records"][0]["payload"].update(code="SH000001"),
    lambda v: batch(v, "industry")["records"][0]["payload"]["association_contract"].update(board_code="BK9999"),
    lambda v: batch(v, "industry")["records"][0]["payload"].update(classification_version="name_guess"),
    lambda v: batch(v, "industry").update(trade_date="2026-10-16"),
    lambda v: batch(v, "market")["records"][0].update(event_time=f"{DAY}T14:50:00.001+08:00"),
    lambda v: batch(v, "quote")["records"][0]["payload"]["field_units"].update(price="fen"),
    lambda v: batch(v, "minute_bar")["records"].pop(),
    lambda v: batch(v, "minute_bar")["records"][0].update(interval_end=f"{DAY}T14:51:00+08:00"),
    lambda v: batch(v, "minute_bar")["records"][0].update(finalized_at=f"{DAY}T14:38:00+08:00"),
    lambda v: batch(v, "minute_bar")["records"][0].update(native_label=f"{DAY}T14:40:00+08:00"),
    lambda v: batch(v, "minute_bar")["records"][0].update(interval_start=f"{DAY}T14:37:59+08:00"),
    lambda v: batch(v, "minute_bar")["records"][0]["payload"]["field_units"].update(volume="lot"),
    lambda v: v["batches"].remove(batch(v, "announcement")),
    lambda v: batch(v, "stock_news")["responses"][0].update(http_status_code=403),
    lambda v: batch(v, "global_news")["responses"][0].update(content_base64=base64.b64encode(b"<html>blocked</html>").decode()),
    lambda v: batch(v, "trading_calendar")["records"][0]["payload"]["trade_dates"].append(DAY),
    lambda v: batch(v, "industry")["records"][0]["payload"].update(change_pct=99),
    lambda v: batch(v, "industry")["records"][0]["payload"]["strength_raw_fields"].update(f152=None),
])
def test_contract_rejections_keep_ranking_and_formal_outputs_empty(mutate):
    value = captured_input()
    mutate(value)
    result = evaluate(value)
    assert not result["research_ready"] and result["research_ranking"] == [], result
    safe(result)


def test_failed_extra_batch_cannot_leak_nested_successful_ranking():
    value = captured_input()
    extra = deepcopy(batch(value, "quote"))
    extra["provider_key"] = "forged.Provider.collect"
    value["batches"].append(extra)
    result = evaluate(value)
    assert not result["research_ready"] and result["research_ranking"] == []
    assert not result["research_result"]["research_ready"] and result["research_result"]["research_ranking"] == []
    safe(result)


def test_complete_industry_native_replay_reaches_features_and_resigned_projection_fails(monkeypatch):
    from overnight_quant.tests import test_phase102_t30_complete_industry_candidate as fixture
    value = captured_input()
    contract = value["time_contract"]
    monkeypatch.setattr(fixture, "DAY", DAY)
    monkeypatch.setattr(fixture, "BATCH_ID", value["batch_id"])
    monkeypatch.setattr(fixture, "BASE", datetime.fromisoformat(f"{DAY}T14:50:06+08:00"))
    monkeypatch.setattr(fixture, "TIMES", {"feature_cutoff": contract["feature_event_cutoff"],
        "collection_deadline": contract["collection_deadline"], "decision_time": contract["decision_time"],
        "execution_start": contract["execution_not_before"]})
    native = fixture.synthetic_batch()
    native["completed_at"] = f"{DAY}T14:50:20+08:00"
    composed = fixture.run(native)
    assert composed["candidate_batch_complete"], composed["errors"]
    batch(value, "industry")["records"] = composed["audit_records"]
    result = evaluate(value)
    assert result["research_ready"], result["missing_reasons"]
    assert len(result["research_ranking"]) == 5
    assert any(r["features"]["industry_change_pct"] < 0 for r in result["research_ranking"])
    assert any(r["features"]["industry_change_pct"] == 0 for r in result["research_ranking"])
    safe(result)
    damaged = deepcopy(value)
    p = batch(damaged, "industry")["records"][0]["payload"]
    p.update(change_pct=99)
    p["strength_raw_fields"]["f170"] = 9900  # Re-signing a projection cannot change captured bytes.
    result = evaluate(damaged)
    assert "industry_strength_raw_replay_rejected" in result["missing_reasons"]
    assert result["research_ranking"] == []
    safe(result)


@pytest.mark.parametrize("published,precision,allowed", [
    ("2026-10-16T14:30:00+08:00", "datetime", True),
    ("2026-10-16T00:00:00+08:00", "date", True),
    (f"{DAY}T00:00:00+08:00", "date", False),
    (f"{DAY}T14:50:00.001+08:00", "datetime", False),
    (f"{DAY}T14:30:00+08:00", None, False),
])
def test_publication_times_and_precision_are_retained(published, precision, allowed):
    value = captured_input()
    b = batch(value, "global_news")
    receipt = b["responses"][0]
    raw = json.dumps({"data": {"fastNewsList": [{"title": "Synthetic item", "published_at": published}]}}).encode()
    receipt.update(content_base64=base64.b64encode(raw).decode(), raw_hash=hashlib.sha256(raw).hexdigest())
    b["records"] = [{**{k: b[k] for k in ("origin_source", "adapter", "source_version", "capability", "test_only")},
        "source": "synthetic_test_only.native_news_provider",
        "event_time": published, "published_at": published, "published_at_precision": precision,
        "observed_at": receipt["observed_at"], "available_at": receipt["available_at"],
        "decision_cutoff": value["time_contract"]["feature_event_cutoff"],
        "request_hash": receipt["request_hash"], "raw_hash": receipt["raw_hash"],
        "payload": {"code": "", "title": "Synthetic item"}}]
    result = evaluate(value)
    assert bool(result["research_ready"]) is allowed, result["missing_reasons"]
    if allowed:
        row = next(r for r in result["audit_records"] if r["data_type"] == "global_news")
        assert row["payload"]["items"][0]["published_at"] == published
    else:
        assert result["research_ranking"] == []
    safe(result)


def test_minute_overlap_missing_end_after_cutoff_and_no_label_shifting():
    value = captured_input()
    bars = batch(value, "minute_bar")["records"]
    bars[0].update(event_time=f"{DAY}T14:50:00+08:00", native_label=f"{DAY}T14:50:00+08:00",
                   interval_start=f"{DAY}T14:50:00+08:00", interval_end=f"{DAY}T14:51:00+08:00")
    result = evaluate(value)
    assert "minute_interval_end_after_feature_cutoff" in result["missing_reasons"]
    assert result["research_ranking"] == []
    safe(result)
    value = captured_input()
    bars = batch(value, "minute_bar")["records"]
    overlap = deepcopy(bars[1])
    overlap["payload"]["close"] += 0.001
    bars[0] = overlap
    assert not evaluate(value)["research_ready"]


def test_order_duplicate_and_flow_do_not_change_research_decision():
    value = captured_input()
    before = evaluate(value)
    value["batches"].append(deepcopy(value["batches"][0]))
    for item in value["batches"]:
        random.Random(40).shuffle(item["records"])
    random.Random(41).shuffle(value["batches"])
    after = evaluate(value)
    assert before["decision_hash"] == after["decision_hash"]
    value["fund_flow"] = {"aggregation_semantics": "incremental", "main_net": 1e99}
    value["batches"].append({"data_type": "fund_flow", "records": "unproven malformed flow ignored"})
    flow = evaluate(value)
    assert flow["decision_hash"] == after["decision_hash"]
    assert flow["research_ranking"] == before["research_ranking"]
    assert flow["ingest_hash"] != before["ingest_hash"]
    safe(flow)


def test_bridge_contract_has_no_unused_fund_flow_identity_dependency(monkeypatch):
    before = bridge.bridge_contract_hash()
    entries = deepcopy(get_source_adapter_registry())
    for entry in entries:
        if entry["capability"] == "fund_flow":
            entry.update(source_version="UNPROVEN-unused-flow", provider_key="unused.flow")
    # Only the hash projection is under test; production Registry is not changed.
    monkeypatch.setattr(bridge, "get_source_adapter_registry", lambda: entries)
    versions = dict(bridge.market.SOURCE_IDENTITIES)
    versions["fund_flow"] = ("unused", "unused", "UNPROVEN")
    monkeypatch.setattr(bridge.market, "SOURCE_IDENTITIES", versions)
    assert bridge.bridge_contract_hash() == before


def test_zero_news_is_success_but_not_a_proof_of_clear_risk():
    value = captured_input()
    receipt = batch(value, "global_news")["responses"][0]
    raw = json.dumps({"data": {"fastNewsList": [{"showTime": f"{DAY} 14:50:01", "title": "Synthetic future item"}]}}).encode()
    receipt.update(content_base64=base64.b64encode(raw).decode(), raw_hash=hashlib.sha256(raw).hexdigest())
    result = evaluate(value)
    assert result["research_ready"], result["missing_reasons"]
    status = next(r for r in result["audit_records"] if r["data_type"] == "global_news")
    assert status["payload"]["status"] == "SUCCESS" and status["payload"]["items"] == []
    value = captured_input()
    batch(value, "stock_news")["responses"][0]["scope_code"] = "600519"
    result = evaluate(value)
    assert "risk_request_scope_mismatch" in result["missing_reasons"]
    assert result["research_ranking"] == []
    safe(result)


def test_exchange_partitioned_empty_responses_and_wrong_exchange_are_not_cninfo():
    from overnight_quant.data import exchange_announcement_providers as exchange
    value = captured_input()
    original = batch(value, "announcement")
    value["batches"].remove(original)
    for origin, codes, body, url in (
        ("sse", ["600000", "600519", "601318"], {"result": []}, exchange.SSE_ANNOUNCEMENT_URL),
        ("szse", ["000001", "000333"], {"data": []}, exchange.SZSE_ANNOUNCEMENT_URL),
    ):
        binding = next(r for r in get_source_adapter_registry() if r["capability"] == "announcement" and r["origin_source"] == origin)
        b = {**{k: deepcopy(v) for k, v in original.items() if k not in {"responses", "records"}},
             **{k: binding[k] for k in ("origin_source", "adapter", "source_version")},
             "provider_key": binding["candidate_provider_key"], "records": [], "responses": []}
        for code in codes:
            material = exchange._request_material("GET" if origin == "sse" else "POST", url,
                params={"SECURITY_CODE": code} if origin == "sse" else None, data=None,
                json_body={"stock": [code]} if origin == "szse" else None)
            raw = json.dumps(body).encode()
            b["responses"].append({**material, "scope_code": code, "http_status_code": 200,
                "request_hash": stable_hash(material), "raw_hash": hashlib.sha256(raw).hexdigest(),
                "content_base64": base64.b64encode(raw).decode(), "response_url": url,
                "observed_at": f"{DAY}T14:49:55+08:00", "available_at": f"{DAY}T14:49:56+08:00"})
        value["batches"].append(b)
    result = evaluate(value)
    assert result["research_ready"], result["missing_reasons"]
    annotations = [r for r in result["audit_records"] if r["data_type"] == "announcement"]
    assert {r["bridge_trace"]["native_identity"][0] for r in annotations} == {"sse", "szse"}
    safe(result)
    sse = next(b for b in value["batches"] if b.get("origin_source") == "sse")
    receipt = sse["responses"][0]
    receipt.update(scope_code="000001", params=[["SECURITY_CODE", "000001"]])
    receipt["request_hash"] = stable_hash({k: receipt[k] for k in ("method", "url", "params", "data", "json_body")})
    result = evaluate(value)
    assert "announcement_exchange_route_mismatch" in result["missing_reasons"]
    assert result["research_ranking"] == []
    safe(result)


def test_real_captures_cannot_use_synthetic_semantics_or_qualification_flags():
    value = captured_input()
    before = (compute_source_adapter_registry_hash(), compute_source_capability_registry_hash())
    value["test_only"] = False
    for b in value["batches"]:
        b.update(test_only=False, qualified=True, eligible_for_hard_gate=True)
        for row in b["records"]:
            row.update(test_only=False, qualified=True, hard_gate_authorized=True)
    result = evaluate(value, test_only=False)
    assert not result["research_ready"] and result["research_ranking"] == []
    assert "MINUTE_SOURCE_INTERVAL_SEMANTICS_UNPROVEN" in result["missing_reasons"]
    assert any("UNQUALIFIED" in r or "CANDIDATE" in r or "REJECTED" in r for r in result["missing_reasons"])
    assert (compute_source_adapter_registry_hash(), compute_source_capability_registry_hash()) == before
    safe(result)
    assert not evaluate(captured_input(), test_only=False)["research_ready"]


def test_actual_cli_anchor_default_no_network_double_replay_and_write_once(tmp_path):
    source = tmp_path / "provider-capture-test-only.json"
    source.write_text(json.dumps(captured_input(), sort_keys=True), encoding="utf-8")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    command = [sys.executable, str(ROOT / "overnight_quant/scripts/run_noflow_research.py"),
               "--strategy", STRATEGY_ID, "--provider-capture", "--test-only", "--input", str(source),
               "--expected-input-sha256", sha]
    outputs = []
    for i in range(2):
        p = subprocess.run([*command, "--output-dir", str(tmp_path / f"replay-{i}")], capture_output=True, text=True, timeout=15)
        assert p.returncode == 0, p.stdout + p.stderr
        outputs.append(p.stdout)
    assert outputs[0] == outputs[1]
    first = next((tmp_path / "replay-0").rglob("*.json"))
    second = next((tmp_path / "replay-1").rglob("*.json"))
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha
    assert "record_bridge_v1" in str(next((tmp_path / "replay-0").rglob("*.json")))
    again = subprocess.run([*command, "--output-dir", str(tmp_path / "replay-0")], capture_output=True, text=True, timeout=15)
    assert again.returncode == 2 and json.loads(again.stdout)["status"] == "OUTPUT_REJECTED"
    wrong = run(input_path=source, expected_sha256="0" * 64, test_only=True, provider_capture=True)
    assert wrong["missing_reasons"] == ["external_input_sha256_mismatch"]
    safe(wrong)
    default = run(input_path=None, expected_sha256=None, provider_capture=True)
    safe(default)
    assert not default["research_ready"]


def test_old_strategy_requires_fund_flow_and_direct_json_cannot_inject_bridge():
    from overnight_quant.tests.test_phase105_noflow_research import old_fixture
    from overnight_quant.data.close_confirmation_readiness import validate_close_confirmation_readiness
    from overnight_quant.strategy.legacy_frozen_baseline import legacy_baseline_is_intact
    from overnight_quant.strategy.close_confirmation_noflow_v1.contracts import formal_time_contract
    value = {"records": [r for r in old_fixture() if r["data_type"] != "fund_flow"],
             "time_contract": formal_time_contract("2026-09-18")}
    old = validate_close_confirmation_readiness(value, decision_time="2026-09-18T14:51:10+08:00")
    assert old["coverage_by_type"]["fund_flow"] == 0
    assert not old["data_ready"]
    assert legacy_baseline_is_intact()
    direct = synthetic_input()
    direct.update(test_only=False, _bridge={"row_hashes": [stable_hash(r) for r in direct["records"]]})
    result = NoFundFlowResearchStrategy().evaluate_snapshot(direct)
    assert not result["research_ready"]
    safe(result)
