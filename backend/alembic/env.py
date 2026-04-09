from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context

# Import models and config
from cubebox.config import config as app_config
from cubebox.models import Artifact, Conversation  # noqa: F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = SQLModel.metadata

# Tables managed by langgraph-checkpoint-mysql — exclude from autogenerate
_CHECKPOINT_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}


def include_object(
    object: object,  # noqa: A002
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Exclude checkpoint tables from autogenerate."""
    if type_ == "table" and name in _CHECKPOINT_TABLES:
        return False
    return True


# 从 app config 各字段拼接数据库 URL（Alembic 用同步驱动 pymysql）
def get_url() -> str:
    from urllib.parse import quote_plus

    host = app_config.get("database.host", "localhost")
    port = app_config.get("database.port", 3306)
    user = app_config.get("database.user", "root")
    password = app_config.get("database.password", "")
    name = app_config.get("database.name", "cubebox")
    # URL encode password to handle special characters
    encoded_password = quote_plus(password)
    url = f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{name}"
    # Escape % for ConfigParser (% -> %%)
    return url.replace("%", "%%")


config.set_main_option("sqlalchemy.url", get_url())

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
