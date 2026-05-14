"""Built-in tools for agents.

After the M6 cubepi migration the langgraph ``BaseTool`` builtins were
removed; cubepi-native tool builders live in the ``*_pi`` modules in
this package (``calculator_pi``, ``datetime_tool_pi``, ``memory_pi``,
``load_skill_pi``, ``view_images_pi``) and are wired by
``cubebox.tools.registry.list_builtin_tools``.
"""
