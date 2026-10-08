from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json

import pytest

from overnight_quant.data.eastmoney_derived_industry_breadth import (
    CORE_CONCEPTION_URL,
    EXPECTED_INDUSTRIES,
    SOURCE_VERSION,
    EastmoneyDerivedIndustryBreadthProvider,
    compute_evidence_hash,
    request_leaves,
    verify_evidence,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_CLIST_URL,
    FIXED_CODES,
    MARKET_DOTNET_TRANSPORT_VERSION,
    MarketHttpResponse,
    MarketSourceContractError,
)
from overnight_quant.scripts import run_derived_industry_breadth_audit as audit


TRADE_DATE = "2026-09-30"
CUTOFF = f"{TRADE_DATE}T14:40:00+08:00"
DECISION = f"{TRADE_DATE}T14:41:05+08:00"
EVENT_TS = int(datetime(2026, 9, 30, 14, 39, 30, tzinfo=CN_TZ).timestamp())


class StepClock:
    def __init__(self, *, initial=False):
        self.base = datetime(2026, 9, 30, 14, 38, tzinfo=CN_TZ)
        self.calls = -1 if initial else 0

    def __call__(self):
        if self.calls == -1:
            self.calls = 0
            return self.base - timedelta(minutes=1)
        value = self.base + timedelta(milliseconds=self.calls * 100)
        self.calls += 1
        return value


class FixtureTransport:
    transport_version = MARKET_DOTNET_TRANSPORT_VERSION

    def __init__(self, *, missing_member="", total_drift=False, duplicate=False):
        self.request_count = 0
        self.missing_member = missing_member
        self.total_drift = total_drift
        self.duplicate = duplicate
        self.board_rows = {
            "BK0475": [self._row("000001", 1.0), self._row("600000", -1.0)],
            "BK1239": [self._row("000333", 0.0), self._row("000651", 2.0)],
            "BK1277": [self._row("600519", None), self._row("000858", -0.5)],
            "BK0474": [self._row("601318", 0.3), self._row("601601", 0.0)],
        }
        if missing_member:
            for rows in self.board_rows.values():
                rows[:] = [row for row in rows if row["f12"] != missing_member]
        if duplicate:
            self.board_rows["BK0475"].append(dict(self.board_rows["BK0475"][0]))

    @staticmethod
    def _row(code, change):
        return {
            "f2": 10.0,
            "f3": change if change is not None else "-",
            "f12": code,
            "f14": code,
            "f124": EVENT_TS,
        }

    def request(self, method, url, *, params, headers, timeout_seconds):
        self.request_count += 1
        if url == CORE_CONCEPTION_URL:
            web_code = str(params["code"])
            code = web_code[2:]
            board_code, board_name = EXPECTED_INDUSTRIES[code]
            payload = {"ssbk": [{
                "SECURITY_CODE": code,
                "BOARD_CODE": board_code[2:],
                "BOARD_NAME": board_name,
                "BOARD_RANK": 2,
            }]}
        elif url == EASTMONEY_CLIST_URL:
            board_code = str(params["fs"]).split(":", 1)[1]
            rows = list(self.board_rows[board_code])
            total = len(rows) + (1 if self.total_drift else 0)
            payload = {"data": {"total": total, "diff": rows}}
        else:
            raise AssertionError(url)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        index = self.request_count - 1
        started = datetime(2026, 9, 30, 14, 38, tzinfo=CN_TZ) + timedelta(
            milliseconds=index * 200 + 10
        )
        completed = started + timedelta(milliseconds=20)
        return MarketHttpResponse(
            raw,
            200,
            url,
            {},
            started.isoformat(timespec="microseconds"),
            completed.isoformat(timespec="microseconds"),
            MARKET_DOTNET_TRANSPORT_VERSION,
            started.isoformat(timespec="microseconds"),
            completed.isoformat(timespec="microseconds"),
            20.0,
            False,
        )


def provider(transport=None, clock=None):
    return EastmoneyDerivedIndustryBreadthProvider(
        FIXED_CODES,
        trade_date=TRADE_DATE,
        cutoff=CUTOFF,
        decision_time=DECISION,
        transport=transport or FixtureTransport(),
        clock=clock or StepClock(),
    )


def test_composite_provider_maps_five_stocks_and_queries_four_boards_once():
    transport = FixtureTransport()
    instance = provider(transport)
    records = instance.collect_records()
    assert len(records) == 5
    assert transport.request_count == 9
    assert len(instance.request_tree["mappings"]) == 5
    assert set(instance.request_tree["boards"]) == {
        "BK0474", "BK0475", "BK1239", "BK1277"
    }
    assert len(request_leaves(instance.request_tree)) == 9
    assert {
        row["payload"]["code"]: (
            row["payload"]["board_code"], row["payload"]["industry_name"]
        )
        for row in records
    } == EXPECTED_INDUSTRIES
    for row in records:
        payload = row["payload"]
        assert payload["valid_count"] == (
            payload["up_count"] + payload["down_count"] + payload["flat_count"]
        )
        assert payload["total_count"] == payload["valid_count"] + payload["invalid_count"]
        assert payload["breadth_ratio"] == pytest.approx(
            payload["up_count"] / payload["valid_count"]
        )
        assert row["source_version"] == SOURCE_VERSION


