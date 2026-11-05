import base64
import json
from pathlib import Path
import subprocess
import sys

import pytest

from overnight_quant.data import concept_batch_observation as concept


def page(total, rows):
    body = json.dumps({"data": {"total": total, "diff": rows}}).encode()
    return {"body_base64": base64.b64encode(body).decode()}


def lists(count):
    return [{"f12": f"BK{i:04d}", "f14": f"方向{i}", "f3": 1.25} for i in range(count)]


def stocks(count):
    return [{"f12": f"{i + 1:06d}", "f13": 0, "f14": f"测试{i}",
             "f3": (1 if i % 3 == 0 else -1 if i % 3 == 1 else 0), "f6": i + 1}
            for i in range(count)]


def head():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def run(tmp_path, responses, name="batch", timeout=45):
    fixture = tmp_path / f"{name}-fixture.json"
    fixture.write_text(json.dumps({"responses": responses}), encoding="utf-8")
    target = tmp_path / name
    cmd = [sys.executable, "-m", "overnight_quant.scripts.run_concept_batch", "--day", "2026-11-05",
           "--approved-sha", head(), "--output", str(target), "--batch-id", name,
           "--fixture", str(fixture), "--test-state", str(tmp_path / f"{name}-state")]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    assert process.stdout, process.stderr
    (tmp_path / f"{name}-returned.json").write_text(process.stdout, encoding="utf-8")
    return process, json.loads(process.stdout), target, cmd


def load(tmp_path, target, name="batch"):
    report = tmp_path / f"{name}-returned.json"
    return concept.load_batch(target, report, concept.sha(report))


def test_default_zero_network_and_missing_parameters(tmp_path):
    result = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_concept_batch"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "NETWORK_NOT_REQUESTED"
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"responses": []}', encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_concept_batch",
                             "--fixture", str(fixture)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stdout)["requests_dispatched"] == 0
    assert list(tmp_path.iterdir()) == [fixture]
    describe = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_concept_batch", "--describe"],
                              capture_output=True, text=True, timeout=10)
    assert json.loads(describe.stdout)["status"] == "DRAFT_NOT_AUTHORIZED"
    refused = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_concept_batch", "--network"],
                             capture_output=True, text=True, timeout=15)
    assert refused.returncode == 2 and json.loads(refused.stdout)["requests_dispatched"] == 0


def test_actual_cli_full_six_pages_replay_and_no_overwrite(tmp_path):
    ls, members = lists(201), stocks(201)
    responses = [page(201, ls[i:i + 100]) for i in (0, 100, 200)]
    responses += [page(201, members[i:i + 100]) for i in (0, 100, 200)]
    process, receipt, target, cmd = run(tmp_path, responses)
    assert process.returncode == 0, receipt
    assert receipt["status"] == "PASSED"
    assert len(list(target.glob("request-*.json"))) == 6
    result = load(tmp_path, target)
    assert result["requests_dispatched"] == 6
    assert result["selected_bk"] == "BK0000"
    assert result["member_coverage"] == {"received": 201, "total": 201, "complete": True}
    assert result["counts"] == {"up": 67, "down": 67, "flat": 67, "invalid": 0}
    assert result["event_time_basis"] == "UNPROVEN"
    assert not result["current_observation_allowed"]
    assert load(tmp_path, target) == result
    report = tmp_path / "batch-returned.json"
    verified = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_concept_batch",
                               "--verify-dir", str(target), "--returned-report", str(report),
                               "--report-sha", concept.sha(report)], capture_output=True, text=True, timeout=10)
    assert verified.returncode == 0 and json.loads(verified.stdout)["status"] == "VERIFIED_OFFLINE"
    extra = target / "raw-6.json"
    extra.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected_page_material"):
        load(tmp_path, target)
    extra.unlink()
    repeated = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert repeated.returncode == 2
    assert json.loads(repeated.stdout)["requests_dispatched"] == 0
    raw = target / "raw-0.json"
    raw.write_text(raw.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="raw_anchor_mismatch"):
        load(tmp_path, target)


