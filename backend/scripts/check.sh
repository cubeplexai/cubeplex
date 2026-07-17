#!/bin/bash
# Code quality check script

set -e

echo "🔍 Running code quality checks..."
echo ""

# Change to backend directory
cd "$(dirname "$0")/.."

# Format check
echo "📝 Checking code formatting..."
uv run black --check cubeplex/ scripts/ tests/ || {
    echo "❌ Black formatting check failed. Run 'make format' to fix."
    exit 1
}
echo "✓ Black formatting check passed"
echo ""

# Import sorting check
echo "📦 Checking import sorting..."
uv run isort --check-only cubeplex/ scripts/ tests/ || {
    echo "❌ Import sorting check failed. Run 'make format' to fix."
    exit 1
}
echo "✓ Import sorting check passed"
echo ""

# Linting
echo "🔎 Running linter..."
uv run ruff check cubeplex/ scripts/ tests/ || {
    echo "❌ Linting failed. Run 'make lint-fix' to auto-fix or fix manually."
    exit 1
}
echo "✓ Linting passed"
echo ""

# Type checking
echo "🔬 Running type checker..."
uv run mypy cubeplex/ || {
    echo "❌ Type checking failed. Fix type errors manually."
    exit 1
}
echo "✓ Type checking passed"
echo ""

echo "✅ All checks passed!"
