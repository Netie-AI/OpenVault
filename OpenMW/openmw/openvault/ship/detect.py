"""Auto-detect project stack for deploy orchestration (no manual type pick).

Language manifests beat Docker. Frameworks come from parsed dependency lists,
not README/description substrings. Monorepos are scanned (not a path whitelist).
Commands are taken from scripts that actually exist.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openmw.openvault.ship.stacks import STACKS, get_stack

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".next",
        ".turbo",
        ".venv",
        "__pycache__",
        "dist",
        "node_modules",
        "target",
        "vendor",
        "venv",
    }
)
_JS_FRAMEWORKS: tuple[tuple[str, str, int], ...] = (
    ("next", "nextjs", 100),
    ("astro", "astro", 90),
    ("hono", "hono", 85),
    ("vite", "vite", 70),
)
_PY_FRAMEWORKS: tuple[tuple[str, str], ...] = (
    ("fastapi", "fastapi"),
    ("django", "django"),
    ("flask", "flask"),
)
_PORT_IN_SCRIPT = re.compile(r"(?:--port|-p)\s+(\d+)")
_EXPOSE = re.compile(r"^EXPOSE\s+(\d+)", re.MULTILINE | re.IGNORECASE)
_DJANGO_SETTINGS = re.compile(r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)['\"]")
_CD_HINT = re.compile(r"\bcd\s+(\S+)\s+&&")
_CREATE_APP = re.compile(r"^def create_app\s*\(", re.MULTILINE)
_FASTAPI_APP = re.compile(r"^app\s*=\s*FastAPI\s*\(", re.MULTILINE)


class DetectionInputError(ValueError):
    """Empty or relative path — resolving it would describe the server cwd."""


@dataclass(frozen=True)
class DetectedStack:
    """Result of scanning a project directory for deployable signals."""

    project_path: str
    primary: str
    confidence: float
    signals: list[str] = field(default_factory=list)
    suggested_build: list[str] = field(default_factory=list)
    suggested_services: list[str] = field(default_factory=list)
    needs_database: bool = False
    needs_mail: bool = False
    framework: str = ""
    category: str = ""
    package_manager: str = ""
    install_command: str = ""
    build_command: str = ""
    start_command: str = ""
    output_directory: str = ""
    root_directory: str = "."
    production_port: int | None = None
    warnings: list[str] = field(default_factory=list)
    host_kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_project(project_path: str | Path) -> DetectedStack:
    """Infer stack from repo files — OpenShip-style without forcing a manual pick."""
    root = _require_absolute(project_path)
    if not root.is_dir():
        return DetectedStack(
            project_path=str(root),
            primary="unknown",
            confidence=0.0,
            signals=["path_missing_or_not_dir"],
            framework="unknown",
            host_kind="unknown",
        )

    vercel = _load_json(root / "vercel.json")
    compose_file = _first_existing(root, ("docker-compose.yml", "compose.yml", "compose.yaml"))
    dockerfile = root / "Dockerfile"
    has_compose = compose_file is not None
    has_docker = dockerfile.is_file()

    js = _detect_js(root, vercel)
    py = _detect_python(root)
    go = (root / "go.mod").is_file()
    rust = (root / "Cargo.toml").is_file()
    static_html = (root / "index.html").is_file()

    language: DetectedStack | None = None
    if js is not None:
        language = js
    elif py is not None:
        language = py
    elif go:
        spec = STACKS["go"]
        language = DetectedStack(
            project_path=str(root),
            primary="go",
            confidence=0.75,
            signals=["go.mod"],
            suggested_build=[spec.default_build_command, spec.default_start_command],
            suggested_services=["go"],
            framework="go",
            category=spec.category,
            build_command=spec.default_build_command,
            start_command=spec.default_start_command,
            production_port=spec.default_port,
            host_kind=spec.host_kind,
        )
    elif rust:
        spec = STACKS["rust"]
        language = DetectedStack(
            project_path=str(root),
            primary="rust",
            confidence=0.75,
            signals=["Cargo.toml"],
            suggested_build=[spec.default_build_command],
            suggested_services=["rust"],
            framework="rust",
            category=spec.category,
            build_command=spec.default_build_command,
            start_command=spec.default_start_command,
            output_directory=spec.output_directory,
            production_port=spec.default_port,
            host_kind=spec.host_kind,
        )
    elif static_html:
        spec = STACKS["static"]
        language = DetectedStack(
            project_path=str(root),
            primary="static",
            confidence=0.55,
            signals=["index.html"],
            suggested_build=[],
            suggested_services=["static-http"],
            framework="static",
            category="static",
            start_command="",
            output_directory=".",
            production_port=spec.default_port,
            host_kind="static_http",
        )

    if language is not None:
        services = list(language.suggested_services)
        signals = list(language.signals)
        if has_compose:
            services.append("compose")
            signals.append(compose_file.name if compose_file is not None else "compose")
        if has_docker:
            services.append("docker")
            signals.append("Dockerfile")
        port = language.production_port
        stack_spec = get_stack(language.framework) or get_stack(language.primary)
        default_port = stack_spec.default_port if stack_spec is not None else None
        if has_docker:
            exposed = _dockerfile_port(dockerfile)
            if exposed is not None and language.production_port in (None, default_port):
                port = exposed
        needs_db, needs_mail = _needs_db_mail(root, compose_file)
        return DetectedStack(
            project_path=language.project_path,
            primary=language.primary,
            confidence=language.confidence,
            signals=signals,
            suggested_build=list(language.suggested_build),
            suggested_services=services,
            needs_database=needs_db,
            needs_mail=needs_mail,
            framework=language.framework,
            category=language.category,
            package_manager=language.package_manager,
            install_command=language.install_command,
            build_command=language.build_command,
            start_command=language.start_command,
            output_directory=language.output_directory,
            root_directory=language.root_directory,
            production_port=port,
            warnings=list(language.warnings),
            host_kind=language.host_kind,
        )

    if has_compose:
        spec = STACKS["docker-compose"]
        return DetectedStack(
            project_path=str(root),
            primary="docker-compose",
            confidence=0.95,
            signals=[compose_file.name if compose_file is not None else "compose"],
            suggested_build=["docker compose build", "docker compose up -d"],
            suggested_services=["docker", "compose"],
            framework="docker-compose",
            category=spec.category,
            build_command=spec.default_build_command,
            start_command=spec.default_start_command,
            production_port=_dockerfile_port(dockerfile) or spec.default_port,
            host_kind="container",
            needs_database=_needs_db_mail(root, compose_file)[0],
            needs_mail=_needs_db_mail(root, compose_file)[1],
        )

    if has_docker:
        spec = STACKS["dockerfile"]
        port = _dockerfile_port(dockerfile) or spec.default_port
        return DetectedStack(
            project_path=str(root),
            primary="dockerfile",
            confidence=0.9,
            signals=["Dockerfile"],
            suggested_build=["docker build -t app .", "docker run -d --name app app"],
            suggested_services=["docker"],
            framework="docker",
            category=spec.category,
            build_command=spec.default_build_command,
            start_command=spec.default_start_command,
            production_port=port,
            host_kind="container",
        )

    return DetectedStack(
        project_path=str(root),
        primary="unknown",
        confidence=0.1,
        signals=["no_known_manifest"],
        framework="unknown",
        host_kind="unknown",
    )


def _require_absolute(project_path: str | Path) -> Path:
    raw = str(project_path)
    if not raw.strip():
        raise DetectionInputError(
            "empty project_path refused — would resolve to the server working directory"
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise DetectionInputError("relative project_path refused — send an absolute local path")
    return path.resolve()


def _first_existing(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        path = root / name
        if path.is_file():
            return path
    return None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        if sys.version_info >= (3, 11):
            import tomllib

            loaded = tomllib.loads(text)
        else:
            import tomli

            loaded = tomli.loads(text)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _detect_js(root: Path, vercel: dict[str, Any]) -> DetectedStack | None:
    pkg_root = _load_json(root / "package.json")
    hint = _vercel_root_hint(vercel)
    workspaces = _looks_like_js_monorepo(root, pkg_root)
    if not pkg_root and hint is None and not workspaces:
        return None
    if not pkg_root and hint is None:
        return None

    app_rel, pkg = _pick_js_app(root, pkg_root, hint)
    if pkg is None and not pkg_root:
        return None
    if pkg is None:
        pkg = pkg_root
        app_rel = "."

    pm_root = root
    pm = _js_package_manager(pm_root, pkg_root or pkg)
    framework, score = _js_framework(pkg)
    spec = get_stack(framework) or STACKS["node"]
    scripts_raw = pkg.get("scripts")
    scripts: dict[str, Any] = scripts_raw if isinstance(scripts_raw, dict) else {}
    warnings: list[str] = []
    install = _js_install(pm, app_rel)
    build = _js_script_cmd(pm, scripts, "build")
    start = _js_script_cmd(pm, scripts, "start")
    if not build:
        if "build" not in scripts:
            warnings.append("no build script in package.json")
        build = ""
    if not start:
        main = pkg.get("main")
        if isinstance(main, str) and main.strip():
            start = f"node {main.strip()}"
        elif "start" not in scripts:
            warnings.append("no start script in package.json")
            start = ""

    vercel_build = vercel.get("buildCommand")
    vercel_out = vercel.get("outputDirectory")
    signals = ["package.json"]
    if hint:
        signals.append("metadata:vercel")
    if vercel_build or vercel_out:
        signals.append("metadata:vercel")
    if isinstance(vercel_build, str) and vercel_build.strip():
        build = vercel_build.strip()
    out = spec.output_directory
    if isinstance(vercel_out, str) and vercel_out.strip():
        out = vercel_out.strip()
    output = _join_rel(app_rel, out)

    port = spec.default_port
    start_body = str(scripts.get("start") or "")
    match = _PORT_IN_SCRIPT.search(start_body)
    if match:
        port = int(match.group(1))

    suggested: list[str] = []
    if install:
        suggested.append(install)
    if build:
        suggested.append(build)

    lock_signals = _js_lock_signals(root)
    signals.extend(lock_signals)
    if (root / "turbo.json").is_file():
        signals.append("turbo.json")
    if app_rel not in (".", ""):
        signals.append(f"app:{app_rel}")

    confidence = 0.92 if score >= 70 else 0.8
    primary = "node"
    return DetectedStack(
        project_path=str(root),
        primary=primary,
        confidence=confidence,
        signals=signals,
        suggested_build=suggested,
        suggested_services=["node"],
        framework=framework,
        category=spec.category,
        package_manager=pm,
        install_command=install,
        build_command=build,
        start_command=start,
        output_directory=output,
        root_directory=app_rel,
        production_port=port,
        warnings=warnings,
        host_kind=spec.host_kind,
    )


def _looks_like_js_monorepo(root: Path, pkg: dict[str, Any]) -> bool:
    if (root / "pnpm-workspace.yaml").is_file() or (root / "turbo.json").is_file():
        return True
    workspaces = pkg.get("workspaces")
    return isinstance(workspaces, list) and bool(workspaces)


def _vercel_root_hint(vercel: dict[str, Any]) -> str | None:
    root_dir = vercel.get("rootDirectory")
    if isinstance(root_dir, str) and root_dir.strip():
        return root_dir.strip().strip("/")
    build = vercel.get("buildCommand")
    if isinstance(build, str):
        match = _CD_HINT.search(build)
        if match:
            return match.group(1).strip().strip("/")
    return None


def _pick_js_app(
    root: Path, pkg_root: dict[str, Any], hint: str | None
) -> tuple[str, dict[str, Any] | None]:
    if hint:
        hinted = root / hint / "package.json"
        data = _load_json(hinted)
        if data:
            return hint, data
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    if pkg_root:
        _, score = _js_framework(pkg_root)
        candidates.append((score, ".", pkg_root))
    for path in _iter_package_jsons(root):
        rel = path.parent.relative_to(root)
        if rel == Path("."):
            continue
        data = _load_json(path)
        if not data:
            continue
        _, score = _js_framework(data)
        rel_s = rel.as_posix()
        if (rel_s.startswith("packages/") or "/packages/" in f"/{rel_s}/") and score < 70:
            # Libraries (react-only UI kits) lose to apps/.
            score -= 20
        if rel_s.startswith("apps/"):
            score += 5
        candidates.append((score, rel_s, data))
    if not candidates:
        return ".", pkg_root or None
    candidates.sort(key=lambda item: (item[0], item[1] != ".", -len(item[1])), reverse=True)
    best = candidates[0]
    # A root package.json that is only a workspace shell should lose to a real app.
    if best[1] == "." and best[0] < 70:
        nested = [c for c in candidates if c[1] != "." and c[0] >= 70]
        if nested:
            nested.sort(key=lambda item: item[0], reverse=True)
            return nested[0][1], nested[0][2]
    return best[1], best[2]


def _iter_package_jsons(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("package.json"):
        rel = path.relative_to(root)
        if any(part in _SKIP_DIR_NAMES for part in rel.parts):
            continue
        if len(rel.parts) > 5:
            continue
        found.append(path)
    return found


def _js_framework(pkg: dict[str, Any]) -> tuple[str, int]:
    names = _dep_names(pkg)
    for dep, framework, score in _JS_FRAMEWORKS:
        if dep in names:
            return framework, score
    if names:
        return "node", 40
    return "node", 10


def _dep_names(pkg: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        block = pkg.get(key)
        if isinstance(block, dict):
            names.update(str(k) for k in block)
    return names


def _js_package_manager(root: Path, pkg: dict[str, Any]) -> str:
    field = str(pkg.get("packageManager") or "")
    if field.startswith("bun"):
        return "bun"
    if field.startswith("pnpm"):
        return "pnpm"
    if field.startswith("yarn"):
        return "yarn"
    if field.startswith("npm"):
        return "npm"
    if (root / "bun.lock").is_file() or (root / "bun.lockb").is_file():
        return "bun"
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "package-lock.json").is_file():
        return "npm"
    return "npm"


def _js_lock_signals(root: Path) -> list[str]:
    out: list[str] = []
    for name in ("bun.lock", "bun.lockb", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"):
        if (root / name).is_file():
            out.append(name)
    return out


def _js_install(pm: str, app_rel: str) -> str:
    cmd = {
        "bun": "bun install",
        "pnpm": "pnpm install",
        "yarn": "yarn install",
        "npm": "npm install",
    }.get(pm, "npm install")
    if app_rel in ("", "."):
        return cmd
    depth = len(Path(app_rel).parts)
    prefix = "/".join([".."] * depth)
    return f"cd {prefix} && {cmd}"


def _js_script_cmd(pm: str, scripts: dict[str, Any], name: str) -> str:
    if name not in scripts:
        return ""
    if pm == "npm":
        return f"npm run {name}" if name != "start" else "npm start"
    if pm == "yarn":
        return f"yarn {name}"
    if pm == "pnpm":
        return f"pnpm {name}"
    if pm == "bun":
        return f"bun run {name}"
    return f"npm run {name}"


def _join_rel(root_dir: str, output: str) -> str:
    if not output:
        return ""
    if root_dir in ("", "."):
        return output
    return f"{root_dir.rstrip('/')}/{output.lstrip('/')}"


def _detect_python(root: Path) -> DetectedStack | None:
    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"
    pipfile = root / "Pipfile"
    if not pyproject.is_file() and not requirements.is_file() and not pipfile.is_file():
        return None

    toml = _load_toml(pyproject) if pyproject.is_file() else {}
    pm = _python_pm(root, toml, requirements.is_file(), pipfile.is_file())
    dep_names = _python_dep_names(root, toml, requirements, pipfile)
    framework = "python"
    for pkg, name in _PY_FRAMEWORKS:
        if pkg in dep_names:
            framework = name
            break
    spec = get_stack(framework) or STACKS["python"]
    signals: list[str] = []
    if pyproject.is_file():
        signals.append("pyproject.toml")
    if (root / "uv.lock").is_file():
        signals.append("uv.lock")
    if (root / "poetry.lock").is_file():
        signals.append("poetry.lock")
    if requirements.is_file():
        signals.append("requirements.txt")
    if pipfile.is_file():
        signals.append("Pipfile")
    project_table = toml.get("project") if isinstance(toml.get("project"), dict) else {}
    scripts = (project_table.get("scripts") or {}) if isinstance(project_table, dict) else {}
    if isinstance(scripts, dict):
        for script_name in scripts:
            signals.append(f"script:{script_name}")

    install = {
        "uv": "uv sync",
        "poetry": "poetry install",
        "pipenv": "pipenv install",
        "pip": "pip install -r requirements.txt",
    }.get(pm, "uv sync")
    if pm == "pip" and not requirements.is_file():
        install = "pip install ."

    start = _python_start(root, framework, pm)
    suggested = [install]
    port_match = _PORT_IN_SCRIPT.search(start) if start else None
    production_port = int(port_match.group(1)) if port_match else spec.default_port
    confidence = 0.9 if framework != "python" else 0.8
    return DetectedStack(
        project_path=str(root),
        primary="python",
        confidence=confidence,
        signals=signals,
        suggested_build=suggested,
        suggested_services=["python"],
        framework=framework,
        category=spec.category,
        package_manager=pm,
        install_command=install,
        build_command="",
        start_command=start,
        production_port=production_port,
        host_kind=spec.host_kind,
    )


def _python_pm(root: Path, toml: dict[str, Any], has_req: bool, has_pipfile: bool) -> str:
    if (root / "uv.lock").is_file() or "uv" in (toml.get("tool") or {}):
        return "uv"
    if (root / "poetry.lock").is_file() or "poetry" in (toml.get("tool") or {}):
        return "poetry"
    if has_pipfile or (root / "Pipfile.lock").is_file():
        return "pipenv"
    if has_req and not (root / "pyproject.toml").is_file():
        return "pip"
    if (root / "pyproject.toml").is_file():
        return "uv"
    return "pip"


def _python_dep_names(
    root: Path, toml: dict[str, Any], requirements: Path, pipfile: Path
) -> set[str]:
    names: set[str] = set()
    project_raw = toml.get("project")
    project: dict[str, Any] = project_raw if isinstance(project_raw, dict) else {}
    for item in project.get("dependencies") or []:
        if isinstance(item, str):
            names.add(_req_name(item))
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for group in optional.values():
            if isinstance(group, list):
                for item in group:
                    if isinstance(item, str):
                        names.add(_req_name(item))
    poetry = ((toml.get("tool") or {}).get("poetry") or {}).get("dependencies")
    if isinstance(poetry, dict):
        names.update(str(k).lower() for k in poetry if k != "python")
    if requirements.is_file():
        for line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            names.add(_req_name(stripped))
    if pipfile.is_file():
        pip_toml = _load_toml(pipfile)
        packages = pip_toml.get("packages")
        if isinstance(packages, dict):
            names.update(str(k).lower() for k in packages)
        # Pipfile is TOML-like; lite fallback scans [packages].
        if not packages:
            in_packages = False
            for line in pipfile.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip() == "[packages]":
                    in_packages = True
                    continue
                if in_packages and line.startswith("["):
                    break
                if in_packages and "=" in line:
                    names.add(line.split("=", 1)[0].strip().strip('"').lower())
    return {n for n in names if n}


def _req_name(item: str) -> str:
    token = item.strip().strip("\"'")
    for sep in ("[", "==", ">=", "<=", "~=", ">", "<", "===", "@"):
        if sep in token:
            token = token.split(sep, 1)[0]
    return token.strip().lower()


def _python_runner(pm: str) -> str:
    if pm == "uv":
        return "uv run "
    if pm == "poetry":
        return "poetry run "
    if pm == "pipenv":
        return "pipenv run "
    return ""


def _python_start(root: Path, framework: str, pm: str) -> str:
    runner = _python_runner(pm)
    if framework == "django" or (root / "manage.py").is_file():
        settings = ""
        manage = root / "manage.py"
        if manage.is_file():
            match = _DJANGO_SETTINGS.search(manage.read_text(encoding="utf-8", errors="ignore"))
            if match:
                settings = match.group(1)
        module = settings.rsplit(".", 1)[0] if settings else "mysite"
        cmd = f"gunicorn {module}.wsgi:application --bind 0.0.0.0:8000"
        return cmd if pm == "pip" else f"{runner}{cmd}"

    factory = _find_fastapi_factory(root)
    if factory:
        return f"{runner}uvicorn {factory} --factory --host 0.0.0.0 --port 8000"
    module_app = _find_fastapi_app(root)
    if module_app:
        return f"{runner}uvicorn {module_app} --host 0.0.0.0 --port 8000"
    if framework == "flask":
        return f"{runner}flask run --host 0.0.0.0 --port 8000"
    return ""


def _skip_python_path(rel: Path) -> bool:
    return any(part in _SKIP_DIR_NAMES or part == "tests" or part == "test" for part in rel.parts)


def _find_fastapi_factory(root: Path) -> str | None:
    best: tuple[int, str] | None = None
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if _skip_python_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "FastAPI" not in text or not _CREATE_APP.search(text):
            continue
        mod = ".".join(rel.with_suffix("").parts)
        score = 1
        if rel.as_posix().endswith("openvault/app.py"):
            score = 20
        elif path.name == "app.py":
            score = 8
        if best is None or score > best[0]:
            best = (score, f"{mod}:create_app")
    return best[1] if best else None


def _find_fastapi_app(root: Path) -> str | None:
    best: tuple[int, str] | None = None
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if _skip_python_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not _FASTAPI_APP.search(text):
            continue
        mod = ".".join(rel.with_suffix("").parts)
        score = 5 if path.name == "main.py" else 1
        if best is None or score > best[0]:
            best = (score, f"{mod}:app")
    return best[1] if best else None


def _dockerfile_port(dockerfile: Path) -> int | None:
    if not dockerfile.is_file():
        return None
    match = _EXPOSE.search(dockerfile.read_text(encoding="utf-8", errors="ignore"))
    if match:
        return int(match.group(1))
    return None


def _needs_db_mail(root: Path, compose_file: Path | None) -> tuple[bool, bool]:
    needs_db = any(
        (root / name).exists()
        for name in ("prisma/schema.prisma", "alembic.ini", "drizzle.config.ts")
    )
    needs_mail = False
    for mail_marker in ("mail", "email", "smtp.json"):
        if (root / mail_marker).exists():
            needs_mail = True
            break
    if (
        compose_file is not None
        and compose_file.is_file()
        and "mail" in compose_file.read_text(encoding="utf-8", errors="ignore").lower()
    ):
        needs_mail = True
    return needs_db, needs_mail
