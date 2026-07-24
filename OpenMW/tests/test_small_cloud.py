"""Small Software LAN cloud + firewall deny-bypass tests."""

from __future__ import annotations

from pathlib import Path

from openmw.openvault.cloud.firewall import evaluate_action
from openmw.openvault.cloud.lan_discover import discover_lan_devices
from openmw.openvault.cloud.multiplayer import create_session, join_session
from openmw.openvault.cloud.share_store import ShareStore


def test_bypass_flags_hard_denied() -> None:
    d = evaluate_action("share_lan", client_flags={"bypass": True})
    assert d.allowed is False
    assert d.level == "deny"
    assert any("WARN" in r for r in d.reasons)

    d2 = evaluate_action("bypass_gate")
    assert d2.allowed is False


def test_public_internet_share_denied() -> None:
    d = evaluate_action("share_lan", destination="https://evil.example.com/app")
    assert d.allowed is False


def test_lan_share_and_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    store = ShareStore(db_path=tmp_path / "shares.db")
    app = store.publish(
        title="Team Todo",
        summary="Small software shared on LAN",
        source_path="apps/todo",
        env_edge={"PORT": "5173", "API_KEY": "should-strip"},
    )
    assert "API_KEY" not in app.env_edge
    assert app.env_edge.get("PORT") == "5173"
    assert store.get_by_code(app.share_code) is not None

    fw = evaluate_action("share_lan", destination="http://127.0.0.1:5173", peer_ip="127.0.0.1")
    assert fw.allowed is True

    sess = create_session(title="Pair on Todo", owner="alice", share_id=app.id)
    joined = join_session(sess.id, user="bob", peer_ip="192.168.1.20")
    assert joined is not None
    assert "bob" in joined.participants


def test_discover_lan_devices_shape() -> None:
    out = discover_lan_devices()
    assert out["ok"] is True
    assert "devices" in out
    assert isinstance(out["devices"], list)
