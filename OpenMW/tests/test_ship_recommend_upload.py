"""Ship recommend + upload + preflight wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.recommend import recommend_target

#: Cleared so these tests do not read the developer's shell. Note the honest
#: scope: preflight resolves the Cloudflare/Netlify *token* from the vault via
#: ``from_vault``, not from the environment, so clearing these is not what makes
#: the assertions hold - the empty vault below is. What the environment does
#: still feed is ``CLOUDFLARE_ACCOUNT_ID`` (cloudflare_pages.py:332) and the
#: OpenShip settings, which is reason enough to start from a known-empty shell.
_HOST_CREDENTIALS = (
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "NETLIFY_AUTH_TOKEN",
    "NETLIFY_SITE_ID",
    "COOLIFY_TOKEN",
    "COOLIFY_URL",
    "COOLIFY_APP_UUID",
    "OPENSHIP_TOKEN",
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Point every test in this module at an empty vault and a clean shell.

    ``create_app()`` with no vault argument opens whatever ``OPENVAULT_HOME``
    points at - in a developer shell, their real key store. These four
    preflight tests assert that a host credential is *absent*, so any row the
    vault happens to hold for that provider inverts the assertion: same commit,
    different machine, opposite result.

    Latent rather than live at the time of writing - neither vault on this
    machine holds a cloudflare/netlify/coolify row - which is exactly why it
    was worth pinning before someone vaults one and spends an afternoon on it.
    """
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENVAULT_KEY", Fernet.generate_key().decode())
    for name in _HOST_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)


def test_recommend_static_next() -> None:
    out = recommend_target({"primary": "nextjs", "category": "frontend", "output_directory": "out"})
    assert out["target"] == "cloudflare_pages"
    assert out["real_publish"] is True
    assert "Pages" in out["reason"] or "static" in out["reason"].lower()


def test_recommend_server_fastapi() -> None:
    out = recommend_target({"primary": "fastapi", "category": "backend"})
    assert out["target"] == "openship_cloud"
    assert out["real_publish"] is False


def test_recommend_never_autoselects_sponsored() -> None:
    out = recommend_target(
        {"primary": "nextjs", "category": "frontend", "output_directory": "out"},
        sponsored_ids={"cloudflare_pages"},
    )
    assert out["target"] != "cloudflare_pages"
    assert out["sponsored_blocked"] is True
    assert out["organic_would_have_been"] == "cloudflare_pages"


def test_upload_zip_and_scan(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from openmw.openvault import paths as ov_paths

    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    ov_paths.clear_caches() if hasattr(ov_paths, "clear_caches") else None

    app = create_app(mock_health=True)
    client = TestClient(app, client=("127.0.0.1", 5555))
    session = client.post("/api/ship/library/upload-session").json()
    sid = session["session_id"]

    # minimal zip with index.html
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<html>hi</html>")
    buf.seek(0)
    up = client.post(
        f"/api/ship/library/upload-session/{sid}/files",
        files={"file": ("site.zip", buf.getvalue(), "application/zip")},
    )
    assert up.status_code == 200
    assert up.json()["ok"] is True
    assert up.json()["files_written"] >= 1

    scan = client.post(f"/api/ship/library/upload-session/{sid}/scan")
    assert scan.status_code == 200
    body = scan.json()
    assert body["ok"] is True
    assert body["file_count"] >= 1


def test_preflight_local_demo() -> None:
    app = create_app(mock_health=True)
    client = TestClient(app, client=("127.0.0.1", 5555))
    res = client.post("/api/ship/preflight", json={"target": "local_demo"})
    assert res.status_code == 200
    assert res.json()["ready"] is True


def test_preflight_cloudflare_missing_token() -> None:
    app = create_app(mock_health=True)
    client = TestClient(app, client=("127.0.0.1", 5555))
    res = client.post("/api/ship/preflight", json={"target": "cloudflare_pages"})
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is False
    assert body["blocker"]


def test_preflight_netlify_missing_token() -> None:
    app = create_app(mock_health=True)
    client = TestClient(app, client=("127.0.0.1", 5555))
    res = client.post("/api/ship/preflight", json={"target": "netlify"})
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is False
    assert body["real_publish"] is True
    assert "token" in body["blocker"].lower()
    assert body["blocker"]


def test_recommend_server_prefers_the_users_own_vps_when_connected() -> None:
    """With a box connected, the honest pick is one we can really publish to."""
    out = recommend_target({"primary": "fastapi", "category": "backend"}, vps_configured=True)
    assert out["target"] == "vps_ssh"
    assert out["real_publish"] is True


def test_preflight_vps_without_a_host_is_actionable_not_a_generic_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENVAULT_VPS_HOST", raising=False)
    client = TestClient(create_app(), client=("127.0.0.1", 5555))
    body = client.post("/api/ship/preflight", json={"target": "vps_ssh"}).json()
    assert body["ready"] is False
    assert body["real_publish"] is True, "vps_ssh is a real host now, not a teach target"
    assert "VPS" in body["blocker"]
