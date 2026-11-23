"""Test-only mutations/faults; never consume real samples or permissions."""
import contextlib
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from overnight_quant.data import ths_single_sample as ths
from overnight_quant.scripts import run_ths_single_sample as cli
from overnight_quant.tests.test_ths_single_sample import HEAD, fixture_file, run_cli, replay


def invoke(args):
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = cli.main(args)
    return code, json.loads(stream.getvalue())


def test_cli_failure_before_supervisor_is_known_zero(monkeypatch):
    monkeypatch.setattr(cli, "uuid4", lambda: (_ for _ in ()).throw(ValueError("before_start")))
    monkeypatch.setattr(cli, "run_probe_worker_process", lambda *a, **k: pytest.fail("controller started"))
    code, result = invoke(["--execute", "--launcher-start", str(cli.ENTRY_STARTED)])
    assert code == 2 and result["status"] == "FAILED"
    assert result["actual_http_requests"] == result["requests_attempted"] == 0
    assert result["supervisor_cleanup_complete"] is True


def test_process_creation_failure_is_known_zero_and_keeps_diagnostics(monkeypatch):
    from overnight_quant.data import probe_worker_process
    monkeypatch.setattr(probe_worker_process.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("test_only_create_failed")))
    code, result = invoke(["--execute", "--launcher-start", str(cli.ENTRY_STARTED)])
    assert code == 2 and result["actual_http_requests"] == result["requests_attempted"] == 0
    assert "test_only_create_failed" in result["error"]
    assert result["supervisor_cleanup_complete"] is True
    assert result["supervisor_process"]["pid"] is None


@pytest.mark.parametrize("mode", ["missing", "empty", "partial", "unknown_count", "runner_raised", "late", "source_failed_late"])
def test_real_controller_receipt_fault_never_resets_dispatch(tmp_path, monkeypatch, mode):
    fixture = fixture_file(tmp_path, **({"body": b"not-json"} if mode == "source_failed_late" else {}))
    runner = cli.run_probe_worker_process
    observed = {}
    def execute_then_fault(task, *args, **kwargs):
        outer = runner(task, *args, **kwargs)
        assert outer["ok"] and outer["pid"] > 0
        observed.update(outer["payload"])
        if mode == "missing":
            outer["payload"] = None
        elif mode == "empty":
            outer["payload"] = {}
        elif mode == "partial":
            outer["payload"].pop("actual_http_requests")
        elif mode == "unknown_count":
            outer["payload"]["actual_http_requests"] = None
        elif mode == "runner_raised":
            raise OSError("controller_return_lost")
        else:
            monkeypatch.setattr(cli, "time", SimpleNamespace(perf_counter=lambda: (
                _ for _ in ()).throw(ValueError("late_processing"))))
        return outer
    monkeypatch.setattr(cli, "ENTRY_STARTED", ths.time.perf_counter())
    monkeypatch.setattr(cli, "run_probe_worker_process", execute_then_fault)
    code, result = invoke(["--fixture", str(fixture), "--test-state", str(tmp_path / "state"),
        "--output", str(tmp_path / "batch"), "--target-date", "2026-11-20", "--approved-sha", HEAD,
        "--batch-id", "fault-test-only", "--launcher-start", str(cli.ENTRY_STARTED)])
    assert (tmp_path / "batch/intent.json").is_file()
    assert code == 2 and result["status"] == "FAILED"
    if mode == "runner_raised":
        assert result["supervisor_cleanup_complete"] is None
        assert (tmp_path / "state/cleanup-incomplete.json").is_file()
    else:
        assert result["supervisor_cleanup_complete"] is True
    if mode in ("missing", "empty", "runner_raised"):
        assert result["actual_http_requests"] is result["requests_attempted"] is None
    else:
        assert result["requests_attempted"] == observed["requests_attempted"] == 1
        if mode in ("partial", "unknown_count"):
            assert result["actual_http_requests"] is None
        else:
            assert result["actual_http_requests"] == observed["actual_http_requests"] == 0
    if mode == "source_failed_late":
        assert "JSONDecodeError" in result["error"]
        assert any("late_processing" in error for error in result["subsequent_errors"])


