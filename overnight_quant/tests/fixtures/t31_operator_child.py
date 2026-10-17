"""Filesystem-only operator fixture. It has no access to a source transport."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

def forbidden(*args, **kwargs):
    raise AssertionError("Synthetic operator fixture cannot connect to a source")

socket.socket.connect = forbidden
socket.create_connection = forbidden
payload, output, marker = map(Path, sys.argv[1:])
data = json.loads(payload.read_text(encoding="utf-8"))
marker.write_text("test_only operator stage", encoding="utf-8")
if data.get("stderr"):
    print("synthetic stderr diagnostic", file=sys.stderr)
if data.get("spawn_child"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path(data["child_pid_file"]).write_text(str(child.pid), encoding="ascii")
if data.get("sleep"):
    time.sleep(data["sleep"])
if data.get("write_result", True):
    output.write_text("{broken" if data.get("malformed") else json.dumps(data["result"]), encoding="utf-8")
raise SystemExit(data.get("exit_code", 0))
