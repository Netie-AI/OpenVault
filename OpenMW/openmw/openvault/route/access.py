"""Access routing — resolve *where* a thing lives, then gate *whether* it may be reached.

This is the surface every other shell asks before it touches anything: memory,
provider APIs, mesh components, runtimes, local models, or the Free* services.
It is deliberately **not** a store and **not** an orchestrator.

Read PRODUCT_ROLES.md before extending this module. The locks that shape it:

* **Lock 5 — no third orchestrator, no second vault.** Cortex already owns
  memory (`CortexOS/api/memory_routes.py`, `/api/memory/*`) and owns which
  architecture runs. So a ``memory`` resolve returns *Cortex's location plus a
  decision*, never memory content, and never a second copy of it. The moment
  this module starts holding what it resolves, it has become the thing the
  contract forbids.
* **Lock 3 — the leave-machine gate is OpenVault's.** Which is why resolve
  returns a `GateDecision` and not just a URL: handing back a location without
  a verdict is the "omni-retrieve without OpenVault as gate" the contract calls
  unsafe.
* **Lock 2 — architecture preset SoT is Cortex.** ``model`` entries report the
  slots and the persisted *slot preference*; they do not pick DAG vs LangGraph.

So the honest one-line summary of every response here: *this is where it lives,
this is who owns it, and this is whether you may go.* The caller still does the
going.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from openmw.openvault.mesh.local_mesh import DEFAULT_CORTEX_URL, LocalMeshState, load_mesh
from openmw.openvault.ship.gate import GateAction, GateDecision, check_gate
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.store import KeyVault

AccessKind = Literal["memory", "api", "component", "runtime", "model", "service"]
AccessIntent = Literal["read", "write", "invoke", "deploy", "leave", "connect"]

ACCESS_KINDS: tuple[AccessKind, ...] = (
    "memory",
    "api",
    "component",
    "runtime",
    "model",
    "service",
)

# An intent is what the caller wants to do; a GateAction is what the existing
# gate understands. Kept as an explicit table rather than a clever mapping so
# that adding an intent forces a decision about how strictly it is gated.
_INTENT_TO_GATE: dict[AccessIntent, GateAction] = {
    "read": "retrieve",
    "write": "run",
    "invoke": "run",
    "deploy": "deploy",
    "leave": "leave",
    "connect": "connect",
}

# Process start, so uptime is real rather than inferred from a log line.
_STARTED_AT = time.time()


@dataclass(frozen=True)
class AccessEntry:
    """One resolvable thing, and who is responsible for it.

    ``owner`` is the surface that holds the data or does the work. When it is
    anything other than ``openvault``, this process is a signpost — it must not
    also become a cache of whatever lives there.
    """

    kind: AccessKind
    id: str
    label: str
    owner: str
    base_url: str = ""
    path: str = ""
    detail: str = ""
    reachable: bool | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def location(self) -> str:
        if self.base_url and self.path:
            return f"{self.base_url.rstrip('/')}{self.path}"
        return self.base_url or self.path


def _peer(state: LocalMeshState, kind: str) -> Any:
    return state.peers.get(kind)


def _peer_url(state: LocalMeshState, kind: str, fallback: str = "") -> str:
    peer = _peer(state, kind)
    return (peer.base_url if peer is not None else "") or fallback


def _peer_reachable(state: LocalMeshState, kind: str) -> bool | None:
    """``None`` means unprobed, which is not the same as offline.

    Reporting an unprobed peer as down would make the uptime view lie every
    time the process restarts, so the tri-state is load-bearing.
    """
    peer = _peer(state, kind)
    if peer is None:
        return None
    if peer.status == "online":
        return True
    if peer.status == "offline":
        return False
    return None


def build_registry(
    *,
    vault: KeyVault,
    state: LocalMeshState | None = None,
    openvault_url: str = "http://127.0.0.1:5000",
) -> list[AccessEntry]:
    """Everything OpenVault knows how to route to, derived from live state.

    Nothing here is a hardcoded catalogue of what *should* exist — each entry is
    built from the mesh peers, the vault's own keys, or this app's own routes.
    An entry that cannot be derived does not appear, because a registry that
    lists things that are not there is worse than no registry.
    """
    state = state if state is not None else load_mesh()
    cortex_url = _peer_url(state, "cortex", DEFAULT_CORTEX_URL)
    freeide_url = _peer_url(state, "openide", "http://127.0.0.1:8765")
    entries: list[AccessEntry] = []

    # --- memory: Cortex owns it. We route, we do not store. ---
    entries.append(
        AccessEntry(
            kind="memory",
            id="cortex.memory",
            label="Cortex memory store",
            owner="cortex",
            base_url=cortex_url,
            path="/api/memory",
            detail=(
                "Cortex is the memory SoT (PRODUCT_ROLES lock 5). OpenVault gates "
                "access; it never holds memory content."
            ),
            reachable=_peer_reachable(state, "cortex"),
        )
    )
    entries.append(
        AccessEntry(
            kind="memory",
            id="cortex.context",
            label="Cortex context assembly",
            owner="cortex",
            base_url=cortex_url,
            path="/api/context",
            detail="Context engineering / budget assembly lives with the brain.",
            reachable=_peer_reachable(state, "cortex"),
        )
    )

    # --- api: provider credentials OpenVault actually holds. ---
    active = [k for k in vault.list_keys() if k.enabled and k.lifecycle == "active"]
    by_provider: dict[str, int] = {}
    for key in active:
        by_provider[str(key.provider)] = by_provider.get(str(key.provider), 0) + 1
    for provider in sorted(by_provider):
        entries.append(
            AccessEntry(
                kind="api",
                id=f"provider.{provider}",
                label=f"{provider} credentials",
                owner="openvault",
                base_url=openvault_url,
                path="/api/keys",
                detail="Reveal is gated and audited; see docs/SECRETS_CUSTODY.md.",
                reachable=True,
                meta={"active_keys": by_provider[provider]},
            )
        )

    # --- component: the mesh peers, whatever they currently are. ---
    display = {
        "openvault": "OpenVault (custody + gate + ship)",
        "cortex": "Cortex (brains / orchestration)",
        "openide": "FreeIDE (coding surface)",
        "airgpt": "AirGPT (shell / control plane)",
        "rust_console": "OpenVault Rust auth console",
    }
    for peer_kind, peer in sorted(state.peers.items()):
        entries.append(
            AccessEntry(
                kind="component",
                id=f"component.{peer_kind}",
                label=display.get(peer_kind, peer.name or peer_kind),
                owner=peer_kind,
                base_url=peer.base_url,
                detail=peer.detail or f"mesh peer, status={peer.status}",
                reachable=_peer_reachable(state, peer_kind),
                meta={"approved": peer.approved, "status": peer.status},
            )
        )

    # --- runtime: where work can actually execute. ---
    local_providers = {p for p in by_provider if p in ("ollama", "cortex", "litellm")}
    entries.append(
        AccessEntry(
            kind="runtime",
            id="runtime.local",
            label="Local ground models",
            owner="openvault",
            base_url=openvault_url,
            path="/api/slots",
            detail="Local engines usable without cloud keys.",
            reachable=bool(local_providers),
            meta={"providers": sorted(local_providers)},
        )
    )
    entries.append(
        AccessEntry(
            kind="runtime",
            id="runtime.cortex",
            label="Cortex engine",
            owner="cortex",
            base_url=cortex_url,
            path="/api/cortex/status",
            detail="Agent loop runs here, not in OpenVault.",
            reachable=_peer_reachable(state, "cortex"),
        )
    )
    entries.append(
        AccessEntry(
            kind="runtime",
            id="runtime.freeide",
            label="FreeIDE bridge",
            owner="openide",
            base_url=freeide_url,
            path="/api/freeide/ready",
            detail=(
                "Coding-expert activation is requested from Cortex; "
                "FreeIDE does not host deploy UX."
            ),
            reachable=_peer_reachable(state, "openide"),
        )
    )

    # --- model: slot preference is ours; architecture choice is Cortex's. ---
    entries.append(
        AccessEntry(
            kind="model",
            id="model.slots",
            label="Model slots + selection",
            owner="openvault",
            base_url=openvault_url,
            path="/api/slots",
            detail=(
                "OpenVault persists the model slot preference. "
                "Cortex picks DAG vs LangGraph (lock 2)."
            ),
            reachable=True,
        )
    )

    # --- service: the Free* family. ---
    entries.append(
        AccessEntry(
            kind="service",
            id="service.freeroute",
            label="FreeRoute (free gateway)",
            owner="openvault",
            base_url=openvault_url,
            path="/api/freeroute/ratelimit",
            detail="Free-gateway routing, token-budgeted. Formerly OpenFree.",
            reachable=True,
        )
    )
    entries.append(
        AccessEntry(
            kind="service",
            id="service.freebuild",
            label="FreeBuild (deploy / ship)",
            owner="openvault",
            base_url=openvault_url,
            path="/api/freebuild",
            detail=(
                "Plan/gate/execute ships. Formerly OpenShip. Owned by OpenVault (PRODUCT_ROLES)."
            ),
            reachable=True,
        )
    )

    return entries


def registry_payload(
    *,
    vault: KeyVault,
    kind: AccessKind | None = None,
    state: LocalMeshState | None = None,
    openvault_url: str = "http://127.0.0.1:5000",
) -> dict[str, Any]:
    entries = build_registry(vault=vault, state=state, openvault_url=openvault_url)
    if kind is not None:
        entries = [e for e in entries if e.kind == kind]
    by_kind: dict[str, int] = {}
    for entry in entries:
        by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1
    return {
        "kinds": list(ACCESS_KINDS),
        "count": len(entries),
        "by_kind": by_kind,
        "entries": [e.to_dict() for e in entries],
        "policy": (
            "OpenVault resolves location and decides access. It does not store memory, "
            "run the agent loop, or pick the architecture preset — see PRODUCT_ROLES.md."
        ),
    }


@dataclass
class ResolveResult:
    """A location plus a verdict. Never the thing itself."""

    found: bool
    allowed: bool
    kind: str
    id: str
    intent: str
    owner: str = ""
    location: str = ""
    reasons: list[str] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    entry: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_access(
    *,
    vault: KeyVault,
    kind: AccessKind,
    resource_id: str,
    intent: AccessIntent = "read",
    fallback: FallbackManager | None = None,
    state: LocalMeshState | None = None,
    openvault_url: str = "http://127.0.0.1:5000",
    required_providers: list[str] | None = None,
) -> ResolveResult:
    """Locate one resource and gate it.

    An unknown id is ``found=False, allowed=False`` — not a 404 that the caller
    might read as "no opinion". A resolver that stays silent about things it
    does not know is a resolver callers learn to route around.
    """
    entries = build_registry(vault=vault, state=state, openvault_url=openvault_url)
    match = next((e for e in entries if e.kind == kind and e.id == resource_id), None)
    if match is None:
        return ResolveResult(
            found=False,
            allowed=False,
            kind=kind,
            id=resource_id,
            intent=intent,
            reasons=[f"no {kind} registered as '{resource_id}'"],
        )

    gate_action = _INTENT_TO_GATE.get(intent)
    if gate_action is None:
        return ResolveResult(
            found=True,
            allowed=False,
            kind=kind,
            id=resource_id,
            intent=str(intent),
            owner=match.owner,
            location=match.location,
            reasons=[f"unknown intent '{intent}'"],
            entry=match.to_dict(),
        )

    decision: GateDecision = check_gate(
        action=gate_action,
        vault=vault,
        fallback=fallback,
        destination=match.location,
        required_providers=required_providers,
    )

    reasons = list(decision.reasons)
    allowed = decision.allowed

    # A location that is known to be down is still a legitimate answer to
    # "where does this live", but it is not a green light to go there.
    if allowed and match.reachable is False:
        allowed = False
        reasons.append(f"{match.owner} is offline at {match.location or 'unknown address'}")

    return ResolveResult(
        found=True,
        allowed=allowed,
        kind=kind,
        id=resource_id,
        intent=intent,
        owner=match.owner,
        location=match.location,
        reasons=reasons,
        gate=decision.to_dict(),
        entry=match.to_dict(),
    )


def uptime_payload(
    *,
    state: LocalMeshState | None = None,
    started_at: float | None = None,
) -> dict[str, Any]:
    """Per-surface uptime for the mesh, plus this process's own.

    ``up`` is tri-state on purpose: ``None`` means never probed. Collapsing that
    into ``false`` would show a wall of red on a fresh start and train everyone
    to ignore the panel. Call ``POST /api/local/mesh/refresh`` to turn unknowns
    into answers.
    """
    state = state if state is not None else load_mesh()
    start = started_at if started_at is not None else _STARTED_AT
    now = time.time()

    surfaces: list[dict[str, Any]] = []
    for peer_kind, peer in sorted(state.peers.items()):
        up = _peer_reachable(state, peer_kind)
        surfaces.append(
            {
                "id": peer_kind,
                "label": peer.name or peer_kind,
                "base_url": peer.base_url,
                "up": up,
                "status": peer.status,
                "last_seen": peer.last_seen,
                "seconds_since_seen": (now - peer.last_seen) if peer.last_seen else None,
                "approved": peer.approved,
            }
        )

    known = [s for s in surfaces if s["up"] is not None]
    return {
        "openvault": {
            "up": True,
            "started_at": start,
            "uptime_seconds": round(now - start, 3),
        },
        "surfaces": surfaces,
        "summary": {
            "total": len(surfaces),
            "up": sum(1 for s in surfaces if s["up"] is True),
            "down": sum(1 for s in surfaces if s["up"] is False),
            "unknown": len(surfaces) - len(known),
        },
        "mesh_updated_at": state.updated_at,
        "note": "up=null means never probed, not down. POST /api/local/mesh/refresh to probe.",
    }
