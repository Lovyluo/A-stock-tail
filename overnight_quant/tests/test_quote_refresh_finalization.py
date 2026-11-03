"""Deterministic terminal-state faults; no real requests or timeout sleeps."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from overnight_quant.data import offline_price_observation as obs
from overnight_quant.data import quote_observation_refresh as refresh
from overnight_quant.tests.test_manual_quote_observation import fixture_file, local_runner, store


class Clock:
    value = 100.0
    def __call__(self):
        return self.value


def setup_controller(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(refresh.time, "perf_counter", clock)
    calls = []
    def runner(*args, **kwargs):
        calls.append(1)
        return local_runner(*args, **kwargs)
    monkeypatch.setattr(refresh, "run_probe_worker_process", runner)
    active = store(tmp_path)
    controller = refresh.RefreshController(active, fixture=fixture_file(tmp_path))
    return active, controller, clock, calls


def outcome(active, result):
    directory = Path(result["output"])
    response = obs._json((directory / "response.json").read_bytes())
    return refresh.committed_refresh_outcome(active.root, directory.name, obs.stable_hash(response))


def test_unlock_crosses_ten_seconds_same_counterexample(tmp_path, monkeypatch, record_property):
    active, controller, clock, calls = setup_controller(tmp_path, monkeypatch)
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path.name == "active.lock":
            clock.value = 110.001
        return result
    monkeypatch.setattr(Path, "unlink", unlink)
    result = controller.refresh(refresh.CODES)
    directory = Path(result["output"])
    receipt = obs._json((directory / "receipt.json").read_bytes())
    assert result["status"] == outcome(active, result)["status"] == "FAILED"
    assert receipt["status"] == "CANDIDATE_SUCCEEDED"
    assert not active.refresh_snapshots() and not (directory / "final-commit.json").exists()
    assert result["elapsed_ms"] == pytest.approx(10001)
    assert len(calls) == 1 and controller.refresh(refresh.CODES)["requests_made"] == 0
    record_property("same_counterexample", json.dumps(dict(returned=result["status"],
                    terminal=outcome(active, result)["status"], receipt=receipt["status"], admitted=0,
                    clock=clock.value, real_requests=0)))


@pytest.mark.parametrize("fault", ["unlock_error", "final_write_error", "final_write_late",
    "commit_write_error", "commit_write_late", "publication_error", "publication_late", "confirmation_late"])
def test_terminal_faults_preserve_originals_and_reject_candidate(tmp_path, monkeypatch, fault):
    active, controller, clock, calls = setup_controller(tmp_path, monkeypatch)
    write = obs.write_once
    unlink = Path.unlink
    link = refresh.os.link
    read_outcome = refresh.committed_refresh_outcome
    def injected_write(path, value):
        path = Path(path)
        if fault == "final_write_error" and path.name == "final-result.json":
            raise OSError("fixture final write failed")
        if fault == "commit_write_error" and path.name == "final-commit-candidate.json":
            raise OSError("fixture commit write failed")
        result = write(path, value)
        if ((fault == "final_write_late" and path.name == "final-result.json")
                or (fault == "commit_write_late" and path.name == "final-commit-candidate.json")):
            clock.value = 110.001
        return result
    def injected_unlink(path, *args, **kwargs):
        if fault == "unlock_error" and path.name == "active.lock":
            raise OSError("fixture unlink denied")
        return unlink(path, *args, **kwargs)
    def injected_link(src, dst, **kwargs):
        if fault == "publication_error":
            raise OSError("fixture atomic publication denied")
        result = link(src, dst, **kwargs)
        if fault == "publication_late":
            clock.value = 110.001
        return result
    def injected_outcome(*args):
        result = read_outcome(*args)
        if fault == "confirmation_late" and result["status"] == "SUCCEEDED":
            clock.value = 110.001
        return result
    monkeypatch.setattr(obs, "write_once", injected_write)
    monkeypatch.setattr(Path, "unlink", injected_unlink)
    monkeypatch.setattr(refresh.os, "link", injected_link)
    monkeypatch.setattr(refresh, "committed_refresh_outcome", injected_outcome)
    result = controller.refresh(refresh.CODES)
    directory = Path(result["output"])
    raw = (directory / "response-received.json").read_bytes()
    final = read_outcome(active.root, directory.name, obs.stable_hash(obs._json((directory / "response.json").read_bytes())))
    assert result["status"] == final["status"] == "FAILED"
    assert not active.refresh_snapshots() and len(calls) == 1
    assert hashlib.sha256((directory / "response-received.json").read_bytes()).digest() == hashlib.sha256(raw).digest()
    assert obs._json((directory / "receipt.json").read_bytes())["status"] == "CANDIDATE_SUCCEEDED"
    assert controller.refresh(refresh.CODES)["requests_made"] == 0


def test_normal_final_commit_and_later_failure_keep_good_snapshot_and_settings(tmp_path, monkeypatch):
    active, controller, clock, calls = setup_controller(tmp_path, monkeypatch)
    revision = active.save_settings(refresh.CODES, {"SZ:000001": obs.make_rule("000001", upper=11)}, ["SZ:000001"])
    settings = active.settings()
    first = controller.refresh(refresh.CODES)
    assert first["status"] == outcome(active, first)["status"] == "SUCCEEDED"
    original = active.refresh_snapshots()
    assert len(original) == 1
    clock.value = 200.0
    mono = refresh.time.monotonic
    monkeypatch.setattr(refresh.time, "monotonic", lambda: mono() + 20)
    unlink = Path.unlink
    def late_unlink(path, *args, **kwargs):
        result = unlink(path, *args, **kwargs)
        if path.name == "active.lock":
            clock.value = 210.001
        return result
    monkeypatch.setattr(Path, "unlink", late_unlink)
    second = controller.refresh(refresh.CODES)
    assert second["status"] == "FAILED" and len(calls) == 2
    assert active.refresh_snapshots() == original and active.settings() == settings
    assert revision in settings
    assert len(active.refresh_snapshots()) < 2  # Failed second batch cannot enter page comparison.


def test_reported_total_includes_final_commit_write(tmp_path, monkeypatch):
    active, controller, clock, _ = setup_controller(tmp_path, monkeypatch)
    write = obs.write_once
    def slow_commit(path, body):
        result = write(path, body)
        if Path(path).name == "final-commit-candidate.json":
            clock.value = 101.25
        return result
    monkeypatch.setattr(obs, "write_once", slow_commit)
    result = controller.refresh(refresh.CODES)
    assert result["status"] == outcome(active, result)["status"] == "SUCCEEDED"
    assert result["elapsed_ms"] == 1250 and result["elapsed_before_final_write_ms"] == 0
    assert len(active.refresh_snapshots()) == 1


@pytest.mark.parametrize("material", ["final-commit.json", "final-result.json"])
def test_missing_or_damaged_final_commit_is_not_early_receipt_success(tmp_path, monkeypatch, material):
    active, controller, _, _ = setup_controller(tmp_path, monkeypatch)
    result = controller.refresh(refresh.CODES)
    assert active.refresh_snapshots()
    directory = Path(result["output"])
    (directory / material).write_text("{incomplete", encoding="utf-8")
    assert not active.refresh_snapshots() and outcome(active, result)["status"] == "FAILED"


def test_interruption_before_publication_leaves_no_success(tmp_path, monkeypatch):
    active, controller, _, _ = setup_controller(tmp_path, monkeypatch)
    monkeypatch.setattr(refresh.os, "link", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        controller.refresh(refresh.CODES)
    assert not active.refresh_snapshots()
    assert list(controller.state.glob("pending-finalization-*.json"))
    assert controller.refresh(refresh.CODES)["requests_made"] == 0


def test_abrupt_process_exit_does_not_publish_private_commit(tmp_path):
    from overnight_quant.data.probe_worker_process import run_probe_worker_process
    active = store(tmp_path / "isolated")
    fixture = fixture_file(tmp_path)
    result = run_probe_worker_process(dict(store=str(tmp_path / "isolated"), fixture=str(fixture)),
        10000, bounded_cleanup_ms=500, retain_streams=True,
        worker_command=[sys.executable, "-m", "overnight_quant.tests.fixtures.quote_refresh_interrupt"])
    assert result["returncode"] == 19 and result["cleanup_complete"], result
    assert not active.refresh_snapshots()
    assert not list((active.root / refresh.VERSION).rglob("final-commit.json"))
    assert list((active.root / refresh.VERSION).rglob("final-commit-candidate.json"))
    assert list((active.root / refresh.VERSION / "control").glob("pending-finalization-*.json"))
    controller = refresh.RefreshController(active, fixture=fixture)
    assert "incomplete_finalization_block" in controller.refresh(refresh.CODES)["error"]


def test_late_publication_with_failed_failure_receipt_keeps_lock_veto(tmp_path, monkeypatch):
    active, controller, clock, _ = setup_controller(tmp_path, monkeypatch)
    link, write = refresh.os.link, obs.write_once
    def late(src, dst, **kwargs):
        result = link(src, dst, **kwargs)
        clock.value = 110.001
        return result
    def denied(path, body):
        if Path(path).name == "final-failure.json":
            raise OSError("failure receipt denied")
        return write(path, body)
    monkeypatch.setattr(refresh.os, "link", late)
    monkeypatch.setattr(obs, "write_once", denied)
    result = controller.refresh(refresh.CODES)
    assert result["status"] == "FAILED" and not active.refresh_snapshots()
    assert (controller.state / "active.lock").exists()


def test_page_cached_success_cannot_bypass_missing_commit(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from overnight_quant.tests.fixtures.apptest_network_guard import AppTestNetworkGuard
    from overnight_quant.ui import offline_observation as ui
    from overnight_quant.ui import quote_observation_refresh as controls
    active = store(tmp_path)
    controller = refresh.RefreshController(active, fixture=fixture_file(tmp_path))
    monkeypatch.setattr(refresh, "run_probe_worker_process", local_runner)
    monkeypatch.setattr(ui, "DEFAULT_STORE", tmp_path)
    renderer = controls.render_refresh
    monkeypatch.setattr(ui, "render_refresh", lambda st, store, state, prefix, **kwargs:
                        renderer(st, store, state, prefix, controller=controller))
    guard = AppTestNetworkGuard(monkeypatch)
    app = AppTest.from_file(str(Path(ui.__file__).with_name("dashboard.py")), default_timeout=15).run()
    next(x for x in app.radio if x.label == "材料空间").set_value("测试材料 · test-only").run()
    next(x for x in app.radio if x.label == "观察方式").set_value("主动刷新观察").run()
    next(x for x in app.button if x.label == "刷新一次报价").click().run()
    assert not app.exception and any("本次报价已保存" in x.value for x in app.success)
    snapshot = active.refresh_snapshots()[0]
    (active.root / refresh.VERSION / "attempts" / snapshot["batch_id"] / "final-commit.json").unlink()
    app.run()
    assert not app.exception and not any("本次报价已保存" in x.value for x in app.success)
    assert any("final_commit_missing_or_invalid" in x.value for x in app.error)
    assert not active.refresh_snapshots() and not any(guard.business_attempts.values())
