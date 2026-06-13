"""Lexical backend selection — config-driven."""

from cubebox.config import config
from cubebox.services.conversation_search.lexical.base import LexicalSearchBackend, LexicalSqlBundle
from cubebox.services.conversation_search.lexical.pgroonga import PgroongaBackend

__all__ = ["LexicalSearchBackend", "LexicalSqlBundle", "build_lexical_backend"]


def build_lexical_backend() -> LexicalSearchBackend:
    name = config.get("search.lexical.backend", "pgroonga")
    if name == "pgroonga":
        return PgroongaBackend()
    if name == "pg_bigm":
        from cubebox.services.conversation_search.lexical.pg_bigm import PgBigmBackend

        backend: LexicalSearchBackend = PgBigmBackend()
        return backend
    raise RuntimeError(f"Unknown lexical backend: {name}")
