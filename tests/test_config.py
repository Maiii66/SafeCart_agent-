# tests/test_config.py
# Guards the default money limits: dollars in the environment, integer cents in code.

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_default_budget_and_daily_cap_are_two_thousand_and_five_hundred_dollars():
    env = dict(os.environ, MAX_BUDGET="", DAILY_CAP="")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend import config; "
            "print(config.MAX_BUDGET_CENTS, config.DAILY_CAP_CENTS)",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.split() == ["200000", "50000"]
