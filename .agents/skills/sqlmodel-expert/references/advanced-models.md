# Advanced SQLModel Patterns

## Table of Contents
1. Model Definition Patterns
2. Relationships (One-to-Many, Many-to-Many)
3. Inheritance and Polymorphism
4. Composite Keys and Constraints
5. Custom Field Types
6. Table Partitioning Strategies

---

## 1. Model Definition Patterns

### Basic Model with Validation

```python
from sqlmodel import Field, SQLModel
from pydantic import EmailStr, validator
from datetime import datetime
from typing import Optional

class User(SQLModel, table=True):
    """User model with validation and defaults"""
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, min_length=3, max_length=50)
    email: EmailStr = Field(unique=True)
    full_name: str
    hashed_password: str
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @validator('username')
    def username_alphanumeric(cls, v):
        assert v.isalnum(), 'must be alphanumeric'
        return v
```

### Separate Read/Write Models

```python
from sqlmodel import Field, SQLModel
from typing import Optional

# Base model (shared fields)
class UserBase(SQLModel):
    username: str = Field(index=True, unique=True)
    email: str = Field(unique=True)
    full_name: str
    is_active: bool = True

# Table model (database schema)
class User(UserBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    hashed_password: str

# Create model (API input)
class UserCreate(UserBase):
    password: str

# Read model (API output - excludes password)
class UserRead(UserBase):
    id: int

# Update model (partial updates)
class UserUpdate(SQLModel):
    username: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    is_active: Optional[bool] = None
```

### Timestamp Mixin

```python
from datetime import datetime
from sqlmodel import Field, SQLModel

class TimestampMixin(SQLModel):
    """Mixin to add timestamp fields"""
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

class SoftDeleteMixin(SQLModel):
    """Mixin for soft delete functionality"""
    deleted_at: Optional[datetime] = Field(default=None, nullable=True)
    is_deleted: bool = Field(default=False)

class Task(TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: Optional[str] = None
    completed: bool = Field(default=False)
```

---

## 2. Relationships

### One-to-Many Relationship

```python
from typing import List, Optional
from sqlmodel import Field, Relationship, SQLModel

class Team(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    headquarters: str

    # Relationship: one team has many heroes
    heroes: List["Hero"] = Relationship(back_populates="team")

class Hero(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    secret_name: str
    age: Optional[int] = None

    # Foreign key
    team_id: Optional[int] = Field(default=None, foreign_key="team.id")

    # Relationship: many heroes belong to one team
    team: Optional[Team] = Relationship(back_populates="heroes")
```

**Querying with relationships:**
```python
from sqlmodel import Session, select

# Eager loading (prevents N+1 queries)
from sqlalchemy.orm import selectinload

statement = select(Team).options(selectinload(Team.heroes))
teams = session.exec(statement).all()

for team in teams:
    print(f"Team: {team.name}")
    for hero in team.heroes:
        print(f"  - {hero.name}")
```

### Many-to-Many Relationship

```python
from typing import List, Optional
from sqlmodel import Field, Relationship, SQLModel

# Link table (association table)
class HeroTeamLink(SQLModel, table=True):
    """Link table for many-to-many relationship"""
    __tablename__ = "hero_team_link"

    hero_id: Optional[int] = Field(
        default=None,
        foreign_key="hero.id",
        primary_key=True
    )
    team_id: Optional[int] = Field(
        default=None,
        foreign_key="team.id",
        primary_key=True
    )
    # Additional fields on the link
    joined_at: datetime = Field(default_factory=datetime.utcnow)
    role: Optional[str] = None

class Hero(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    secret_name: str

    # Many-to-many relationship
    teams: List["Team"] = Relationship(
        back_populates="heroes",
        link_model=HeroTeamLink
    )

class Team(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    headquarters: str

    # Many-to-many relationship
    heroes: List[Hero] = Relationship(
        back_populates="teams",
        link_model=HeroTeamLink
    )
```

### Self-Referential Relationship

```python
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str

    # Self-referential foreign key
    manager_id: Optional[int] = Field(default=None, foreign_key="user.id")

    # Relationships
    manager: Optional["User"] = Relationship(
        back_populates="subordinates",
        sa_relationship_kwargs={"remote_side": "User.id"}
    )
    subordinates: List["User"] = Relationship(back_populates="manager")
```

