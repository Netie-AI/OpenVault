"""Ship recommend + upload + preflight wiring."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.recommend import recommend_target


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
    client = TestClient(app)
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
    client = TestClient(app)
    res = client.post("/api/ship/preflight", json={"target": "local_demo"})
    assert res.status_code == 200
    assert res.json()["ready"] is True


def test_preflight_cloudflare_missing_token() -> None:
    app = create_app(mock_health=True)
    client = TestClient(app)
    res = client.post("/api/ship/preflight", json={"target": "cloudflare_pages"})
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is False
    assert body["blocker"]
