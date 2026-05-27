"""
Entry point for cubebox Backend.

Starts the FastAPI application with uvicorn.
"""

from pathlib import Path

import uvicorn

from cubebox.config import config

if __name__ == "__main__":
    reload_kwargs: dict[str, object] = {}
    if config.api.reload:
        # Watch the backend root so editing config*.yaml / .env reloads too, but
        # exclude the dependency/cache trees — otherwise the reloader churns the
        # whole .venv (~16k files) and pins a CPU core. uvicorn only treats an
        # exclude as a whole-subtree skip when it's an existing absolute dir
        # (it checks `dir in path.parents`); a glob like ".venv/*" only matches
        # one level and misses .venv/lib64/.../x.py, so anchor to absolute paths.
        backend_dir = Path(__file__).resolve().parent
        reload_kwargs = {
            "reload_dirs": [str(backend_dir)],
            "reload_includes": ["*.py", "config*.yaml", ".env"],
            "reload_excludes": [
                str(backend_dir / ".venv"),
                str(backend_dir / "cubepi-traces"),
                "*.py[cod]",
            ],
        }

    uvicorn.run(
        "cubebox.api.app:create_app",
        host=config.api.host,
        port=config.api.port,
        reload=config.api.reload,
        factory=True,
        **reload_kwargs,
    )
