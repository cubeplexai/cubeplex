"""Agent system module.

Exports are lazy to avoid a circular import: middleware modules import
`cubeplex.agents.state.CubeplexState`, which would trigger this package's
__init__ → graph.py → middleware.memory while middleware.memory is still
mid-load. Pull `create_cubeplex_agent` from `cubeplex.agents.graph` directly.
"""
