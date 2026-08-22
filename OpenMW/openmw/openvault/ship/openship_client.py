"""Thin OpenShip HTTP client. Local simulate is the default when no URL is set."""

from __future__ import annotations

import os
from typing import Any


def adapter_status() -> dict[str, Any]:
    url = os.environ.get("OPENSHIP_URL", "").rstrip("/")
    mode = os.environ.get("OPENSHIP_MODE", "auto")
    api_ready = bool(url)
    if mode == "simulate":
        api_ready = False
    return {
        "api_url": url or None,
        "api_ready": api_ready,
        "mode": mode,
        "ready": api_ready or mode == "simulate",
    }


class OpenShipClient:
    def __init__(self) -> None:
        self.base = os.environ.get("OPENSHIP_URL", "").rstrip("/")
        self.available = bool(self.base)

    def cloud_status(self) -> dict[str, Any]:
        return {"online": False, "detail": "remote OpenShip not wired"}

    def billing_state(self) -> dict[str, Any]:
        return {"ok": True, "detail": "no remote billing"}

    def close(self) -> None:
        return None
