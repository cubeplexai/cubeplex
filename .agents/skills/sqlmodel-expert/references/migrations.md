# Comprehensive Alembic Migrations Guide

## Table of Contents
1. Alembic Setup and Configuration
2. Creating Migrations
3. Schema Changes Patterns
4. Data Migrations
5. Migration Best Practices
6. Rollback Strategies
7. Production Migration Workflow
8. Troubleshooting

---

## 1. Alembic Setup and Configuration

### Initial Setup

```bash
# Install Alembic
pip install alembic

# Initialize Alembic in your project
alembic init alembic

# This creates:
# alembic/
# ├── env.py          # Migration environment
# ├── script.py.mako  # Migration template
# └── versions/       # Migration files
# alembic.ini         # Alembic configuration
```

### Configure alembic.ini

```ini
# alembic.ini
[alembic]
script_location = alembic
prepend_sys_path = .

# Database URL (use environment variable in production)
sqlalchemy.url = postgresql://user:password@localhost/dbname

# Or use environment variable:
# sqlalchemy.url = ${DATABASE_URL}

file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s
```

### Configure env.py for SQLModel

```python
# alembic/env.py
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os

# Import your SQLModel models
from app.models import SQLModel  # Your base model
from app.database import get_database_url

# Alembic Config object
config = context.config

# Override sqlalchemy.url from environment
config.set_main_option("sqlalchemy.url", get_database_url())

# Interpret config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata for 'autogenerate'
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

---

## 2. Creating Migrations

### Autogenerate Migration (Recommended)

```bash
# Generate migration from model changes
alembic revision --autogenerate -m "Add user table"

