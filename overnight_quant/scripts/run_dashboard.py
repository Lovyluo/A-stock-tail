from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "overnight_quant" / "ui" / "dashboard.py"


def streamlit_available() -> bool:
    return importlib.util.find_spec("streamlit") is not None


def main() -> int:
    if not streamlit_available():
        print("UI_DEPENDENCY_MISSING: please run pip install -r requirements-ui.txt")
        return 2
    env = {
        **os.environ,
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": os.environ.get("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false"),
        "STREAMLIT_SERVER_HEADLESS": os.environ.get("STREAMLIT_SERVER_HEADLESS", "true"),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(DASHBOARD)],
        shell=False,
        cwd=str(ROOT),
        env=env,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
