"""PGroonga backend — `&@~` operator with pgroonga_score()."""

from cubebox.search.lexical.base import LexicalSqlBundle


class PgroongaBackend:
    name = "pgroonga"

    def normalize_query(self, q: str) -> str:
        # PGroonga treats unescaped " ( ) \\ as operators; drop them so the
        # query string can't accidentally inject syntax.
        bad = set('"()\\')
        return "".join(c for c in q if c not in bad).strip()

    def search_sql(self, limit: int) -> LexicalSqlBundle:
        sql = f"""
            SELECT id, pgroonga_score(tableoid, ctid) AS score
            FROM conversation_chunks
            WHERE org_id = :org_id
              AND workspace_id = :ws_id
              AND creator_user_id = :user_id
              AND text &@~ :q
            ORDER BY score DESC
            LIMIT {int(limit)}
        """
        return LexicalSqlBundle(sql=sql, bind_keys=["org_id", "ws_id", "user_id", "q"])
