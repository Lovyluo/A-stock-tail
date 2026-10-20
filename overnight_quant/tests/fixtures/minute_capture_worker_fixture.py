"""Explicit test-only client; exercises the real capture loop and child process."""
from __future__ import annotations
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from overnight_quant.data import minute_capture_worker as worker

task = json.loads(sys.stdin.read())
mode = sys.argv[1] if len(sys.argv) > 1 else "complete"
base = datetime.fromisoformat(task["parent_started_at"])
worker.now = lambda: (base + timedelta(seconds=time.perf_counter() - task["parent_started_monotonic"])).isoformat()

if mode == "orphan_pipe":
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    Path(sys.argv[2]).write_text(str(child.pid))
    print('{"ok":true,"payload":{"synthetic_test_only":true}}', flush=True)
    print("synthetic stderr before root exit", file=sys.stderr, flush=True)
    os._exit(0)  # The descendant deliberately keeps both output pipes open.


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        return self.rows


class Client:
    def __init__(self):
        self.bodies = []

    def bars(self, symbol, frequency, start, offset):
        assert frequency == "1m" and start == 0 and offset == 800
        if mode == "block" and symbol == "000333":
            if len(sys.argv) > 2:
                child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
                Path(sys.argv[2]).write_text(str(child.pid))
            time.sleep(30)
        if mode == "late":
            time.sleep(2)
        rows = [{"datetime": f"{task['trade_date']} 14:{m:02d}", "open": 10, "close": 10,
                 "high": 10, "low": 10, "vol": 1000, "amount": 10000,
                 "native_extra": {"retained": True, "synthetic_test_only": True}}
                for m in range(39, 51)]
        # Include a non-target row: it must survive unchanged in the raw capture.
        rows.append({**rows[0], "datetime": f"{task['trade_date']} 14:51"})
        if mode in ("missing", "pagination"):
            rows.pop(2)
        if mode == "duplicate":
            rows.append(dict(rows[0]))
        if mode == "overlap":
            for row in rows[:12]:
                row.update(interval_start=f"{task['trade_date']}T14:38:00+08:00",
                           interval_end=f"{task['trade_date']}T14:39:00+08:00")
        if mode == "revision" and task["wave"] == 3:
            rows[-2]["amount"] += 1
        if mode == "invalid":
            rows[0]["amount"] = None
        if mode == "reverse":
            rows.reverse()
        self.bodies = [json.dumps(rows, sort_keys=True).encode()]
        self.body_times = [{"parser_received_at": worker.now(), "parser_received_monotonic": time.perf_counter()}]
        return Frame(rows)

    def close(self):
        pass


try:
    result = worker.capture_wave(task, client_factory=Client)
    print(worker.encoded({"ok": True, "payload": result}), flush=True)
except Exception as exc:
    print(worker.encoded({"ok": False, "error_code": "TEST_ONLY_CAPTURE_FAILURE", "error": str(exc)}), flush=True)
    sys.exit(2)
