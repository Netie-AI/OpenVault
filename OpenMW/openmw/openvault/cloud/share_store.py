"""Shared Small Software app registry — LAN share like a Google Doc.

Stores metadata + relative path / git URL / scaffold slug. Never stores secrets.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openmw.openvault.paths import ensure_home


@dataclass
class SharedApp:
    id: str
    title: str
    slug: str
    summary: str
    source_path: str
    owner: str
    visibility: str  # lan | loopback | invite
    share_code: str
    created_at: float
    updated_at: float
    peers_allowed: list[str]
    env_edge: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _db_path() -> Path:
    return ensure_home() / "small_cloud_shares.db"


class ShareStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else _db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shares (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT 'lan',
                    share_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    peers_json TEXT NOT NULL DEFAULT '[]',
                    env_edge_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def publish(
        self,
        *,
        title: str,
        slug: str = "",
        summary: str = "",
        source_path: str = "",
        owner: str = "",
        visibility: str = "lan",
        peers_allowed: list[str] | None = None,
        env_edge: dict[str, str] | None = None,
    ) -> SharedApp:
        now = time.time()
        app_id = uuid.uuid4().hex[:12]
        share_code = uuid.uuid4().hex[:8]
        slug = (slug or title).lower().replace(" ", "-")[:48]
        vis = visibility if visibility in ("lan", "loopback", "invite") else "lan"
        edge: dict[str, str] = {}
        for k, v in (env_edge or {}).items():
            ku = k.upper()
            if any(x in ku for x in ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")):
                continue
            edge[str(k)[:64]] = str(v)[:200]
        row = SharedApp(
            id=app_id,
            title=(title or slug)[:120],
            slug=slug,
            summary=(summary or "")[:500],
            source_path=(source_path or "")[:500],
            owner=(owner or "local")[:80],
            visibility=vis,
            share_code=share_code,
            created_at=now,
            updated_at=now,
            peers_allowed=list(peers_allowed or []),
            env_edge=edge,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO shares(
                    id, title, slug, summary, source_path, owner, visibility,
                    share_code, created_at, updated_at, peers_json, env_edge_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row.id,
                    row.title,
                    row.slug,
                    row.summary,
                    row.source_path,
                    row.owner,
                    row.visibility,
                    row.share_code,
                    row.created_at,
                    row.updated_at,
                    json.dumps(row.peers_allowed),
                    json.dumps(row.env_edge),
                ),
            )
        return row

    def list_shares(self) -> list[SharedApp]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM shares ORDER BY updated_at DESC")
            return [self._row(r) for r in cur.fetchall()]

    def get(self, share_id: str) -> SharedApp | None:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM shares WHERE id=?", (share_id,))
            r = cur.fetchone()
            return self._row(r) if r else None

    def get_by_code(self, code: str) -> SharedApp | None:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM shares WHERE share_code=?", (code,))
            r = cur.fetchone()
            return self._row(r) if r else None

    def _row(self, r: sqlite3.Row) -> SharedApp:
        return SharedApp(
            id=r["id"],
            title=r["title"],
            slug=r["slug"],
            summary=r["summary"],
            source_path=r["source_path"],
            owner=r["owner"],
            visibility=r["visibility"],
            share_code=r["share_code"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            peers_allowed=json.loads(r["peers_json"] or "[]"),
            env_edge=json.loads(r["env_edge_json"] or "{}"),
        )
