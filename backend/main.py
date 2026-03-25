"""
Entry point for cubebox Backend.

Starts the FastAPI application with uvicorn.
"""

import uvicorn

from cubebox.config import config

if __name__ == "__main__":
    uvicorn.run(
        "cubebox.api.app:create_app",
        host=config.api.host,
        port=config.api.port,
        reload=config.api.reload,
        factory=True,
    )
