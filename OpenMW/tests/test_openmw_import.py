"""Package import must not pull numpy (console / one-seat demo)."""

from __future__ import annotations

import subprocess
import sys


def test_openmw_import_does_not_load_numpy() -> None:
    script = (
        "import openmw, sys; "
        "raise SystemExit(0 if 'numpy' not in sys.modules else 1)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
