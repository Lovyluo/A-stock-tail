"""Test-only local approval evidence through the real limited quote entry points."""
from datetime import timedelta
from pathlib import Path
import json
import time

import pytest

from overnight_quant.data import quote_observation_permit as permit
from overnight_quant.data import quote_observation_refresh as refresh
from overnight_quant.tests.test_limited_quote_observation import POOL, SELECTION, approval, controller, target
from overnight_quant.tests.test_manual_quote_observation import NOW, local_runner
from overnight_quant.tests.test_manual_quote_selection import fixture


def write_permit(path, body):
    path.write_bytes(json.dumps(body).encode("utf-8"))
    return path, permit._sha(path.read_bytes())


def local_permit(tmp_path):
    path, digest = approval(tmp_path, NOW)
    legacy, _ = permit.read_permit(path, digest, test_only=True)
    original = tmp_path / "test-only-original.utf8"
    raw = ("  TEST_ONLY \u4ec5\u79bb\u7ebf\U0001f4dd\r\n\u672a\u6388\u6743\n"
           + legacy["approved_sha"] + "\n").encode("utf-8")
    original.write_bytes(raw)
    anchor = permit._sha(raw)
    local = {**legacy, "schema": permit.LOCAL_PERMIT_SCHEMA, "approval_reference": {
        "kind": permit.LOCAL_REFERENCE_KIND,
        "thread_id": "00000000-0000-7000-8000-000000000001",
        "message_id": "00000000-0000-7000-8000-000000000002",
        "verbatim_quote": raw.decode("utf-8"),
        "quote_sha256": anchor,
        "local_original_path": str(original),
        "local_original_sha256": anchor,
        "operator_check": {"status": "MATCHED_CURRENT_USER_MESSAGE", "operator": "test-only",
                           "checked_at": NOW.isoformat()},
    }}
    return local, original, legacy


def opened(tmp_path, body):
    base = controller(tmp_path, NOW)
    source = write_permit(tmp_path / "local-permit.json", body)
    return refresh.RefreshController(base.store, fixture=base.fixture, contract=permit.LIMITED_VERSION,
                                     test_permit=source)


