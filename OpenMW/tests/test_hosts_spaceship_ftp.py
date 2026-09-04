"""Spaceship FTP adapter - existing host only; never invent a URL."""

from __future__ import annotations

from pathlib import Path

from openmw.openvault.ship.hosts import ADAPTERS, adapter_ids, needs_local_build
from openmw.openvault.ship.hosts.spaceship_ftp import (
    SpaceshipFtpAdapter,
    host_configured,
    iter_upload_relpaths,
    normalize_public_url,
    public_ftp_env_refused,
    publish_allowed,
)
from openmw.openvault.ship.recommend import recommend_target


class TestHelpers:
    def test_normalize_public_url_never_invents_host(self) -> None:
        assert normalize_public_url("https://netie.ai") == "https://netie.ai"
        assert normalize_public_url("http://example.com/") == "http://example.com"
        assert normalize_public_url("netie.ai") == ""
        assert normalize_public_url("") == ""

    def test_host_configured_needs_host_and_user(self) -> None:
        assert host_configured(host="", user="ship@x") is False
        assert host_configured(host="ftp.example", user="") is False
        assert host_configured(host="ftp.example", user="ship@x") is True

    def test_publish_allowed_is_opt_in(self) -> None:
        assert publish_allowed("") is False
        assert publish_allowed("1") is True
        assert publish_allowed("yes") is True

    def test_dotenv_excluded_from_upload(self, tmp_path: Path) -> None:
        artifact = tmp_path / "out"
        artifact.mkdir()
        (artifact / "index.html").write_text("ok", encoding="utf-8")
        (artifact / ".env").write_text("SECRET=1", encoding="utf-8")
        (artifact / ".env.local").write_text("SECRET=2", encoding="utf-8")
        (artifact / ".env.example").write_text("SECRET=", encoding="utf-8")
        rels = iter_upload_relpaths(artifact)
        assert "index.html" in rels
        assert ".env" not in rels
        assert ".env.local" not in rels
        assert ".env.example" in rels


class TestRegistry:
    def test_spaceship_registered(self) -> None:
        assert "spaceship_ftp" in ADAPTERS
        assert ADAPTERS["spaceship_ftp"] is SpaceshipFtpAdapter
        assert "spaceship_ftp" in adapter_ids()
        assert needs_local_build("spaceship_ftp") is True


class TestPreflightAndDeploy:
    def test_missing_host_is_actionable(self) -> None:
        pre = SpaceshipFtpAdapter(
            host=None, user="ship@x", password="secret"
        ).preflight()
        assert pre.ready is False
        assert "SPACESHIP_FTP_HOST" in pre.blocker

    def test_refuse_overwrite_without_allow_and_no_url(self, tmp_path: Path) -> None:
        artifact = tmp_path / "out"
        artifact.mkdir()
        (artifact / "index.html").write_text("ok", encoding="utf-8")
        adapter = SpaceshipFtpAdapter(
            host="ftp.example",
            user="ship@example",
            password="secret",
            public_url="https://example.com",
            allow_publish=False,
        )
        result = adapter.deploy(artifact, project="site")
        assert result.ok is False
        assert result.url == ""
        assert "ALLOW_PUBLISH" in result.detail

    def test_upload_without_configured_url_does_not_invent(
        self, tmp_path: Path
    ) -> None:
        artifact = tmp_path / "out"
        artifact.mkdir()
        (artifact / "index.html").write_text("ok", encoding="utf-8")

        def _upload(*_args: object) -> int:
            return 1

        adapter = SpaceshipFtpAdapter(
            host="ftp.example",
            user="ship@example",
            password="secret",
            public_url="",
            allow_publish=True,
            probe=lambda _url: True,
            uploader=_upload,
        )
        result = adapter.deploy(artifact, project="site")
        assert result.ok is False
        assert result.url == ""
        assert "invent" in result.detail.lower()

    def test_observed_url_only_after_probe(self, tmp_path: Path) -> None:
        artifact = tmp_path / "out"
        artifact.mkdir()
        (artifact / "index.html").write_text("ok", encoding="utf-8")

        adapter = SpaceshipFtpAdapter(
            host="ftp.example",
            user="ship@example",
            password="secret",
            public_url="https://example.com",
            allow_publish=True,
            probe=lambda url: url == "https://example.com",
            uploader=lambda *_a: 1,
        )
        result = adapter.deploy(artifact, project="site")
        assert result.ok is True
        assert result.url == "https://example.com"

        adapter_miss = SpaceshipFtpAdapter(
            host="ftp.example",
            user="ship@example",
            password="secret",
            public_url="https://example.com",
            allow_publish=True,
            probe=lambda _url: False,
            uploader=lambda *_a: 1,
        )
        miss = adapter_miss.deploy(artifact, project="site")
        assert miss.ok is False
        assert miss.url == ""


