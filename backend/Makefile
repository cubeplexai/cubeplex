.PHONY: help install dev-install format lint type-check test test-cov clean pre-commit-install pre-commit-run

help:
	@echo "Available commands:"
	@echo "  make install           - Install production dependencies"
	@echo "  make dev-install       - Install development dependencies"
	@echo "  make format            - Format code with ruff"
	@echo "  make lint              - Run ruff linter"
	@echo "  make type-check        - Run mypy type checker"
	@echo "  make test              - Run tests"
	@echo "  make test-cov          - Run tests with coverage report"
	@echo "  make check             - Run all checks (format, lint, type-check)"
	@echo "  make clean             - Clean cache and build files"
	@echo "  make pre-commit-install - Install pre-commit hooks"
	@echo "  make pre-commit-run    - Run pre-commit on all files"

install:
	uv sync --no-dev

dev-install:
	uv sync --all-extras

format:
	@echo "Running ruff format..."
	uv run ruff format cubebox/ scripts/ tests/
	@echo "Running ruff check --select I --fix (import sorting)..."
	uv run ruff check --select I --fix cubebox/ scripts/ tests/
	@echo "✓ Code formatted"

lint:
	@echo "Running ruff..."
	uv run ruff check cubebox/ scripts/ tests/
	@echo "✓ Linting passed"

lint-fix:
	@echo "Running ruff with auto-fix..."
	uv run ruff check --fix cubebox/ scripts/ tests/
	@echo "✓ Linting issues fixed"

type-check:
	@echo "Running mypy..."
	uv run mypy cubebox/
	@echo "✓ Type checking passed"

test:
	@echo "Running tests..."
	uv run pytest -s -v
	@echo "✓ Tests passed"

test-cov:
	@echo "Running tests with coverage..."
	uv run pytest -s --cov=cubebox --cov-report=term-missing --cov-report=html
	@echo "✓ Coverage report generated in htmlcov/"

check: format lint type-check
	@echo "✓ All checks passed"

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

pre-commit-run:
	@echo "Running pre-commit on all files..."
	uv run pre-commit run --all-files
	@echo "✓ Pre-commit checks completed"
