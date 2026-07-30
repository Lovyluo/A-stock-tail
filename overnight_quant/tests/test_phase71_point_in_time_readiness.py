from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from overnight_quant.backtest.point_in_time_provider import PointInTimeProvider
from overnight_quant.data.point_in_time import build_point_in_time_record
from overnight_quant.data.snapshot_store import CloseWindowCollector, ImmutableSnapshotStore
from overnight_quant.scripts import run_close_snapshot_collector as collector_script
from overnight_quant.scripts.run_close_confirmation import run_close_confirmation
from overnight_quant.scripts.run_preflight import run_preflight
from overnight_quant.ui.dashboard import action_feedback, localize_table_value, status_badge


TRADE_DATE = "2026-07-30"
DECISION_TIME = f"{TRADE_DATE} 14:50"


def test_records_only_frozen_snapshot_materializes_and_classifies_at_decision(tmp_path):
    records = [
        _record(
            "quote",
            {"code": "000001", "name": "平安银行", "industry_name": "银行", "price": 10.2},
            "14:49",
        ),
        _record(
            "minute_bar",
            {"code": "000001", "price": 10.2, "volume": 1000, "amount": 10200},
            "14:50",
        ),
        _record(
            "market",
            {"index_change_pct": 0.4, "market_breadth": 0.62},
            "14:49",
        ),
        _record(
            "industry",
            {"name": "银行", "change_pct": 0.8, "breadth": 0.7},
            "14:49",
        ),
        _record(
            "news",
            {"code": "000001", "title": "公司公告摘要"},
            "14:40",
            published_at="14:39",
        ),
        _record(
            "news",
            {"title": "市场政策摘要"},
            "14:41",
            published_at="14:40",
        ),
        _record(
            "minute_bar",
            {"code": "000001", "price": 10.3, "volume": 2000, "amount": 20600},
            "14:51",
            cutoff="15:00",
        ),
    ]
    store = ImmutableSnapshotStore(tmp_path)
    snapshot_path = store.write_once(
        "frozen_1450",
        TRADE_DATE,
        {
            "status": "FROZEN_1450",
            "trade_date": TRADE_DATE,
            "decision_time": f"{TRADE_DATE}T14:50:00+08:00",
            "records": records,
        },
    )

    result = PointInTimeProvider.from_frozen_file(snapshot_path).snapshot_at(
        TRADE_DATE,
        "14:50",
    )

    assert [row["code"] for row in result["stocks"]] == ["000001"]
    stock = result["stocks"][0]
    assert [row["event_time"][11:16] for row in stock["intraday_bars"]] == ["14:50"]
    assert stock["market"]["index_change_pct"] == 0.4
    assert stock["industry"]["name"] == "银行"
    assert stock["news"][0]["title"] == "公司公告摘要"
    assert result["news"][0]["payload"]["title"] == "市场政策摘要"
    assert any(
        row.get("pit_reject_reason") == "event_after_decision"
        for row in result["pit_rejected_records"]
    )


def test_collector_without_provider_returns_no_data_source_and_writes_nothing(tmp_path):
    collector = CloseWindowCollector(ImmutableSnapshotStore(tmp_path), {})

    result = collector.collect(datetime.fromisoformat(f"{TRADE_DATE}T14:45:00+08:00"))

    assert result["status"] == "NO_DATA_SOURCE"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    assert not any(tmp_path.rglob("*"))


@pytest.mark.parametrize(
    "provider",
    [
        lambda observed_at: [],
        lambda observed_at: (_ for _ in ()).throw(RuntimeError("offline")),
        lambda observed_at: [{"data_type": "quote", "payload": {"code": "000001"}}],
    ],
)
def test_collector_without_valid_records_writes_nothing(tmp_path, provider):
    collector = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path),
        {"test": provider},
    )

    result = collector.collect(datetime.fromisoformat(f"{TRADE_DATE}T14:45:00+08:00"))

    assert result["status"] == "NO_VALID_RECORDS"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    assert not any(tmp_path.rglob("*"))


def test_freeze_without_valid_records_writes_nothing(tmp_path):
    collector = CloseWindowCollector(ImmutableSnapshotStore(tmp_path), {})

    result = collector.freeze(TRADE_DATE, [])

    assert result["status"] == "NO_VALID_RECORDS"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    assert result["record_count"] == 0
    assert "path" not in result
    assert not any(tmp_path.rglob("*"))


