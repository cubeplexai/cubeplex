"""Tool system module.

The langgraph ``ToolRegistry`` was removed in M6; cubepi assembles its
tool list per-run in ``cubebox.streams.run_manager._run_cubepi_path``
via ``cubebox.tools.registry.list_builtin_tools`` and the
per-middleware ``tools`` hooks.
"""
