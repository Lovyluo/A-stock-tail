from copy import deepcopy
import json
from pathlib import Path

import pytest

from overnight_quant.data.concept_batch_observation import CONTRACT
from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard
from overnight_quant.ui import hotspot_core as ui


def assert_empty_product(state):
    assert state["event_date"] == state["hotspot_coverage"] == "UNKNOWN"
    assert state["qualification"] == "0/3"
    assert state["data_ready"] is state["hard_gate_authorized"] is False
    assert all(state[key] == [] for key in ("hotspot_events", "core_stock_observation_candidates",
                                           "records", "candidates", "tickets", "orders"))


def test_homepage_state_is_independent_and_always_unqualified():
    state = ui.homepage_state()
    assert_empty_product(state)
    state["core_stock_observation_candidates"].append("not shared")
    assert_empty_product(ui.homepage_state())


@pytest.fixture
def structural_batch():
    # Synthetic test input only. Not a product event or a source qualification.
    return {
        "batch_id": "test-structure", "synthetic_test_only": False, "contract_hash": CONTRACT,
        "capture_completed_at": "2026-11-10T16:27:17+08:00", "selected_bk": "BK0001",
        "selected_name": "测试结构", "list_coverage": {"received": 1, "total": 2, "complete": False},
        "member_coverage": {"received": 1, "total": 1, "complete": True},
        "member_raw": [{"f12": "000001", "f13": 0, "f14": "测试成分", "f3": 99, "f6": 999}],
        "candidates": ["must not be copied"], "core_stock_observation_candidates": ["must not be copied"],
    }


def test_history_projects_only_structural_fields(monkeypatch, structural_batch):
    checks = []
    monkeypatch.setattr(ui, "require_catalog_selection", lambda *args: checks.append(args))
    monkeypatch.setattr(ui, "load_batch", lambda *args: deepcopy(structural_batch))
    inputs = ("directory", "report", "report-sha", "confirmation-sha")
    history = ui.historical_structure("test-structure", inputs)
    assert len(checks) == 2 and checks[0] == checks[1] == (inputs, "test-structure")
    assert history["members"] == [{"代码原值": "000001", "市场原值": 0, "名称原值": "测试成分"}]
    assert history["list_coverage"]["complete"] is False
    assert "f3" not in str(history) and "f6" not in str(history) and "candidates" not in history
    assert_empty_product(ui.homepage_state())


@pytest.mark.parametrize("field,value,error", [
    ("synthetic_test_only", True, "real_historical_batch_required"),
    ("synthetic_test_only", 0, "real_historical_batch_required"),
    ("batch_id", "other", "real_historical_batch_required"),
    ("contract_hash", "unknown", "historical_membership_unproven"),
    ("selected_name", "", "historical_membership_unproven"),
])
def test_history_fails_closed(monkeypatch, structural_batch, field, value, error):
    structural_batch[field] = value
    monkeypatch.setattr(ui, "require_catalog_selection", lambda *args: None)
    monkeypatch.setattr(ui, "load_batch", lambda *args: structural_batch)
    with pytest.raises(ValueError, match=error):
        ui.historical_structure("test-structure", ("d", "r", "s", "c"))


def test_history_rechecks_catalog_after_replay(monkeypatch, structural_batch):
    calls = []
    def check(*args):
        calls.append(args)
        if len(calls) == 2:
            raise ValueError("catalog_batch_anchor_changed")
    monkeypatch.setattr(ui, "require_catalog_selection", check)
    monkeypatch.setattr(ui, "load_batch", lambda *args: structural_batch)
    with pytest.raises(ValueError, match="anchor_changed"):
        ui.historical_structure("test-structure", ("d", "r", "s", "c"))


