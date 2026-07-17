"""Database engine and session factory."""

from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from cubeplex.config import config


def _build_database_url() -> str:
    """Build database URL from individual config fields."""
    host = config.get("database.host", "localhost")
    port = config.get("database.port", 5432)
    user = config.get("database.user", "postgres")
    password = config.get("database.password", "")
    name = config.get("database.name", "cubeplex")
    encoded_password = quote_plus(password)
    return f"postgresql+psycopg://{user}:{encoded_password}@{host}:{port}/{name}"


def get_engine() -> AsyncEngine:
    """Get async database engine."""
    database_url = _build_database_url()
    pool_size = config.get("database.pool_size", 10)
    max_overflow = config.get("database.max_overflow", 20)
    echo = config.get("database.echo", False)

    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )


engine = get_engine()
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Initialize database tables (for testing only, use Alembic in production)."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
