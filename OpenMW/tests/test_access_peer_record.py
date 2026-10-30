"""Lock the access-gate peer snapshot to the mesh Peer record.

``_PeerRecord`` is a second copy of ``mesh.local_mesh.Peer``. ``_PeerRecord(**row)``
raises if a field written to disk is missing here, and the registry mis-labels
peers if the default ports diverge. This test fails when either side drifts.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path
from urllib.parse import urlparse

import pytest

from openmw.openvault.mesh.local_mesh import DEFAULT_NETIE_KB_URL, DEFAULT_PORTS, Peer
from openmw.openvault.route import access as access_mod
from openmw.openvault.route.access import _default_mesh, _PeerRecord

_PORT_ENV = ("CORTEX_URL", "NETIE_KB_URL", "OPENIDE_URL", "OPENVAULT_PORT", "OPENVAULT_RUST_URL")


def _env_get_default(tree: ast.AST, name: str) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get":
            continue
        key = node.args[0]
        default = node.args[1]
        if (
            isinstance(key, ast.Constant)
            and key.value == name
            and isinstance(default, ast.Constant)
            and isinstance(default.value, str)
        ):
            return default.value
    raise AssertionError(f"access.py has no os.environ.get default for {name}")


def _loopback_port(url: str) -> int:
    parsed = urlparse(url)
    if parsed.port is None:
        raise AssertionError(f"not a loopback port url: {url}")
    return parsed.port


def _hardcoded_loopback_ports(tree: ast.AST) -> set[int]:
    ports: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            # f"http://127.0.0.1:{port}" splits into a prefix with no port.
            if text.startswith("http://127.0.0.1:") and urlparse(text).port is not None:
                ports.add(_loopback_port(text))
    return ports


def test_access_peer_record_matches_mesh_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tuple(field.name for field in fields(_PeerRecord)) == tuple(
        field.name for field in fields(Peer)
    )

    source = Path(access_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    relied = {
        "openvault": int(_env_get_default(tree, "OPENVAULT_PORT")),
        "cortex": _loopback_port(access_mod._DEFAULT_CORTEX_URL),
        "openide": _loopback_port(_env_get_default(tree, "OPENIDE_URL")),
        "rust_console": _loopback_port(_env_get_default(tree, "OPENVAULT_RUST_URL")),
    }
    assert relied == DEFAULT_PORTS

    kb_port = _loopback_port(DEFAULT_NETIE_KB_URL)
    assert _loopback_port(access_mod._DEFAULT_NETIE_KB_URL) == kb_port
    hardcoded = _hardcoded_loopback_ports(tree)
    hardcoded.add(relied["openvault"])
    assert hardcoded - {kb_port} == set(DEFAULT_PORTS.values())

    for name in _PORT_ENV:
        monkeypatch.delenv(name, raising=False)
    view = _default_mesh()
    live = {kind: _loopback_port(peer.base_url) for kind, peer in view.peers.items()}
    assert live == DEFAULT_PORTS
