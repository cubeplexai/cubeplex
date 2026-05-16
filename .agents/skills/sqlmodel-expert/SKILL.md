---
name: sqlmodel-expert
description: Advanced SQLModel patterns and comprehensive database migrations with Alembic. Use when creating SQLModel models, defining relationships (one-to-many, many-to-many, self-referential), setting up database migrations, optimizing queries, solving N+1 problems, implementing inheritance patterns, working with composite keys, creating indexes, performing data migrations, or troubleshooting Alembic issues. Triggers include "SQLModel", "Alembic migration", "database model", "relationship", "foreign key", "migration", "N+1 query", "query optimization", "database schema", or questions about ORM patterns.
---

# SQLModel Expert

Advanced SQLModel patterns and comprehensive Alembic migrations for production databases.

## Quick Start

### Define a Basic Model

```python
from sqlmodel import Field, SQLModel
from typing import Optional
from datetime import datetime

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    description: Optional[str] = None
    completed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

### Initialize Database

```bash
# Using provided script
python scripts/init_db.py --url postgresql://user:pass@localhost/db

# Or manually
from sqlmodel import create_engine
engine = create_engine("postgresql://user:pass@localhost/db")
SQLModel.metadata.create_all(engine)
```

### Create Migration

```bash
# Using provided helper script
./scripts/migrate.sh create "add user table"

# Or directly with Alembic
alembic revision --autogenerate -m "add user table"
alembic upgrade head
```

## Core Topics

### 1. Advanced Model Patterns
**See:** [references/advanced-models.md](references/advanced-models.md)

- **Relationships**: One-to-many, many-to-many, self-referential
- **Inheritance**: Single table, joined table, polymorphism
- **Validation**: Pydantic validators, custom constraints
- **Mixins**: Timestamp, soft delete, reusable patterns
- **Field Types**: Enums, JSON, arrays, custom types
- **Indexes**: Single, composite, partial indexes
- **Constraints**: Unique, check, foreign key cascades

### 2. Comprehensive Migrations
**See:** [references/migrations.md](references/migrations.md)

- **Alembic Setup**: Configuration, env.py for SQLModel
- **Creating Migrations**: Autogenerate vs manual
- **Schema Changes**: Add/drop columns, rename, change types
- **Data Migrations**: Complex data transformations
- **Production Workflow**: Zero-downtime migrations
- **Rollback Strategies**: Safe downgrade patterns
- **Troubleshooting**: Common issues and solutions

### 3. Query Optimization
**See:** [references/queries-optimization.md](references/queries-optimization.md)

- **N+1 Problem**: Solutions with eager loading
- **Query Patterns**: Joins, aggregations, subqueries
- **Performance**: Indexes, batch operations, profiling
- **Advanced Queries**: Window functions, CTEs
- **Bulk Operations**: Insert, update, delete at scale
- **Testing**: Query counting, explain analyze

## Common Patterns

### One-to-Many Relationship

```python
from typing import List
from sqlmodel import Field, Relationship, SQLModel

