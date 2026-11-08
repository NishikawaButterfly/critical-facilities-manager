"""The measurement behind the limiter's published cost keeps running.

The figures in the rate-limiting chapter come from
``scripts/measure_auth_limiter.py``. Nothing else imports the script, so a
change to the limiter that broke it would go unnoticed until somebody next
measured; a run far too small to be worth reading catches that.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_auth_limiter.py"


def test_the_measurement_script_runs() -> None:
    # Fixed argument list, absolute interpreter path, no shell.
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--number", "10", "--repeat", "2", "--requests", "5"],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    for label in (
        "working out the sources",
        "lockout check",
        "failure path",
        "without the header (one source)",
        "with the header (two sources)",
    ):
        assert label in result.stdout
