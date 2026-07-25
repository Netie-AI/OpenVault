"""Regression tests for stack detection.

Every case here reproduces a defect a live probe found in the previous detector
(1 pass / 15 fail over 16 real and synthetic projects). Cases against real trees
skip when the tree is absent so the suite stays green on another machine; the
synthetic cases carry the same defects and always run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.routers.ship import router as ship_router
from openmw.openvault.ship.detect import DetectionInputError, detect_project

VENDOR_OPENSHIP = Path(r"D:\OpenVault\vendor\openship")
OPENMW_ROOT = Path(__file__).resolve().parents[1]
CORTEX = Path(r"D:\Cortex")


def _requires(path: Path) -> None:
    if not path.is_dir():
        pytest.skip(f"tree not present on this machine: {path}")


# ─── Input validation (defect 7) ─────────────────────────────────────────────


def test_empty_path_is_rejected_not_resolved_to_cwd() -> None:
    """Path("").resolve() is the server's own directory — never a project."""
    with pytest.raises(DetectionInputError):
        detect_project("")


def test_relative_path_is_rejected() -> None:
    with pytest.raises(DetectionInputError):
        detect_project("some/relative/project")


def test_nonexistent_path_reports_unknown(tmp_path: Path) -> None:
    stack = detect_project(tmp_path / "does-not-exist")
    assert stack.primary == "unknown"
    assert stack.confidence == 0.0
    assert stack.signals == ["path_missing_or_not_dir"]


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ship_router)
    return TestClient(app)


def test_api_detect_empty_path_is_400() -> None:
    response = _client().post("/api/detect", json={"project_path": ""})
    assert response.status_code == 400


def test_api_detect_relative_path_is_400() -> None:
    response = _client().post("/api/detect", json={"project_path": "./app"})
    assert response.status_code == 400


def test_api_detect_returns_the_stack(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"hono": "^4"}}), encoding="utf-8"
    )
    response = _client().post("/api/detect", json={"project_path": str(tmp_path)})
    assert response.status_code == 200
    assert response.json()["framework"] == "hono"


# ─── Docker must not shadow a real language (defect 1) ───────────────────────


