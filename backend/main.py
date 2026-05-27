"""
Entry point for cubebox Backend.

Starts the FastAPI application with uvicorn.
"""

import uvicorn

from cubebox.config import config

if __name__ == "__main__":
    reload_kwargs: dict[str, object] = {}
    if config.api.reload:
        # Watch the backend root so editing config*.yaml / .env reloads too, but
        # exclude the dependency/cache trees — otherwise the reloader churns the
        # whole .venv (~16k files) and pins a CPU core.
        reload_kwargs = {
            "reload_dirs": ["."],
            "reload_includes": ["*.py", "config*.yaml", ".env"],
            "reload_excludes": [".venv/*", "cubepi-traces/*", "**/__pycache__/*"],
        }

    uvicorn.run(
        "cubebox.api.app:create_app",
        host=config.api.host,
        port=config.api.port,
        reload=config.api.reload,
        factory=True,
        **reload_kwargs,
    )