def test_freeze_cli_without_input_fails_with_machine_readable_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_close_snapshot_collector.py",
            "--freeze",
            "--trade-date",
            TRADE_DATE,
            "--snapshot-root",
            str(tmp_path),
        ],
    )

    exit_code = collector_script.main()
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Status: NO_VALID_RECORDS" in output
    assert "execution_ok: true" in output
    assert "data_ready: false" in output
    assert not any(tmp_path.rglob("*"))


def test_freeze_cli_accepts_valid_input_and_keeps_immutable_snapshot(tmp_path):
    input_path = tmp_path / "records.json"
    snapshot_root = tmp_path / "snapshots"
    input_path.write_text(
        json.dumps([_record("minute_bar", {"code": "000001", "price": 10.2}, "14:50")]),
        encoding="utf-8",
    )

    first = collector_script.run_snapshot_collection(
        input_path=input_path,
        trade_date=TRADE_DATE,
        snapshot_root=snapshot_root,
        freeze=True,
    )
    second = collector_script.run_snapshot_collection(
        input_path=input_path,
        trade_date=TRADE_DATE,
        snapshot_root=snapshot_root,
        freeze=True,
    )

    assert first["status"] == second["status"] == "FROZEN_1450"
    assert first["path"] == second["path"]
    assert Path(first["path"]).is_file()


def test_close_confirmation_unavailable_is_executable_but_not_data_ready(tmp_path):
    config = {
        "paths": {"reports_dir": str(tmp_path / "reports")},
        "close_confirmation": {},
    }

    result = run_close_confirmation(
        mode="shadow",
        snapshot_path=tmp_path / "missing.json",
        trade_date=TRADE_DATE,
        config=config,
    )

    assert result["status"] == "POINT_IN_TIME_DATA_UNAVAILABLE"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    assert result["demo_field_count"] == 0
    for key in ("scored", "shadow_candidates", "selected", "tickets", "orders"):
        assert result[key] == []
    report_text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "execution_ok: true" in report_text
    assert "data_ready: false" in report_text


def test_preflight_degrades_when_latest_close_data_is_not_ready(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / f"close_confirmation_shadow_{TRADE_DATE}.md").write_text(
        "status: POINT_IN_TIME_DATA_UNAVAILABLE\n"
        "execution_ok: true\n"
        "data_ready: false\n",
        encoding="utf-8",
    )
    config = {
        "paths": {
            "records_dir": str(tmp_path / "records"),
            "reports_dir": str(reports),
        },
        "backtest": {"output_dir": str(tmp_path / "backtest")},
    }
    monkeypatch.setattr(
        "overnight_quant.scripts.run_preflight._is_writable_dir",
        lambda path: True,
    )
    monkeypatch.setattr(
        "overnight_quant.scripts.run_preflight._config_readable",
        lambda: True,
    )

    result = run_preflight(
        config=config,
        client=object(),
        now=datetime.fromisoformat(f"{TRADE_DATE}T14:55:00+08:00"),
        trade_date=TRADE_DATE,
    )

    assert result["status"] == "PROJECT_HEALTH_DEGRADED"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    assert result["dashboard_parser"]["data_ready"] is False


def test_dashboard_renders_point_in_time_unavailable_as_yellow_not_ready():
    assert status_badge("POINT_IN_TIME_DATA_UNAVAILABLE")["tone"] == "yellow"
    assert localize_table_value("status", "POINT_IN_TIME_DATA_UNAVAILABLE", "zh") == "数据未就绪"

    feedback = action_feedback(
        "formal_live_scan",
        {
            "ok": True,
            "execution_ok": True,
            "data_ready": False,
            "stdout": (
                "Status: POINT_IN_TIME_DATA_UNAVAILABLE\n"
                "execution_ok: true\n"
                "data_ready: false\n"
            ),
        },
        "en",
    )

    assert feedback["severity"] == "warning"
    assert "not ready" in feedback["message"].lower()


def _record(
    data_type: str,
    payload: dict,
    clock: str,
    *,
    published_at: str | None = None,
    cutoff: str = "14:50",
) -> dict:
    event_time = f"{TRADE_DATE} {clock}"
    return build_point_in_time_record(
        payload,
        event_time=event_time,
        published_at=f"{TRADE_DATE} {published_at}" if published_at else None,
        observed_at=event_time,
        available_at=event_time,
        decision_cutoff=f"{TRADE_DATE} {cutoff}",
        source=f"unit.{data_type}",
        source_version="1",
        request={"data_type": data_type},
        raw=payload,
        data_type=data_type,
    ).as_dict()