def test_python_wins_over_docker_compose(tmp_path: Path) -> None:
    """A compose file is a deployment hint, not a language."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='svc'\ndependencies=['fastapi>=0.1']\n", encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )
    (tmp_path / "docker-compose.yml").write_text("services:\n  api: {}\n", encoding="utf-8")

    stack = detect_project(tmp_path)
    assert stack.primary == "python"
    assert stack.framework == "fastapi"
    assert stack.install_command == "uv sync"
    # The old detector returned framework "docker-compose" with all three
    # commands empty for exactly this layout.
    assert stack.start_command == "uv run uvicorn main:app --host 0.0.0.0 --port 8000"
    assert "compose" in stack.suggested_services  # the hint is kept, not promoted


def test_node_wins_over_docker_compose(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "^16"},
                "scripts": {"build": "next build", "start": "next start"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text("services:\n  web: {}\n", encoding="utf-8")
    assert detect_project(tmp_path).framework == "nextjs"


def test_compose_only_project_still_detects_compose(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.primary == "docker-compose"
    # Still runnable: the deploy plan needs commands, not just a label.
    assert stack.suggested_build == ["docker compose build", "docker compose up -d"]


def test_dockerfile_only_project(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\nEXPOSE 9000\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.primary == "dockerfile"
    assert stack.framework == "docker"
    assert stack.production_port == 9000
    assert stack.suggested_build == ["docker build -t app .", "docker run -d --name app app"]


def test_static_site_has_no_start_command_and_says_so(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>hi</h1>\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.framework == "static"
    assert stack.category == "static"
    assert stack.start_command == ""
    assert stack.warnings == []  # a static site has nothing to run by design


@pytest.mark.parametrize("tree", [CORTEX])
def test_cortex_is_python_not_docker_compose(tree: Path) -> None:
    _requires(tree)
    stack = detect_project(tree)
    assert stack.primary == "python"
    assert stack.framework in ("python", "fastapi")
    assert stack.install_command == "uv sync"  # Cortex ships uv.lock
    assert stack.start_command, "a Python project must get a start command"


# ─── Package-manager inference (defect 2) ────────────────────────────────────


def test_bun_lock_and_package_manager_field(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "bun@1.3.10",
                "dependencies": {"hono": "^4"},
                "scripts": {"build": "tsup src/index.ts", "start": "node dist/index.js"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("{}\n", encoding="utf-8")

    stack = detect_project(tmp_path)
    assert stack.framework == "hono"
    assert stack.package_manager == "bun"
    assert stack.install_command == "bun install"
    assert stack.build_command == "bun run build"
    assert stack.start_command == "bun run start"


def test_pnpm_lock_drives_vite_commands(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vite": "^5"}, "scripts": {"build": "vite build"}}),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    stack = detect_project(tmp_path)
    assert stack.framework == "vite"
    assert stack.package_manager == "pnpm"
    assert stack.install_command == "pnpm install"
    assert stack.build_command == "pnpm build"
    assert stack.output_directory == "dist"


@pytest.mark.parametrize(
    ("lockfile", "expected_pm", "expected_install"),
    [
        ("yarn.lock", "yarn", "yarn install"),
        ("package-lock.json", "npm", "npm install"),
    ],
)
def test_js_lockfiles(
    tmp_path: Path, lockfile: str, expected_pm: str, expected_install: str
) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"express": "^4"}}), encoding="utf-8"
    )
    (tmp_path / lockfile).write_text("\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.package_manager == expected_pm
    assert stack.install_command == expected_install


def test_requirements_only_django_installs_with_pip(tmp_path: Path) -> None:
    """The old detector hardcoded `uv sync` — impossible here, there is no pyproject."""
    (tmp_path / "requirements.txt").write_text("Django==5.0\ngunicorn\n", encoding="utf-8")
    (tmp_path / "manage.py").write_text(
        "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')\n",
        encoding="utf-8",
    )

    stack = detect_project(tmp_path)
    assert stack.framework == "django"
    assert stack.package_manager == "pip"
    assert stack.install_command == "pip install -r requirements.txt"
    assert "uv" not in stack.install_command
    assert stack.start_command == "gunicorn mysite.wsgi:application --bind 0.0.0.0:8000"


def test_poetry_lock_installs_with_poetry(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname='svc'\n\n[tool.poetry.dependencies]\nflask='^3'\n", encoding="utf-8"
    )
    (tmp_path / "poetry.lock").write_text("\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.package_manager == "poetry"
    assert stack.install_command == "poetry install"
    assert stack.framework == "flask"


def test_pipfile_installs_with_pipenv(tmp_path: Path) -> None:
    (tmp_path / "Pipfile").write_text('[packages]\nflask = "*"\n', encoding="utf-8")
    (tmp_path / "Pipfile.lock").write_text("{}\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.package_manager == "pipenv"
    assert stack.install_command == "pipenv install"


# ─── Monorepo scan, not a name whitelist (defect 4) ──────────────────────────


def _write_monorepo(root: Path, app_dir: str) -> None:
    root.joinpath("package.json").write_text(
        json.dumps({"name": "repo", "private": True, "workspaces": ["apps/*"]}), encoding="utf-8"
    )
    root.joinpath("pnpm-workspace.yaml").write_text('packages:\n  - "apps/*"\n', encoding="utf-8")
    root.joinpath("pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    app = root / app_dir
    app.mkdir(parents=True)
    app.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "site",
                "dependencies": {"next": "^16"},
                "scripts": {"build": "next build", "start": "next start"},
            }
        ),
        encoding="utf-8",
    )
    app.joinpath("next.config.mjs").write_text("export default {};\n", encoding="utf-8")


def test_monorepo_app_outside_the_old_whitelist(tmp_path: Path) -> None:
    """`apps/site` was missed entirely by the five blessed paths."""
    _write_monorepo(tmp_path, "apps/site")

    stack = detect_project(tmp_path)
    assert stack.root_directory == "apps/site"
    assert stack.framework == "nextjs"
    assert stack.package_manager == "pnpm"
    assert stack.install_command == "cd ../.. && pnpm install"
    assert stack.build_command == "pnpm build"
    assert stack.start_command == "pnpm start"
    assert stack.output_directory == "apps/site/.next"


def test_monorepo_ignores_library_packages(tmp_path: Path) -> None:
    _write_monorepo(tmp_path, "apps/site")
    lib = tmp_path / "packages" / "ui"
    lib.mkdir(parents=True)
    lib.joinpath("package.json").write_text(
        json.dumps({"name": "ui", "dependencies": {"react": "^19"}}), encoding="utf-8"
    )
    assert detect_project(tmp_path).root_directory == "apps/site"


def test_turbo_monorepo_without_a_workspace_field(tmp_path: Path) -> None:
    """No `workspaces` array anywhere — the sub-app is still found by scan."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "repo", "private": True, "scripts": {"build": "turbo run build"}}),
        encoding="utf-8",
    )
    (tmp_path / "turbo.json").write_text(json.dumps({"tasks": {"build": {}}}), encoding="utf-8")
    app = tmp_path / "apps" / "storefront"
    app.mkdir(parents=True)
    app.joinpath("package.json").write_text(
        json.dumps({"dependencies": {"astro": "^5"}, "scripts": {"build": "astro build"}}),
        encoding="utf-8",
    )

    stack = detect_project(tmp_path)
    assert stack.root_directory == "apps/storefront"
    assert stack.framework == "astro"
    assert stack.output_directory == "apps/storefront/dist"


def test_vercel_root_directory_hint_wins(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"name": "repo"}), encoding="utf-8")
    (tmp_path / "vercel.json").write_text(
        json.dumps({"buildCommand": "cd frontend && npm run build"}), encoding="utf-8"
    )
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    frontend.joinpath("package.json").write_text(
        json.dumps({"dependencies": {"next": "^16"}, "scripts": {"build": "next build"}}),
        encoding="utf-8",
    )

    stack = detect_project(tmp_path)
    assert stack.root_directory == "frontend"
    assert stack.framework == "nextjs"


