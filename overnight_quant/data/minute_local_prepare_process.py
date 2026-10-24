"""Specialized per-wave READY/GO controller using the existing tree/pipe cleanup."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time

from . import minute_local_prepare_contract as contract
from .minute_capture_worker import digest
from hashlib import sha256
from .probe_worker_process import _ProcessScope, _PipeDrain, _bounded_cleanup, _decode_worker_output, _resume_suspended_primary


def run_prepared_wave(task, *, worker_command=None, cancel_event=None, before_go=None, _test=None,
                      contract_profile=None):
    profile = contract_profile or contract
    task = dict(task)
    profile.check_task(task)
    if os.name != "nt":
        raise ValueError("prepared_parent_death_contract_requires_windows_job")
    command = worker_command or [sys.executable, "-m", getattr(profile, "WORKER_MODULE", "overnight_quant.data.minute_local_prepare_worker")]
    entered = time.perf_counter()
    started = task["preparation_started_monotonic"]
    target = task["target_monotonic"]
    process = scope = drain = None
    ready = None
    ready_checked = go_sent = None
    ready_job_verified = False
    startup_ms = None
    output_limit = getattr(profile, "MAX_OUTPUT_BYTES", None)
    frozen_output = None
    output_freeze_ms = None
    output_frozen_in_work = False
    errors, seen_lines = [], 0
    stage, timed_out = "preparation", False

    def observe():
        nonlocal seen_lines, ready, ready_checked, ready_job_verified
        received = drain.step()
        if getattr(drain, "output_limit_exceeded", False):
            raise ValueError("prepared_total_output_limit_partial_material_retained")
        text = drain.text("stdout")
        lines = text.splitlines(keepends=True)
        complete = [value for value in lines if value.endswith("\n")]
        for raw in complete[seen_lines:]:
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("execution_worker_version") != profile.WORKER_VERSION:
                raise ValueError("prepared_worker_output_version_invalid")
            if value.get("kind") == "READY_LOCAL_ONLY":
                if ready is not None:
                    raise ValueError("prepared_duplicate_READY")
                if type(value.get("pid")) is not int or not scope.contains(value["pid"]):
                    raise ValueError("prepared_READY_pid_not_in_owned_job")
                profile.check_ready(value, task, value["pid"])
                ready_job_verified = True
                ready, ready_checked = value, time.perf_counter()
                if ready_checked >= task["preparation_work_deadline"]:
                    raise TimeoutError("prepared_READY_parent_validation_late")
            if value.get("ok") is False:
                raise ValueError("prepared_child_rejected:" + str(value.get("error")))
        seen_lines = len(complete)
        return received

    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise ValueError("prepared_parent_cancelled")

    try:
        if task["parent_pid"] != os.getpid() or entered < target - 5 or entered < started:
            raise ValueError("prepared_dispatch_before_T_minus_5_or_wrong_parent")
        if entered >= task["preparation_work_deadline"]:
            raise TimeoutError("prepared_no_remaining_local_work_budget")
        payload = (json.dumps(task, ensure_ascii=True) + "\n").encode()
        if len(payload) > 4096:
            raise ValueError("prepared_task_exceeds_4096_bytes")
        cancelled()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW | 0x4)  # CREATE_SUSPENDED
        scope = _ProcessScope(process, kill_on_parent_close=True)
        drain = _PipeDrain(process, max_output_bytes=output_limit) if output_limit is not None else _PipeDrain(process)
        _resume_suspended_primary(process)
        startup_ms = (time.perf_counter() - started) * 1000
        task["launch_root_pid"] = process.pid
        payload = (json.dumps(task, ensure_ascii=True) + "\n").encode()
        if len(payload) > 4096:
            raise ValueError("prepared_task_exceeds_4096_bytes")
        process.stdin.write(payload)
        while ready is None:
            cancelled()
            observe()
            if process.poll() is not None:
                raise ValueError("prepared_worker_exited_before_READY")
            if time.perf_counter() >= task["preparation_work_deadline"]:
                raise TimeoutError("prepared_READY_timeout")
            time.sleep(.001)
        stage = "ready_wait"
        # Test-only fault injection never changes the task's absolute deadlines.
        controls = _test or {}
        release = target + controls.get("go_offset_ms", 0) / 1000
        while time.perf_counter() < release:
            cancelled()
            observe()
            if process.poll() is not None:
                raise ValueError("prepared_worker_exited_waiting_for_GO")
            if time.perf_counter() >= task["io_deadline_monotonic"]:
                raise TimeoutError("prepared_GO_remaining_budget_exhausted")
            time.sleep(min(.002, max(0, release - time.perf_counter())))
        if time.perf_counter() >= task["io_deadline_monotonic"]:
            raise TimeoutError("prepared_GO_remaining_budget_exhausted")
        cancelled()
        if before_go is not None:
            before_go()
        go_sent = time.perf_counter()
        if go_sent >= task["io_deadline_monotonic"]:
            raise TimeoutError("prepared_GO_remaining_budget_exhausted")
        go = profile.go_message(task, go_sent)
        if controls.get("wrong_go_identity"):
            go["task_hash"] = "0" * 64
        message = (json.dumps(go) + "\n").encode()
        if controls.get("duplicate_go"):
            message += message
        process.stdin.write(message)
        if not controls.get("keep_input_open"):
            process.stdin.close()
        stage = "network_and_normal_exit"
        while time.perf_counter() < task["io_deadline_monotonic"]:
            cancelled()
            received = observe()
            if process.poll() is not None and all(drain.eof.values()) and scope.empty():
                if time.perf_counter() >= task["io_deadline_monotonic"]:
                    raise TimeoutError("prepared_normal_exit_validation_after_work_deadline")
                break
            if not received:
                time.sleep(min(.002, max(0, task["io_deadline_monotonic"] - time.perf_counter())))
        else:
            raise TimeoutError("prepared_absolute_work_deadline_exceeded")
        if output_limit is not None:
            freeze_started = time.perf_counter()
            stdout, stderr = drain.text("stdout"), drain.text("stderr")
            frozen_output = (stdout, stderr, _decode_worker_output(stdout),
                             sha256(stdout.encode()).hexdigest(), sha256(stderr.encode()).hexdigest())
            output_freeze_ms = (time.perf_counter() - freeze_started) * 1000
            if time.perf_counter() >= task["io_deadline_monotonic"]:
                raise TimeoutError("prepared_output_freeze_after_work_deadline")
            output_frozen_in_work = True
    except TimeoutError as exc:
        timed_out = True
        errors.append(f"{stage}:{type(exc).__name__}:{exc}")
    except Exception as exc:
        errors.append(f"{stage}:{type(exc).__name__}:{exc}")
    finally:
        io_completed = time.perf_counter()
        phase_hard = task["preparation_hard_deadline"] if ready is None else task["hard_deadline_monotonic"]
        cleanup_deadline = min(phase_hard, io_completed + .5)
        cleaned = _bounded_cleanup(process, scope, drain, deadline=cleanup_deadline, monotonic=time.perf_counter)
        errors.extend(cleaned["diagnostics"])
    if output_limit is not None:
        if frozen_output is None:
            freeze_started = time.perf_counter()
            stdout, stderr = (drain.text("stdout"), drain.text("stderr")) if drain else ("", "")
            frozen_output = (stdout, stderr, _decode_worker_output(stdout),
                             sha256(stdout.encode()).hexdigest(), sha256(stderr.encode()).hexdigest())
            output_freeze_ms = (time.perf_counter() - freeze_started) * 1000
        stdout, stderr, terminal, stdout_hash, stderr_hash = frozen_output
    else:
        stdout, stderr = (drain.text("stdout"), drain.text("stderr")) if drain else ("", "")
        terminal = _decode_worker_output(stdout)
    finished = time.perf_counter()
    cleanup_complete = cleaned["cleanup_complete"] and finished < cleanup_deadline
    if not cleanup_complete:
        errors.append("prepared_cleanup_incomplete_no_next_worker")
    ok = (not errors and not timed_out and cleanup_complete and process is not None and process.returncode == 0
          and terminal is not None and terminal.get("ok") is True and go_sent is not None
          and finished <= task["hard_deadline_monotonic"])
    return {"ok": ok, "error_code": "" if ok else "PREPARED_MINUTE_WAVE_FAILED",
            "error": ";".join(errors), "stage": stage, "request_timed_out": timed_out,
            "pid": process.pid if process else None, "returncode": process.poll() if process else None,
            "task": task, "task_hash": digest(task), "ready": ready,
            "ready_pid_in_owned_job": ready_job_verified,
            "ready_checked_monotonic": ready_checked, "go_sent_monotonic": go_sent,
            "process_started_monotonic": started, "normal_exit_checked_monotonic": io_completed,
            "controller_entered_monotonic": entered,
            "process_startup_and_scope_ms": startup_ms,
            "process_start_policy": "suspended_until_owned_job_then_resume",
            "finished_monotonic": finished, "cleanup_deadline_monotonic": cleanup_deadline,
            "preparation_elapsed_ms": ((ready_checked or io_completed) - started) * 1000,
            "waiting_after_ready_ms": ((go_sent or io_completed) - ready_checked) * 1000 if ready_checked else None,
            "work_elapsed_ms": (io_completed - go_sent) * 1000 if go_sent else None,
            "wave_elapsed_ms": max(0, (finished - target) * 1000) if go_sent else None,
            "total_residence_ms": (finished - started) * 1000, "elapsed_ms": (finished - started) * 1000,
            "cleanup_ms": (finished - io_completed) * 1000,
            **{key: value for key, value in cleaned.items() if key != "diagnostics"},
            "cleanup_complete": cleanup_complete,
            "cleanup_budget_exhausted": finished >= cleanup_deadline,
            "tree_tracking": scope.kind if scope is not None else "UNCONFIRMED",
            "stdout_eof": drain.eof["stdout"] if drain else False,
            "stderr_eof": drain.eof["stderr"] if drain else False,
            "stdout": stdout, "stderr": stderr,
            "stdout_sha256": stdout_hash if output_limit is not None else sha256(stdout.encode()).hexdigest(),
            "stderr_sha256": stderr_hash if output_limit is not None else sha256(stderr.encode()).hexdigest(),
            **({"output_complete": not drain.output_limit_exceeded and isinstance(terminal, dict)
                                    and type(terminal.get("ok")) is bool, "output_limit_bytes": output_limit,
                "output_frozen_before_work_deadline": output_frozen_in_work, "output_freeze_ms": output_freeze_ms}
               if output_limit is not None and drain is not None else {}),
            "payload": terminal.get("payload", {}) if ok else {}}
