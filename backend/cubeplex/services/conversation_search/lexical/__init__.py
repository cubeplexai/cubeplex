"""Lexical backend selection — config-driven."""

from cubeplex.config import config
from cubeplex.services.conversation_search.lexical.base import (
    LexicalSearchBackend,
    LexicalSqlBundle,
)
from cubeplex.services.conversation_search.lexical.pgroonga import PgroongaBackend

__all__ = ["LexicalSearchBackend", "LexicalSqlBundle", "build_lexical_backend"]


def build_lexical_backend() -> LexicalSearchBackend:
    name = config.get("search.lexical.backend", "pgroonga")
    if name == "pgroonga":
        return PgroongaBackend()
    if name == "pg_bigm":
        from cubeplex.services.conversation_search.lexical.pg_bigm import PgBigmBackend

        backend: LexicalSearchBackend = PgBigmBackend()
        return backend
    raise RuntimeError(f"Unknown lexical backend: {name}")
