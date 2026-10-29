"""One plan for the super app: route a model, ship an app, or both.

Provider secrets never enter this plan. Callers pass provider ids that
already have a KeyVault row. FreeRoute (:20128) and FreeBuild (:3031) are
the engines; OpenVault stays the key vault and the gate.
"""

from __future__ import annotations

import re
from typing import Any

from openmw.openvault.ship.detect import DetectionInputError, detect_project
from openmw.openvault.ship.hosts import ADAPTERS
from openmw.openvault.vault.providers import spendable_for_freeroute

_TOKEN = re.compile(r"[a-z0-9_]+")

_ROUTE = frozenset({"chat", "ask", "model", "route", "prompt", "complete", "llm"})
_SHIP = frozenset({"ship", "deploy", "host", "build", "publish", "launch"})
_LIGHT = frozenset({"light", "small", "landing", "widget", "static"})

_STATIC_CLOUDS = ("cloudflare_pages", "netlify", "spaceship_ftp")
_CONTAINER_CLOUDS = ("coolify", "vps_ssh")

FREEROUTE_URL = "http://127.0.0.1:20128"
FREEBUILD_URL = "http://127.0.0.1:3031"
OPENVAULT_URL = "http://127.0.0.1:5000"


class SuperappInputError(ValueError):
    """The caller gave nothing to plan, or a path we refuse to guess."""


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _lanes(tokens: set[str], *, has_project: bool) -> list[str]:
    route = bool(tokens & _ROUTE)
    ship = bool(tokens & _SHIP) or has_project
    if route and ship:
        return ["route", "ship"]
    if route:
        return ["route"]
    if ship:
        return ["ship"]
    return ["route"]


def _clouds(keyed: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for host_id in sorted(ADAPTERS):
        adapter = ADAPTERS[host_id]
        provider = str(getattr(adapter, "credential_provider", ""))
        rows.append(
            {
                "id": host_id,
                "name": str(getattr(adapter, "name", host_id)),
                "credential_provider": provider,
                "needs_local_build": bool(getattr(adapter, "needs_local_build", False)),
                "ready": bool(provider) and provider in keyed,
            }
        )
    return rows


def _recommend(clouds: list[dict[str, Any]], *, light: bool, host_kind: str) -> str:
    if light or host_kind == "static_http":
        order = _STATIC_CLOUDS
    elif host_kind == "container":
        order = _CONTAINER_CLOUDS
    else:
        order = tuple(row["id"] for row in clouds)
    by_id = {row["id"]: row for row in clouds}
    for host_id in order:
        row = by_id.get(host_id)
        if row is not None and row["ready"]:
            return host_id
    for host_id in order:
        if host_id in by_id:
            return host_id
    return clouds[0]["id"] if clouds else ""


def _pick_model(tokens: set[str], keyed: set[str]) -> dict[str, Any] | None:
    catalog = spendable_for_freeroute()
    mentioned = [row for row in catalog if row["id"] in tokens]
    pool = mentioned or [row for row in catalog if row["id"] in keyed]
    if not pool:
        return None
    chosen = pool[0]
    provider_id = str(chosen["id"])
    models = list(chosen.get("chat_models") or [])
    return {
        "provider": provider_id,
        "model": models[0] if models else "",
        "key_present": provider_id in keyed,
        "base_url": str(chosen.get("base_url") or ""),
    }


def plan_superapp(
    text: str,
    *,
    project_path: str = "",
    keyed_providers: set[str] | None = None,
) -> dict[str, Any]:
    """Plan a route, a ship, or both. Does not deploy and does not read secrets."""
    raw = text.strip()
    path = project_path.strip()
    if not raw and not path:
        raise SuperappInputError("text or project_path is required")

    keyed = set(keyed_providers or ())
    tokens = _tokens(raw)
    app: dict[str, Any] | None = None
    host_kind = ""
    if path:
        try:
            detected = detect_project(path)
        except DetectionInputError as exc:
            raise SuperappInputError(str(exc)) from exc
        app = detected.to_dict()
        host_kind = detected.host_kind

    light = bool(tokens & _LIGHT) or (app is not None and app.get("category") == "static")
    lanes = _lanes(tokens, has_project=app is not None and app.get("primary") != "unknown")
    if light and "ship" not in lanes:
        lanes.append("ship")
    clouds = _clouds(keyed)
    cloud_id = _recommend(clouds, light=light, host_kind=host_kind)
    model = _pick_model(tokens, keyed) if "route" in lanes else None

    tasks: list[dict[str, Any]] = []
    if "route" in lanes:
        tasks.append(
            {
                "id": "route",
                "engine": "freeroute",
                "url": f"{FREEROUTE_URL}/v1/chat/completions",
                "fallback_url": f"{OPENVAULT_URL}/v1/chat/completions",
                "provider": None if model is None else model["provider"],
                "model": None if model is None else model["model"],
                "parallel_group": "super",
            }
        )
    if "ship" in lanes:
        build = [] if app is None else list(app.get("suggested_build") or [])
        if app is not None and app.get("build_command"):
            build = [str(app["build_command"])]
        tasks.append(
            {
                "id": "ship",
                "engine": "freebuild",
                "url": FREEBUILD_URL,
                "fallback_url": f"{OPENVAULT_URL}/api/ship/ready",
                "cloud": cloud_id,
                "light_app": light,
                "build": build,
                "parallel_group": "super",
            }
        )

    return {
        "lanes": lanes,
        "parallel": len(lanes) > 1,
        "light_app": light,
        "model": model,
        "app": app,
        "clouds": clouds,
        "recommended_cloud": cloud_id,
        "tasks": tasks,
        "keys": "provider keys stay in OpenVault KeyVault",
        "engines": {
            "freeroute": FREEROUTE_URL,
            "freebuild": FREEBUILD_URL,
            "openvault": OPENVAULT_URL,
        },
    }
