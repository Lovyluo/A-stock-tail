"""Test-only concurrent manual capture driver; not a production entry point."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from overnight_quant.data import minute_capture_audit as audit

root, barrier, ready = map(Path, sys.argv[1:4])
ready.write_text("synthetic_test_only")
limit = time.monotonic() + 15
while not barrier.exists():
    if time.monotonic() >= limit:
        raise TimeoutError("test barrier")
    time.sleep(.01)
base = datetime.fromisoformat("2026-10-21T14:49:55+08:00")
start = time.monotonic()


def clock():
    return base + timedelta(seconds=time.monotonic() - start)


def sleep(seconds):
    global base
    base += timedelta(seconds=seconds)


try:
    audit.capture(audit.frozen_plan(), "2026-10-21", authorization={"synthetic_test_only": True},
                  audit_root=root, _test={"test_only": True, "clock": clock, "sleep": sleep,
                    "worker_command": [sys.executable, str(Path(__file__).with_name("minute_capture_worker_fixture.py")), "complete"]})
except FileExistsError:
    raise SystemExit(3)
