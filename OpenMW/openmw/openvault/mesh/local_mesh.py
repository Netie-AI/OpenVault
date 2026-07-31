"""Local mesh — OpenVault ↔ Cortex ↔ FreeIDE perfect localhost connection.

Peers announce, OpenVault probes health, operator (or auto-approve on loopback)
approves the connection pack used by FreeIDE and Cortex.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from openmw.openvault.paths import ensure_home

log = structlog.get_logger()

PeerKind = Literal["openvault", "cortex", "openide", "airgpt", "rust_console"]
PeerStatus = Literal["unknown", "online", "offline", "pending_approve", "approved", "rejected"]

DEFAULT_PORTS: dict[str, int] = {
    "openvault": 5000,
    "cortex": 8000,
    # FreeIDE is served by AirGPT on :8765 (PRODUCT_ROLES). :5100 is the legacy
    # standalone stub (scripts/openide_stub.py) and is no longer the default.
    "openide": 8765,
    "rust_console": 5055,
}

# Canonical loopback URLs — single source of truth for the mesh contract.
DEFAULT_URLS: dict[str, str] = {
    kind: f"http://127.0.0.1:{port}" for kind, port in DEFAULT_PORTS.items()
}
OPENIDE_DEFAULT_URL = DEFAULT_URLS["openide"]


def mesh_path() -> Path:
    return ensure_home() / "local_mesh.json"


def connect_pack_path() -> Path:
    return ensure_home() / "connect_pack.json"


@dataclass
class Peer:
    kind: PeerKind
    name: str
    base_url: str
    status: PeerStatus = "unknown"
    detail: str = ""
    last_seen: float | None = None
    approved: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HandshakeRequest:
    request_id: str
    peer_kind: PeerKind
    name: str
    base_url: str
    capabilities: list[str]
    created_at: float
    status: Literal["pending", "approved", "rejected"] = "pending"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalMeshState:
    peers: dict[str, Peer] = field(default_factory=dict)
    handshakes: dict[str, HandshakeRequest] = field(default_factory=dict)
    auto_approve_loopback: bool = True
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peers": {k: v.to_dict() for k, v in self.peers.items()},
            "handshakes": {k: v.to_dict() for k, v in self.handshakes.items()},
            "auto_approve_loopback": self.auto_approve_loopback,
            "updated_at": self.updated_at,
        }


def _is_loopback(url: str) -> bool:
    u = url.lower()
    return "127.0.0.1" in u or "localhost" in u or "[::1]" in u


def default_mesh() -> LocalMeshState:
    ov_port = int(os.environ.get("OPENVAULT_PORT", str(DEFAULT_PORTS["openvault"])))
    cortex = os.environ.get("CORTEX_URL", f"http://127.0.0.1:{DEFAULT_PORTS['cortex']}")
    openide = os.environ.get("OPENIDE_URL", f"http://127.0.0.1:{DEFAULT_PORTS['openide']}")
    rust = os.environ.get("OPENVAULT_RUST_URL", f"http://127.0.0.1:{DEFAULT_PORTS['rust_console']}")
    state = LocalMeshState()
    state.peers["openvault"] = Peer(
        kind="openvault",
        name="OpenVault Python console",
        base_url=f"http://127.0.0.1:{ov_port}",
        status="online",
        approved=True,
        detail="self",
        last_seen=time.time(),
    )
    state.peers["cortex"] = Peer(
        kind="cortex",
        name="Cortex / Netie Engine",
        base_url=cortex.rstrip("/"),
        status="unknown",
    )
    state.peers["openide"] = Peer(
        kind="openide",
        name="FreeIDE local bridge",
        base_url=openide.rstrip("/"),
        status="unknown",
    )
    state.peers["rust_console"] = Peer(
        kind="rust_console",
        name="OpenVault Rust auth console",
        base_url=rust.rstrip("/"),
        status="unknown",
    )
    return state


def load_mesh() -> LocalMeshState:
    path = mesh_path()
    if not path.is_file():
        state = default_mesh()
        save_mesh(state)
        return state
    raw = json.loads(path.read_text(encoding="utf-8"))
    state = LocalMeshState(
        auto_approve_loopback=bool(raw.get("auto_approve_loopback", True)),
        updated_at=float(raw.get("updated_at", time.time())),
    )
    for key, row in raw.get("peers", {}).items():
        state.peers[key] = Peer(**row)
    for key, row in raw.get("handshakes", {}).items():
        state.handshakes[key] = HandshakeRequest(**row)
    # Ensure required peers always exist
    base = default_mesh()
    for k, peer in base.peers.items():
        if k not in state.peers:
            state.peers[k] = peer
    return state


def save_mesh(state: LocalMeshState) -> Path:
    state.updated_at = time.time()
    path = mesh_path()
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return path


def _probe_url(url: str, paths: tuple[str, ...], timeout_s: float = 2.0) -> tuple[bool, str]:
    if not paths:
        return False, "no paths"
    detail = "unreachable"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            for path in paths:
                try:
                    resp = client.get(f"{url.rstrip('/')}{path}")
                except httpx.HTTPError as exc:
                    detail = f"{path} → {exc}"
                    continue
                if 200 <= resp.status_code < 300:
                    return True, f"{path} → HTTP {resp.status_code}"
                detail = f"{path} → HTTP {resp.status_code}"
            return False, detail
    except httpx.HTTPError as exc:
        return False, str(exc)


PROBE_PATHS: dict[str, tuple[str, ...]] = {
    "openvault": ("/api/healthz", "/api/local/mesh"),
    "cortex": ("/health", "/api/health", "/"),
    "openide": ("/api/healthz", "/health", "/api/openvault/ping", "/"),
    "rust_console": ("/api/healthz", "/"),
    "airgpt": ("/health", "/api/healthz", "/"),
}


def probe_peer(peer: Peer) -> Peer:
    paths = PROBE_PATHS.get(peer.kind, ("/health", "/api/healthz", "/"))
    online, detail = _probe_url(peer.base_url, paths)
    peer.status = "online" if online else "offline"
    peer.detail = detail
    if online:
        peer.last_seen = time.time()
    return peer


def refresh_mesh(state: LocalMeshState | None = None) -> LocalMeshState:
    state = state if state is not None else load_mesh()
    for key, peer in list(state.peers.items()):
        if key == "openvault":
            peer.status = "online"
            peer.approved = True
            peer.last_seen = time.time()
            peer.detail = "self"
            continue
        probe_peer(peer)
        # Keep approval bit; pending peers stay pending unless approved
        if (
            peer.status == "online"
            and not peer.approved
            and state.auto_approve_loopback
            and _is_loopback(peer.base_url)
        ):
            peer.approved = True
            peer.status = "approved"
            peer.detail = f"{peer.detail}; auto-approved loopback"
    save_mesh(state)
    log.info(
        "local_mesh_refresh",
        peers={k: v.status for k, v in state.peers.items()},
    )
    return state


def announce_peer(
    *,
    peer_kind: PeerKind,
    name: str,
    base_url: str,
    capabilities: list[str] | None = None,
    auto_approve: bool | None = None,
) -> dict[str, Any]:
    """FreeIDE / Cortex call this to join the local mesh."""
    state = load_mesh()
    caps = capabilities or []
    req_id = uuid.uuid4().hex[:12]
    hs = HandshakeRequest(
        request_id=req_id,
        peer_kind=peer_kind,
        name=name,
        base_url=base_url.rstrip("/"),
        capabilities=caps,
        created_at=time.time(),
    )
    approve = (
        auto_approve
        if auto_approve is not None
        else (state.auto_approve_loopback and _is_loopback(base_url))
    )
    peer_key = peer_kind
    peer = Peer(
        kind=peer_kind,
        name=name,
        base_url=base_url.rstrip("/"),
        status="pending_approve",
        meta={"capabilities": caps},
    )
    probe_peer(peer)
    if approve:
        hs.status = "approved"
        hs.note = "auto-approved loopback" if _is_loopback(base_url) else "auto-approved"
        peer.approved = True
        peer.status = "approved"
        if peer.last_seen is None:
            peer.detail = f"{peer.detail}; approved (offline until process starts)"
    else:
        hs.status = "pending"
        peer.status = "pending_approve"
    state.handshakes[req_id] = hs
    state.peers[peer_key] = peer
    save_mesh(state)
    return {
        "handshake": hs.to_dict(),
        "peer": peer.to_dict(),
        "connect_pack": build_connect_pack(state),
    }


def decide_handshake(request_id: str, *, approve: bool, note: str = "") -> dict[str, Any]:
    state = load_mesh()
    hs = state.handshakes.get(request_id)
    if hs is None:
        raise KeyError(request_id)
    hs.status = "approved" if approve else "rejected"
    hs.note = note
    peer = state.peers.get(hs.peer_kind)
    if peer is not None:
        peer.approved = approve
        peer.base_url = hs.base_url
        peer.name = hs.name
        if approve:
            probe_peer(peer)
            peer.status = "approved"
        else:
            peer.status = "rejected"
    state.handshakes[request_id] = hs
    save_mesh(state)
    return {"handshake": hs.to_dict(), "peer": peer.to_dict() if peer else None}


def build_connect_pack(state: LocalMeshState | None = None) -> dict[str, Any]:
    """Single JSON pack FreeIDE + Cortex paste/load for perfect local wiring."""
    state = state if state is not None else load_mesh()
    ov = state.peers.get("openvault")
    cortex = state.peers.get("cortex")
    openide = state.peers.get("openide")
    rust = state.peers.get("rust_console")
    pack = {
        "schema": "openvault.local.connect_pack/v1",
        "generated_at": time.time(),
        "bind": "127.0.0.1",
        "openvault": {
            "base_url": ov.base_url if ov else "http://127.0.0.1:5000",
            "v1": f"{(ov.base_url if ov else 'http://127.0.0.1:5000').rstrip('/')}/v1",
            "mesh": f"{(ov.base_url if ov else 'http://127.0.0.1:5000').rstrip('/')}/api/local/mesh",
            "deploy_from_cortex": (
                f"{(ov.base_url if ov else 'http://127.0.0.1:5000').rstrip('/')}"
                "/api/deploy/from-cortex"
            ),
            "approved": bool(ov.approved) if ov else True,
        },
        "cortex": {
            "base_url": cortex.base_url if cortex else "http://127.0.0.1:8000",
            "health": f"{(cortex.base_url if cortex else 'http://127.0.0.1:8000').rstrip('/')}/health",
            "approved": bool(cortex and cortex.approved),
            "status": cortex.status if cortex else "unknown",
        },
        "openide": {
            "base_url": openide.base_url if openide else OPENIDE_DEFAULT_URL,
            "announce": (
                f"{(ov.base_url if ov else 'http://127.0.0.1:5000').rstrip('/')}"
                "/api/local/handshake"
            ),
            "invoke_signin": (
                f"{(ov.base_url if ov else 'http://127.0.0.1:5000').rstrip('/')}/api/freeide/invoke"
            ),
            "approved": bool(openide and openide.approved),
            "status": openide.status if openide else "unknown",
        },
        "rust_console": {
            "base_url": rust.base_url if rust else "http://127.0.0.1:5055",
            "auth_ui": rust.base_url if rust else "http://127.0.0.1:5055",
            "status": rust.status if rust else "unknown",
        },
        "env": {
            "CORTEX_URL": cortex.base_url if cortex else "http://127.0.0.1:8000",
            "OPENIDE_URL": openide.base_url if openide else OPENIDE_DEFAULT_URL,
            "OPENVAULT_URL": ov.base_url if ov else "http://127.0.0.1:5000",
            "OPENVAULT_RUST_URL": rust.base_url if rust else "http://127.0.0.1:5055",
        },
        "perfect_local": _perfect_local(state),
    }
    connect_pack_path().write_text(json.dumps(pack, indent=2), encoding="utf-8")
    return pack


def _perfect_local(state: LocalMeshState) -> dict[str, Any]:
    need = ("openvault", "cortex", "openide")
    missing = []
    for k in need:
        p = state.peers.get(k)
        if p is None or not p.approved:
            missing.append(f"{k}:not_approved")
        elif k != "openvault" and p.status == "offline":
            missing.append(f"{k}:offline")
    cortex = state.peers.get("cortex")
    openide = state.peers.get("openide")
    ready = (
        cortex is not None
        and cortex.approved
        and cortex.status in ("online", "approved")
        and openide is not None
        and openide.approved
    )
    return {
        "ready": ready and not any(m.endswith(":not_approved") for m in missing),
        "missing": missing,
        "message": (
            "OpenVault ↔ Cortex ↔ FreeIDE approved and reachable on loopback"
            if ready and not missing
            else "Approve peers and start Cortex + FreeIDE locally"
        ),
    }


def openide_invoke(
    *,
    action: str,
    username: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """FreeIDE asks OpenVault to complete a local laptop action."""
    state = refresh_mesh()
    openide = state.peers.get("openide")
    if openide is None or not openide.approved:
        return {
            "ok": False,
            "error": "FreeIDE not approved — POST /api/local/handshake first",
            "connect_pack": build_connect_pack(state),
        }
    action = action.strip().lower()
    rust = state.peers.get("rust_console")
    cortex_peer = state.peers.get("cortex")
    ov = state.peers["openvault"]
    if action == "complete_signin":
        return {
            "ok": True,
            "action": action,
            "username": username,
            "instructions": [
                "Open Rust auth UI or Python Accounts tab",
                "Sign in with passkey on this laptop (default)",
                "Vault secrets remain in OpenVault; Cortex uses /v1 via connect pack",
            ],
            "urls": {
                "rust_auth": rust.base_url if rust else "http://127.0.0.1:5055",
                "openvault": ov.base_url,
                "cortex": cortex_peer.base_url if cortex_peer else "http://127.0.0.1:8000",
            },
            "payload": payload or {},
        }
    if action == "register_passkey":
        return {
            "ok": True,
            "action": action,
            "username": username,
            "open_url": f"{(rust.base_url if rust else 'http://127.0.0.1:5055')}/#auth",
            "message": "Register laptop passkey under username; password stays argon2 backup",
        }
    if action == "push_selection_to_cortex":
        return {
            "ok": True,
            "action": action,
            "message": (
                "OpenVault keeps keys; Cortex reads models via approved mesh. "
                "Use PUT /api/orchestration/selection then Cortex engine refresh."
            ),
            "cortex_url": cortex_peer.base_url if cortex_peer else "http://127.0.0.1:8000",
        }
    return {"ok": False, "error": f"unknown action: {action}"}
