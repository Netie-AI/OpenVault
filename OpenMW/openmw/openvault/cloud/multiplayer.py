"""Multiplayer agent sessions — shared live work (LAN).

v0: in-memory + JSON persistence under OPENVAULT_HOME. Peers join by session id.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openmw.openvault.paths import ensure_home

_LOCK = threading.Lock()
_SESSIONS: dict[str, AgentSession] = {}


@dataclass
class AgentSession:
    id: str
    title: str
    owner: str
    created_at: float
    updated_at: float
    status: str  # live | paused | done
    participants: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    share_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _persist_path() -> Path:
    return ensure_home() / "multiplayer_sessions.json"


def _save_all() -> None:
    payload = {k: v.to_dict() for k, v in _SESSIONS.items()}
    _persist_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_all() -> None:
    path = _persist_path()
    if not path.is_file() or _SESSIONS:
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    for sid, row in (raw or {}).items():
        _SESSIONS[sid] = AgentSession(
            id=row["id"],
            title=row.get("title") or "",
            owner=row.get("owner") or "",
            created_at=float(row.get("created_at") or time.time()),
            updated_at=float(row.get("updated_at") or time.time()),
            status=row.get("status") or "live",
            participants=list(row.get("participants") or []),
            events=list(row.get("events") or [])[-200:],
            share_id=row.get("share_id") or "",
        )


def create_session(*, title: str, owner: str = "local", share_id: str = "") -> AgentSession:
    _load_all()
    now = time.time()
    sess = AgentSession(
        id=uuid.uuid4().hex[:12],
        title=(title or "Shared agent")[:120],
        owner=(owner or "local")[:80],
        created_at=now,
        updated_at=now,
        status="live",
        participants=[owner or "local"],
        events=[{"t": now, "type": "created", "by": owner or "local"}],
        share_id=share_id[:32],
    )
    with _LOCK:
        _SESSIONS[sess.id] = sess
        _save_all()
    return sess


def list_sessions() -> list[AgentSession]:
    _load_all()
    return sorted(_SESSIONS.values(), key=lambda s: s.updated_at, reverse=True)


def get_session(session_id: str) -> AgentSession | None:
    _load_all()
    return _SESSIONS.get(session_id)


def join_session(session_id: str, *, user: str, peer_ip: str = "") -> AgentSession | None:
    _load_all()
    with _LOCK:
        sess = _SESSIONS.get(session_id)
        if sess is None:
            return None
        u = (user or "guest")[:80]
        if u not in sess.participants:
            sess.participants.append(u)
        sess.updated_at = time.time()
        sess.events.append({"t": sess.updated_at, "type": "join", "by": u, "peer_ip": peer_ip[:64]})
        sess.events = sess.events[-200:]
        _save_all()
        return sess


def post_event(
    session_id: str, *, user: str, event_type: str, detail: str = ""
) -> AgentSession | None:
    _load_all()
    with _LOCK:
        sess = _SESSIONS.get(session_id)
        if sess is None:
            return None
        sess.updated_at = time.time()
        sess.events.append(
            {
                "t": sess.updated_at,
                "type": (event_type or "note")[:40],
                "by": (user or "guest")[:80],
                "detail": (detail or "")[:1000],
            }
        )
        sess.events = sess.events[-200:]
        _save_all()
        return sess