### Cascading Deletes

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship as sa_relationship

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str

    # Cascade delete posts when user is deleted
    posts: List["Post"] = Relationship(
        back_populates="author",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

class Post(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    content: str
    user_id: int = Field(foreign_key="user.id")

    author: User = Relationship(back_populates="posts")
```

---

## 3. Inheritance and Polymorphism

### Single Table Inheritance

```python
from sqlmodel import Field, SQLModel
from typing import Optional

class Person(SQLModel, table=True):
    """Base person table with discriminator"""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    type: str  # Discriminator column

    # Employee-specific fields (nullable for non-employees)
    employee_id: Optional[str] = None
    department: Optional[str] = None

    # Customer-specific fields (nullable for non-customers)
    customer_number: Optional[str] = None
    loyalty_points: Optional[int] = None

# Create views/models for specific types
class EmployeeCreate(SQLModel):
    name: str
    employee_id: str
    department: str

class CustomerCreate(SQLModel):
    name: str
    customer_number: str
    loyalty_points: int = 0
```

### Joined Table Inheritance (Better approach)

```python
from sqlmodel import Field, SQLModel
from typing import Optional

# Base table
class Person(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

# Separate tables for subtypes
class Employee(SQLModel, table=True):
    id: Optional[int] = Field(default=None, foreign_key="person.id", primary_key=True)
    employee_id: str = Field(unique=True)
    department: str
    salary: float

class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, foreign_key="person.id", primary_key=True)
    customer_number: str = Field(unique=True)
    loyalty_points: int = Field(default=0)
```

---

## 4. Composite Keys and Constraints

### Composite Primary Key

```python
from sqlmodel import Field, SQLModel
from typing import Optional

class UserRole(SQLModel, table=True):
    """User role assignment with composite primary key"""
    __tablename__ = "user_roles"

    user_id: int = Field(foreign_key="user.id", primary_key=True)
    role_id: int = Field(foreign_key="role.id", primary_key=True)
    granted_at: datetime = Field(default_factory=datetime.utcnow)
    granted_by: Optional[int] = Field(foreign_key="user.id")
```

### Unique Constraints

```python
from sqlalchemy import UniqueConstraint

class Product(SQLModel, table=True):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint('sku', 'warehouse_id', name='unique_product_warehouse'),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(index=True)
    name: str
    warehouse_id: int = Field(foreign_key="warehouse.id")
```

### Check Constraints

```python
from sqlalchemy import CheckConstraint

class BankAccount(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint('balance >= 0', name='check_positive_balance'),
        CheckConstraint('overdraft_limit >= 0', name='check_positive_overdraft'),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    account_number: str = Field(unique=True)
    balance: float = Field(default=0.0)
    overdraft_limit: float = Field(default=0.0)
```

---

## 5. Custom Field Types

### Enum Fields

```python
from enum import Enum
from sqlmodel import Field, SQLModel

class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"

class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    status: TaskStatus = Field(default=TaskStatus.TODO)
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM)
```

### JSON Fields

```python
from typing import Optional, Dict, Any
from sqlalchemy import JSON, Column

class UserPreferences(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True)

    # JSON field for flexible settings
    settings: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )

    # Example usage:
    # settings = {
    #     "theme": "dark",
    #     "notifications": {"email": True, "push": False},
    #     "language": "en"
    # }
```

### Array Fields (PostgreSQL)

```python
from typing import List
from sqlalchemy import ARRAY, String, Column

class Article(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str

    # Array field (PostgreSQL only)
    tags: List[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(String))
    )
```

---

## 6. Index Strategies

### Single Column Index

```python
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)  # Creates index automatically
    username: str = Field(index=True)
```

### Composite Index

```python
from sqlalchemy import Index

class Order(SQLModel, table=True):
    __tablename__ = "orders"
    __table_args__ = (
        Index('idx_user_created', 'user_id', 'created_at'),
        Index('idx_status_priority', 'status', 'priority'),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    status: str
    priority: int
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

### Partial Index (PostgreSQL)

```python
from sqlalchemy import Index

class Task(SQLModel, table=True):
    __tablename__ = "tasks"
    __table_args__ = (
        # Index only non-completed tasks
        Index(
            'idx_active_tasks',
            'user_id',
            'created_at',
            postgresql_where=text('completed = false')
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    title: str
    completed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

---

## Best Practices

### 1. Always Use Type Hints
```python
# Good
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True)
    age: int

# Bad
class User(SQLModel, table=True):
    id = Field(default=None, primary_key=True)
    email = Field(unique=True)
    age: int
```

### 2. Use Indexes Wisely
- Index foreign keys
- Index columns used in WHERE clauses
- Index columns used in ORDER BY
- Don't over-index (slows down writes)

### 3. Separate Read/Write Models
- Use different models for API input/output
- Prevents accidental exposure of sensitive fields
- Allows different validation rules

### 4. Use Relationships Carefully
- Prefer lazy loading for large collections
- Use `selectinload()` for eager loading to prevent N+1
- Consider `back_populates` for bidirectional relationships

### 5. Naming Conventions
```python
# Tables: plural, lowercase with underscores
__tablename__ = "user_preferences"

# Columns: lowercase with underscores
created_at: datetime
is_active: bool

# Foreign keys: singular_table_id
user_id: int = Field(foreign_key="user.id")
```