@pytest.mark.parametrize("responses,expected_requests", [
    ([page(2, lists(1))], 1),
    ([page(101, lists(100)), page(101, [lists(1)[0]])], 2),
    ([page(101, lists(100)), page(102, lists(2))], 2),
    ([page(1, lists(1)), {"network_error": True}], 2),
    ([page(1, lists(1)), {**page(1, stocks(1)), "status": 503}], 2),
])
def test_pagination_and_failure_stop_without_retry(tmp_path, responses, expected_requests):
    process, receipt, target, _ = run(tmp_path, responses)
    assert process.returncode == 2
    assert receipt["status"] == "FAILED"
    assert len(list(target.glob("request-*.json"))) == expected_requests
    assert (target / "raw-0.json").exists()
    with pytest.raises((ValueError, FileNotFoundError)):
        concept.load_batch(target, tmp_path / "batch-returned.json", "0" * 64)


def test_invalid_value_is_not_flat_and_no_current_claim(tmp_path):
    members = stocks(3)
    members[1]["f3"] = "-"
    process, receipt, target, _ = run(tmp_path, [page(1, lists(1)), page(3, members)])
    assert process.returncode == 0, receipt
    result = load(tmp_path, target)
    assert result["counts"] == {"up": 1, "down": 0, "flat": 1, "invalid": 1}
    assert not result["current_observation_allowed"]
    assert result["synthetic_test_only"]


def test_bounded_list_is_not_full_market_and_missing_amount_is_not_ranked(tmp_path):
    ls, members = lists(301), stocks(2)
    ls[0]["f3"] = "-"
    members[0]["f6"] = None
    responses = [page(301, ls[i:i + 100]) for i in (0, 100, 200)] + [page(2, members)]
    process, receipt, target, _ = run(tmp_path, responses)
    assert process.returncode == 0, receipt
    result = load(tmp_path, target)
    assert result["list_coverage"] == {"received": 300, "total": 301, "complete": False}
    assert result["list_invalid_numeric_rows"] == 1
    assert result["active_representatives"] == []
    assert result["amount_basis"] == "MISSING_OR_INVALID"
    assert result["selected_daily_change"] is None


def test_unproven_real_semantics_do_not_produce_metrics():
    result = concept.summarize(lists(1), stocks(3), total_list=1, total_members=3, synthetic=False)
    assert result["field_basis"] == "UNPROVEN"
    assert result["identity_basis"] == "UNPROVEN"
    assert result["counts"] is None
    assert result["metrics_computable"] == {"constituent_counts": False, "active_representatives": False}
    assert result["active_representatives"] == []
    assert not result["current_observation_allowed"]


def test_simultaneous_start_has_one_dispatcher(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"responses": [page(1, lists(1)), page(3, stocks(3))]}), encoding="utf-8")
    target, state = tmp_path / "batch", tmp_path / "state"
    cmd = [sys.executable, "-m", "overnight_quant.scripts.run_concept_batch", "--day", "2026-11-05",
           "--approved-sha", head(), "--output", str(target), "--batch-id", "once",
           "--fixture", str(fixture), "--test-state", str(state)]
    workers = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        results = [json.loads(p.communicate(timeout=30)[0]) for p in workers]
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate(timeout=5)
    assert sorted(row["status"] for row in results) == ["FAILED", "PASSED"]
    assert len(list(target.glob("request-*.json"))) == 2
    assert (state / "intents" / "once.json").exists()
    another = concept.capture(day="2026-11-05", approved_sha=head(), output=tmp_path / "other",
                              batch_id="once", fixture=str(fixture), state=state)
    assert another["status"] == "FAILED" and another["requests_dispatched"] == 0
    assert not (tmp_path / "other").exists()


def test_incomplete_member_set_stops_and_retains_pages(tmp_path):
    members = stocks(301)
    responses = [page(1, lists(1))] + [page(301, members[i:i + 100]) for i in (0, 100, 200)]
    process, receipt, target, _ = run(tmp_path, responses)
    assert process.returncode == 2 and receipt["status"] == "FAILED"
    assert len(list(target.glob("request-*.json"))) == 4
    failure = json.loads((target / "failure.json").read_text(encoding="utf-8"))
    assert "member_page_limit_exceeded" in failure["error"]
    assert not (target / "batch-result.json").exists()


