from pathlib import Path

from overnight_quant.data.stock_catalog import (
    load_stock_catalog,
    resolve_stock_name,
    update_stock_catalog,
)
from overnight_quant.execution.order_recorder import record_position_update
from overnight_quant.ui.dashboard import (
    APPROVED_ACTIONS,
    maintenance_action_keys,
    resolve_position_stock_name,
)


def _catalog_rows():
    return [
        {"code": "000001", "name": "平安银行", "market": "sz"},
        {"code": "600000", "name": "浦发银行", "market": "sh"},
    ]


def test_stock_catalog_update_and_lookup(tmp_path):
    path = tmp_path / "stock_catalog.csv"

    result = update_stock_catalog(
        path,
        source_fetchers=[("fixture", _catalog_rows)],
    )

    assert result["status"] == "STOCK_CATALOG_READY"
    assert result["count"] == 2
    assert resolve_stock_name("000001", path, fetch_remote=False) == "平安银行"
    assert load_stock_catalog(path)["600000"]["name"] == "浦发银行"


def test_missing_code_uses_single_quote_and_is_cached(tmp_path):
    path = tmp_path / "stock_catalog.csv"
    calls = []

    def quote_fetcher(code):
        calls.append(code)
        return {"code": code, "name": "测试股份", "market": "sz"}

    first = resolve_stock_name("001234", path, quote_fetcher=quote_fetcher)
    second = resolve_stock_name("001234", path, quote_fetcher=lambda code: (_ for _ in ()).throw(AssertionError(code)))

    assert first == "测试股份"
    assert second == "测试股份"
    assert calls == ["001234"]


def test_position_record_uses_catalog_name_instead_of_wrong_manual_name(tmp_path):
    records = tmp_path / "records"
    reports = tmp_path / "reports"
    catalog = tmp_path / "stock_catalog.csv"
    config = {
        "paths": {
            "records_dir": str(records),
            "reports_dir": str(reports),
            "stock_catalog_path": str(catalog),
        }
    }
    update_stock_catalog(catalog, source_fetchers=[("fixture", _catalog_rows)])

    result = record_position_update(
        config,
        code="000001",
        name="错误名称",
        price=10.0,
        qty=100,
        side="BUY",
        trade_time="2026-07-24 10:00:00",
    )

    assert result["allow"] is True
    assert result["row"]["name"] == "平安银行"


def test_dashboard_resolves_catalog_name_before_existing_wrong_name(tmp_path):
    catalog = tmp_path / "stock_catalog.csv"
    update_stock_catalog(catalog, source_fetchers=[("fixture", _catalog_rows)])
    state = {
        "stock_catalog_path": str(catalog),
        "stock_catalog": load_stock_catalog(catalog),
        "position_summary": [],
    }

    assert resolve_position_stock_name("000001", state, fetch_remote=False) == "平安银行"


def test_stock_catalog_update_is_available_only_as_maintenance_action():
    assert "stock_catalog_update" in maintenance_action_keys()
    command = " ".join(APPROVED_ACTIONS["stock_catalog_update"])
    assert "run_stock_catalog_update.py" in command
