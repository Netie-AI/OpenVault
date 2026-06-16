FROM python:3.12-slim

RUN apt-get update && apt-get install -y nvme-cli && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml .
COPY README.md .
COPY nvme_sentinel/ nvme_sentinel/
COPY tests/ tests/
COPY scripts/ scripts/

RUN uv sync --dev

CMD ["uv", "run", "pytest", "-n", "auto", "--cov=nvme_sentinel.hal", "--cov=nvme_sentinel.adapters.mock", "--cov-fail-under=80", "--cov-report=term-missing", "-v", "tests/unit", "tests/integration"]
