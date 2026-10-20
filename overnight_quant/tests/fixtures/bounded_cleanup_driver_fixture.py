"""Supervised fault injection: a legacy taskkill path would hang or fail."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from overnight_quant.data import probe_worker_process as process

task = json.loads(sys.stdin.read())
mode, directory = sys.argv[1], Path(sys.argv[2])
fixture = Path(__file__).with_name("minute_capture_worker_fixture.py")
native_start = subprocess.Popen


def owned_start(*args, **kwargs):
    child = native_start(*args, **kwargs)
    directory.joinpath(f"owned-{child.pid}.pid").write_text(str(child.pid))
    return child


process.subprocess.Popen = owned_start


def legacy_taskkill(_):
    directory.joinpath("legacy-called").write_text(mode)
    helper = subprocess.Popen([sys.executable, "-c",
        "import time;time.sleep(30)" if mode == "hang" else "raise SystemExit(1)"])
    directory.joinpath("taskkill.pid").write_text(str(helper.pid))
    # Intentionally bad legacy behavior; the bounded path must never call this.
    helper.wait()
    if helper.returncode:
        raise OSError("synthetic taskkill failure")


process._terminate_process_tree = legacy_taskkill
started = time.monotonic()
result = process.run_probe_worker_process(task, 2000, bounded_cleanup_ms=500,
    retain_streams=True, worker_command=[sys.executable, str(fixture), "block",
                                       str(directory / "grandchild.pid")])
print(json.dumps({"result": result, "driver_elapsed_ms": (time.monotonic() - started) * 1000}), flush=True)
