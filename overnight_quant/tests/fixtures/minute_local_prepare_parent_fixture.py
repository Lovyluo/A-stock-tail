"""Test-only owner killed while its independently job-tracked child is in bars."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from overnight_quant.data.minute_local_prepare_process import run_prepared_wave
from overnight_quant.data.minute_capture_audit import write
from overnight_quant.tests.test_phase112_minute_local_prepare import task, FIXTURE

directory = Path(sys.argv[1])
(directory / "owner.pid").write_text(str(os.getpid()))
(directory / "intent.json").write_text(json.dumps({"test_only": True, "pending": True}))
value = task(directory)
write(directory / value["trade_date"] / "wave-0.started.json", {"task": value})
result = run_prepared_wave(value, worker_command=[sys.executable, str(FIXTURE), "parent_death", str(directory)])
(directory / "parent-result.json").write_text(json.dumps(result))
