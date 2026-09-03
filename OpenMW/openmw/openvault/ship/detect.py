"""Auto-detect project stack for deploy — priority rules over `stacks.py`.

Rewritten against FreeBuild's detector (`apps/api/src/lib/stack-detector.ts` +
`project-root-detector.ts`), which we port rather than run. What that buys over
the previous heuristic pass:

  * a compose file is a deployment hint, not a language — Python and Node stacks
    are matched first and `docker-compose` only wins when nothing else does;
  * the package manager is inferred (bun/pnpm/yarn/npm, uv/poetry/pipenv/pip)
    instead of always emitting `npm i` / `uv sync`;
  * monorepo sub-projects come from a real workspace scan and the winner's path
    is reported in `root_directory`, with its commands read from ITS manifest;
  * dependencies are parsed from the manifest structure, never grepped from its
    text, so a description that mentions Django is not a Django project;
  * commands are derived from scripts/entrypoints that actually exist — when
    there is no build or start, we say so in `warnings` instead of inventing one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openmw.openvault.ship.languages import (
    LANGUAGE_MANIFEST_FILES,
    collect_dependencies,
    detect_port,
    parse_package_json,
    parse_pyproject,
)
from openmw.openvault.ship.metadata import (
    METADATA_FILES,
    DeploymentMetadata,
    parse_deployment_metadata,
)
from openmw.openvault.ship.project_root import (
    discover_project_root_hints,
    normalize_root_directory,
    score_candidate,
)
from openmw.openvault.ship.reader import IGNORED_DIRS, LocalReader
from openmw.openvault.ship.stacks import (
    OUTPUT_DIRECTORIES,
    STACKS,
    get_build_image,
    get_project_type,
    get_stack,
)
from openmw.openvault.ship.workspaces import (
    WORKSPACE_MANIFEST_FILES,
    detect_workspaces,
    workspace_package_manager,
)


class DetectionInputError(ValueError):
    """The caller gave us a path we refuse to guess about.

    An empty or relative path used to resolve against the API process's own
    working directory, so detection would confidently describe whatever the
    server happened to be sitting in. The HTTP layer turns this into a 400.
    """


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
    # FreeBuild-style deploy config
    framework: str = "unknown"
    category: str = "fullstack"  # frontend | backend | fullstack | static | docker | services
    install_command: str = ""
    build_command: str = ""
    start_command: str = ""
    output_directory: str = ""
    production_port: int = 3000
    use_detected: bool = True
    # Repo-relative directory the commands above must run in ("" = repo root).
    # `output_directory` stays repo-relative — it is a path, not a command.
    root_directory: str = ""
    package_manager: str = ""
    project_type: str = "app"
    build_image: str = ""
    # Things a human must decide, e.g. "no build script in package.json".
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Framework rules ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Rule:
    stack: str
    #: Override when a marker file alone is not enough (Rails needs Gemfile AND
    #: bin/rails) or when the stack has no durable marker (CRA).
    file_match: Callable[[set[str]], bool] | None = None
    #: Override the dep gate (Vue CLI needs `vue` AND NOT `nuxt`).
    dep_match: Callable[[set[str]], bool] | None = None


def _has_pkg(names: set[str]) -> bool:
    return "package.json" in names


def _has_any(names: set[str], *wanted: str) -> bool:
    return any(name in names for name in wanted)


def _has_project_file(names: set[str], *suffixes: str) -> bool:
    return any(name.endswith(suffixes) for name in names)


#: Priority-ordered. Frontend/fullstack before backend (a Next app also carries
#: Express transitively); every real language before docker/compose, because a
#: compose file describes how to RUN a repo, not what it is written in.
_FRAMEWORK_RULES: tuple[_Rule, ...] = (
    _Rule("nextjs"),
    _Rule("nuxt"),
    _Rule("sveltekit"),
    _Rule("astro"),
    _Rule("remix"),
    _Rule("angular"),
    _Rule("gatsby"),
    _Rule("vite"),
    _Rule("cra", file_match=_has_pkg),
    _Rule("vue", dep_match=lambda deps: "vue" in deps and "nuxt" not in deps),
    _Rule("nestjs"),
    _Rule("adonis"),
    _Rule("elysia", file_match=_has_pkg),
    _Rule("hono", file_match=_has_pkg),
    _Rule("fastify", file_match=_has_pkg),
    _Rule("koa", file_match=_has_pkg),
    _Rule("express", file_match=_has_pkg),
    _Rule("django"),
    _Rule("flask"),
    _Rule("fastapi"),
    _Rule("gin"),
    _Rule("fiber"),
    _Rule("echo"),
    _Rule("go", file_match=lambda names: _has_any(names, "go.mod", "main.go")),
    _Rule("actix"),
    _Rule("axum"),
    _Rule("rocket"),
    _Rule("rust"),
    _Rule(
        "rails",
        file_match=lambda names: (
            "gemfile" in names and _has_any(names, "config/routes.rb", "bin/rails")
        ),
    ),
    _Rule("sinatra"),
    _Rule("laravel"),
    _Rule(
        "symfony",
        file_match=lambda names: "composer.json" in names and "symfony.lock" in names,
    ),
    _Rule("springboot"),
    _Rule("quarkus"),
    _Rule("kotlin"),
    _Rule("blazor", file_match=lambda names: _has_project_file(names, ".csproj")),
    _Rule(
        "dotnet",
        file_match=lambda names: _has_project_file(names, ".csproj", ".fsproj", ".sln"),
    ),
    _Rule(
        "phoenix",
        file_match=lambda names: "mix.exs" in names and _has_any(names, "lib", "config/config.exs"),
    ),
    _Rule("python"),
    _Rule(
        "node",
        file_match=lambda names: _has_any(names, "package.json", "server.js", "app.js", "index.js"),
    ),
    _Rule("static", file_match=lambda names: "index.html" in names and "package.json" not in names),
    _Rule("docker-compose"),
    _Rule("docker"),
)

#: Stacks whose match says nothing about how the project builds — for these we
#: only ever emit commands the project itself declares.
_GENERIC_STACKS: frozenset[str] = frozenset(
    {"node", "static", "unknown", "docker", "docker-compose"}
)

#: Nested markers that must be probed as paths, not directory-listing entries.
_NESTED_MARKERS: tuple[str, ...] = ("bin/rails", "config/routes.rb", "config/config.exs")

_LANGUAGE_TO_PRIMARY: dict[str, str] = {
    "javascript": "node",
    "typescript": "node",
    "python": "python",
    "go": "go",
    "rust": "rust",
    "ruby": "ruby",
    "php": "php",
    "java": "java",
    "csharp": "dotnet",
    "elixir": "elixir",
}


# ─── Directory snapshot ──────────────────────────────────────────────────────


@dataclass
class _Snapshot:
    root_directory: str
    source: str
    names: set[str]
    contents: dict[str, str]
    package_json: dict[str, Any]
    deps: set[str]
    stack_id: str
    matched_by: str
    metadata: list[DeploymentMetadata]

    @property
    def stack(self) -> Any:
        return get_stack(self.stack_id)

    @property
    def scripts(self) -> dict[str, str]:
        scripts = self.package_json.get("scripts")
        return {str(k): str(v) for k, v in scripts.items()} if isinstance(scripts, dict) else {}


#: Compose files are read for the mail/service heuristics, not as a manifest.
_COMPOSE_FILES: frozenset[str] = frozenset(
    {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
)

_READABLE_FILES: frozenset[str] = (
    frozenset(LANGUAGE_MANIFEST_FILES)
    | frozenset(METADATA_FILES)
    | WORKSPACE_MANIFEST_FILES
    | _COMPOSE_FILES
)


def _snapshot(reader: LocalReader, root_directory: str, source: str) -> _Snapshot:
    listing = reader.list_directory(root_directory)
    on_disk = {entry.name.lower(): entry.name for entry in listing}
    names = set(on_disk)
    base = Path(reader.root, root_directory) if root_directory else reader.root
    names.update(marker for marker in _NESTED_MARKERS if (base / marker).exists())

    contents: dict[str, str] = {}
    for name in names:
        if name in _READABLE_FILES or name.endswith((".csproj", ".fsproj", ".sln")):
            prefix = f"{root_directory}/" if root_directory else ""
            # Listing keys are lowercased; Linux read_text("dockerfile") misses Dockerfile.
            text = reader.read_text(f"{prefix}{on_disk.get(name, name)}")
            if text:
                contents[name] = text

    package_json = parse_package_json(contents.get("package.json", ""))
    deps = {name.lower() for name in collect_dependencies(contents)}
    stack_id, matched_by = _match_stack(names, deps, contents)
    return _Snapshot(
        root_directory=root_directory,
        source=source,
        names=names,
        contents=contents,
        package_json=package_json,
        deps=deps,
        stack_id=stack_id,
        matched_by=matched_by,
        metadata=parse_deployment_metadata(contents),
    )


def _match_stack(names: set[str], deps: set[str], contents: dict[str, str]) -> tuple[str, str]:
    """First rule whose file gate AND (dep OR content) gate passes."""
    for rule in _FRAMEWORK_RULES:
        detection = STACKS[rule.stack].detection
        if rule.file_match is not None:
            file_ok = rule.file_match(names)
        else:
            file_ok = any(marker.lower() in names for marker in detection.root_markers)
            # A config file is optional for most JS frameworks (next.config.*,
            # vite.config.*): a package.json declaring the dependency is the
            # real signal, and the dep gate below still has to pass.
            if not file_ok and detection.deps and "package.json" in names:
                file_ok = STACKS[rule.stack].language in ("javascript", "typescript")
        if not file_ok:
            continue

        has_dep_gate = rule.dep_match is not None or bool(detection.deps)
        has_content_gate = bool(detection.content_patterns)
        if not has_dep_gate and not has_content_gate:
            return rule.stack, "marker"

        if rule.dep_match is not None:
            if rule.dep_match(deps):
                return rule.stack, "deps"
        elif any(dep.lower() in deps for dep in detection.deps):
            return rule.stack, "deps"

        for filename, pattern in detection.content_patterns:
            content = contents.get(filename.lower())
            if content and re.search(pattern, content, re.IGNORECASE):
                return rule.stack, "content"

    return "unknown", "none"


# ─── Package manager inference ───────────────────────────────────────────────

_INSTALL_COMMANDS: dict[str, str] = {
    "npm": "npm install",
    "pnpm": "pnpm install",
    "yarn": "yarn install",
    "bun": "bun install",
    "go": "go mod download",
    "cargo": "",  # `cargo build` resolves dependencies itself
    "pip": "pip install -r requirements.txt",
    "uv": "uv sync",
    "poetry": "poetry install",
    "pipenv": "pipenv install",
    "bundler": "bundle install",
    "composer": "composer install --no-dev --optimize-autoloader",
    "maven": "mvn dependency:resolve",
    "gradle": "gradle dependencies",
    "dotnet": "dotnet restore",
    "mix": "mix deps.get",
}


def _js_package_manager(names: set[str], package_json: dict[str, Any]) -> str:
    declared = package_json.get("packageManager")
    if isinstance(declared, str):
        for candidate in ("pnpm", "yarn", "bun", "npm"):
            if declared.startswith(candidate):
                return candidate

    if _has_any(names, "bun.lock", "bun.lockb"):
        return "bun"
    if "pnpm-lock.yaml" in names:
        return "pnpm"
    if "yarn.lock" in names:
        return "yarn"
    if "package-lock.json" in names:
        return "npm"

    scripts = package_json.get("scripts")
    if isinstance(scripts, dict):
        joined = " ".join(str(v) for v in scripts.values())
        for candidate in ("pnpm", "yarn", "bun"):
            if candidate in joined:
                return candidate

    engines = package_json.get("engines")
    if isinstance(engines, dict):
        for candidate in ("pnpm", "yarn", "bun"):
            if engines.get(candidate):
                return candidate

    if _has_any(names, "pnpm-workspace.yaml", ".pnpmfile.cjs"):
        return "pnpm"
    if "bunfig.toml" in names:
        return "bun"
    if _has_any(names, ".yarnrc", ".yarnrc.yml"):
        return "yarn"
    return "npm" if "package.json" in names else ""


def _python_package_manager(names: set[str], contents: dict[str, str]) -> str:
    pyproject = parse_pyproject(contents.get("pyproject.toml", ""))
    raw_tool = pyproject.get("tool")
    tool: dict[str, Any] = raw_tool if isinstance(raw_tool, dict) else {}

    if "uv.lock" in names or "uv" in tool:
        return "uv"
    if "poetry.lock" in names or "poetry" in tool:
        return "poetry"
    if _has_any(names, "pipfile.lock", "pipfile"):
        return "pipenv"
    if "requirements.txt" in names:
        return "pip"
    # A PEP 621 pyproject with no lockfile: uv can install it as-is; pip cannot
    # without a requirements file.
    return "uv" if "pyproject.toml" in names else ""


def _package_manager(snapshot: _Snapshot) -> str:
    language = snapshot.stack.language
    if language in ("javascript", "typescript"):
        return _js_package_manager(snapshot.names, snapshot.package_json)
    if language == "python":
        return _python_package_manager(snapshot.names, snapshot.contents)
    if language == "go":
        return "go"
    if language == "rust":
        return "cargo"
    if language == "ruby":
        return "bundler"
    if language == "php":
        return "composer"
    if language == "java":
        return "gradle" if _has_any(snapshot.names, "build.gradle", "build.gradle.kts") else "maven"
    if language == "csharp":
        return "dotnet"
    if language == "elixir":
        return "mix"
    # multi-language (docker / static / unknown): report whatever the directory
    # actually declares rather than nothing.
    if "package.json" in snapshot.names:
        return _js_package_manager(snapshot.names, snapshot.package_json)
    return _python_package_manager(snapshot.names, snapshot.contents)


def _script_runner(pm: str) -> str:
    """`bun build` / `npm build` are not npm-scripts — both need `run`."""
    return f"{pm} run" if pm in ("npm", "bun") else pm


def _python_runner(pm: str) -> str:
    return {"uv": "uv run ", "poetry": "poetry run ", "pipenv": "pipenv run "}.get(pm, "")


# ─── Entrypoint derivation ───────────────────────────────────────────────────

_PY_SCAN_MAX_FILES = 400
_PY_SCAN_MAX_DEPTH = 4
_ASGI_ASSIGN = re.compile(r"^(\w+)\s*(?::[^=\n]+)?=\s*FastAPI\(", re.MULTILINE)
_ASGI_FACTORY = re.compile(r"^def\s+(create_app|get_app|make_app)\s*\(", re.MULTILINE)
_FLASK_ASSIGN = re.compile(r"^(\w+)\s*=\s*Flask\(", re.MULTILINE)
_DJANGO_SETTINGS = re.compile(r"DJANGO_SETTINGS_MODULE[\"']?\s*,\s*[\"']([\w.]+)[\"']")


def _python_files(root: Path) -> list[Path]:
    """Shallow, bounded, ignore-aware list of candidate entrypoint modules."""
    found: list[Path] = []
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue and len(found) < _PY_SCAN_MAX_FILES:
        current, depth = queue.pop(0)
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if (
                        depth + 1 < _PY_SCAN_MAX_DEPTH
                        and not entry.name.startswith(".")
                        and entry.name.lower() not in IGNORED_DIRS
                    ):
                        queue.append((entry, depth + 1))
                elif entry.suffix == ".py":
                    found.append(entry)
            except OSError:
                continue
    return found


def _module_path(root: Path, file: Path) -> str:
    relative = file.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _find_asgi_entrypoint(root: Path) -> tuple[str, str, bool] | None:
    """`(module, attribute, is_factory)` for the app this project actually serves.

    Looks for a real `FastAPI()` instance or an app factory rather than
    assuming `main:app` — most projects, this one included, have no `main.py`.
    """
    for file in _python_files(root):
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "FastAPI(" not in text:
            continue
        module = _module_path(root, file)
        if not module:
            continue
        assign = _ASGI_ASSIGN.search(text)
        if assign:
            return module, assign.group(1), False
        factory = _ASGI_FACTORY.search(text)
        if factory:
            return module, factory.group(1), True
    return None


def _find_flask_entrypoint(root: Path) -> tuple[str, str] | None:
    for file in _python_files(root):
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = _FLASK_ASSIGN.search(text)
        if match:
            module = _module_path(root, file)
            if module:
                return module, match.group(1)
    return None


def _console_scripts(contents: dict[str, str]) -> list[str]:
    project = parse_pyproject(contents.get("pyproject.toml", "")).get("project")
    scripts = project.get("scripts") if isinstance(project, dict) else None
    return sorted(str(name) for name in scripts) if isinstance(scripts, dict) else []


def _django_wsgi_module(root: Path) -> str:
    """The settings package named in manage.py — `mysite.settings` → `mysite`."""
    try:
        text = (root / "manage.py").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = _DJANGO_SETTINGS.search(text)
    return match.group(1).split(".")[0] if match else ""


# ─── Command derivation ──────────────────────────────────────────────────────


def _js_commands(snapshot: _Snapshot, pm: str, warnings: list[str]) -> tuple[str, str]:
    scripts = snapshot.scripts
    runner = _script_runner(pm)
    stack = snapshot.stack

    if "build" in scripts:
        build = f"{runner} build"
    elif snapshot.stack_id in _GENERIC_STACKS:
        build = ""
        warnings.append("no build script in package.json")
    else:
        build = stack.default_build_command

    if "start" in scripts:
        start = f"{runner} start"
    elif isinstance(snapshot.package_json.get("main"), str):
        start = f"node {snapshot.package_json['main']}"
    elif snapshot.stack_id in _GENERIC_STACKS:
        start = ""
        warnings.append("no start script and no main entry in package.json")
    else:
        start = stack.default_start_command
    return build, start


def _python_commands(
    snapshot: _Snapshot, pm: str, root: Path, port: int, warnings: list[str]
) -> tuple[str, str]:
    runner = _python_runner(pm)
    stack_id = snapshot.stack_id

    if stack_id == "django":
        project = _django_wsgi_module(root)
        build = f"{runner}python manage.py collectstatic --noinput"
        start = (
            f"{runner}gunicorn {project}.wsgi:application --bind 0.0.0.0:{port}"
            if project
            else f"{runner}python manage.py runserver 0.0.0.0:{port}"
        )
        return build, start

    if stack_id == "fastapi":
        entry = _find_asgi_entrypoint(root)
        if entry:
            module, attr, is_factory = entry
            factory = " --factory" if is_factory else ""
            return "", f"{runner}uvicorn {module}:{attr}{factory} --host 0.0.0.0 --port {port}"
        scripts = _console_scripts(snapshot.contents)
        if scripts:
            return "", f"{runner}{scripts[0]}"
        warnings.append("no ASGI app found — set the start command manually")
        return "", ""

    if stack_id == "flask":
        flask_entry = _find_flask_entrypoint(root)
        if flask_entry:
            module, attr = flask_entry
            return "", f"{runner}gunicorn {module}:{attr} --bind 0.0.0.0:{port}"
        return "", f"{runner}flask run --host 0.0.0.0 --port {port}"

    scripts = _console_scripts(snapshot.contents)
    if scripts:
        return "", f"{runner}{scripts[0]}"
    for candidate in ("main.py", "app.py", "server.py"):
        if candidate in snapshot.names:
            return "", f"{runner}python {candidate}"
    warnings.append("no Python entrypoint found — set the start command manually")
    return "", ""


def _jvm_build_command(pm: str, names: set[str]) -> str:
    if pm == "gradle":
        return f"{'./gradlew' if 'gradlew' in names else 'gradle'} build -x test"
    return f"{'./mvnw' if 'mvnw' in names else 'mvn'} clean package -DskipTests"


def _commands(
    snapshot: _Snapshot, pm: str, root: Path, port: int, warnings: list[str]
) -> tuple[str, str, str]:
    """`(install, build, start)` for one directory, from what it declares."""
    language = snapshot.stack.language
    install = _INSTALL_COMMANDS.get(pm, "")

    if language in ("javascript", "typescript"):
        build, start = _js_commands(snapshot, pm, warnings)
    elif language == "python":
        build, start = _python_commands(snapshot, pm, root, port, warnings)
    elif language == "java":
        build = _jvm_build_command(pm, snapshot.names)
        start = (
            "java -jar build/libs/*.jar" if pm == "gradle" else snapshot.stack.default_start_command
        )
    elif snapshot.stack_id == "docker-compose":
        # FreeBuild leaves these empty because its executor drives the daemon
        # itself; we have no image executor, so we emit the CLI equivalent.
        build, start = "docker compose build", "docker compose up -d"
    elif snapshot.stack_id == "docker":
        build, start = "docker build -t app .", "docker run -d --name app app"
    else:
        build = snapshot.stack.default_build_command
        start = snapshot.stack.default_start_command
        # A static site has nothing to run by design — that is not a gap.
        generic = snapshot.stack_id in _GENERIC_STACKS and snapshot.stack.category != "static"
        if generic and not start:
            warnings.append(f"{snapshot.stack.name} projects have no default start command")

    return install, build, start


def _apply_metadata(
    snapshot: _Snapshot, install: str, build: str, start: str, output_directory: str
) -> tuple[str, str, str, str, str]:
    """Fold vercel.json / railway.* / render.yaml / openvault.json over detection."""
    framework = ""
    for meta in snapshot.metadata:
        if meta.non_local:
            # These commands `cd` elsewhere — they describe another directory.
            continue

        def take(current: str, incoming: str, *, fill_only: bool = meta.fill_only) -> str:
            if not incoming:
                return current
            return current if fill_only and current else incoming

        if meta.framework and meta.framework in STACKS:
            framework = meta.framework
        install = take(install, meta.install_command)
        build = take(build, meta.build_command)
        start = take(start, meta.start_command)
        output_directory = take(output_directory, meta.output_directory)
    return install, build, start, output_directory, framework


# ─── Public API ──────────────────────────────────────────────────────────────


def _services(snapshot: _Snapshot, root_names: set[str]) -> list[str]:
    services = [snapshot.stack.language] if snapshot.stack.language != "multi" else []
    if _has_any(root_names, "dockerfile"):
        services.append("docker")
    if _has_any(
        root_names, "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"
    ):
        services.extend(["docker", "compose"])
    return list(dict.fromkeys(services))


def _confidence(snapshot: _Snapshot) -> float:
    if snapshot.stack_id == "unknown":
        return 0.1
    # A dependency or content match names the framework; a bare marker only
    # names the language.
    return 0.95 if snapshot.matched_by in ("deps", "content") else 0.8


def _signals(snapshot: _Snapshot, pm: str, workspace_ids: list[str]) -> list[str]:
    signals: list[str] = []
    for marker in snapshot.stack.detection.root_markers:
        if marker.lower() in snapshot.names:
            signals.append(marker)
    if snapshot.matched_by == "deps":
        for dep in snapshot.stack.detection.deps:
            if dep.lower() in snapshot.deps:
                signals.append(f"dep:{dep}")
    if snapshot.matched_by == "content":
        signals.append(f"content:{snapshot.stack_id}")
    if snapshot.root_directory:
        signals.append(f"root:{snapshot.root_directory}")
    if pm:
        signals.append(f"pm:{pm}")
    for workspace_id in workspace_ids:
        signals.append(f"workspace:{workspace_id}")
    for script in _console_scripts(snapshot.contents):
        signals.append(f"script:{script}")
    for meta in snapshot.metadata:
        signals.append(f"metadata:{meta.source}")
    return list(dict.fromkeys(signals))


def _unknown(root: Path, reason: str, confidence: float = 0.1) -> DetectedStack:
    return DetectedStack(
        project_path=str(root),
        primary="unknown",
        confidence=confidence,
        signals=[reason],
        suggested_build=[],
        suggested_services=[],
    )


def detect_project(project_path: str | Path) -> DetectedStack:
    """Infer the stack at `project_path` without forcing a manual pick.

    Raises `DetectionInputError` for an empty or relative path — resolving those
    silently against the server's own working directory produced confident
    answers about the wrong repository.
    """
    raw = str(project_path).strip()
    if not raw:
        raise DetectionInputError("project_path is required")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise DetectionInputError(f"project_path must be an absolute path: {raw!r}")

    root = candidate.resolve()
    if not root.is_dir():
        return _unknown(root, "path_missing_or_not_dir", confidence=0.0)

    reader = LocalReader(root)
    root_snapshot = _snapshot(reader, "", "root")

    selected = _select_project(reader, root_snapshot)
    selected_root = Path(root, selected.root_directory) if selected.root_directory else root

    workspaces = detect_workspaces(root_snapshot.contents)
    workspace_ids = [match.detector.id for match in workspaces]

    pm = _package_manager(selected)
    if selected.root_directory and workspaces:
        # In a workspace the sub-project inherits the root's package manager —
        # apps/dashboard has no lockfile of its own, the repo root does. What
        # the root declares beats the workspace manifest's family hint: openship
        # keeps a stale pnpm-workspace.yaml but is `packageManager: bun`.
        root_pm = _package_manager(root_snapshot) or workspace_package_manager(
            root_snapshot.contents
        )
        if root_pm:
            pm = root_pm

    port = detect_port(selected.package_json, selected.contents) or selected.stack.default_port
    warnings: list[str] = []
    install, build, start = _commands(selected, pm, selected_root, port, warnings)

    if selected.root_directory and workspaces and install:
        # A workspace install must run once at the repo root; commands are
        # otherwise relative to root_directory.
        depth = len(selected.root_directory.split("/"))
        install = f"cd {'/'.join(['..'] * depth)} && {install}"

    output_directory = OUTPUT_DIRECTORIES.get(selected.stack_id, "")
    install, build, start, output_directory, override = _apply_metadata(
        selected, install, build, start, output_directory
    )
    stack_id = override or selected.stack_id
    if override:
        selected.stack_id = override
        output_directory = output_directory or OUTPUT_DIRECTORIES.get(override, "")

    # Commands are relative to root_directory; the build artifact path is
    # repo-relative, because whoever collects it starts at the repo root.
    if selected.root_directory and output_directory and output_directory != ".":
        output_directory = f"{selected.root_directory}/{output_directory}"

    stack = get_stack(stack_id)
    primary = _primary_for(stack_id)
    suggested_build = [command for command in (install, build, start) if command]

    return DetectedStack(
        project_path=str(root),
        primary=primary,
        confidence=_confidence(selected),
        signals=_signals(selected, pm, workspace_ids),
        suggested_build=suggested_build,
        suggested_services=_services(selected, root_snapshot.names),
        needs_database=_needs_database(root, selected_root),
        needs_mail=_needs_mail(root, root_snapshot),
        framework=stack_id,
        category=stack.category,
        install_command=install,
        build_command=build,
        start_command=start,
        output_directory=output_directory,
        production_port=port,
        use_detected=True,
        root_directory=selected.root_directory,
        package_manager=pm,
        project_type=get_project_type(stack_id),
        build_image=get_build_image(stack_id, pm),
        warnings=warnings,
    )


def _primary_for(stack_id: str) -> str:
    if stack_id == "docker-compose":
        return "docker-compose"
    if stack_id == "docker":
        return "dockerfile"
    if stack_id in ("static", "unknown"):
        return stack_id
    return _LANGUAGE_TO_PRIMARY.get(get_stack(stack_id).language, "unknown")


#: Categories a promoted sub-project may have. A `services` sub-project belongs
#: to the compose flow, not the app flow.
_NESTED_APP_CATEGORIES: frozenset[str] = frozenset({"frontend", "fullstack", "static", "backend"})


def _select_project(reader: LocalReader, root_snapshot: _Snapshot) -> _Snapshot:
    """Pick the deployable project: the repo root, or the best sub-project.

    The root only yields when it is a container rather than an app of its own —
    a bare package.json, a compose file, or nothing recognisable.
    """
    if root_snapshot.stack_id not in _GENERIC_STACKS:
        return root_snapshot

    tree = reader.list_tree()
    hints = discover_project_root_hints(tree, root_snapshot.contents)
    if not hints:
        return root_snapshot

    best: _Snapshot | None = None
    best_score = -1
    for hint in hints:
        candidate = _snapshot(reader, hint.root_directory, hint.source)
        if candidate.stack_id == "unknown":
            continue
        if candidate.stack.category not in _NESTED_APP_CATEGORIES:
            continue
        score = score_candidate(
            root_directory=candidate.root_directory,
            source=candidate.source,
            category=candidate.stack.category,
            project_type=get_project_type(candidate.stack_id),
            has_build_script="build" in candidate.scripts,
            file_names=candidate.names,
        )
        if score > best_score:
            best, best_score = candidate, score
    return best or root_snapshot


def _needs_database(root: Path, selected_root: Path) -> bool:
    markers = ("prisma/schema.prisma", "alembic.ini", "drizzle.config.ts")
    return any((base / name).exists() for base in {root, selected_root} for name in markers)


def _needs_mail(root: Path, root_snapshot: _Snapshot) -> bool:
    if any((root / name).exists() for name in ("mail", "email", "smtp.json")):
        return True
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        if "mail" in root_snapshot.contents.get(name, "").lower():
            return True
    return False


def normalize_project_root(value: str | None) -> str:
    """Re-exported so callers can normalize a user-supplied sub-path the same way."""
    return normalize_root_directory(value)
