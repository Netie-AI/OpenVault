"""Stack catalog — ids, ports, commands, and Origin/Vercel vs VM host kinds.

`GET /api/ship/stacks` exposes this so the UI can override detection using the
same ids the detector emits. Host kinds (cited in hosting.py):

- ``static_http`` / ``edge_http`` — git on Cursor Origin; HTTP via Vercel App
  (push/PR preview, merge = production). Origin itself is a git forge, not
  an app runtime: https://cursor.com/docs/origin
- ``process`` / ``container`` — git on Origin; HTTP on a VM / compose / static
  file server the operator already has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HostKind = Literal["static_http", "edge_http", "process", "container", "unknown"]
ProjectType = Literal["web", "api", "static", "container", "language", "unknown"]


@dataclass(frozen=True)
class StackSpec:
    name: str
    language: str
    category: str
    project_type: ProjectType
    default_port: int
    output_directory: str
    default_build_command: str
    default_start_command: str
    host_kind: HostKind
    origin_http: bool


STACKS: dict[str, StackSpec] = {
    "nextjs": StackSpec(
        name="Next.js",
        language="javascript",
        category="fullstack",
        project_type="web",
        default_port=3000,
        output_directory=".next",
        default_build_command="next build",
        default_start_command="next start",
        host_kind="edge_http",
        origin_http=True,
    ),
    "vite": StackSpec(
        name="Vite",
        language="javascript",
        category="frontend",
        project_type="web",
        default_port=4173,
        output_directory="dist",
        default_build_command="vite build",
        default_start_command="vite preview",
        host_kind="static_http",
        origin_http=True,
    ),
    "astro": StackSpec(
        name="Astro",
        language="javascript",
        category="frontend",
        project_type="web",
        default_port=4321,
        output_directory="dist",
        default_build_command="astro build",
        default_start_command="astro preview",
        host_kind="static_http",
        origin_http=True,
    ),
    "hono": StackSpec(
        name="Hono",
        language="javascript",
        category="backend",
        project_type="api",
        default_port=3000,
        output_directory="dist",
        default_build_command="",
        default_start_command="",
        host_kind="edge_http",
        origin_http=True,
    ),
    "node": StackSpec(
        name="Node.js",
        language="javascript",
        category="backend",
        project_type="api",
        default_port=3000,
        output_directory="",
        default_build_command="",
        default_start_command="node .",
        host_kind="process",
        origin_http=False,
    ),
    "python": StackSpec(
        name="Python",
        language="python",
        category="backend",
        project_type="api",
        default_port=8000,
        output_directory="",
        default_build_command="",
        default_start_command="",
        host_kind="process",
        origin_http=False,
    ),
    "fastapi": StackSpec(
        name="FastAPI",
        language="python",
        category="backend",
        project_type="api",
        default_port=8000,
        output_directory="",
        default_build_command="",
        default_start_command="uvicorn app:app --host 0.0.0.0 --port 8000",
        host_kind="process",
        origin_http=False,
    ),
    "django": StackSpec(
        name="Django",
        language="python",
        category="backend",
        project_type="api",
        default_port=8000,
        output_directory="",
        default_build_command="",
        default_start_command="gunicorn mysite.wsgi:application --bind 0.0.0.0:8000",
        host_kind="process",
        origin_http=False,
    ),
    "flask": StackSpec(
        name="Flask",
        language="python",
        category="backend",
        project_type="api",
        default_port=8000,
        output_directory="",
        default_build_command="",
        default_start_command="flask run --host 0.0.0.0 --port 8000",
        host_kind="process",
        origin_http=False,
    ),
    "go": StackSpec(
        name="Go",
        language="go",
        category="backend",
        project_type="api",
        default_port=8080,
        output_directory="",
        default_build_command="go build -o app .",
        default_start_command="./app",
        host_kind="process",
        origin_http=False,
    ),
    "rust": StackSpec(
        name="Rust",
        language="rust",
        category="backend",
        project_type="api",
        default_port=8080,
        output_directory="target/release",
        default_build_command="cargo build --release",
        default_start_command="./target/release/app",
        host_kind="process",
        origin_http=False,
    ),
    "static": StackSpec(
        name="Static HTML",
        language="html",
        category="static",
        project_type="static",
        default_port=80,
        output_directory=".",
        default_build_command="",
        default_start_command="",
        host_kind="static_http",
        origin_http=True,
    ),
    "docker": StackSpec(
        name="Docker",
        language="docker",
        category="container",
        project_type="container",
        default_port=8080,
        output_directory="",
        default_build_command="docker build -t app .",
        default_start_command="docker run -d --name app -p 8080:8080 app",
        host_kind="container",
        origin_http=False,
    ),
    "dockerfile": StackSpec(
        name="Dockerfile",
        language="docker",
        category="container",
        project_type="container",
        default_port=8080,
        output_directory="",
        default_build_command="docker build -t app .",
        default_start_command="docker run -d --name app app",
        host_kind="container",
        origin_http=False,
    ),
    "docker-compose": StackSpec(
        name="Docker Compose",
        language="docker",
        category="container",
        project_type="container",
        default_port=80,
        output_directory="",
        default_build_command="docker compose build",
        default_start_command="docker compose up -d",
        host_kind="container",
        origin_http=False,
    ),
}


def get_project_type(stack_id: str) -> ProjectType:
    spec = STACKS.get(stack_id)
    return spec.project_type if spec is not None else "unknown"


def get_stack(stack_id: str) -> StackSpec | None:
    return STACKS.get(stack_id)
