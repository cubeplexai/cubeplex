# SQLModel Query Patterns and Optimization

## Table of Contents
1. Basic Query Patterns
2. Advanced Queries
3. N+1 Query Problem Solutions
4. Query Optimization Techniques
5. Bulk Operations
6. Raw SQL and Performance
7. Testing and Profiling

---

## 1. Basic Query Patterns

### Simple Queries

```python
from sqlmodel import Session, select
from app.models import User

# Get all users
statement = select(User)
users = session.exec(statement).all()

# Get one user
statement = select(User).where(User.id == 1)
user = session.exec(statement).first()

# Get or 404
user = session.get(User, user_id)
if not user:
    raise HTTPException(status_code=404, detail="User not found")
```

### Filtering

```python
# Simple filter
statement = select(User).where(User.is_active == True)

# Multiple conditions (AND)
statement = select(User).where(
    User.is_active == True,
    User.email_verified == True
)

# OR conditions
from sqlalchemy import or_
statement = select(User).where(
    or_(
        User.email == "user@example.com",
        User.username == "user123"
    )
)

# IN clause
user_ids = [1, 2, 3, 4]
statement = select(User).where(User.id.in_(user_ids))

# LIKE clause
statement = select(User).where(User.username.like("%john%"))

# BETWEEN
from datetime import datetime, timedelta
start_date = datetime.utcnow() - timedelta(days=7)
statement = select(User).where(User.created_at.between(start_date, datetime.utcnow()))

# IS NULL / IS NOT NULL
statement = select(User).where(User.deleted_at.is_(None))
statement = select(User).where(User.deleted_at.isnot(None))
```

### Ordering

```python
# Order by single column
statement = select(User).order_by(User.created_at.desc())

# Order by multiple columns
statement = select(User).order_by(
    User.is_active.desc(),
    User.created_at.desc()
)

# Dynamic ordering
from sqlalchemy import asc, desc

order_direction = "desc"
order_column = User.created_at

if order_direction == "desc":
    statement = select(User).order_by(desc(order_column))
else:
    statement = select(User).order_by(asc(order_column))
```

### Pagination

```python
# Offset-based pagination
def get_users(skip: int = 0, limit: int = 100):
    statement = select(User).offset(skip).limit(limit)
    return session.exec(statement).all()

# Cursor-based pagination (better for large datasets)
def get_users_cursor(cursor_id: int = None, limit: int = 100):
    statement = select(User)

    if cursor_id:
        statement = statement.where(User.id > cursor_id)

    statement = statement.order_by(User.id).limit(limit)
    return session.exec(statement).all()
```

---

## 2. Advanced Queries

### Joins

```python
from sqlmodel import select
from app.models import User, Post

# Inner join
statement = (
    select(User, Post)
    .join(Post, User.id == Post.user_id)
)
results = session.exec(statement).all()

# Left outer join
from sqlalchemy import outerjoin
statement = (
    select(User, Post)
    .outerjoin(Post, User.id == Post.user_id)
)

# Join with filtering
statement = (
    select(User, Post)
    .join(Post)
    .where(Post.published == True)
    .where(User.is_active == True)
)
```

### Aggregations

```python
from sqlalchemy import func

# Count
statement = select(func.count(User.id))
count = session.exec(statement).one()

# Count with filter
statement = select(func.count(User.id)).where(User.is_active == True)

# Group by
statement = (
    select(User.country, func.count(User.id))
    .group_by(User.country)
)
results = session.exec(statement).all()

# Having clause
statement = (
    select(User.country, func.count(User.id))
    .group_by(User.country)
    .having(func.count(User.id) > 10)
)

# Multiple aggregations
statement = (
    select(
        User.country,
        func.count(User.id).label('user_count'),
        func.avg(User.age).label('avg_age'),
        func.max(User.created_at).label('latest_signup')
    )
    .group_by(User.country)
)
```

### Subqueries