def test_launcher_late_failure_keeps_count_and_cleanup(tmp_path):
    run, result, output, returned = run_cli(tmp_path, fixture_file(tmp_path),
                                         extra=("-TestScenario", "SerializationOverrun"))
    assert run.returncode == 2 and result["status"] == "FAILED"
    assert result["requests_attempted"] == 1 and result["actual_http_requests"] == 0
    assert result["supervisor_cleanup_complete"] is True
    assert result["serialized_before_deadline"] is False
    verified = ths.verify(output, ths.digest((output / "completion.json").read_bytes()),
                          returned, ths.digest(returned.read_bytes()))
    assert verified["evidence_integrity_verified"] is True
    assert verified["capture_execution_result"] == "FAILED"


def test_launcher_secondary_failure_keeps_source_error(tmp_path):
    run, result, _, _ = run_cli(tmp_path, fixture_file(tmp_path, body=b"not-json"),
                                extra=("-TestScenario", "SerializationOverrun"))
    assert run.returncode == 2 and result["status"] == "FAILED"
    assert result["requests_attempted"] == 1 and result["actual_http_requests"] == 0
    assert "JSONDecodeError" in result["error"]
    assert "receipt_serialization_deadline" in result["subsequent_errors"]
    assert result["supervisor_cleanup_complete"] is True


@pytest.mark.parametrize("fault_file", ["response.json", "intent.json"])
def test_response_facts_survive_later_read_failure(tmp_path, monkeypatch, fault_file):
    original = Path.read_bytes
    failed_reads = []
    def fail_later(path):
        if path.name == fault_file:
            failed_reads.append(path.name)
            raise OSError("test_only_post_response_read_failure")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", fail_later)
    result = ths.capture(target="2026-11-20", head=HEAD, output=tmp_path / "batch",
        batch="response-facts-test-only", fixture=fixture_file(tmp_path, body=b"not-json"),
        state=tmp_path / "state")
    assert result["status"] == "FAILED"
    assert result["requests_attempted"] == 1 and result["actual_http_requests"] == 0
    assert "JSONDecodeError" in result["error"]
    assert result["cleanup_complete"] is True
    if fault_file == "response.json":
        assert failed_reads == []  # The successfully loaded bytes must be reused.
    else:
        assert failed_reads
        assert any("post_response_read_failure" in item for item in result["subsequent_errors"])


def test_launcher_blocks_unconfirmed_receipt_without_python_block(tmp_path):
    run, result, _, _ = run_cli(tmp_path, fixture_file(tmp_path), extra=("-TestScenario", "UnconfirmedReceipt"))
    assert run.returncode == 2 and result["status"] == "FAILED"
    assert result["requests_attempted"] == 1 and result["actual_http_requests"] == 0
    assert result["supervisor_cleanup_complete"] is False
    block = json.loads((tmp_path / "state/cleanup-incomplete.json").read_bytes())
    assert block["status"] == "LAUNCHER_CLEANUP_UNCONFIRMED"


@pytest.fixture(scope="module")
def success_sample(tmp_path_factory):
    path = tmp_path_factory.mktemp("ths-boundary-success-test-only")
    run, result, output, returned = run_cli(path, fixture_file(path))
    assert run.returncode == 0, result
    return output, returned