@pytest.mark.parametrize("tree", [VENDOR_OPENSHIP])
def test_openship_monorepo(tree: Path) -> None:
    _requires(tree)
    stack = detect_project(tree)
    assert stack.root_directory == "apps/dashboard"
    assert stack.framework == "nextjs"
    # The repo declares packageManager bun@1.3.10 and ships bun.lock.
    assert stack.package_manager == "bun"
    assert stack.install_command == "cd ../.. && bun install"
    assert stack.build_command == "bun run build"
    assert stack.start_command == "bun run start"
    assert stack.output_directory == "apps/dashboard/.next"


@pytest.mark.parametrize("tree", [VENDOR_OPENSHIP / "apps" / "api"])
def test_openship_api_is_hono(tree: Path) -> None:
    _requires(tree)
    stack = detect_project(tree)
    assert stack.framework == "hono"
    assert stack.category == "backend"
    assert stack.primary == "node"


# ─── Real dependency parsing, not substring search (defect 5) ────────────────


def test_description_mentioning_django_is_not_django(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'scaffolder'\n"
        "description = 'a CLI that scaffolds flask and django apps'\n"
        "dependencies = ['typer>=0.12']\n",
        encoding="utf-8",
    )
    stack = detect_project(tmp_path)
    assert stack.framework == "python"
    assert stack.framework not in ("django", "flask")
    assert stack.primary == "python"


def test_readme_words_do_not_make_a_framework(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("fastapi django flask\n", encoding="utf-8")
    assert detect_project(tmp_path).framework == "python"


def test_optional_dependency_group_still_counts(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='svc'\ndependencies=[]\n\n"
        "[project.optional-dependencies]\napi = ['fastapi>=0.115']\n",
        encoding="utf-8",
    )
    assert detect_project(tmp_path).framework == "fastapi"


# ─── Commands come from scripts that exist (defect 6) ────────────────────────


def test_dev_only_package_json_invents_nothing(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "toy", "scripts": {"dev": "node server.js"}}), encoding="utf-8"
    )
    stack = detect_project(tmp_path)
    assert stack.framework == "node"
    assert stack.build_command == ""
    assert stack.start_command == ""
    assert stack.suggested_build == ["npm install"]
    assert any("build script" in warning for warning in stack.warnings)
    assert any("start script" in warning for warning in stack.warnings)


def test_main_field_is_used_when_there_is_no_start_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "toy", "main": "server.js"}), encoding="utf-8"
    )
    assert detect_project(tmp_path).start_command == "node server.js"


# ─── Entrypoints and ports (Python entrypoint + production_port) ─────────────


@pytest.mark.parametrize("tree", [OPENMW_ROOT])
def test_openmw_start_command_is_the_real_entrypoint(tree: Path) -> None:
    stack = detect_project(tree)
    assert stack.primary == "python"
    assert stack.framework == "fastapi"
    assert stack.package_manager == "uv"
    assert stack.install_command == "uv sync"
    # There is no main.py in this repo — the app comes from the create_app factory.
    assert stack.start_command != "uv run uvicorn main:app --host 0.0.0.0 --port 8000"
    assert "openmw.openvault.app:create_app" in stack.start_command
    assert "--factory" in stack.start_command
    assert "script:openmw" in stack.signals


def test_fastapi_module_level_app_is_found(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )
    stack = detect_project(tmp_path)
    assert stack.framework == "fastapi"
    assert stack.start_command == "uvicorn main:app --host 0.0.0.0 --port 8000"
    assert stack.install_command == "pip install -r requirements.txt"


def test_port_comes_from_the_start_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "^16"},
                "scripts": {"build": "next build", "start": "next start --port 3010"},
            }
        ),
        encoding="utf-8",
    )
    assert detect_project(tmp_path).production_port == 3010


def test_port_comes_from_dockerfile_expose(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"express": "^4"}}), encoding="utf-8"
    )
    (tmp_path / "Dockerfile").write_text("FROM node:22\nEXPOSE 8081\n", encoding="utf-8")
    assert detect_project(tmp_path).production_port == 8081


# ─── Metadata overlay ────────────────────────────────────────────────────────


def test_vercel_json_overrides_detected_commands(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vite": "^5"}, "scripts": {"build": "vite build"}}),
        encoding="utf-8",
    )
    (tmp_path / "vercel.json").write_text(
        json.dumps({"buildCommand": "npm run build:prod", "outputDirectory": "out"}),
        encoding="utf-8",
    )
    stack = detect_project(tmp_path)
    assert stack.build_command == "npm run build:prod"
    assert stack.output_directory == "out"
    assert "metadata:vercel" in stack.signals


# ─── Contract kept for existing callers ──────────────────────────────────────


def test_result_round_trips_through_to_dict(tmp_path: Path) -> None:
    """deploy.py/openship.py rebuild a stack with DetectedStack(**payload)."""
    from openmw.openvault.ship.detect import DetectedStack

    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", encoding="utf-8")
    stack = detect_project(tmp_path)
    assert stack.primary == "go"
    assert DetectedStack(**stack.to_dict()) == stack
