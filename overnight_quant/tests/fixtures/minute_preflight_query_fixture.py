"""Isolated query substitute: no service, task, NTP or source access."""
import json
from pathlib import Path
import sys
import time

sys.stdin.read()
path, mode = Path(sys.argv[1]), sys.argv[2]
if mode == "timeout":
    path.with_suffix(".pid").write_text(str(__import__("os").getpid()))
    time.sleep(30)
elif mode == "exit":
    print("synthetic query crash", file=sys.stderr, flush=True)
    raise SystemExit(7)
elif mode == "missing":
    raise SystemExit(0)
elif mode == "invalid":
    print("not-json", flush=True)
elif mode == "denied":
    print(json.dumps({"ok": False, "error_code": "PermissionError", "error": "task inventory access denied"}), flush=True)
    raise SystemExit(2)
else:
    print(json.dumps({"ok": True, "payload": json.loads(path.read_text())}), flush=True)