def changed_sample(tmp_path, success_sample, *, completed_offset=4.999, bad_field=None, bad_value=None):
    original, old_returned = success_sample
    output = tmp_path / "batch"
    shutil.copytree(original, output)
    def read(name):
        return json.loads((output / name).read_text(encoding="utf-8-sig"))
    def write(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    intent, response, execution, completion = [read(name) for name in (
        "intent.json", "response.json", "execution.json", "completion.json")]
    start = intent["started_monotonic"]
    work = start + 5
    intent.update(output=str(output.resolve()), work_deadline=work)
    response.update(started_monotonic=start + .4, response_completed_monotonic=start + 4.8,
                    raw_sealed_monotonic=start + 4.9, completed_monotonic=start + completed_offset)
    process = execution["process"]
    process.update(io_deadline_monotonic=work, hard_deadline_monotonic=work + 1,
                   cleanup_deadline_monotonic=work + .5)
    if bad_field:
        group, key = bad_field.split(".")
        {"intent": intent, "response": response, "process": process}[group][key] = bad_value
    write("intent.json", intent)
    write("response.json", response)
    execution.update(intent_sha256=ths.digest((output / "intent.json").read_bytes()),
                     response_sha256=ths.digest((output / "response.json").read_bytes()))
    write("execution.json", execution)
    completion.update(execution_sha256=ths.digest((output / "execution.json").read_bytes()),
                      checked_monotonic=start + 6.5, execution_elapsed_ms=((start + 6.5) - start) * 1000)
    write("completion.json", completion)
    anchor = ths.digest((output / "completion.json").read_bytes())
    python_result = read("python-result.json")
    python_result.update(output=str(output.resolve()), completion_sha256=anchor,
                         intent_sha256=execution["intent_sha256"], response_sha256=execution["response_sha256"])
    write("python-result.json", python_result)
    returned = json.loads(old_returned.read_bytes().decode("utf-8-sig"))
    returned.update(output=str(output.resolve()), completion_sha256=anchor,
                    launcher_report_sha256=ths.digest((output / "python-result.json").read_bytes()))
    returned_path = tmp_path / "returned.json"
    returned_path.write_text(json.dumps(returned), encoding="utf-8")
    return output, anchor, returned_path


@pytest.mark.parametrize("offset,expected", [(4.999, "PASSED"), (5, "FAILED"), (5.001, "FAILED")])
def test_absolute_work_deadline_replayed_not_relative_budget(tmp_path, success_sample, offset, expected):
    output, anchor, returned = changed_sample(tmp_path, success_sample, completed_offset=offset)
    result = ths.verify(output, anchor, returned, ths.digest(returned.read_bytes()))
    assert result["evidence_integrity_verified"] is True
    assert result["capture_execution_result"] == expected
    assert result["execution_time_verified"] is (expected == "PASSED")


@pytest.mark.parametrize("field,value", [
    ("intent.work_deadline", float("nan")), ("intent.work_deadline", float("inf")),
    ("intent.work_deadline", True), ("intent.work_deadline", "105"),
    ("intent.work_deadline", None), ("intent.execution_deadline_monotonic", 0),
    ("response.completed_monotonic", float("inf")),
    ("process.io_deadline_monotonic", 1), ("process.hard_deadline_monotonic", float("nan")),
    ("process.cleanup_deadline_monotonic", float("inf")),
    ("process.request_timed_out", True), ("process.cleanup_complete", False),
])
def test_invalid_or_inconsistent_absolute_stage_fails_closed(tmp_path, success_sample, field, value):
    output, anchor, returned = changed_sample(tmp_path, success_sample, bad_field=field, bad_value=value)
    result = ths.verify(output, anchor, returned, ths.digest(returned.read_bytes()))
    assert result["evidence_integrity_verified"] is True
    assert result["capture_execution_result"] == "FAILED"
    assert result["execution_time_verified"] is False
    assert result["execution_validation_errors"]


def test_legal_failed_sample_still_has_integrity(tmp_path):
    run, result, output, returned = run_cli(tmp_path, fixture_file(tmp_path, body=b"not-json"))
    assert run.returncode == 2
    verified = replay(result, output, returned)
    assert verified["evidence_integrity_verified"] is True
    assert verified["capture_execution_result"] == "FAILED"