```python
# Scalar subquery
subquery = (
    select(func.count(Post.id))
    .where(Post.user_id == User.id)
    .scalar_subquery()
)

statement = select(User, subquery.label('post_count'))

# Subquery in WHERE
active_user_ids = (
    select(User.id)
    .where(User.is_active == True)
    .subquery()
)

statement = select(Post).where(Post.user_id.in_(active_user_ids))

# Common Table Expression (CTE)
recent_posts = (
    select(Post)
    .where(Post.created_at > datetime.utcnow() - timedelta(days=7))
    .cte('recent_posts')
)

statement = (
    select(User)
    .join(recent_posts, User.id == recent_posts.c.user_id)
    .where(recent_posts.c.published == True)
)
```

### Window Functions

```python
from sqlalchemy import func, over

# Row number
statement = select(
    User.username,
    User.country,
    func.row_number().over(
        partition_by=User.country,
        order_by=User.created_at.desc()
    ).label('row_num')
)

# Rank users by post count per country
post_count = (
    select(
        User.id.label('user_id'),
        func.count(Post.id).label('post_count')
    )
    .join(Post)
    .group_by(User.id)
    .subquery()
)

statement = select(
    User.username,
    User.country,
    post_count.c.post_count,
    func.rank().over(
        partition_by=User.country,
        order_by=post_count.c.post_count.desc()
    ).label('rank')
).join(post_count, User.id == post_count.c.user_id)
```

---

## 3. N+1 Query Problem Solutions

### The Problem

```python
# BAD - N+1 query problem
users = session.exec(select(User)).all()

for user in users:
    # Each iteration triggers a new query!
    posts = user.posts  # SELECT * FROM post WHERE user_id = ?
    print(f"{user.username}: {len(posts)} posts")

# This executes 1 + N queries (1 for users, N for each user's posts)
```

### Solution 1: Eager Loading with selectinload

```python
from sqlalchemy.orm import selectinload

# GOOD - Only 2 queries total
statement = select(User).options(selectinload(User.posts))
users = session.exec(statement).all()

for user in users:
    # No additional query! Data already loaded
    posts = user.posts
    print(f"{user.username}: {len(posts)} posts")
```

### Solution 2: Joined Load

```python
from sqlalchemy.orm import joinedload

# Single query with JOIN
statement = select(User).options(joinedload(User.posts))
users = session.exec(statement).unique().all()
```

### Solution 3: Nested Eager Loading

```python
# Load users with posts and post comments
statement = (
    select(User)
    .options(
        selectinload(User.posts).selectinload(Post.comments)
    )
)
users = session.exec(statement).all()
```

### Solution 4: Manual Join and Group

```python
# For simple cases, manual join can be more efficient
statement = (
    select(
        User.id,
        User.username,
        func.count(Post.id).label('post_count')
    )
    .outerjoin(Post)
    .group_by(User.id, User.username)
)
results = session.exec(statement).all()
```

---

## 4. Query Optimization Techniques

### Use Indexes

```python
# Add indexes to frequently queried columns
class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int = Field(primary_key=True)
    email: str = Field(index=True, unique=True)  # Single column index
    username: str = Field(index=True)

    # Composite index for common query pattern
    __table_args__ = (
        Index('idx_active_created', 'is_active', 'created_at'),
    )
```

### Select Only Needed Columns

```python
# BAD - loads entire object
users = session.exec(select(User)).all()

# GOOD - select only needed columns
statement = select(User.id, User.username, User.email)
results = session.exec(statement).all()
```

### Use Exists for Checking

```python
from sqlalchemy import exists

# BAD - loads all data just to check
statement = select(User).where(User.email == email)
user = session.exec(statement).first()
if user:
    # exists

# GOOD - only checks existence
statement = select(exists(select(User).where(User.email == email)))
exists_result = session.exec(statement).one()
```

### Batch Queries

```python
# BAD - multiple individual queries
for user_id in user_ids:
    user = session.get(User, user_id)
    process(user)

# GOOD - single batch query
statement = select(User).where(User.id.in_(user_ids))
users = session.exec(statement).all()
for user in users:
    process(user)
```

---

## 5. Bulk Operations

### Bulk Insert

```python
# Create multiple records efficiently
users = [
    User(username=f"user{i}", email=f"user{i}@example.com")
    for i in range(1000)
]

# Add all at once
session.add_all(users)
session.commit()

# Or use bulk_insert_mappings (faster, bypasses ORM)
user_dicts = [
    {"username": f"user{i}", "email": f"user{i}@example.com"}
    for i in range(1000)
]
session.bulk_insert_mappings(User, user_dicts)
session.commit()
```

