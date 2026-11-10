from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from overnight_quant.data import concept_batch_observation as concept
from overnight_quant.data.probe_worker_process import encode_bounded_worker_input, run_probe_worker_process


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures/concept_utf8_ipc"
STAMP = datetime(2030, 1, 2, 10, 1, tzinfo=concept.CN_TZ)
BATCH = "utf8-offline-fixture"
FAKE_SHA = "a" * 40
QUOTE = "\u6211\u540c\u610f\u4e2d\u6587\u7ed3\u6784\u9a8c\u8bc1\U0001f680"


def read(path):
    return json.loads(path.read_bytes())


def permit_for(output, quote=QUOTE):
    return {
        "schema_version": concept.AUTH_VERSION, "status": "USER_APPROVED_LIMITED_EXPERIMENT",
        "single_use": True, "user_approved": True, "user_acknowledges_unknown_basis": True,
        "source_use_basis": "UNKNOWN", "known_use_constraint": "NO_KNOWN_PROHIBITION",
        "access_control_bypass": False, "purpose": concept.AUTH_PURPOSE, "scope": dict(concept.AUTH_SCOPE),
        "approval_evidence": {"thread_id": "fixture-thread", "message_id": "fixture-message",
                              "verbatim_quote": quote, "quote_sha256": "0" * 64},
        "run_date": STAMP.date().isoformat(), "approved_sha": FAKE_SHA, "contract_hash": concept.CONTRACT,
        "batch_id": BATCH, "output": str(output), "budget": dict(concept.BUDGET),
        "window_start": (STAMP - timedelta(minutes=1)).isoformat(),
        "window_end": (STAMP + timedelta(minutes=9)).isoformat(),
    }


def launch(tmp_path, quote=QUOTE, *, malformed=False, escaped=False):
    root = tmp_path / "isolated"
    script = root / "overnight_quant/scripts/run_concept_batch_manual.ps1"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "overnight_quant/scripts" / script.name, script)
    state = root / "overnight_quant/data/cache/concept-batch-observation"
    output = state / BATCH
    permit = permit_for(output, quote)
    if not any(0xD800 <= ord(c) <= 0xDFFF for c in quote):
        permit["approval_evidence"]["quote_sha256"] = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    data = json.dumps(permit, ensure_ascii=escaped).encode("utf-8")
    if malformed:
        data = data.replace(QUOTE.encode("utf-8"), b"\xff")
    auth = root / "permit.json"
    auth.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(map(str, (FIXTURE, ROOT))),
           "CONCEPT_UTF8_FIXTURE_ROOT": str(root), "PYTHONIOENCODING": "gbk:surrogateescape"}
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-Network", "-Day", permit["run_date"], "-ApprovedSha", FAKE_SHA,
         "-Output", str(output), "-BatchId", BATCH, "-Authorization", str(auth), "-AuthorizationSha", digest,
         "-PythonPath", sys.executable], cwd=root, env=env, capture_output=True, timeout=45)
    assert not (root / "network-attempt.txt").exists()
    assert not (state / "launcher.lock").exists() and not (state / "active.lock").exists()
    assert not (state / "cleanup-incomplete.json").exists()
    return proc, root, output, permit, digest


