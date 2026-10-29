"""Light app: files are built, then the ship engine receives that folder."""

from __future__ import annotations

from pathlib import Path

from openmw.openvault.ship.hosts.base import DeployResult
from openmw.openvault.superapp.light import build_light_app, ship_light_app


def test_light_app_copies_html_into_dist(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>light</h1>", encoding="utf-8")
    built = build_light_app(str(tmp_path))
    assert built["category"] == "static"
    assert (tmp_path / "dist" / "index.html").read_text(encoding="utf-8") == "<h1>light</h1>"


def test_light_app_deploy_hands_the_folder_to_cloudflare(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "index.html").write_text("<h1>light</h1>", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_deploy(self, artifact_dir, *, project):  # noqa: ANN001
        seen["artifact"] = Path(artifact_dir)
        seen["project"] = project
        html = Path(artifact_dir) / "index.html"
        assert html.is_file()
        return DeployResult(ok=True, url="https://light.pages.dev", detail="uploaded")

    monkeypatch.setattr(
        "openmw.openvault.ship.hosts.cloudflare_pages.CloudflarePagesAdapter.deploy",
        fake_deploy,
    )
    result = ship_light_app(str(tmp_path), keyed_providers={"cloudflare"})
    assert result["plan"]["recommended_cloud"] == "cloudflare_pages"
    assert result["plan"]["parallel"] is False
    assert Path(seen["artifact"]).joinpath("index.html").is_file()
    assert result["ship"]["deployment"]["public_url"] == "https://light.pages.dev"
    assert result["ship"]["ok"] is True
