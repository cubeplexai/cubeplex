#!/bin/bash
# Code formatting script

set -e

echo "🎨 Formatting code..."
echo ""

# Change to backend directory
cd "$(dirname "$0")/.."

# Run black
echo "📝 Running black..."
uv run black cubeplex/ scripts/ tests/
echo "✓ Black formatting completed"
echo ""

# Run isort
echo "📦 Running isort..."
uv run isort cubeplex/ scripts/ tests/
echo "✓ Import sorting completed"
echo ""

# Run ruff auto-fix
echo "🔧 Running ruff auto-fix..."
uv run ruff check --fix cubeplex/ scripts/ tests/ || true
echo "✓ Ruff auto-fix completed"
echo ""

echo "✅ Code formatting completed!"
