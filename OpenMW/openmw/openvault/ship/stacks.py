"""Stack registry — Python port of FreeBuild's `packages/core/src/stacks.ts`.

Single source of truth for every stack we can detect: language, category,
default port, default build/start commands, output directory and the detection
signals (root markers / dependency names / content patterns).

Ported by hand from the vendored TypeScript at
`vendor/openship/packages/core/src/stacks.ts` and committed as data, so nothing
at runtime depends on the vendor tree being present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StackCategory = Literal[
    "frontend", "backend", "fullstack", "static", "docker", "services", "generic"
]
ProjectType = Literal["app", "docker", "services", "monorepo"]

#: How OpenVault serves HTTP for a stack (cited in `hosting.py`): Caddy
#: `file_server` for built static output, systemd + Caddy `reverse_proxy` for a
#: long-running process, compose on the box for containers. Cursor Origin is a
#: source forge, not an app runtime, so `origin_http` only says whether the
#: stack is a web artefact Caddy can serve straight after a push.
HostKind = Literal["static_http", "edge_http", "process", "container", "unknown"]

#: Backend stacks that ship as an edge-style HTTP handler rather than a plain
#: long-running process. Kept explicit so the catalog does not guess.
_EDGE_HTTP_STACKS: frozenset[str] = frozenset({"hono"})


@dataclass(frozen=True)
class LanguageDefinition:
    name: str
    build_image: str
    runtime_image: str
    package_managers: tuple[str, ...]
    required_tools: tuple[str, ...]


LANGUAGES: dict[str, LanguageDefinition] = {
    "javascript": LanguageDefinition(
        "JavaScript", "node:22", "node:22", ("npm", "yarn", "pnpm", "bun"), ("node", "npm")
    ),
    "typescript": LanguageDefinition(
        "TypeScript", "node:22", "node:22", ("npm", "yarn", "pnpm", "bun"), ("node", "npm")
    ),
    "go": LanguageDefinition("Go", "golang:1.22-alpine", "alpine:3.19", ("go",), ("go",)),
    "rust": LanguageDefinition(
        "Rust", "rust:1.77-slim", "debian:bookworm-slim", ("cargo",), ("rustc", "cargo")
    ),
    "python": LanguageDefinition(
        "Python",
        "python:3.12-slim",
        "python:3.12-slim",
        ("pip", "poetry", "pipenv", "uv"),
        ("python3", "pip"),
    ),
    "ruby": LanguageDefinition(
        "Ruby", "ruby:3.3-slim", "ruby:3.3-slim", ("bundler",), ("ruby", "bundler")
    ),
    "php": LanguageDefinition(
        "PHP", "php:8.3-cli", "php:8.3-fpm", ("composer",), ("php", "composer")
    ),
    "java": LanguageDefinition(
        "Java",
        "maven:3.9-eclipse-temurin-21",
        "eclipse-temurin:21-jre-alpine",
        ("maven", "gradle"),
        ("java", "javac"),
    ),
    "csharp": LanguageDefinition(
        "C#",
        "mcr.microsoft.com/dotnet/sdk:8.0",
        "mcr.microsoft.com/dotnet/aspnet:8.0",
        ("dotnet",),
        ("dotnet",),
    ),
    "elixir": LanguageDefinition(
        "Elixir", "elixir:1.16-alpine", "elixir:1.16-alpine", ("mix",), ("elixir", "mix")
    ),
    "multi": LanguageDefinition("Multi-language", "ubuntu:22.04", "ubuntu:22.04", (), ()),
}


@dataclass(frozen=True)
class StackDetection:
    """Declarative detection signals for one stack.

    `root_markers` are lowercased basenames (nested markers such as
    `config/routes.rb` are matched verbatim against the relative path).
    `deps` are real dependency names parsed out of a manifest — never a
    substring search over the manifest text.
    """

    root_markers: tuple[str, ...] = ()
    deps: tuple[str, ...] = ()
    content_patterns: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Stack:
    name: str
    language: str
    category: StackCategory
    output_directory: str
    default_port: int
    default_build_command: str = ""
    default_start_command: str = ""
    build_image: str = ""
    runtime_image: str = ""
    production_paths: tuple[str, ...] = ()
    cache_dirs: tuple[str, ...] = ()
    default_build_strategy: str = "server"
    required_tools: tuple[str, ...] = ()
    required_tool_versions: tuple[tuple[str, str], ...] = ()
    detection: StackDetection = field(default_factory=StackDetection)

    @property
    def host_kind(self) -> HostKind:
        return host_kind_for_category(self.category, stack_id=self.name.lower())

    @property
    def origin_http(self) -> bool:
        return self.host_kind in ("static_http", "edge_http")


def host_kind_for_category(category: str, *, stack_id: str = "") -> HostKind:
    """Map a stack category onto the OpenVault HTTP runtime that serves it."""
    if stack_id in _EDGE_HTTP_STACKS:
        return "edge_http"
    if category in ("static", "frontend"):
        return "static_http"
    if category == "fullstack":
        return "edge_http"
    if category == "backend":
        return "process"
    if category in ("docker", "services"):
        return "container"
    return "unknown"


STACKS: dict[str, Stack] = {
    # ── JavaScript / TypeScript — frontend & fullstack ──────────────────────
    "nextjs": Stack(
        name="Next.js",
        language="typescript",
        category="fullstack",
        output_directory=".next",
        default_port=3000,
        default_build_command="next build",
        default_start_command="next start",
        cache_dirs=(".next/cache",),
        default_build_strategy="local",
        required_tool_versions=(("node", "20.9.0"),),
        detection=StackDetection(
            root_markers=("next.config.js", "next.config.mjs", "next.config.ts"),
            deps=("next",),
        ),
    ),
    "nuxt": Stack(
        name="Nuxt",
        language="typescript",
        category="fullstack",
        output_directory=".output",
        default_port=3000,
        default_build_command="nuxt build",
        default_start_command="node .output/server/index.mjs",
        cache_dirs=(".nuxt",),
        default_build_strategy="local",
        detection=StackDetection(
            root_markers=("nuxt.config.js", "nuxt.config.ts", "nuxt.config.mjs"),
            deps=("nuxt", "@nuxt/core"),
        ),
    ),
    "sveltekit": Stack(
        name="SvelteKit",
        language="typescript",
        category="fullstack",
        output_directory=".svelte-kit",
        default_port=3000,
        default_build_command="vite build",
        default_start_command="node build/index.js",
        default_build_strategy="local",
        detection=StackDetection(
            root_markers=("svelte.config.js", "svelte.config.mjs"),
            deps=("svelte", "@sveltejs/kit"),
        ),
    ),
    "remix": Stack(
        name="Remix",
        language="typescript",
        category="fullstack",
        output_directory="build",
        default_port=3000,
        default_build_command="remix build",
        default_start_command="remix-serve build/index.js",
        default_build_strategy="local",
        detection=StackDetection(
            root_markers=("remix.config.js", "remix.config.ts"),
            deps=("@remix-run/react", "@remix-run/node", "remix"),
        ),
    ),
    "astro": Stack(
        name="Astro",
        language="typescript",
        category="frontend",
        output_directory="dist",
        default_port=4321,
        default_build_command="astro build",
        default_start_command="node dist/server/entry.mjs",
        default_build_strategy="local",
        detection=StackDetection(
            root_markers=("astro.config.mjs", "astro.config.js", "astro.config.ts"),
            deps=("astro",),
        ),
    ),
    "vite": Stack(
        name="Vite",
        language="typescript",
        category="frontend",
        output_directory="dist",
        default_port=5173,
        default_build_command="vite build",
        default_build_strategy="local",
        detection=StackDetection(
            root_markers=("vite.config.js", "vite.config.ts", "vite.config.mjs"),
            deps=("vite",),
        ),
    ),
    "angular": Stack(
        name="Angular",
        language="typescript",
        category="frontend",
        output_directory="dist",
        default_port=4200,
        default_build_command="ng build --configuration production",
        default_build_strategy="local",
        detection=StackDetection(root_markers=("angular.json",), deps=("@angular/core",)),
    ),
    "gatsby": Stack(
        name="Gatsby",
        language="javascript",
        category="frontend",
        output_directory="public",
        default_port=8000,
        default_build_command="gatsby build",
        default_start_command="gatsby serve",
        cache_dirs=(".cache",),
        default_build_strategy="local",
        detection=StackDetection(
            root_markers=("gatsby-config.js", "gatsby-config.ts"), deps=("gatsby",)
        ),
    ),
    "cra": Stack(
        name="Create React App",
        language="javascript",
        category="frontend",
        output_directory="build",
        default_port=3000,
        default_build_command="react-scripts build",
        default_build_strategy="local",
        # CRA's only durable signal is the react-scripts dep.
        detection=StackDetection(deps=("react-scripts",)),
    ),
    "vue": Stack(
        name="Vue CLI",
        language="javascript",
        category="frontend",
        output_directory="dist",
        default_port=8080,
        default_build_command="vue-cli-service build",
        default_build_strategy="local",
        detection=StackDetection(root_markers=("vue.config.js", "vue.config.ts"), deps=("vue",)),
    ),
    "react": Stack(
        name="React",
        language="javascript",
        category="frontend",
        output_directory="build",
        default_port=3000,
        default_build_strategy="local",
    ),
    # ── JavaScript / TypeScript — backend ────────────────────────────────────
    "express": Stack(
        name="Express",
        language="javascript",
        category="backend",
        output_directory="dist",
        default_port=3000,
        default_start_command="node index.js",
        default_build_strategy="local",
        detection=StackDetection(deps=("express",)),
    ),
    "fastify": Stack(
        name="Fastify",
        language="typescript",
        category="backend",
        output_directory="dist",
        default_port=3000,
        default_start_command="node dist/index.js",
        default_build_strategy="local",
        detection=StackDetection(deps=("fastify",)),
    ),
    "hono": Stack(
        name="Hono",
        language="typescript",
        category="backend",
        output_directory="dist",
        default_port=3000,
        default_start_command="node dist/index.js",
        default_build_strategy="local",
        detection=StackDetection(deps=("hono",)),
    ),
    "nestjs": Stack(
        name="NestJS",
        language="typescript",
        category="backend",
        output_directory="dist",
        default_port=3000,
        default_build_command="nest build",
        default_start_command="node dist/main.js",
        default_build_strategy="local",
        detection=StackDetection(root_markers=("nest-cli.json",), deps=("@nestjs/core",)),
    ),
    "koa": Stack(
        name="Koa",
        language="javascript",
        category="backend",
        output_directory="dist",
        default_port=3000,
        default_start_command="node index.js",
        default_build_strategy="local",
        detection=StackDetection(deps=("koa",)),
    ),
    "adonis": Stack(
        name="AdonisJS",
        language="typescript",
        category="fullstack",
        output_directory="build",
        default_port=3333,
        default_build_command="node ace build --production",
        default_start_command="node build/server.js",
        default_build_strategy="local",
        detection=StackDetection(
            root_markers=("ace.js", ".adonisrc.json", "adonisrc.ts"), deps=("@adonisjs/core",)
        ),
    ),
    "elysia": Stack(
        name="Elysia",
        language="typescript",
        category="backend",
        output_directory="dist",
        default_port=3000,
        default_start_command="bun dist/index.js",
        default_build_strategy="local",
        detection=StackDetection(deps=("elysia",)),
    ),
    # ── Go ──────────────────────────────────────────────────────────────────
    "go": Stack(
        name="Go",
        language="go",
        category="backend",
        output_directory=".",
        default_port=8080,
        default_build_command="go build -o app .",
        default_start_command="./app",
        production_paths=("app",),
        detection=StackDetection(root_markers=("go.mod",)),
    ),
    "gin": Stack(
        name="Gin",
        language="go",
        category="backend",
        output_directory=".",
        default_port=8080,
        default_build_command="go build -o app .",
        default_start_command="./app",
        production_paths=("app",),
        detection=StackDetection(root_markers=("go.mod",), deps=("github.com/gin-gonic/gin",)),
    ),
    "fiber": Stack(
        name="Fiber",
        language="go",
        category="backend",
        output_directory=".",
        default_port=3000,
        default_build_command="go build -o app .",
        default_start_command="./app",
        production_paths=("app",),
        detection=StackDetection(root_markers=("go.mod",), deps=("github.com/gofiber/fiber",)),
    ),
    "echo": Stack(
        name="Echo",
        language="go",
        category="backend",
        output_directory=".",
        default_port=8080,
        default_build_command="go build -o app .",
        default_start_command="./app",
        production_paths=("app",),
        detection=StackDetection(root_markers=("go.mod",), deps=("github.com/labstack/echo",)),
    ),
    # ── Rust ────────────────────────────────────────────────────────────────
    "rust": Stack(
        name="Rust",
        language="rust",
        category="backend",
        output_directory="target/release",
        default_port=8080,
        default_build_command="cargo build --release",
        default_start_command="./target/release/app",
        production_paths=("target/release/app",),
        detection=StackDetection(root_markers=("cargo.toml",)),
    ),
    "actix": Stack(
        name="Actix Web",
        language="rust",
        category="backend",
        output_directory="target/release",
        default_port=8080,
        default_build_command="cargo build --release",
        default_start_command="./target/release/app",
        production_paths=("target/release/app",),
        detection=StackDetection(root_markers=("cargo.toml",), deps=("actix-web",)),
    ),
    "axum": Stack(
        name="Axum",
        language="rust",
        category="backend",
        output_directory="target/release",
        default_port=3000,
        default_build_command="cargo build --release",
        default_start_command="./target/release/app",
        production_paths=("target/release/app",),
        detection=StackDetection(root_markers=("cargo.toml",), deps=("axum",)),
    ),
    "rocket": Stack(
        name="Rocket",
        language="rust",
        category="backend",
        output_directory="target/release",
        default_port=8000,
        default_build_command="cargo build --release",
        default_start_command="./target/release/app",
        production_paths=("target/release/app",),
        detection=StackDetection(root_markers=("cargo.toml",), deps=("rocket",)),
    ),
    # ── Python ──────────────────────────────────────────────────────────────
    "python": Stack(
        name="Python",
        language="python",
        category="backend",
        output_directory=".",
        default_port=8000,
        default_build_command="",
        default_start_command="python app.py",
        detection=StackDetection(
            root_markers=("requirements.txt", "pyproject.toml", "pipfile", "setup.py")
        ),
    ),
    "django": Stack(
        name="Django",
        language="python",
        category="fullstack",
        output_directory=".",
        default_port=8000,
        default_build_command="python manage.py collectstatic --noinput",
        default_start_command="gunicorn config.wsgi:application --bind 0.0.0.0:8000",
        detection=StackDetection(root_markers=("manage.py",)),
    ),
    "flask": Stack(
        name="Flask",
        language="python",
        category="backend",
        output_directory=".",
        default_port=5000,
        default_build_command="",
        default_start_command="gunicorn app:app --bind 0.0.0.0:5000",
        detection=StackDetection(
            root_markers=("requirements.txt", "pyproject.toml", "pipfile"), deps=("flask",)
        ),
    ),
    "fastapi": Stack(
        name="FastAPI",
        language="python",
        category="backend",
        output_directory=".",
        default_port=8000,
        default_build_command="",
        default_start_command="uvicorn main:app --host 0.0.0.0 --port 8000",
        detection=StackDetection(
            root_markers=("requirements.txt", "pyproject.toml", "pipfile"), deps=("fastapi",)
        ),
    ),
    # ── Ruby ────────────────────────────────────────────────────────────────
    "rails": Stack(
        name="Ruby on Rails",
        language="ruby",
        category="fullstack",
        output_directory=".",
        default_port=3000,
        default_build_command="bundle exec rails assets:precompile",
        default_start_command="bundle exec rails server -b 0.0.0.0",
        detection=StackDetection(root_markers=("gemfile", "bin/rails", "config/routes.rb")),
    ),
    "sinatra": Stack(
        name="Sinatra",
        language="ruby",
        category="backend",
        output_directory=".",
        default_port=4567,
        default_start_command="ruby app.rb",
        detection=StackDetection(root_markers=("gemfile",), deps=("sinatra",)),
    ),
    # ── PHP ─────────────────────────────────────────────────────────────────
    "laravel": Stack(
        name="Laravel",
        language="php",
        category="fullstack",
        output_directory="public",
        default_port=8000,
        default_build_command="composer install --no-dev --optimize-autoloader",
        default_start_command="php artisan serve --host 0.0.0.0 --port 8000",
        detection=StackDetection(
            root_markers=("artisan", "composer.json"), deps=("laravel/framework",)
        ),
    ),
    "symfony": Stack(
        name="Symfony",
        language="php",
        category="fullstack",
        output_directory="public",
        default_port=8000,
        default_build_command="composer install --no-dev --optimize-autoloader",
        default_start_command="php -S 0.0.0.0:8000 -t public",
        detection=StackDetection(
            root_markers=("composer.json", "symfony.lock"), deps=("symfony/framework-bundle",)
        ),
    ),
    # ── Java / JVM ──────────────────────────────────────────────────────────
    "springboot": Stack(
        name="Spring Boot",
        language="java",
        category="backend",
        output_directory="target",
        default_port=8080,
        default_build_command="mvn clean package -DskipTests",
        default_start_command="java -jar target/*.jar",
        production_paths=("target",),
        default_build_strategy="local",
        required_tools=("java", "javac", "maven"),
        detection=StackDetection(
            root_markers=("pom.xml", "build.gradle", "build.gradle.kts"),
            deps=("org.springframework.boot:spring-boot-starter-web", "spring-boot"),
            content_patterns=(
                ("pom.xml", r"spring[-.]boot"),
                ("build.gradle", r"spring[-.]boot"),
                ("build.gradle.kts", r"spring[-.]boot"),
            ),
        ),
    ),
    "quarkus": Stack(
        name="Quarkus",
        language="java",
        category="backend",
        output_directory="target",
        default_port=8080,
        default_build_command="mvn clean package -DskipTests",
        default_start_command="java -jar target/quarkus-app/quarkus-run.jar",
        production_paths=("target",),
        default_build_strategy="local",
        required_tools=("java", "javac", "maven"),
        detection=StackDetection(
            root_markers=("pom.xml", "build.gradle", "build.gradle.kts"),
            deps=("io.quarkus:quarkus-core", "quarkus"),
            content_patterns=(
                ("pom.xml", r"io\.quarkus"),
                ("build.gradle", r"io\.quarkus"),
                ("build.gradle.kts", r"io\.quarkus"),
            ),
        ),
    ),
    "kotlin": Stack(
        name="Kotlin",
        language="java",
        category="backend",
        output_directory="build/libs",
        default_port=8080,
        default_build_command="gradle build -x test",
        default_start_command="java -jar build/libs/*.jar",
        production_paths=("build/libs",),
        default_build_strategy="local",
        required_tools=("java", "javac", "gradle"),
        detection=StackDetection(
            root_markers=("build.gradle.kts", "build.gradle"),
            content_patterns=(
                ("build.gradle.kts", r"kotlin\s*\(|org\.jetbrains\.kotlin"),
                ("build.gradle", r"org\.jetbrains\.kotlin|kotlin[- ]"),
            ),
        ),
    ),
    # ── C# / .NET ───────────────────────────────────────────────────────────
    "dotnet": Stack(
        name=".NET",
        language="csharp",
        category="backend",
        output_directory="publish",
        default_port=5000,
        default_build_command="dotnet publish -c Release -o publish",
        default_start_command="ASPNETCORE_URLS=http://0.0.0.0:$PORT dotnet publish/app.dll",
        production_paths=("publish",),
    ),
    "blazor": Stack(
        name="Blazor",
        language="csharp",
        # Blazor WebAssembly compiles to a static bundle — served as files.
        category="static",
        output_directory="publish/wwwroot",
        default_port=5000,
        default_build_command="dotnet publish -c Release -o publish",
        production_paths=("publish/wwwroot",),
        detection=StackDetection(deps=("Microsoft.AspNetCore.Components.WebAssembly",)),
    ),
    # ── Elixir ──────────────────────────────────────────────────────────────
    "phoenix": Stack(
        name="Phoenix",
        language="elixir",
        category="fullstack",
        output_directory="_build/prod/rel",
        default_port=4000,
        default_build_command="MIX_ENV=prod mix do deps.get, compile, assets.deploy, release",
        default_start_command="_build/prod/rel/app/bin/app start",
        production_paths=("_build/prod/rel",),
        detection=StackDetection(root_markers=("mix.exs",), deps=("phoenix",)),
    ),
    # ── Generic ─────────────────────────────────────────────────────────────
    "node": Stack(
        name="Node.js",
        language="javascript",
        category="backend",
        output_directory="dist",
        default_port=3000,
        default_start_command="node index.js",
        default_build_strategy="local",
        detection=StackDetection(root_markers=("package.json",)),
    ),
    "static": Stack(
        name="Static Site",
        language="multi",
        category="static",
        output_directory=".",
        default_port=3000,
        build_image="node:22",
        default_build_strategy="local",
        detection=StackDetection(root_markers=("index.html",)),
    ),
    "docker": Stack(
        name="Dockerfile",
        language="multi",
        category="docker",
        output_directory=".",
        default_port=3000,
        detection=StackDetection(root_markers=("dockerfile",)),
    ),
    "docker-compose": Stack(
        name="Docker Compose",
        language="multi",
        category="services",
        output_directory=".",
        default_port=3000,
        detection=StackDetection(
            root_markers=(
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            )
        ),
    ),
    "unknown": Stack(
        name="Unknown",
        language="multi",
        category="generic",
        output_directory="dist",
        default_port=3000,
    ),
    # ── Opinionated installs (commands fixed by the runner) ─────────────────
    # No detection signals by design: the vendor never auto-detects these, they
    # are chosen explicitly. Present so the catalog and the stack ids stay a
    # faithful port of vendor/openship/packages/core/src/stacks.ts.
    "webmail": Stack(
        name="Webmail",
        language="typescript",
        category="fullstack",
        output_directory="client/build",
        default_port=4080,
        default_build_command="bun run build",
        default_start_command="bun run src/main.ts",
        # Runs on bun, not node — the toolchain layer installs bun.
        required_tools=("bun",),
        required_tool_versions=(("bun", "1.2.0"),),
    ),
}

STACK_IDS: tuple[str, ...] = tuple(STACKS)

#: Output directory per stack — derived, never edited by hand.
OUTPUT_DIRECTORIES: dict[str, str] = {sid: s.output_directory for sid, s in STACKS.items()}

#: Every filename any stack uses as a project-root marker, lowercased. The
#: project-root scanner unions this with workspace markers to find candidates.
STACK_ROOT_MARKERS: frozenset[str] = frozenset(
    marker.lower() for stack in STACKS.values() for marker in stack.detection.root_markers
)

#: Paths always excluded when transferring project files (source or output).
TRANSFER_EXCLUDES: tuple[str, ...] = (
    ".git",
    "node_modules",
    "vendor",
    ".next",
    ".vite",
    ".turbo",
    ".cache",
    ".react-router",
    ".nuxt",
    ".svelte-kit",
    ".astro",
    ".output",
    ".nx",
    "dist",
    "build",
    # Runtime state — sqlite DBs, uploads, dev-only generated secrets.
    "data",
    ".dev-secrets.json",
)

#: The excludes whose names are ALSO ordinary source-folder names. Pruning these
#: at any depth deletes real source, so they are only pruned at the tree root.
PACKAGE_ROOT_ONLY_EXCLUDES: tuple[str, ...] = ("build", "dist", "data")

_UNAMBIGUOUS_UPLOAD_EXCLUDES: frozenset[str] = frozenset(
    name for name in TRANSFER_EXCLUDES if name not in PACKAGE_ROOT_ONLY_EXCLUDES
)

#: JS/TS languages that build on oven/bun when the package manager is bun.
_BUN_ELIGIBLE_LANGUAGES: frozenset[str] = frozenset({"javascript", "typescript"})


def is_upload_ignored_path(relative_path: str) -> bool:
    """Should this repo-relative POSIX path be excluded from an upload archive?"""
    segments = [seg for seg in relative_path.replace("\\", "/").split("/") if seg]
    if not segments:
        return False
    if any(seg in _UNAMBIGUOUS_UPLOAD_EXCLUDES for seg in segments):
        return True
    return segments[0] in PACKAGE_ROOT_ONLY_EXCLUDES


def get_stack(stack_id: str) -> Stack:
    return STACKS.get(stack_id, STACKS["unknown"])


def host_kind_for_stack(stack_id: str) -> HostKind:
    """`host_kind` for a catalog id; unknown ids resolve to the `unknown` stack."""
    return host_kind_for_category(get_stack(stack_id).category, stack_id=stack_id)


def get_project_type(stack_id: str) -> str:
    category = get_stack(stack_id).category
    if category == "docker":
        return "docker"
    if category == "services":
        return "services"
    return "app"


def get_build_image(stack_id: str, package_manager: str = "") -> str:
    stack = get_stack(stack_id)
    if package_manager == "bun" and stack.language in _BUN_ELIGIBLE_LANGUAGES:
        return "oven/bun:latest"
    return stack.build_image or LANGUAGES[stack.language].build_image


def get_runtime_image(stack_id: str, package_manager: str = "") -> str:
    stack = get_stack(stack_id)
    if package_manager == "bun" and stack.language in _BUN_ELIGIBLE_LANGUAGES:
        return "oven/bun:latest"
    return stack.runtime_image or LANGUAGES[stack.language].runtime_image


def is_services_framework(framework: str | None) -> bool:
    """Is the project itself a set of services (compose), not a single app?"""
    if not framework:
        return False
    return get_project_type(framework) == "services"