class Team(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str

    # One team has many heroes
    heroes: List["Hero"] = Relationship(back_populates="team")

class Hero(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    team_id: Optional[int] = Field(foreign_key="team.id")

    # Many heroes belong to one team
    team: Optional[Team] = Relationship(back_populates="heroes")
```

### Many-to-Many with Link Table

```python
class HeroTeamLink(SQLModel, table=True):
    hero_id: int = Field(foreign_key="hero.id", primary_key=True)
    team_id: int = Field(foreign_key="team.id", primary_key=True)
    joined_at: datetime = Field(default_factory=datetime.utcnow)

class Hero(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    teams: List["Team"] = Relationship(
        back_populates="heroes",
        link_model=HeroTeamLink
    )

class Team(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    heroes: List[Hero] = Relationship(
        back_populates="teams",
        link_model=HeroTeamLink
    )
```

### Solving N+1 Query Problem

```python
from sqlalchemy.orm import selectinload

# BAD - N+1 queries
users = session.exec(select(User)).all()
for user in users:
    posts = user.posts  # Each triggers a query!

# GOOD - Eager loading (2 queries total)
statement = select(User).options(selectinload(User.posts))
users = session.exec(statement).all()
for user in users:
    posts = user.posts  # No additional query!
```

### Creating a Migration

```python
# 1. Modify your model
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str
    phone: str  # New field added

# 2. Generate migration
# alembic revision --autogenerate -m "add phone to user"

# 3. Review generated migration
def upgrade() -> None:
    op.add_column('user', sa.Column('phone', sa.String(), nullable=True))

def downgrade() -> None:
    op.drop_column('user', 'phone')

# 4. Apply migration
# alembic upgrade head
```

## Migration Helper Scripts

### Initialize Database
```bash
python scripts/init_db.py --url postgresql://user:pass@localhost/db
```

### Migration Operations
```bash
./scripts/migrate.sh init              # Initialize Alembic
./scripts/migrate.sh create "message"  # Create migration
./scripts/migrate.sh upgrade           # Apply migrations
./scripts/migrate.sh downgrade         # Rollback one
./scripts/migrate.sh current           # Show current
./scripts/migrate.sh history           # Show history
./scripts/migrate.sh test              # Test up & down
```

## Example Models

Use the example models in `assets/example-models.py` as templates:

- User model with timestamp mixin
- Task model with enums and relationships
- Team model with many-to-many
- Tag system with link tables
- Separate read/write/update models

Copy to your project:
```bash
cp assets/example-models.py your-project/app/models.py
```

## Best Practices Checklist

### Model Design
- [ ] Use type hints for all fields
- [ ] Separate read/write/update models
- [ ] Use mixins for common fields (timestamps, soft delete)
- [ ] Define indexes on foreign keys and frequently queried columns
- [ ] Use enums for constrained choices
- [ ] Implement proper validation with Pydantic validators

### Relationships
- [ ] Use `back_populates` for bidirectional relationships
- [ ] Create explicit link tables for many-to-many
- [ ] Consider cascade delete behavior
- [ ] Use eager loading to prevent N+1 queries
- [ ] Index foreign key columns

### Migrations
- [ ] Always review autogenerated migrations
- [ ] One logical change per migration
- [ ] Test both upgrade and downgrade
- [ ] Use descriptive migration names
- [ ] Never edit applied migrations
- [ ] Add data migrations when changing schemas
- [ ] Backup database before production migrations

### Query Optimization
- [ ] Use eager loading (selectinload) for relationships
- [ ] Select only needed columns
- [ ] Use indexes for WHERE/ORDER BY columns
- [ ] Batch operations instead of loops
- [ ] Profile slow queries
- [ ] Use connection pooling

## Troubleshooting Guide

### Migration Issues

**Problem**: Alembic doesn't detect model changes
```python
# Solution: Ensure models are imported in env.py
from app.models import User, Task, Team  # Import all models
target_metadata = SQLModel.metadata
```

**Problem**: Failed migration
```bash
# Check current state
alembic current

# Manually fix issue, then stamp
alembic stamp head

# Or downgrade and retry
alembic downgrade -1
alembic upgrade head
```

### Query Performance

**Problem**: Slow queries
```python
# Enable query logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# Use EXPLAIN ANALYZE
explain = session.exec(text("EXPLAIN ANALYZE SELECT ...")).all()

# Profile queries
# See references/queries-optimization.md for detailed patterns
```

**Problem**: N+1 queries
```python
# Use selectinload
statement = select(User).options(selectinload(User.posts))

# Or joinedload
from sqlalchemy.orm import joinedload
statement = select(User).options(joinedload(User.posts))
```

## Production Workflow

### Development
1. Modify SQLModel models
2. Generate migration: `./scripts/migrate.sh create "description"`
3. Review generated migration file
4. Test migration: `./scripts/migrate.sh test`
5. Commit migration file

### Staging
1. Deploy application code
2. Run migrations: `alembic upgrade head`
3. Verify data integrity
4. Test application

### Production
1. Backup database: `pg_dump mydb > backup.sql`
2. Deploy in maintenance window
3. Run migrations: `alembic upgrade head`
4. Monitor logs and metrics
5. Verify application functionality

## Zero-Downtime Migration Strategy

For large production databases:

```python
# Phase 1: Add new column (nullable)
def upgrade():
    op.add_column('user', sa.Column('new_email', sa.String(), nullable=True))

# Deploy app version that writes to both columns

# Phase 2: Backfill data
def upgrade():
    op.execute("UPDATE user SET new_email = email WHERE new_email IS NULL")

# Phase 3: Make non-nullable
def upgrade():
    op.alter_column('user', 'new_email', nullable=False)

# Deploy app version that reads from new column

# Phase 4: Drop old column
def upgrade():
    op.drop_column('user', 'email')
```

## Additional Resources

- **Advanced Patterns**: See references/advanced-models.md for inheritance, polymorphism, composite keys
- **Migration Guide**: See references/migrations.md for Alembic mastery
- **Query Optimization**: See references/queries-optimization.md for performance tuning

This skill provides everything needed for professional SQLModel development and database management.