def test_unconfirmed_page_cleanup_blocks_later_dispatch(monkeypatch, tmp_path):
    state, target = tmp_path / "state", tmp_path / "batch"
    state.mkdir()
    target.mkdir()
    monkeypatch.setattr(concept, "run_probe_worker_process", lambda *a, **k: {
        "ok": False, "cleanup_complete": False, "error_code": "CLEANUP_UNCONFIRMED"})
    task = {"output": str(target), "state": str(state), "batch_id": "test",
            "work_deadline": concept.time.perf_counter() + 20}
    with pytest.raises(RuntimeError, match="page_cleanup_incomplete"):
        concept.page_request(task, "list", 1, None, 0)
    assert (state / "cleanup-incomplete.json").exists()
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"responses": []}', encoding="utf-8")
    failure = concept.capture(day="2026-11-05", approved_sha=head(), output=tmp_path / "next",
                              batch_id="next", fixture=str(fixture), state=state)
    assert failure["status"] == "FAILED" and failure["requests_dispatched"] == 0
    assert not (tmp_path / "next").exists()


def test_late_unlock_never_admits_early_success_receipt(monkeypatch, tmp_path):
    clock = [100.0]
    monkeypatch.setattr(concept.time, "perf_counter", lambda: clock[0])
    def worker(task, *args, **kwargs):
        concept.write(Path(task["output"]) / "batch-result.json", {
            "status": "CAPTURE_COMPLETE", "batch_id": task["batch_id"], "requests_dispatched": 1})
        return {"ok": True, "cleanup_complete": True}
    monkeypatch.setattr(concept, "run_probe_worker_process", worker)
    original = Path.unlink
    def delayed_unlink(path, *args, **kwargs):
        if path.name == "active.lock":
            clock[0] = 190.001
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", delayed_unlink)
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"responses": []}', encoding="utf-8")
    target = tmp_path / "batch"
    result = concept.capture(day="2026-11-05", approved_sha=head(), output=target,
                             batch_id="batch", fixture=str(fixture), state=tmp_path / "state")
    assert result["status"] == "FAILED" and result["error"] == "batch_deadline_exceeded"
    assert json.loads((target / "execution.json").read_text(encoding="utf-8"))["status"] == "PASSED"
    report = tmp_path / "returned.json"
    report.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError, match="returned_report_not_confirmed"):
        concept.load_batch(target, report, concept.sha(report))


def test_real_process_timeout_cleans_up_and_stops(tmp_path):
    process, receipt, target, _ = run(tmp_path, [{"sleep": 12}], timeout=30)
    assert process.returncode == 2
    assert receipt["status"] == "FAILED"
    assert len(list(target.glob("request-*.json"))) == 1
    detail = json.loads((target / "process-0.json").read_text(encoding="utf-8"))
    assert detail["request_timed_out"] and detail["cleanup_complete"]
    assert not list(target.glob("raw-*.json"))


def test_dashboard_opens_only_explicit_fixture_batch_without_business_network(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.ui import dashboard, offline_observation
    from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard
    process, receipt, target, _ = run(tmp_path, [page(1, lists(1)), page(3, stocks(3))])
    assert process.returncode == 0, receipt
    user_store = tmp_path / "user-store"
    monkeypatch.setattr(offline_observation, "DEFAULT_STORE", user_store)
    guard = AppTestNetworkGuard(monkeypatch)
    monkeypatch.setattr(dashboard, "run_dashboard_action", guard.deny_legacy_action)
    monkeypatch.setattr(dashboard, "run_approved_action", guard.deny_legacy_action)
    app = AppTest.from_file(str(Path(dashboard.__file__)), default_timeout=15).run()
    next(x for x in app.radio if x.label == "工作区").set_value("捕获批次观察").run()
    assert any("尚未选择" in x.value for x in app.info)
    next(x for x in app.text_input if x.label == "批次目录").set_value(str(target))
    report = tmp_path / "batch-returned.json"
    next(x for x in app.text_input if x.label == "命令返回报告文件").set_value(str(report))
    next(x for x in app.text_input if x.label == "返回报告外部 SHA-256").set_value(concept.sha(report))
    next(x for x in app.button if x.label == "打开指定批次").click().run()
    assert not app.exception
    assert any("测试数据" in x.value for x in app.warning)
    assert any("原生事件时点未确认" in x.value for x in app.markdown)
    assert any("不代表全市场" in x.value for x in app.markdown)
    next(x for x in app.text_input if x.label == "返回报告外部 SHA-256").set_value("0" * 64)
    next(x for x in app.button if x.label == "打开指定批次").click().run()
    assert any("批次不可用" in x.value for x in app.error)
    assert any("尚未选择" in x.value for x in app.info)
    assert not any(guard.business_attempts.values())
    assert not user_store.exists()
