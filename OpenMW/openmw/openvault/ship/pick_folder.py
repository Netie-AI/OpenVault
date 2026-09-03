"""Native OS folder picker for Ship — browsers only expose the folder name."""

from __future__ import annotations

import contextlib
import threading
from typing import Any


def pick_local_folder(*, title: str = "Select project folder") -> dict[str, Any]:
    """Open a blocking native directory dialog (tkinter). Safe for local console."""
    holder: list[str | None] = [None]
    err: list[BaseException | None] = [None]

    def _run() -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            with contextlib.suppress(tk.TclError):
                root.attributes("-topmost", True)
            path = filedialog.askdirectory(title=title, mustexist=True)
            holder[0] = path or None
            root.destroy()
        except BaseException as exc:
            err[0] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=300)
    if thread.is_alive():
        return {"ok": False, "path": "", "error": "picker timed out"}
    if err[0] is not None:
        return {"ok": False, "path": "", "error": str(err[0])}
    path = holder[0] or ""
    return {"ok": bool(path), "path": path, "error": None if path else "cancelled"}
