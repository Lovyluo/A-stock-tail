from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_CLIST_URL,
    EASTMONEY_STOCK_URL,
    FIXED_CODES,
    MARKET_DOTNET_TRANSPORT_VERSION,
    EastmoneyMarketSourceProviders,
    MarketDotNetHttpClientTransport,
    MarketHttpResponse,
    MarketSourceContractError,
    _run_dotnet_http_worker,
)


TRADE_DATE = "2026-09-29"
CUTOFF = f"{TRADE_DATE}T14:50:00+08:00"
DEADLINE = f"{TRADE_DATE}T14:51:05+08:00"
EVENT_TS = int(datetime(2026, 9, 29, 14, 50, tzinfo=CN_TZ).timestamp())


def _worker_result(body: bytes, url: str = EASTMONEY_CLIST_URL):
    return {
        "ok": True,
        "transport_version": MARKET_DOTNET_TRANSPORT_VERSION,
        "worker_terminated": False,
        "process_started_at": f"{TRADE_DATE}T14:50:01.000000+08:00",
        "process_completed_at": f"{TRADE_DATE}T14:50:01.050000+08:00",
        "process_elapsed_ms": 50.0,
        "response": {
            "status_code": 200,
            "url": url,
            "redirect_chain": [],
            "headers": {},
            "request_started_at": f"{TRADE_DATE}T14:50:01.000000+08:00",
            "request_completed_at": f"{TRADE_DATE}T14:50:01.040000+08:00",
            "byte_count": len(body),
            "raw_hash": hashlib.sha256(body).hexdigest(),
            "content_base64": base64.b64encode(body).decode("ascii"),
        },
    }


def test_dotnet_transport_is_single_attempt_and_has_no_fallback():
    calls = []

    def runner(payload, timeout_seconds, command):
        calls.append((payload, timeout_seconds, command))
        return _worker_result(b'{"data":{}}')

    transport = MarketDotNetHttpClientTransport(worker_runner=runner)
    response = transport.request(
        "GET",
        EASTMONEY_CLIST_URL,
        params={"pn": "1"},
        headers={},
        timeout_seconds=2.0,
    )
    assert len(calls) == transport.request_count == 1
    assert response.transport_version == MARKET_DOTNET_TRANSPORT_VERSION
    assert response.process_elapsed_ms == 50.0
    assert transport.attempts[0]["ok"] is True


def test_dotnet_transport_timeout_is_one_attempt_and_terminated():
    calls = 0

    def runner(payload, timeout_seconds, command):
        nonlocal calls
        calls += 1
        raise TimeoutError("deadline")

    transport = MarketDotNetHttpClientTransport(worker_runner=runner)
    with pytest.raises(MarketSourceContractError, match="MARKET_SOURCE_TIMEOUT"):
        transport.request(
            "GET",
            EASTMONEY_CLIST_URL,
            params={},
            headers={},
            timeout_seconds=2.0,
        )
    assert calls == transport.request_count == 1
    assert transport.attempts == [
        {
            "ok": False,
            "error_code": "MARKET_SOURCE_TIMEOUT",
            "worker_terminated": True,
        }
    ]


class _Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 29, 14, 50, 1, tzinfo=CN_TZ)

    def __call__(self):
        value = self.value
        self.value += timedelta(microseconds=100)
        return value


class _PagedTransport:
    transport_version = MARKET_DOTNET_TRANSPORT_VERSION

    def __init__(self, *, missing_page=False, total_drift=False):
        self.request_count = 0
        self.missing_page = missing_page
        self.total_drift = total_drift
        industries = {
            "000001": "银行",
            "000333": "家用电器",
            "600000": "银行",
            "600519": "白酒",
            "601318": "保险",
        }
        names = [f"行业{index:03d}" for index in range(97)] + sorted(set(industries.values()))
        self.boards = [
            {
                "f12": f"BK{index:04d}",
                "f14": name,
                "f3": 1.0,
                "f104": 3,
                "f105": 2,
                "f106": 1,
                "f124": EVENT_TS,
            }
            for index, name in enumerate(names, 1)
        ]
        self.industries = industries

    def request(self, method, url, *, params, headers, timeout_seconds):
        self.request_count += 1
        if url == EASTMONEY_CLIST_URL:
            page = int(params["pn"])
            if self.missing_page and page == 2:
                rows = []
            else:
                start = (page - 1) * 100
                rows = self.boards[start : start + 100]
            total = len(self.boards) + (1 if self.total_drift and page == 2 else 0)
            payload = {"data": {"total": total, "diff": rows}}
        else:
            code = str(params["secid"]).split(".")[-1]
            payload = {
                "data": {
                    "f57": code,
                    "f58": code,
                    "f127": self.industries[code],
                    "f86": EVENT_TS,
                }
            }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        instant = datetime(2026, 9, 29, 14, 50, 1, tzinfo=CN_TZ) + timedelta(
            microseconds=(self.request_count - 1) * 200 + 20
        )
        return MarketHttpResponse(
            raw,
            200,
            url,
            {},
            instant.isoformat(timespec="microseconds"),
            (instant + timedelta(microseconds=30)).isoformat(timespec="microseconds"),
            MARKET_DOTNET_TRANSPORT_VERSION,
            instant.isoformat(timespec="microseconds"),
            (instant + timedelta(microseconds=30)).isoformat(timespec="microseconds"),
            1.0,
            False,
        )


def _industry_provider(transport):
    return EastmoneyMarketSourceProviders(
        FIXED_CODES,
        trade_date=TRADE_DATE,
        feature_cutoff=CUTOFF,
        collection_deadline=DEADLINE,
        transport=transport,
        clock=_Clock(),
    )


def test_industry_complete_pagination_covers_all_fixed_codes():
    transport = _PagedTransport()
    batch = _industry_provider(transport).collect_industry_batch()
    assert {row["payload"]["code"] for row in batch.records} == set(FIXED_CODES)
    assert all(row["payload"]["board_page_count"] == 2 for row in batch.records)
    assert all(len(row["payload"]["board_page_raw_hashes"]) == 2 for row in batch.records)
    assert transport.request_count == 7


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"missing_page": True}, "INDUSTRY_PAGE_MISSING:2"),
        ({"total_drift": True}, "INDUSTRY_TOTAL_DRIFT"),
    ],
)
def test_industry_pagination_failure_rejects_whole_batch(changes, error):
    with pytest.raises(MarketSourceContractError, match=error):
        _industry_provider(_PagedTransport(**changes)).collect_industry_batch()


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="PowerShell required")
def test_real_dotnet_worker_deadline_terminates_process(tmp_path: Path):
    script = tmp_path / "slow-worker.ps1"
    script.write_text("Start-Sleep -Seconds 5\n", encoding="utf-8")
    started = time.perf_counter()
    with pytest.raises(TimeoutError, match="deadline"):
        _run_dotnet_http_worker(
            {},
            0.15,
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script),
            ],
        )
    assert time.perf_counter() - started < 3
    marker = str(script).replace("'", "''")
    command = (
        "@(Get-CimInstance Win32_Process | Where-Object { "
        f"$_.CommandLine -like '*{marker}*' -and $_.ProcessId -ne $PID }}).Count"
    )
    count = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-Command", command], text=True
    ).strip()
    assert count == "0"
