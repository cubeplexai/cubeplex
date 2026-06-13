"""Lexical search backend abstraction. One impl per Postgres extension."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LexicalSqlBundle:
    """A complete, parameterised SQL chunk:

      SELECT id, <score_expr> AS score
      FROM conversation_chunks
      WHERE <scope_cols> AND <match_clause>
      ORDER BY score DESC
      LIMIT $n

    The service composes the final SQL by wrapping this with scope binds.
    """

    sql: str
    bind_keys: list[str]


@runtime_checkable
class LexicalSearchBackend(Protocol):
    name: str

    def normalize_query(self, q: str) -> str: ...

    def search_sql(self, limit: int) -> LexicalSqlBundle: ...
