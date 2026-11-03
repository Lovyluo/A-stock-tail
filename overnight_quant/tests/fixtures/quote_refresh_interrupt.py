"""Test-only abrupt exit at selected execution/confirmation boundaries."""
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
    from overnight_quant.data import offline_price_observation as obs
    from overnight_quant.tests.test_manual_quote_observation import local_runner, store
    refresh.run_probe_worker_process = local_runner
    phase = task.get("phase", "before_publication")
    clock = [100.0]
    refresh.time.perf_counter = lambda: clock[0]
    original = os.link
    def interrupt(src, dst, **kwargs):
        name = Path(dst).name
        if name == "final-commit.json" and phase == "before_publication":
            os._exit(19)
        if name == "final-confirmation.json" and phase == "before_confirmation":
            os._exit(19)
        result = original(src, dst, **kwargs)
        if name == "final-commit.json" and phase == "after_publication_before_confirmation":
            clock[0] = 110.001
            os._exit(19)
        if name == "final-confirmation.json" and phase == "after_confirmation_publication":
            os._exit(19)
        return result
    os.link = interrupt
    write = obs.write_once
    def partial(path, value):
        if phase == "during_confirmation" and Path(path).name == "confirmation-candidate.json":
            Path(path).write_text('{"schema":', encoding="utf-8")
            os._exit(19)
        return write(path, value)
    obs.write_once = partial
    result = refresh.RefreshController(store(task["store"]), fixture=task["fixture"]).refresh(refresh.CODES)
    if phase == "after_return":
        write(Path(task["store"]) / "caller-observed-return.json", result)
        os._exit(19)
    return 1  # Other phases must terminate at their injected point.


if __name__ == "__main__":
    raise SystemExit(main())