def open_home(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.ui import dashboard, offline_observation
    monkeypatch.delenv("CONCEPT_BATCH_VERIFIED_INDEXES", raising=False)
    monkeypatch.delenv("THS_HISTORY_INDEX", raising=False)
    monkeypatch.delenv("THS_HISTORY_INDEX_SHA256", raising=False)
    monkeypatch.setattr(offline_observation, "DEFAULT_STORE", tmp_path / "user-store")
    guard = AppTestNetworkGuard(monkeypatch)
    def reject_old(*args, **kwargs):
        pytest.fail("default homepage entered legacy state/render/action")
    monkeypatch.setattr(dashboard, "load_dashboard_state", reject_old)
    monkeypatch.setattr(dashboard, "run_dashboard_action", reject_old)
    monkeypatch.setattr(dashboard, "run_approved_action", reject_old)
    monkeypatch.setattr(offline_observation, "render_offline_observation", reject_old)
    app = AppTest.from_file(str(Path(dashboard.__file__)), default_timeout=15).run()
    return app, guard


def test_default_dashboard_early_return_zero_network_and_writes(monkeypatch, tmp_path, record_property):
    app, guard = open_home(monkeypatch, tmp_path)
    workspace = next(x for x in app.radio if x.label == "工作区")
    assert workspace.value == "热点与核心股 · 证据"
    assert set(workspace.options) == {"热点与核心股 · 证据", "历史/离线观察", "全天短线观察 · 演示",
                                      "捕获批次观察", "原有研究工作台"}
    assert not app.exception
    assert [x.value for x in app.title] == ["历史题材归因线索"]
    assert any("尚未选择封存" in x.value for x in app.info)
    assert [x.label for x in app.text_input] == ["已封存线索索引", "索引外部SHA-256"]
    assert not app.button and not app.text_area and not app.file_uploader
    assert_empty_product(json.loads(app.json[0].value))
    app.run()
    assert not app.exception and not any(guard.business_attempts.values())
    assert not list(tmp_path.rglob("*"))
    record_property("business_attempts", json.dumps(guard.business_attempts, sort_keys=True))
    record_property("framework_connections", guard.framework_connections)


def test_dashboard_explicit_history_never_populates_hotspots(monkeypatch, tmp_path, structural_batch):
    app, guard = open_home(monkeypatch, tmp_path)
    inputs = ("d", "r", "s", "c")
    monkeypatch.setattr(ui, "_catalog", lambda: ({"test-structure": inputs}, 0))
    monkeypatch.setattr(ui, "require_catalog_selection", lambda *args: None)
    monkeypatch.setattr(ui, "load_batch", lambda *args: deepcopy(structural_batch))
    app.run()
    next(x for x in app.selectbox if x.label == "历史结构批次").select("test-structure").run()
    assert not app.exception and len(app.dataframe) == 1
    assert any("历史结构归属样本，不是热点" in x.value for x in app.markdown)
    assert any("1/2" in x.value and "1/1" in x.value for x in app.markdown)
    assert_empty_product(json.loads(app.json[0].value))
    structural_batch["synthetic_test_only"] = True
    app.run()
    assert not app.exception and any("历史材料拒绝" in x.value for x in app.error)
    assert not app.dataframe and not any(guard.business_attempts.values())
    assert not list(tmp_path.rglob("*"))


def test_real_catalog_replay_rejects_synthetic_and_revalidates_anchors_each_rerun(monkeypatch, tmp_path):
    from overnight_quant.data.concept_batch_observation import sha
    from overnight_quant.tests.test_concept_batch_observation import run, page, lists, stocks
    process, receipt, target, _ = run(tmp_path, [page(1, lists(1)), page(2, stocks(2))])
    assert process.returncode == 0, receipt
    report = target / "returned-report.json"
    confirmation = target / "final-confirmation.json"
    report_bytes, confirmation_bytes = report.read_bytes(), confirmation.read_bytes()
    index = tmp_path / "catalog.json"
    entry = {"batch_id": "batch", "independent_verification": "VERIFIED_OFFLINE", "capture_status": "PASSED",
             "output_directory": str(target), "returned_report": str(report), "report_sha256": sha(report),
             "confirmation_sha256": sha(confirmation)}
    index.write_text(json.dumps(entry), encoding="utf-8")
    app, guard = open_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CONCEPT_BATCH_VERIFIED_INDEXES", str(index))
    app.run()
    next(x for x in app.selectbox if x.label == "历史结构批次").select("batch").run()
    assert any("real_historical_batch_required" in x.value for x in app.error)
    report.write_bytes(report_bytes + b" ")
    app.run()
    assert any("returned_report_anchor_mismatch" in x.value for x in app.error)
    report.write_bytes(report_bytes)
    confirmation.write_bytes(confirmation_bytes + b" ")
    app.run()
    assert any("final_confirmation_anchor_mismatch" in x.value for x in app.error)
    confirmation.write_bytes(confirmation_bytes)
    entry["report_sha256"] = "0" * 64
    index.write_text(json.dumps(entry), encoding="utf-8")
    app.run()
    assert any("returned_report_anchor_mismatch" in x.value for x in app.error)
    entry["independent_verification"] = "UNKNOWN"
    index.write_text(json.dumps(entry), encoding="utf-8")
    app.run()
    assert not app.exception and not app.dataframe
    assert any("无效或冲突" in x.value for x in app.warning)
    assert_empty_product(json.loads(app.json[0].value))
    assert not (tmp_path / "user-store").exists() and not any(guard.business_attempts.values())
