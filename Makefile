.PHONY: demo test lint typecheck coverage docker-test clean

demo:       ## Run end-to-end demo (mock adapter, no hardware)
	uv run nvme-sentinel demo

test:       ## Run full test suite
	uv run pytest tests/ -n auto -q

lint:       ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

typecheck:  ## mypy strict (package + tests)
	uv run mypy nvme_sentinel
	uv run mypy nvme_sentinel tests

coverage:   ## HAL + mock coverage gate (≥80%)
	uv run pytest --cov=nvme_sentinel.hal --cov=nvme_sentinel.adapters.mock \
	              --cov-fail-under=80 --cov-report=term-missing tests/unit tests/integration

docker-test: ## Run tests in Docker (Linux environment on any host)
	docker compose run --rm test

fixtures:   ## Regenerate binary test fixtures
	uv run python scripts/build_fixtures.py

clean:      ## Remove build artefacts
	rm -rf .venv htmlcov .coverage coverage.xml .mypy_cache .ruff_cache .pytest_cache
	rm -rf reports/ dist/ build/ *.egg-info

ci: lint typecheck test coverage ## Full local CI gate
