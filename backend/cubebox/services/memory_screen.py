# backend/cubebox/services/memory_screen.py
"""Write-time screen for shared memory. Personal memory bypasses this."""


class MemoryScreenError(ValueError):
    """Raised when shared-memory content fails the adversarial screen."""


def screen_shared_content(content: str) -> None:
    """No-op stub. Replaced in Task 6.1 with rule-based screen."""
    return