class TestEnvInject:
    def test_public_dir_refuses_to_write_secrets(self) -> None:
        note = public_ftp_env_refused({"GROQ_API_KEY": "sk-secret"}, "", "public_html")
        assert "SPACESHIP_FTP_ENV_DIR" in note
        assert "sk-secret" not in note
        same = public_ftp_env_refused({"GROQ_API_KEY": "sk-secret"}, "public_html", "public_html")
        assert "must differ" in same
        assert public_ftp_env_refused({"GROQ_API_KEY": "sk-secret"}, "private", "public_html") == ""

    def test_deploy_does_not_write_env_to_public_dir(self, tmp_path: Path) -> None:
        artifact = tmp_path / "out"
        artifact.mkdir()
        (artifact / "index.html").write_text("ok", encoding="utf-8")
        written: list[object] = []
        adapter = SpaceshipFtpAdapter(
            host="ftp.example",
            user="ship@example",
            password="secret",
            remote_dir="public_html",
            public_url="https://example.com",
            allow_publish=True,
            probe=lambda _url: True,
            uploader=lambda *_a: 1,
            env_writer=lambda *_a: written.append(_a),
        )
        result = adapter.deploy(
            artifact, project="site", env={"GROQ_API_KEY": "sk-secret-never-echo"}
        )
        assert result.ok is True
        assert written == []
        assert "sk-secret" not in result.detail
        assert "not written" in result.detail

    def test_distinct_env_dir_writes_names_not_values(self, tmp_path: Path) -> None:
        artifact = tmp_path / "out"
        artifact.mkdir()
        (artifact / "index.html").write_text("ok", encoding="utf-8")
        captured: dict[str, object] = {}

        def _write(
            host: str, user: str, password: str, env_dir: str, env: dict[str, str]
        ) -> None:
            captured["dir"] = env_dir
            captured["env"] = dict(env)

        adapter = SpaceshipFtpAdapter(
            host="ftp.example",
            user="ship@example",
            password="secret",
            remote_dir="public_html",
            env_dir="private",
            public_url="https://example.com",
            allow_publish=True,
            probe=lambda _url: True,
            uploader=lambda *_a: 1,
            env_writer=_write,
        )
        result = adapter.deploy(
            artifact, project="site", env={"GROQ_API_KEY": "sk-secret-never-echo"}
        )
        assert result.ok is True
        assert captured["dir"] == "private"
        assert captured["env"] == {"GROQ_API_KEY": "sk-secret-never-echo"}
        assert "sk-secret" not in result.detail
        assert "1 vault env" in result.detail


def test_recommend_prefers_existing_spaceship_for_static() -> None:
    out = recommend_target(
        {"primary": "nextjs", "category": "frontend", "output_directory": "out"},
        spaceship_configured=True,
    )
    assert out["target"] == "spaceship_ftp"
    assert out["real_publish"] is True
    assert "Spaceship" in out["reason"]