### Bulk Update

```python
# Update multiple records
statement = (
    update(User)
    .where(User.is_active == False)
    .values(deleted_at=datetime.utcnow())
)
session.exec(statement)
session.commit()

# Bulk update with mappings
user_updates = [
    {"id": 1, "last_login": datetime.utcnow()},
    {"id": 2, "last_login": datetime.utcnow()},
]
session.bulk_update_mappings(User, user_updates)
session.commit()
```

### Bulk Delete

```python
# Delete multiple records
statement = delete(User).where(User.deleted_at.isnot(None))
session.exec(statement)
session.commit()

# Batch delete (for large sets)
batch_size = 1000
while True:
    statement = (
        delete(User)
        .where(User.is_active == False)
        .limit(batch_size)
    )
    result = session.exec(statement)
    session.commit()

    if result.rowcount < batch_size:
        break
```

---

## 6. Raw SQL and Performance

### Execute Raw SQL

```python
from sqlalchemy import text

# Read query
statement = text("SELECT * FROM users WHERE is_active = :is_active")
results = session.exec(statement, {"is_active": True}).all()

# Write query
statement = text("""
    UPDATE users
    SET last_login = :timestamp
    WHERE id = :user_id
""")
session.exec(statement, {"timestamp": datetime.utcnow(), "user_id": 1})
session.commit()
```

### Use Raw SQL for Complex Operations

```python
# Complex analytical query
statement = text("""
    WITH monthly_stats AS (
        SELECT
            DATE_TRUNC('month', created_at) as month,
            COUNT(*) as user_count,
            COUNT(CASE WHEN is_active THEN 1 END) as active_count
        FROM users
        GROUP BY DATE_TRUNC('month', created_at)
    )
    SELECT
        month,
        user_count,
        active_count,
        ROUND(100.0 * active_count / user_count, 2) as active_percentage
    FROM monthly_stats
    ORDER BY month DESC
""")
results = session.exec(statement).all()
```

---

## 7. Testing and Profiling

### Query Logging

```python
import logging

# Enable SQLAlchemy query logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# Now all queries are logged
statement = select(User).where(User.is_active == True)
users = session.exec(statement).all()

# Output:
# SELECT users.id, users.username, users.email, users.is_active
# FROM users WHERE users.is_active = true
```

### Query Profiling

```python
from sqlalchemy import event
from time import time

# Profile slow queries
@event.listens_for(engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault('query_start_time', []).append(time())

@event.listens_for(engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total = time() - conn.info['query_start_time'].pop(-1)
    if total > 0.1:  # Log queries slower than 100ms
        print(f"Slow query ({total:.2f}s): {statement}")
```

### Explain Analyze

```python
# PostgreSQL EXPLAIN ANALYZE
statement = select(User).where(User.is_active == True)
explain = session.exec(
    text(f"EXPLAIN ANALYZE {str(statement.compile(engine))}")
).all()

for row in explain:
    print(row)
```

### Query Count Testing

```python
import pytest
from sqlalchemy import event

@pytest.fixture
def query_counter(session):
    """Count queries executed in test"""
    queries = []

    def receive_after_cursor_execute(conn, cursor, statement, *args):
        queries.append(statement)

    event.listen(engine, "after_cursor_execute", receive_after_cursor_execute)

    yield queries

    event.remove(engine, "after_cursor_execute", receive_after_cursor_execute)

def test_no_n_plus_one(session, query_counter):
    # Load users with posts (should be 2 queries)
    statement = select(User).options(selectinload(User.posts))
    users = session.exec(statement).all()

    # Access posts (should not trigger additional queries)
    for user in users:
        _ = user.posts

    # Assert only 2 queries were executed
    assert len(query_counter) == 2
```

---

## Best Practices Summary

1. **Always use indexes** on foreign keys and frequently queried columns
2. **Use eager loading** (selectinload/joinedload) to prevent N+1 queries
3. **Select only needed columns** when possible
4. **Use pagination** for large result sets
5. **Batch operations** instead of loops
6. **Profile queries** in development
7. **Use connection pooling** in production
8. **Monitor slow queries** with logging
9. **Cache expensive queries** when appropriate
10. **Test query counts** in integration tests
