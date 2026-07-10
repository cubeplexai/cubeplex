"""PGroonga backend — `&@~` operator with pgroonga_score()."""

from cubebox.services.conversation_search.lexical.base import LexicalSqlBundle


class PgroongaBackend:
    name = "pgroonga"

    def normalize_query(self, q: str) -> str:
        # PGroonga treats unescaped " ( ) \\ as operators; drop them so the
        # query string can't accidentally inject syntax.
        bad = set('"()\\')
        return "".join(c for c in q if c not in bad).strip()

    def search_sql(self, limit: int, *, visibility_sql: str) -> LexicalSqlBundle:
        sql = f"""
            SELECT cc.id, pgroonga_score(cc.tableoid, cc.ctid) AS score
            FROM conversation_chunks cc
            JOIN conversations c ON c.id = cc.conversation_id AND c.deleted_at IS NULL
            WHERE cc.org_id = :org_id
              AND cc.workspace_id = :ws_id
              AND ({visibility_sql})
              AND cc.text &@~ :q
            ORDER BY score DESC
            LIMIT {int(limit)}
        """
        return LexicalSqlBundle(sql=sql, bind_keys=["org_id", "ws_id", "user_id", "q"])
