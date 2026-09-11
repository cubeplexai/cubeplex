"""Smoke test: alembic env.py loads with cubeloop_metadata included."""

from pathlib import Path


def test_env_module_references_cubeloop_metadata() -> None:
    """alembic env.py must import cubeloop_metadata and include it in target_metadata."""
    env_path = Path(__file__).parent.parent.parent / "alembic" / "env.py"
    assert env_path.exists()

    text = env_path.read_text()
    assert "cubeloop.checkpointer.postgres" in text, (
        "alembic env.py must import from cubeloop.checkpointer.postgres"
    )
    assert "cubeloop_metadata" in text, "alembic env.py must reference cubeloop_metadata"
    # target_metadata must be a list to combine cubeplex + cubeloop metadata
    assert "target_metadata = [" in text, (
        "target_metadata must be a list to combine cubeplex + cubeloop metadata"
    )
