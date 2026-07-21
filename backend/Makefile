.PHONY: help install dev-install format lint lint-fix type-check test test-real-llm test-cov test-e2e test-e2e-ci test-contracts migrate start cleanup-sandboxes check check-ci clean pre-commit-install pre-commit-install-all pre-commit-run

help:
	@echo "Available commands:"
	@echo "  make install           - Install production dependencies"
	@echo "  make dev-install       - Install development dependencies"
	@echo "  make format            - Format code with ruff"
	@echo "  make lint              - Run ruff linter"
	@echo "  make type-check        - Run mypy type checker"
	@echo "  make test              - Run backend tests (excludes real_llm)"
	@echo "  make test-real-llm     - Run real-LLM injection tests"
	@echo "  make test-cov          - Run backend tests with coverage report"
	@echo "  make test-e2e          - Run backend E2E tests"
	@echo "  make test-e2e-ci       - Run backend E2E tests, writing coverage-e2e.xml for Codecov"
	@echo "  make test-contracts    - Run plugin contract tests (EE compat)"
	@echo "  make migrate           - Run Alembic migrations"
	@echo "  make start             - Start backend server"
	@echo "  make cleanup-sandboxes - Cleanup leftover test sandboxes"
	@echo "  make check             - Run all checks (format, lint, type-check)"
	@echo "  make check-ci          - CI-equivalent checks (ruff check + format check + mypy + pytest unit)"
	@echo "  make pre-commit-install-all - Install both pre-commit and pre-push hooks"
	@echo "  make clean             - Clean cache and build files"
	@echo "  make pre-commit-install - Install pre-commit hooks"
	@echo "  make pre-commit-run    - Run pre-commit on all files"

install:
	uv sync --no-dev

dev-install:
	uv sync --all-extras

format:
	@echo "Running ruff format..."
	uv run ruff format cubeplex/ scripts/ tests/
	@echo "Running ruff check --select I --fix (import sorting)..."
	uv run ruff check --select I --fix cubeplex/ scripts/ tests/
	@echo "✓ Code formatted"

lint:
	@echo "Running ruff..."
	uv run ruff check cubeplex/ scripts/ tests/
	@echo "✓ Linting passed"

lint-fix:
	@echo "Running ruff with auto-fix..."
	uv run ruff check --fix cubeplex/ scripts/ tests/
	@echo "✓ Linting issues fixed"

type-check:
	@echo "Running mypy..."
	uv run mypy cubeplex/
	@echo "✓ Type checking passed"

test:
	@echo "Running tests..."
	uv run pytest -s -v -m "not real_llm"
	@echo "✓ Tests passed"

test-real-llm:
	@echo "Running real-LLM tests..."
	uv run pytest -s -v -m real_llm tests/e2e/memory/ tests/e2e/middleware/
	@echo "✓ Real-LLM tests passed"

test-cov:
	@echo "Running tests with coverage..."
	uv run pytest -s --cov=cubeplex --cov-report=term-missing --cov-report=html
	@echo "✓ Coverage report generated in htmlcov/"

test-e2e:
	@echo "Running backend E2E tests (real_llm tests deselected)..."
	uv run pytest tests/e2e/ -v -m "not real_llm"
	@echo "✓ Backend E2E tests passed"

test-e2e-ci:
	@echo "Running backend E2E tests (real_llm tests deselected)..."
	uv run pytest tests/e2e/ -v -m "not real_llm" --cov-report=xml:coverage-e2e.xml
	@echo "✓ Backend E2E tests passed"

test-contracts:
	uv run pytest tests/plugins/test_contracts.py -v --no-cov

migrate:
	uv run alembic upgrade head

start:
	uv run python main.py

cleanup-sandboxes:
	uv run python scripts/cleanup_sandboxes.py

check: format lint type-check
	@echo "✓ All checks passed"

check-ci:
	@echo "Running CI-equivalent checks..."
	uv run ruff check cubeplex/ scripts/ tests/
	uv run ruff format --check cubeplex/ scripts/ tests/
	uv run mypy cubeplex/
	@echo "Running pytest unit tests (dummy env for dynaconf @format)..."
	CUBEPLEX_E2E_LLM_BASE_URL=http://dummy \
	CUBEPLEX_E2E_LLM_API_KEY=dummy \
	CUBEPLEX_E2E_LLM_MODEL_ID=dummy \
	CUBEPLEX_SANDBOX__DOMAIN=dummy:9000 \
	CUBEPLEX_SANDBOX__IMAGE=dummy:image \
	CUBEPLEX_SANDBOX__API_KEY=dummy \
		uv run pytest --ignore=tests/e2e --ignore=tests/diagnostic -m "not sandbox and not e2e and not real_llm and not diagnostic and not requires_api_key" --cov-report=xml
	@echo "✓ CI-equivalent checks passed"

clean:
	@echo "Cleaning cache and build files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "✓ Cleaned"

pre-commit-install:
	@echo "Installing pre-commit hooks..."
	uv run pre-commit install
	@echo "✓ Pre-commit hooks installed"

pre-commit-install-all:
	@echo "Installing pre-commit and pre-push hooks..."
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push
	@echo "✓ Pre-commit and pre-push hooks installed"

pre-commit-run:
	@echo "Running pre-commit on all files..."
	uv run pre-commit run --all-files
	@echo "✓ Pre-commit checks completed"
