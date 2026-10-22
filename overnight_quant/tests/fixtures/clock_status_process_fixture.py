"""Local clock-query process fixture; never calls a clock service or NTP."""
import os
from pathlib import Path
import sys
import time

mode, pid_file = sys.argv[1:]
Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
sys.stdout.buffer.write(b"Leap Indicator: 0\nLast Successful Sync Time: 2026/10/21 14:30:00\n"
                        b"Source: synthetic.invalid,0x9\nLast Sync Error: 0\n")
sys.stderr.buffer.write(b"synthetic stderr retained\n")
if mode == "output":
    sys.stdout.buffer.write(b"synthetic output line\n" * 1000)
    sys.stderr.buffer.write(b"synthetic error line\n" * 1000)
sys.stdout.buffer.flush()
sys.stderr.buffer.flush()
if mode == "timeout":
    time.sleep(60)
raise SystemExit(7 if mode == "nonzero" else 0)
