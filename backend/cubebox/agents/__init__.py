"""Agent system module.

Exports are lazy to avoid a circular import: middleware modules import
`cubebox.agents.state.CubeboxState`, which would trigger this package's
__init__ → graph.py → middleware.memory while middleware.memory is still
mid-load. Pull `create_cubebox_agent` from `cubebox.agents.graph` directly.
"""
