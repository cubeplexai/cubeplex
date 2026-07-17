"""
Entry point for cubeplex Backend.

Starts the FastAPI application with uvicorn.
"""

from pathlib import Path

import uvicorn

from cubeplex.config import config

if __name__ == "__main__":
    reload_kwargs: dict[str, object] = {}
    if config.api.reload:
        # Watch the backend root so editing config*.yaml / .env reloads too, but
        # exclude the dependency/cache trees — otherwise the reloader churns the
        # whole .venv (~16k files) and pins a CPU core. uvicorn only treats an
        # exclude as a whole-subtree skip when it's an existing absolute dir
        # (it checks `dir in path.parents`); a glob like ".venv/*" only matches
        # one level and misses .venv/lib64/.../x.py, so anchor to absolute paths.
        #
        # An absolute exclude that doesn't exist yet is fatal, not ignored:
        # uvicorn falls back to `Path.cwd().glob(pattern)`, and pathlib refuses
        # an absolute glob pattern (NotImplementedError on 3.13). cubepi-traces
        # is gitignored and created lazily on the first agent run, so a fresh
        # checkout / worktree would crash on `python main.py` before it exists.
        # mkdir it up front so the existing-dir short-circuit always applies.
        backend_dir = Path(__file__).resolve().parent
        excluded_dirs = [
            backend_dir / ".venv",
            backend_dir / "cubepi-traces",
            backend_dir / "skills_cache",
        ]
        for d in excluded_dirs:
            d.mkdir(parents=True, exist_ok=True)
        reload_kwargs = {
            "reload_dirs": [str(backend_dir)],
            "reload_includes": ["*.py", "config*.yaml", ".env"],
            "reload_excludes": [str(d) for d in excluded_dirs] + ["*.py[cod]"],
        }

    uvicorn.run(
        "cubeplex.api.app:create_app",
        host=config.api.host,
        port=config.api.port,
        reload=config.api.reload,
        factory=True,
        **reload_kwargs,
    )
