"""Native folder picker — web file inputs cannot return absolute paths."""

from __future__ import annotations

from typing import Any


def pick_local_folder() -> dict[str, Any]:
    return {
        "ok": False,
        "path": "",
        "detail": (
            "No GUI folder picker in this environment — POST /api/detect with an absolute path."
        ),
    }
