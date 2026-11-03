"""Test-only abrupt exit after private commit write, before public publication."""
import json
import os
from pathlib import Path
import sys


def main():
    task = json.loads(sys.stdin.read())
    def deny(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
            raise RuntimeError("fixture_network_forbidden")
    sys.addaudithook(deny)
    from overnight_quant.data import quote_observation_refresh as refresh
    from overnight_quant.tests.test_manual_quote_observation import local_runner, store
    refresh.run_probe_worker_process = local_runner
    original = os.link
    def interrupt(src, dst, **kwargs):
        if Path(dst).name == "final-commit.json":
            os._exit(19)
        return original(src, dst, **kwargs)
    os.link = interrupt
    refresh.RefreshController(store(task["store"]), fixture=task["fixture"]).refresh(refresh.CODES)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
