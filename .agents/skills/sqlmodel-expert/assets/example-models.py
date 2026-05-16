"""
Example SQLModel models demonstrating best practices and common patterns
"""
from sqlmodel import Field, Relationship, SQLModel
from typing import Optional, List
from datetime import datetime
from enum import Enum


# Enums for type safety
class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Mixins for common fields
class TimestampMixin(SQLModel):
    """Add created_at and updated_at timestamps"""
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


# Base models (shared fields)
class UserBase(SQLModel):
    """Base user fields"""
    username: str = Field(index=True, unique=True, min_length=3, max_length=50)
    email: str = Field(unique=True)
    full_name: str


# Database models
class User(UserBase, TimestampMixin, table=True):
    """User table model"""
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)

    # Relationships
    tasks: List["Task"] = Relationship(back_populates="owner")
    teams: List["Team"] = Relationship(back_populates="members", link_model="UserTeamLink")


class Task(TimestampMixin, table=True):
    """Task table model"""
    __tablename__ = "tasks"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    description: Optional[str] = None
    completed: bool = Field(default=False)
    status: TaskStatus = Field(default=TaskStatus.TODO)
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM)
    due_date: Optional[datetime] = None

    # Foreign keys
    owner_id: int = Field(foreign_key="users.id")

    # Relationships
    owner: User = Relationship(back_populates="tasks")
    tags: List["Tag"] = Relationship(back_populates="tasks", link_model="TaskTagLink")


class Team(TimestampMixin, table=True):
    """Team table model"""
    __tablename__ = "teams"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None

    # Relationships
    members: List[User] = Relationship(back_populates="teams", link_model="UserTeamLink")


class Tag(SQLModel, table=True):
    """Tag table model"""
    __tablename__ = "tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    color: Optional[str] = None

    # Relationships
    tasks: List[Task] = Relationship(back_populates="tags", link_model="TaskTagLink")


# Link tables for many-to-many relationships
class UserTeamLink(SQLModel, table=True):
    """Link table for User-Team many-to-many relationship"""
    __tablename__ = "user_team_link"

    user_id: int = Field(foreign_key="users.id", primary_key=True)
    team_id: int = Field(foreign_key="teams.id", primary_key=True)
    role: Optional[str] = None
    joined_at: datetime = Field(default_factory=datetime.utcnow)


class TaskTagLink(SQLModel, table=True):
    """Link table for Task-Tag many-to-many relationship"""
    __tablename__ = "task_tag_link"

    task_id: int = Field(foreign_key="tasks.id", primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", primary_key=True)


# API models (separate from database models)
class UserCreate(UserBase):
    """Model for creating a new user"""
    password: str = Field(min_length=8)


class UserRead(UserBase):
    """Model for reading user data (excludes password)"""
    id: int
    is_active: bool
    created_at: datetime


class UserUpdate(SQLModel):
    """Model for updating user (all fields optional)"""
    username: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    is_active: Optional[bool] = None


class TaskCreate(SQLModel):
    """Model for creating a new task"""
    title: str
    description: Optional[str] = None
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: Optional[datetime] = None


class TaskRead(SQLModel):
    """Model for reading task data"""
    id: int
    title: str
    description: Optional[str]
    completed: bool
    status: TaskStatus
    priority: TaskPriority
    due_date: Optional[datetime]
    created_at: datetime
    owner_id: int


class TaskUpdate(SQLModel):
    """Model for updating task"""
    title: Optional[str] = None
    description: Optional[str] = None
    completed: Optional[bool] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    due_date: Optional[datetime] = None
