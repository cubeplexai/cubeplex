#!/bin/bash
# Database migration helper script

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Functions
print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Check if alembic is installed
if ! command -v alembic &> /dev/null; then
    print_error "Alembic is not installed. Install it with: pip install alembic"
    exit 1
fi

# Show help
show_help() {
    echo "Database Migration Helper"
    echo ""
    echo "Usage: ./migrate.sh [command] [options]"
    echo ""
    echo "Commands:"
    echo "  init                  Initialize Alembic (first time setup)"
    echo "  create <message>      Create a new migration"
    echo "  upgrade               Upgrade to latest migration"
    echo "  downgrade             Downgrade one migration"
    echo "  current               Show current migration"
    echo "  history               Show migration history"
    echo "  test                  Test migration up and down"
    echo ""
}

# Initialize Alembic
init_alembic() {
    print_warning "Initializing Alembic..."
    alembic init alembic
    print_success "Alembic initialized!"
    print_warning "Don't forget to:"
    echo "  1. Update alembic.ini with your database URL"
    echo "  2. Update alembic/env.py to import your models"
}

# Create migration
create_migration() {
    if [ -z "$1" ]; then
        print_error "Migration message is required"
        echo "Usage: ./migrate.sh create 'your migration message'"
        exit 1
    fi

    print_warning "Creating migration: $1"
    alembic revision --autogenerate -m "$1"
    print_success "Migration created!"
    print_warning "Review the migration file before applying"
}

# Upgrade database
upgrade_db() {
    print_warning "Upgrading database..."
    alembic upgrade head
    print_success "Database upgraded!"
}

# Downgrade database
downgrade_db() {
    print_warning "Downgrading database..."
    alembic downgrade -1
    print_success "Database downgraded!"
}

# Show current revision
show_current() {
    echo "Current revision:"
    alembic current
}

# Show history
show_history() {
    echo "Migration history:"
    alembic history --verbose
}

# Test migration
test_migration() {
    print_warning "Testing migration..."

    echo "Step 1: Upgrading..."
    alembic upgrade head
    print_success "Upgrade successful"

    echo ""
    echo "Step 2: Downgrading..."
    alembic downgrade -1
    print_success "Downgrade successful"

    echo ""
    echo "Step 3: Upgrading again..."
    alembic upgrade head
    print_success "Re-upgrade successful"

    echo ""
    print_success "Migration test completed successfully!"
}

# Main script
case "$1" in
    init)
        init_alembic
        ;;
    create)
        create_migration "$2"
        ;;
    upgrade)
        upgrade_db
        ;;
    downgrade)
        downgrade_db
        ;;
    current)
        show_current
        ;;
    history)
        show_history
        ;;
    test)
        test_migration
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        print_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
