from logging.config import fileConfig

from cubepi.checkpointer.postgres.models import (  # noqa: F401
    cubepi_metadata,
)
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context

# Import models and config
from cubebox.config import config as app_config
from cubebox.models import (  # noqa: F401
    AgentConfig,
    Artifact,
    ArtifactVersion,
    Attachment,
    BillingEvent,
    Conversation,
    Credential,
    EgressRef,
    LlmBillingEvent,
    MCPConnectorInstall,
    MCPConnectorTemplate,
    MCPCredentialGrant,
    MCPWorkspaceConnectorState,
    Membership,
    MemoryItem,
    Model,
    Organization,
    OrgPreinstalledTombstone,
    OrgProviderOverride,
    OrgSettings,
    OrgSkillInstall,
    Provider,
    SandboxEnvVar,
    Skill,
    SkillRegistry,
    SkillVersion,
    User,
    UserSandbox,
    Workspace,
    WorkspaceSkillBinding,
)

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
target_metadata = [SQLModel.metadata, cubepi_metadata]

# Tables managed by cubepi PostgresCheckpointer — exclude from autogenerate
_CHECKPOINT_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "cubepi_hitl_answers",
    "cubepi_messages",
    "cubepi_runs",
    "cubepi_schema_version",
}

# Hand-crafted PostgreSQL indexes that SQLModel can't declare (HNSW from
# pgvector, pgroonga full-text). Without this guard autogen proposes to
# drop them on every run — applying that migration would silently break
# search. Owned by alembic/versions/fabe1279b9f6_conversation_search_tables.
_HAND_BUILT_INDEXES = {
    "ix_chunks_embedding_hnsw",
    "ix_chunks_text_lexical",
}


def include_object(
    object: object,  # noqa: A002
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Exclude cubepi-checkpointer tables and hand-built indexes from autogenerate."""
    if type_ == "table" and name is not None:
        if name in _CHECKPOINT_TABLES:
            return False
        if name.startswith("cubepi_messages_p"):
            return False
        if name.startswith("cubepi_runs_p"):
            return False
    if type_ == "index" and name in _HAND_BUILT_INDEXES:
        return False
    return True


def get_url() -> str:
    from urllib.parse import quote_plus

    host = app_config.get("database.host", "localhost")
    port = app_config.get("database.port", 5432)
    user = app_config.get("database.user", "postgres")
    password = app_config.get("database.password", "")
    name = app_config.get("database.name", "cubebox")
    encoded_password = quote_plus(password)
    url = f"postgresql+psycopg://{user}:{encoded_password}@{host}:{port}/{name}"
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
