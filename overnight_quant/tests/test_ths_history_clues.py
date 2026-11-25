"""Synthetic unit inputs stay in tmp_path; real sample regression is separately indexed."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from overnight_quant.data import ths_history_clues as clues
from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard


def row(code="000001", reason="题材甲+题材乙", **extra):
    return {"code": code, "name": "测试证券", "market": 99, "date": "2026-10-30", "reason": reason, **extra}


def sample(rows):
    return {"received_rows": len(rows), "source_time_raw": {"date": "2026-11-25"},
            "clues": [{"row": i, "source_values": values} for i, values in enumerate(rows)]}


def explain(rows, catalog=None):
    return clues.explain_sample(sample(rows), {"target_date": "2026-10-30", "started_at": "2026-11-25T09:53:58+08:00"},
                                {"response_completed_at": "2026-11-25T09:53:59+08:00"}, trusted_catalog=catalog)


@pytest.mark.parametrize("text,labels,mode", [
    ("题材甲+题材乙+“羊”字辈", ["题材甲", "题材乙", "“羊”字辈"], "ASCII_PLUS_DELIMITED"),
    (" 题材甲 + 题材乙 ", ["题材甲", "题材乙"], "ASCII_PLUS_DELIMITED"),
    ("C+++人工智能", ["C+++人工智能"], "WHOLE_TEXT"),
    ("题材甲+(题材乙+题材丙)", ["题材甲+(题材乙+题材丙)"], "WHOLE_TEXT"),
    ("题材甲+“题材乙+题材丙”", ["题材甲+“题材乙+题材丙”"], "WHOLE_TEXT"),
    ("题材甲/题材乙、题材丙", ["题材甲/题材乙、题材丙"], "WHOLE_TEXT"),
    ("甲+", ["甲+"], "WHOLE_TEXT"), ("甲+（乙", ["甲+（乙"], "WHOLE_TEXT"),
    (None, [], "NO_READABLE_TEXT"), ("", [], "NO_READABLE_TEXT"),
])
def test_split_is_conservative_and_offsets_preserve_original(text, labels, mode):
    split = clues.split_reason(text)
    assert split["mode"] == mode
    assert [part["label"] for part in split["segments"]] == labels
    assert all(text[part["start"]:part["end"]] == part["label"] for part in split["segments"])


def test_exact_record_duplicates_and_repeated_tags_not_double_counted():
    a = row(reason="三季报增长+三季报增长+PCB")
    b = row(reason="三季报大增+PCB")
    history = explain([a, deepcopy(a), b])
    assert history["received_records"] == 3 and history["unique_records"] == 2
    assert history["duplicate_records"] == 1
    assert {x["label"]: x["mention_records"] for x in history["tags"]} == {"PCB": 2, "三季报增长": 1, "三季报大增": 1}
    assert history["records"][0]["reason_raw"] == a["reason"]
    assert len(history["records"][0]["tag_trace"]["segments"]) == 3


def test_identity_only_from_explicit_trusted_directory_and_exact_name():
    catalog = {"000001": {"code": "000001", "name": "测试证券", "market": "sz", "updated_at": "2026-07-24"}}
    matched = explain([row()], catalog)["records"][0]["identity_check"]
    assert matched["canonical_identity"] == "SZ:000001"
    assert matched["source_market_semantics"] == matched["historical_name_as_of_row_date"] == "UNKNOWN"
    assert explain([row()])["records"][0]["identity_check"]["canonical_identity"] is None
    assert explain([row(name="其他名称")], catalog)["records"][0]["identity_check"]["status"] == "CATALOG_NAME_OR_CODE_MISMATCH"
    assert explain([row(code="not-code")], catalog)["records"][0]["identity_check"]["canonical_identity"] is None


def test_dates_and_unknowns_remain_independent():
    history = explain([row()])
    assert history["request_target_date"] == "2026-10-30"
    assert history["row_dates_raw"] == ["2026-10-30"]
    assert history["source_top_level_time_raw"] == {"date": "2026-11-25"}
    assert history["response_completed_at"] == "2026-11-25T09:53:59+08:00"
    assert history["coverage"] == history["source_time_semantics"] == history["source_use_basis"] == "UNKNOWN"
    assert history["risk_assessment"] == "NOT_ASSESSED"
    assert not history["hotspot_ranking"] and not history["core_stock_candidates"]
    assert history["data_ready"] is history["hard_gate_authorized"] is False


@pytest.fixture
def pack(tmp_path, monkeypatch):
    output = tmp_path / "synthetic-unit-pack"
    output.mkdir()
    documents = {"request.json": {"target_date": "2026-10-30", "started_at": "2026-11-25T09:53:58+08:00"},
                 "response.json": {"response_completed_at": "2026-11-25T09:53:59+08:00"},
                 "intent.json": {"batch_id": "test-only-pack"}, "completion.json": {"unit_test_only": True}}
    files = []
    for name, value in documents.items():
        path = output / name
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        files.append({"file": name, "path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    returned = tmp_path / "returned.json"
    returned.write_bytes(b'{"unit_test_only":true}')
    index = tmp_path / "sample-file-anchors.json"
    index.write_text(json.dumps({"output": str(output), "files": files,
        "completion_sha256": next(x["sha256"] for x in files if x["file"] == "completion.json"),
        "returned_result": str(returned), "returned_sha256": hashlib.sha256(returned.read_bytes()).hexdigest()}), encoding="utf-8")
    verified = {"evidence_integrity_verified": True, "capture_execution_result": "PASSED", "execution_time_verified": True,
                "synthetic_test_only": False, "sample": sample([row(), row("600000", "题材丙")])}
    # Isolated reader unit seam only, not a real-source or real-verifier acceptance claim.
    monkeypatch.setattr(clues, "verify", lambda *args: deepcopy(verified))
    return index, hashlib.sha256(index.read_bytes()).hexdigest(), output, returned, verified


@pytest.mark.parametrize("target", ["index", "request.json", "response.json", "completion.json", "intent.json", "returned"])
def test_anchor_tampering_rejected_each_read(pack, target):
    index, sha, output, returned, _ = pack
    assert clues.load_history(index, sha)["unique_records"] == 2
    path = index if target == "index" else returned if target == "returned" else output / target
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="anchor_changed"):
        clues.load_history(index, sha)


@pytest.mark.parametrize("key,value", [("capture_execution_result", "FAILED"), ("evidence_integrity_verified", False),
                                     ("execution_time_verified", False), ("synthetic_test_only", True)])
def test_reader_does_not_upgrade_failure_or_synthetic(pack, key, value):
    index, sha, _, _, verified = pack
    verified[key] = value
    with pytest.raises(ValueError, match="real_confirmed"):
        clues.load_history(index, sha)


def test_file_change_during_verification_rejected(pack, monkeypatch):
    index, sha, output, _, verified = pack
    def change(*args):
        path = output / "response.json"
        path.write_bytes(path.read_bytes() + b" ")
        return deepcopy(verified)
    monkeypatch.setattr(clues, "verify", change)
    with pytest.raises(ValueError, match="anchor_changed"):
        clues.load_history(index, sha)


def test_dashboard_tags_search_full_reason_and_invalidated_anchor(pack, monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.ui import dashboard, offline_observation
    index, sha, output, _, _ = pack
    monkeypatch.setenv("THS_HISTORY_INDEX", str(index))
    monkeypatch.setenv("THS_HISTORY_INDEX_SHA256", sha)
    monkeypatch.delenv("CONCEPT_BATCH_VERIFIED_INDEXES", raising=False)
    monkeypatch.setattr(offline_observation, "DEFAULT_STORE", tmp_path / "user-store")
    guard = AppTestNetworkGuard(monkeypatch)
    monkeypatch.setattr(dashboard, "load_dashboard_state", lambda *a: pytest.fail("legacy state"))
    monkeypatch.setattr(dashboard, "run_dashboard_action", lambda *a: pytest.fail("legacy action"))
    app = AppTest.from_file(str(Path(dashboard.__file__)), default_timeout=15).run()
    assert not app.exception
    assert app.title[0].value == "历史题材归因线索"
    assert any("2条样本" in x.value for x in app.markdown)
    assert any("题材甲+题材乙" == x.value for x in app.text)
    next(x for x in app.selectbox if x.label == "查看题材关联记录").select("题材丙").run()
    assert not app.exception and app.dataframe[1].value["来源代码"].tolist() == ["600000"]
    next(x for x in app.text_input if x.label == "搜索代码、名称或归因原文").set_value("不存在").run()
    assert any("本次筛选没有" in x.value for x in app.info)
    assert not app.button and not (tmp_path / "user-store").exists()
    (output / "intent.json").write_bytes(b"{}")
    app.run()
    assert any("拒绝读取" in x.value for x in app.error)
    assert not app.dataframe and not any(guard.business_attempts.values())
