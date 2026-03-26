"""Checkpointer module for LangGraph conversation persistence."""

from typing import Any

from loguru import logger


async def create_checkpointer() -> Any:
    """Create a new LangGraph checkpointer instance with its own connection.

    This creates a fresh connection for each request to avoid issues with
    connection sharing across event loops or processes.

    Returns:
        AIOMySQLSaver instance or None if initialization fails
    """
    try:
        import aiomysql
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

        from cubebox.config import config

        # Build connection parameters
        conn_params = {
            "host": config.get("database.host", "localhost"),
            "port": config.get("database.port", 3306),
            "user": config.get("database.user", "root"),
            "password": config.get("database.password", ""),
            "db": config.get("database.name", "cubebox"),
            "autocommit": True,
        }

        # Create connection and checkpointer
        conn = await aiomysql.connect(**conn_params)
        checkpointer = AIOMySQLSaver(conn=conn)
        logger.debug("Created new checkpointer instance")
        return checkpointer

    except Exception as e:
        logger.warning("Failed to create checkpointer: {}", str(e))
        return None