# Result: alembic/versions/2024_01_15_1430-abc123_add_user_table.py
```

**Generated migration file:**
```python
"""Add user table

Revision ID: abc123
Revises:
Create Date: 2024-01-15 14:30:00

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers
revision = 'abc123'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('hashed_password', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_email'), 'user', ['email'], unique=True)
    op.create_index(op.f('ix_user_username'), 'user', ['username'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_username'), table_name='user')
    op.drop_index(op.f('ix_user_email'), table_name='user')
    op.drop_table('user')
```

### Manual Migration

```bash
# Create empty migration file
alembic revision -m "custom migration"
```

---

## 3. Schema Changes Patterns

### Adding a Column

```python
def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column('phone_number', sa.String(20), nullable=True)
    )

def downgrade() -> None:
    op.drop_column('user', 'phone_number')
```

### Adding a Non-Nullable Column with Default

```python
def upgrade() -> None:
    # Step 1: Add column as nullable
    op.add_column(
        'user',
        sa.Column('role', sa.String(50), nullable=True)
    )

    # Step 2: Set default value for existing rows
    op.execute("UPDATE user SET role = 'user' WHERE role IS NULL")

    # Step 3: Make column non-nullable
    op.alter_column('user', 'role', nullable=False)

def downgrade() -> None:
    op.drop_column('user', 'role')
```

### Renaming a Column

```python
def upgrade() -> None:
    op.alter_column(
        'user',
        'name',
        new_column_name='full_name'
    )

def downgrade() -> None:
    op.alter_column(
        'user',
        'full_name',
        new_column_name='name'
    )
```

### Changing Column Type

```python
def upgrade() -> None:
    # PostgreSQL
    op.alter_column(
        'user',
        'age',
        type_=sa.String(3),
        postgresql_using='age::text'
    )

    # SQLite (requires recreation)
    # See "Complex SQLite Migrations" below

def downgrade() -> None:
    op.alter_column(
        'user',
        'age',
        type_=sa.Integer()
    )
```

### Adding Foreign Key

```python
def upgrade() -> None:
    op.add_column(
        'post',
        sa.Column('user_id', sa.Integer(), nullable=True)
    )

    op.create_foreign_key(
        'fk_post_user_id',  # Constraint name
        'post',              # Source table
        'user',              # Referenced table
        ['user_id'],         # Source columns
        ['id'],              # Referenced columns
        ondelete='CASCADE'   # Optional: cascade delete
    )

def downgrade() -> None:
    op.drop_constraint('fk_post_user_id', 'post', type_='foreignkey')
    op.drop_column('post', 'user_id')
```

### Adding Index

```python
def upgrade() -> None:
    op.create_index(
        'idx_user_email_username',
        'user',
        ['email', 'username'],
        unique=False
    )

def downgrade() -> None:
    op.drop_index('idx_user_email_username', table_name='user')
```

### Adding Unique Constraint

```python
def upgrade() -> None:
    op.create_unique_constraint(
        'uq_user_email',
        'user',
        ['email']
    )

def downgrade() -> None:
    op.drop_constraint('uq_user_email', 'user', type_='unique')
```

### Creating a New Table

```python
def upgrade() -> None:
    op.create_table(
        'task',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('completed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_task_user_id', 'task', ['user_id'])

def downgrade() -> None:
    op.drop_index('idx_task_user_id', table_name='task')
    op.drop_table('task')
```

---

## 4. Data Migrations

### Simple Data Update

```python
from alembic import op
from sqlalchemy import text

def upgrade() -> None:
    # Update data
    op.execute(
        text("UPDATE user SET is_active = true WHERE is_active IS NULL")
    )

def downgrade() -> None:
    # Revert data (if possible)
    op.execute(
        text("UPDATE user SET is_active = NULL WHERE is_active = true")
    )
```

### Complex Data Migration with SQLModel

```python
from alembic import op
from sqlalchemy.orm import Session
from app.models import User, Task  # Your SQLModel models

def upgrade() -> None:
    # Get database connection
    bind = op.get_bind()
    session = Session(bind=bind)

    # Perform complex data manipulation
    users = session.query(User).all()
    for user in users:
        # Create default task for each user
        task = Task(
            title=f"Welcome task for {user.username}",
            description="Get started with the app",
            user_id=user.id,
            completed=False
        )
        session.add(task)

    session.commit()

def downgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)

    # Delete welcome tasks
    session.query(Task).filter(
        Task.title.like("Welcome task for %")
    ).delete()

    session.commit()
```

### Batch Operations for Large Tables

```python
from alembic import op
from sqlalchemy import text

def upgrade() -> None:
    # Process in batches to avoid locking large tables
    batch_size = 1000
    offset = 0

    while True:
        result = op.execute(
            text(f"""
                UPDATE user
                SET email_verified = false
                WHERE email_verified IS NULL
                AND id IN (
                    SELECT id FROM user
                    WHERE email_verified IS NULL
                    ORDER BY id
                    LIMIT {batch_size} OFFSET {offset}
                )
            """)
        )

        if result.rowcount == 0:
            break

        offset += batch_size

def downgrade() -> None:
    pass  # Not reversible
```

---

## 5. Migration Best Practices

### 1. Always Review Autogenerated Migrations

```python
# Autogenerate creates this:
def upgrade() -> None:
    op.drop_column('user', 'password')

# But you should add data migration:
def upgrade() -> None:
    # Copy data before dropping
    op.add_column('user', sa.Column('hashed_password', sa.String()))
    op.execute("UPDATE user SET hashed_password = password")
    op.drop_column('user', 'password')
```

### 2. Use Descriptive Migration Names

```bash
# Good
alembic revision --autogenerate -m "add_user_email_verification_fields"
alembic revision --autogenerate -m "create_task_priority_index"

# Bad
alembic revision --autogenerate -m "update"
alembic revision --autogenerate -m "changes"
```

### 3. One Logical Change Per Migration

```python
# Good - focused migration
def upgrade() -> None:
    op.add_column('user', sa.Column('email_verified', sa.Boolean()))
    op.add_column('user', sa.Column('email_verified_at', sa.DateTime()))

# Better - split into separate migrations if unrelated
# Migration 1: Add email verification
# Migration 2: Add user roles
```

### 4. Test Migrations Both Ways

```bash
# Test upgrade
alembic upgrade head

# Test downgrade
alembic downgrade -1

# Test upgrade again
alembic upgrade head
```

### 5. Never Edit Applied Migrations

```python
# Never modify a migration that's been applied to production
# Instead, create a new migration to fix issues
```

---

## 6. Running Migrations

### Basic Commands

```bash
# Show current revision
alembic current

# Show migration history
alembic history --verbose

# Upgrade to latest
alembic upgrade head

# Upgrade one step
alembic upgrade +1

# Downgrade one step
alembic downgrade -1

# Downgrade to specific revision
alembic downgrade abc123

# Downgrade all
alembic downgrade base

# Show SQL without executing
alembic upgrade head --sql

# Stamp database at specific revision (mark as applied without running)
alembic stamp head
```

### Migration to Specific Revision

```bash
# Upgrade to specific revision
alembic upgrade abc123

# Downgrade to specific revision
alembic downgrade xyz789
```

---

## 7. Production Migration Workflow

### Pre-Deployment Checklist

```bash
# 1. Test migrations locally
alembic upgrade head
alembic downgrade base
alembic upgrade head

# 2. Review all migration files
cat alembic/versions/*.py

# 3. Backup production database
pg_dump mydb > backup_$(date +%Y%m%d_%H%M%S).sql

# 4. Test on staging environment
# Deploy to staging
alembic upgrade head

# 5. Monitor for issues
# Check application logs
# Verify data integrity
```

### Safe Production Migration

```python
# Dockerfile entrypoint
#!/bin/bash
set -e

# Run migrations
echo "Running database migrations..."
alembic upgrade head

# Start application
echo "Starting application..."
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Zero-Downtime Migration Strategy

```python
# Phase 1: Add new column (nullable)
def upgrade() -> None:
    op.add_column('user', sa.Column('new_email', sa.String(), nullable=True))

# Deploy application version that writes to both columns

# Phase 2: Backfill data
def upgrade() -> None:
    op.execute("UPDATE user SET new_email = email WHERE new_email IS NULL")

# Phase 3: Make column non-nullable
def upgrade() -> None:
    op.alter_column('user', 'new_email', nullable=False)

# Deploy application version that reads from new column

# Phase 4: Drop old column
def upgrade() -> None:
    op.drop_column('user', 'email')

# Phase 5: Rename new column
def upgrade() -> None:
    op.alter_column('user', 'new_email', new_column_name='email')
```

---

## 8. Troubleshooting

### Issue: Alembic Can't Detect Changes

```python
# Problem: Models changed but autogenerate doesn't detect them

# Solution 1: Check env.py imports all models
from app.models import User, Task, Team  # Import all models

# Solution 2: Verify target_metadata is set correctly
target_metadata = SQLModel.metadata

# Solution 3: Use compare_type=True for column type changes
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    compare_type=True
)
```

### Issue: Migration Conflicts

```bash
# Multiple heads (branching)
alembic heads

# Merge branches
alembic merge -m "merge branches" head1 head2
```

### Issue: Failed Migration

```bash
# Check current state
alembic current

# Manually fix database issue
# Then stamp as if migration succeeded
alembic stamp head

# Or downgrade and retry
alembic downgrade -1
# Fix the issue
alembic upgrade head
```

### Issue: SQLite Limitations

```python
# SQLite doesn't support many ALTER operations
# Use batch operations

from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.add_column(sa.Column('new_field', sa.String()))
        batch_op.alter_column('old_field', new_column_name='renamed_field')

def downgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.drop_column('new_field')
        batch_op.alter_column('renamed_field', new_column_name='old_field')
```

---

## Advanced Patterns

### Multiple Database Support

```python
# alembic/env.py
def run_migrations_online() -> None:
    # Get database URLs from environment
    databases = {
        'main': os.getenv('MAIN_DB_URL'),
        'analytics': os.getenv('ANALYTICS_DB_URL'),
    }

    for name, url in databases.items():
        engine = create_engine(url)
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata
            )
            with context.begin_transaction():
                context.run_migrations()
```

### Custom Migration Template

```python
# alembic/script.py.mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
Author: ${author}  # Custom field
Ticket: ${ticket}  # Custom field
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

This comprehensive guide covers all aspects of SQLModel migrations with Alembic!
