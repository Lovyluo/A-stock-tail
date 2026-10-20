from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable


WORKER_TIMEOUT_ERROR = "REQUEST_DEADLINE_EXCEEDED"
WORKER_OUTPUT_ERROR = "WORKER_OUTPUT_INVALID"
WORKER_PROCESS_ERROR = "WORKER_PROCESS_FAILED"


def run_probe_worker_process(
    task: dict[str, Any],
    deadline_ms: int,
    *,
    python_executable: str | None = None,
    worker_command: list[str] | None = None,
    monotonic: Callable[[], float] | None = None,
    termination_grace_seconds: float = 0.5,
    include_startup_in_deadline: bool = False,
    retain_streams: bool = False,
) -> dict[str, Any]:
    """Run one provider request in a killable child process."""
    runtime_monotonic = monotonic or time.monotonic
    command = list(worker_command or [
        python_executable or sys.executable,
        "-m",
        "overnight_quant.data.minute_probe_worker",
    ])
    creationflags = (
        subprocess.CREATE_NO_WINDOW
        if os.name == "nt"
        else 0
    )
    started = runtime_monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    worker_terminated = False
    startup_ms = None
    try:
        remaining = max(1, int(deadline_ms)) / 1000.0
        if include_startup_in_deadline:
            startup_ms = (runtime_monotonic() - started) * 1000
            remaining -= startup_ms / 1000
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, 0)
        stdout, stderr = process.communicate(
            json.dumps(task, ensure_ascii=False),
            timeout=remaining,
        )
    except subprocess.TimeoutExpired:
        worker_terminated = True
        cleanup_started = runtime_monotonic() if retain_streams else None
        _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(
                timeout=max(0.05, float(termination_grace_seconds))
            )
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        elapsed_ms = round((runtime_monotonic() - started) * 1000, 3)
        return {
            "ok": False,
            "error_code": WORKER_TIMEOUT_ERROR,
            "error": WORKER_TIMEOUT_ERROR,
            "request_timed_out": True,
            "worker_terminated": worker_terminated,
            "elapsed_ms": elapsed_ms,
            "returncode": process.returncode,
            **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
            **({"process_startup_ms": startup_ms,
                "cleanup_ms": elapsed_ms - (cleanup_started - started) * 1000}
               if retain_streams else {}),
        }

    elapsed_ms = round(
        (runtime_monotonic() - started) * 1000,
        3,
    )
    payload = _decode_worker_output(stdout)
    diagnostics = ({"process_startup_ms": startup_ms, "cleanup_ms": 0.0,
                    "io_and_normal_exit_ms": elapsed_ms - (startup_ms or 0.0)} if retain_streams else {})
    if payload is None:
        return {
            "ok": False,
            "error_code": WORKER_OUTPUT_ERROR,
            "error": _bounded_error(stderr or stdout),
            "request_timed_out": False,
            "worker_terminated": False,
            "elapsed_ms": elapsed_ms,
            "returncode": process.returncode,
            **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
            **diagnostics,
        }
    if process.returncode != 0 or payload.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(
                payload.get("error_code") or WORKER_PROCESS_ERROR
            ),
            "error": str(
                payload.get("error")
                or _bounded_error(stderr)
                or WORKER_PROCESS_ERROR
            ),
            "request_timed_out": False,
            "worker_terminated": False,
            "elapsed_ms": elapsed_ms,
            "returncode": process.returncode,
            **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
            **diagnostics,
        }
    return {
        "ok": True,
        "payload": payload.get("payload") or {},
        "error_code": "",
        "error": "",
        "request_timed_out": False,
        "worker_terminated": False,
        "elapsed_ms": elapsed_ms,
        "returncode": process.returncode,
        **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
        **diagnostics,
    }


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
    else:
        process.terminate()


def _decode_worker_output(value: str) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text, *reversed(text.splitlines())]
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _bounded_error(value: str, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]
