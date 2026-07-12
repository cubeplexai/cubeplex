"""Application State Management

Stores application-level singleton objects such as client instances.
"""


class AppState:
    """Application state container"""

    def __init__(self) -> None:
        self.llm_client: None = None
        self.mcp_manager: None = None


# Global application state instance
app_state = AppState()
