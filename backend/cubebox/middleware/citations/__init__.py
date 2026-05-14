"""Citation utilities shared by citation_pi middleware.

The langgraph ``CitationMiddleware`` was removed in M6; only the
shared chunker/config/counter helpers remain here and are consumed by
``cubebox.middleware.citation_pi.CitationMiddlewarePi``.
"""

from cubebox.middleware.citations.config import CitationConfig, load_citation_configs
from cubebox.middleware.citations.counter import (
    CitationCounter,
    citation_counter_var,
    citation_event_queue,
)

__all__ = [
    "CitationConfig",
    "CitationCounter",
    "citation_counter_var",
    "citation_event_queue",
    "load_citation_configs",
]
