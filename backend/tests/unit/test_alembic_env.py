"""Smoke test: alembic env.py loads with cubepi_metadata included."""

from pathlib import Path


def test_env_module_references_cubepi_metadata() -> None:
    """alembic env.py must import cubepi_metadata and include it in target_metadata."""
    env_path = Path(__file__).parent.parent.parent / "alembic" / "env.py"
    assert env_path.exists()

    text = env_path.read_text()
    assert "cubepi.checkpointer.postgres" in text, (
        "alembic env.py must import from cubepi.checkpointer.postgres"
    )
    assert "cubepi_metadata" in text, "alembic env.py must reference cubepi_metadata"
    # target_metadata must be a list to combine cubeplex + cubepi metadata
    assert "target_metadata = [" in text, (
        "target_metadata must be a list to combine cubeplex + cubepi metadata"
    )
