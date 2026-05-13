"""Smoke test: run_manager imports + new tool wiring compiles (M2.5)."""


def test_run_manager_imports_with_cubepi_tools() -> None:
    """RunManager still imports after M2.5 wiring."""
    from cubebox.streams.run_manager import RunManager

    assert RunManager is not None


def test_run_cubepi_path_method_exists() -> None:
    """The cubepi dispatch method is still on RunManager after M2.5."""
    from cubebox.streams.run_manager import RunManager

    assert hasattr(RunManager, "_run_cubepi_path")