def test_real_launcher_preserves_chinese_and_non_bmp_across_workers(tmp_path):
    proc, root, output, permit, digest = launch(tmp_path)
    assert proc.returncode == 0, read(output / "process-0.json") if (output / "process-0.json").exists() else proc.stdout
    launch_report = json.loads(proc.stdout)
    quote_sha = permit["approval_evidence"]["quote_sha256"]
    capture = read(root / "capture.json")
    assert capture["file_sha256"] == capture["supplied_sha256"] == digest
    for role in ("capture", "coordinate", "page-0", "page-1"):
        trace = read(root / (role + ".json"))
        assert trace["quote_sha256"] == quote_sha and trace["quote_bytes"] == len(QUOTE.encode("utf-8"))
        assert (trace["stdin_encoding"], trace["stdin_errors"]) == ("utf-8", "strict")
        if role.startswith("page"):
            assert trace["wire_quote"]["quote_sha256"] == quote_sha
    intent = read(output / "intent.json")
    assert intent["authorization"]["approval_evidence"]["verbatim_quote"] == QUOTE
    assert intent["authorization_sha"] == digest
    report = read(output / "returned-report.json")
    assert report["process"]["tree_empty_observed"] and report["supervisor_cleanup_complete"]
    assert len(list(root.glob("transport-*.json"))) == launch_report["requests_dispatched"] == 2
    for index in range(2):
        page = read(output / f"process-{index}.json")
        assert page["cleanup_complete"] and page["tree_empty_observed"]
        raw = read(output / f"raw-{index}.json")
        assert raw["experiment_permission"]["approval_evidence"]["quote_sha256"] == quote_sha
    replay = concept.load_batch(output, output / "returned-report.json", launch_report["report_sha256"],
                                launch_report["confirmation_sha256"])
    assert replay["experiment_permission"]["approval_evidence"]["quote_sha256"] == quote_sha
    assert replay["records"] == replay["candidates"] == replay["tickets"] == replay["orders"] == []


@pytest.mark.parametrize(("quote", "malformed", "escaped", "reason"), [
    (QUOTE, True, False, "authorization_file_invalid_utf8"),
    ("\udc8e", False, True, "authorization_approval_quote_invalid_unicode"),
    ("\u7532" * 1365 + "xy", False, True, "authorization_approval_quote_exceeds_4096_bytes"),
    ("\u7532" * 1365 + "x", False, True, "bounded_worker_input_exceeds_4096_bytes"),
])
def test_real_launcher_rejects_damaged_or_oversize_permit_without_dispatch(tmp_path, quote, malformed, escaped, reason):
    proc, root, output, _, _ = launch(tmp_path, quote, malformed=malformed, escaped=escaped)
    assert proc.returncode == 2
    result = json.loads(proc.stdout)
    assert result["status"] == "FAILED" and result["error"] == "ValueError:" + reason
    assert result["requests_dispatched"] == 0 and result["request_count_status"] == "KNOWN_ZERO"
    assert not output.exists() and not list(root.glob("transport-*.json"))
    assert not (root / "coordinate.json").exists()
    assert not list(root.glob("page-*.json"))


@pytest.mark.parametrize(("wire", "reason"), [
    (b'{"value":"\xff"}', "bounded_worker_input_invalid_utf8"),
    (br'{"value":"\ud800"}', "bounded_worker_input_invalid_unicode"),
    (b"{}" + b" " * 4095, "bounded_worker_input_exceeds_4096_bytes"),
    (b"{}" + b" " * 4094, "worker_contract_mismatch"),
])
def test_actual_worker_strictly_decodes_and_bounds_stdin(wire, reason):
    proc = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_concept_batch_worker"],
                          input=wire, capture_output=True, timeout=10,
                          env={**os.environ, "PYTHONIOENCODING": "gbk:surrogateescape"})
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"] == "ValueError:" + reason


def test_worker_encoder_uses_complete_utf8_task_size():
    task = {"value": "\u7532" * 1360 + "abc"}
    assert len(encode_bounded_worker_input(task)) == 4096
    task["value"] += "x"
    with pytest.raises(ValueError, match="bounded_worker_input_exceeds_4096_bytes"):
        encode_bounded_worker_input(task)


def test_strict_worker_output_rejects_invalid_utf8_after_cleanup():
    result = run_probe_worker_process(
        {}, 3000, bounded_cleanup_ms=500, strict_utf8=True, retain_streams=True,
        worker_command=[sys.executable, "-c",
                        "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'\\xff')"])
    assert not result["ok"] and result["error"] == "worker_output_invalid_utf8"
    assert result["cleanup_complete"] and result["tree_empty_observed"] and result["stdout"] == ""
