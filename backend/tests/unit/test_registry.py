"""registry_pi tests (M2.1)."""

from __future__ import annotations

from pydantic import BaseModel

from cubeplex.tools.registry import list_builtin_tools


def test_registry_returns_at_least_two_nodep_tools() -> None:
    tools = list_builtin_tools()
    names = {t.name for t in tools}
    assert "calculator" in names
    assert "datetime" in names
    # view_images requires DI so it's not in the no-DI list
    assert len(tools) >= 2


def test_registry_tools_have_pydantic_parameters() -> None:
    for t in list_builtin_tools():
        assert issubclass(t.parameters, BaseModel), f"{t.name} parameters not a BaseModel"


def test_registry_tools_have_non_empty_descriptions() -> None:
    for t in list_builtin_tools():
        assert t.description.strip(), f"{t.name} has empty description"


def test_registry_tools_have_callable_execute() -> None:
    import inspect

    for t in list_builtin_tools():
        assert callable(t.execute), f"{t.name}.execute is not callable"
        # execute must be an async function
        assert inspect.iscoroutinefunction(t.execute), f"{t.name}.execute is not async"


def test_view_images_factory_exported() -> None:
    """make_view_images_tool must be importable from view_images_pi."""
    from cubeplex.tools.builtin.view_images import make_view_images_tool

    assert callable(make_view_images_tool)