@pytest.mark.parametrize("change,reason", [
    ("missing_file", "FileNotFoundError"),
    ("unreadable_file", "PermissionError"),
    ("tampered_file", "local_original_mismatch"),
    ("quote_mismatch", "approval_reference_invalid"),
    ("both_hashes_reanchored", "local_original_mismatch"),
    ("wrong_file_hash", "local_original_mismatch"),
    ("invalid_message_id", "local_message_reference_invalid"),
    ("old_approval_new_code", "local_approval_code_unbound"),
    ("identical_ids", "local_message_reference_invalid"),
    ("missing_operator", "local_operator_check_invalid"),
    ("surrogate", "invalid_utf8"),
    ("wrong_version", "approval_reference_invalid"),
    ("old_reference_in_v2", "local_message_reference_invalid"),
    ("repo_file", "local_original_path_invalid"),
    ("expired", "outside_window"),
    ("future_check", "approval_check_after_evaluation"),
])
def test_bad_local_reference_or_window_never_dispatches(tmp_path, monkeypatch, change, reason):
    body, original, legacy = local_permit(tmp_path)
    ref = body["approval_reference"]
    stamp = NOW
    if change == "missing_file":
        original.unlink()
    elif change == "unreadable_file":
        open_file = Path.open

        def denied(path, *args, **kwargs):
            if path == original:
                raise PermissionError("fixture original cannot be read")
            return open_file(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
    elif change == "tampered_file":
        original.write_bytes(original.read_bytes() + b"\n")
    elif change in {"quote_mismatch", "both_hashes_reanchored"}:
        ref["verbatim_quote"] += " "
        if change == "both_hashes_reanchored":
            ref["quote_sha256"] = ref["local_original_sha256"] = permit._sha(ref["verbatim_quote"].encode("utf-8"))
    elif change == "wrong_file_hash":
        ref["local_original_sha256"] = "f" * 64
    elif change == "invalid_message_id":
        ref["message_id"] = "not-a-codex-message-id"
    elif change == "old_approval_new_code":
        body["approved_sha"] = "b" * 40
    elif change == "identical_ids":
        ref["thread_id"] = ref["message_id"]
    elif change == "missing_operator":
        ref["operator_check"].pop("operator")
    elif change == "surrogate":
        ref["verbatim_quote"] += "\ud800"
    elif change == "wrong_version":
        body["schema"] = permit.PERMIT_SCHEMA
    elif change == "old_reference_in_v2":
        body["approval_reference"] = legacy["approval_reference"]
    elif change == "repo_file":
        (tmp_path / ".git").mkdir()
    elif change == "expired":
        stamp = permit._time(body["window_end"])
    elif change == "future_check":
        ref["operator_check"]["checked_at"] = (NOW + timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(refresh, "utc_now", lambda: stamp)
    monkeypatch.setattr(refresh, "run_probe_worker_process", lambda *a, **k: pytest.fail("worker dispatched"))
    control = opened(tmp_path, body)
    result = control.refresh(POOL, selection=SELECTION)
    assert result["status"] == "REJECTED" and result["requests_made"] == 0 and reason in result["error"], result
    assert not list(control.state.glob("limited-slot-*"))


def test_local_reference_does_not_skip_configured_code_gate(tmp_path, monkeypatch):
    body, _, _ = local_permit(tmp_path)
    path, digest = write_permit(tmp_path / "local-permit.json", body)
    read = permit.read_permit
    with pytest.raises(ValueError, match="limited_permit_invalid"):
        read(path, digest)  # A TEST_ONLY approval cannot enter the real scope.
    calls = []

    def fixture_read(path, digest):
        calls.append(path)
        return read(path, digest, test_only=True)

    monkeypatch.setattr(permit, "read_permit", fixture_read)
    monkeypatch.setattr(permit, "STARTUP_HEAD", "b" * 40)
    monkeypatch.setenv(permit.LIMITED_SWITCH, "1")
    monkeypatch.setenv(permit.PERMIT_FILE, str(path))
    monkeypatch.setenv(permit.PERMIT_SHA, digest)
    with pytest.raises(ValueError, match="limited_permit_code_mismatch"):
        permit.configured_permit(stamp=NOW)
    assert calls == [str(path)]


def test_local_bytes_replayed_and_format_swap_cannot_reset_quota(tmp_path, monkeypatch):
    body, original, legacy = local_permit(tmp_path)
    clock = [NOW]
    monkeypatch.setattr(refresh, "utc_now", lambda: clock[0])
    calls = []

    def worker(task, *args, **kwargs):
        assert task["synthetic_test_only"]
        calls.append(task)
        return local_runner(task, *args, **kwargs)

    monkeypatch.setattr(refresh, "run_probe_worker_process", worker)
    control = opened(tmp_path, body)
    read, _ = permit.read_permit(*control.test_permit, test_only=True)
    assert read["approval_reference"]["verbatim_quote"].encode("utf-8") == original.read_bytes()
    first = control.refresh(POOL, selection=SELECTION)
    assert first["status"] == "SUCCEEDED", first
    ticks = time.monotonic
    monkeypatch.setattr(refresh.time, "monotonic", lambda: ticks() + 11)
    clock[0] += timedelta(seconds=11)
    alternate = refresh.RefreshController(control.store, fixture=control.fixture, contract=permit.LIMITED_VERSION,
        test_permit=write_permit(tmp_path / "legacy.json", legacy))
    assert "limited_permit_id_reused" in alternate.refresh(POOL, selection=SELECTION)["error"]
    fixture(control.fixture, ["000002"], event=clock[0] - timedelta(seconds=2))
    second = control.refresh(POOL, selection=SELECTION)
    assert second["status"] == "SUCCEEDED", second
    monkeypatch.setattr(refresh.time, "monotonic", lambda: ticks() + 22)
    clock[0] += timedelta(seconds=11)
    assert "limited_permit_quota_exhausted" in control.refresh(POOL, selection=SELECTION)["error"]
    assert len(calls) == 2
    directory = target(control, first)
    final_sha = permit._sha((directory / "final-confirmation.json").read_bytes())
    args = (control.store.root, first["batch_id"], control.test_permit[1], final_sha)
    assert permit.verify_batch(*args, test_only=True)["status"] == "OBSERVATION_VERIFIED"
    original.write_bytes(original.read_bytes() + b" ")
    with pytest.raises(ValueError, match="local_original_mismatch"):
        permit.verify_batch(*args, test_only=True)