@pytest.mark.parametrize("code", ["000333", "600519"])
def test_mapped_stock_must_appear_in_exact_board_constituents(code):
    with pytest.raises(
        MarketSourceContractError,
        match=f"INDUSTRY_MAPPED_STOCK_NOT_IN_BOARD:{code}",
    ):
        provider(FixtureTransport(missing_member=code)).collect_records()


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"duplicate": True}, "INDUSTRY_CONSTITUENT_DUPLICATE"),
        ({"total_drift": True}, "INDUSTRY_BOARD_TOTAL_COUNT_MISMATCH"),
    ],
)
def test_constituent_pagination_contract_fails_closed(changes, error):
    with pytest.raises(MarketSourceContractError, match=error):
        provider(FixtureTransport(**changes)).collect_records()


def test_audit_cutoff_equal_or_later_never_calls_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "write_json_atomic", lambda path, payload: None)
    for offset in (0, 1):
        transport = FixtureTransport()
        now = datetime.fromisoformat(CUTOFF) + timedelta(milliseconds=offset)
        result = audit.run_audit(
            network=True,
            trade_date=TRADE_DATE,
            cutoff=CUTOFF,
            decision_time=DECISION,
            output=tmp_path / f"missed-{offset}.json",
            schema_audit_only=True,
            clock=lambda now=now: now,
            transport=transport,
        )
        assert result["status"] == "AUDIT_WINDOW_MISSED"
        assert result["network_requests_made"] == transport.request_count == 0
        assert result["data_ready"] is False
        assert result["candidates"] == result["tickets"] == result["orders"] == []


def _evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "write_json_atomic", lambda path, payload: None)
    return audit.run_audit(
        network=True,
        trade_date=TRADE_DATE,
        cutoff=CUTOFF,
        decision_time=DECISION,
        output=tmp_path / "evidence.json",
        schema_audit_only=True,
        clock=StepClock(initial=True),
        transport=FixtureTransport(),
    )


def test_verifier_recomputes_nested_leaf_request_count(tmp_path, monkeypatch):
    evidence = _evidence(tmp_path, monkeypatch)
    assert evidence["network_requests_made"] == 9
    verified = verify_evidence(evidence, expected_file_sha256="a" * 64)
    assert verified["status"] == "DERIVED_INDUSTRY_BREADTH_EVIDENCE_VERIFIED"
    assert verified["network_requests_recomputed"] == 9

    tampered = deepcopy(evidence)
    tampered["network_requests_made"] = 0
    tampered["evidence_hash"] = compute_evidence_hash(tampered)
    rejected = verify_evidence(tampered, expected_file_sha256="a" * 64)
    assert rejected["status"] == "DERIVED_INDUSTRY_BREADTH_EVIDENCE_INVALID"
    assert "network_request_count_mismatch" in rejected["errors"]
    assert rejected["data_ready"] is False
    assert rejected["candidates"] == rejected["tickets"] == rejected["orders"] == []


def test_verifier_accepts_json_round_trip_request_tree(tmp_path, monkeypatch):
    evidence = _evidence(tmp_path, monkeypatch)
    persisted = json.loads(json.dumps(evidence, ensure_ascii=False))

    verified = verify_evidence(persisted, expected_file_sha256="c" * 64)

    assert verified["status"] == "DERIVED_INDUSTRY_BREADTH_EVIDENCE_VERIFIED"
    assert verified["network_requests_recomputed"] == 9


def test_raw_response_tamper_is_rejected_after_resigning(tmp_path, monkeypatch):
    evidence = deepcopy(_evidence(tmp_path, monkeypatch))
    leaf = evidence["request_tree"]["mappings"][0]
    raw = base64.b64decode(leaf["content_base64"])
    payload = json.loads(raw)
    payload["ssbk"][0]["BOARD_CODE"] = "9999"
    changed = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    leaf["content_base64"] = base64.b64encode(changed).decode()
    leaf["byte_count"] = len(changed)
    leaf["raw_hash"] = hashlib.sha256(changed).hexdigest()
    evidence["evidence_hash"] = compute_evidence_hash(evidence)
    result = verify_evidence(evidence, expected_file_sha256="b" * 64)
    assert result["status"] == "DERIVED_INDUSTRY_BREADTH_EVIDENCE_INVALID"
    assert any("INDUSTRY_MAPPING_MISMATCH" in error for error in result["errors"])
