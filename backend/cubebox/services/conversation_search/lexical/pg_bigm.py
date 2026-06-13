"""pg_bigm backend — LIKE-based with bigm_similarity()."""

from cubebox.services.conversation_search.lexical.base import LexicalSqlBundle


class PgBigmBackend:
    name = "pg_bigm"

    def normalize_query(self, q: str) -> str:
        # Escape SQL LIKE wildcards. Leading/trailing % are added by SQL.
        return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").strip()

    def search_sql(self, limit: int) -> LexicalSqlBundle:
        sql = f"""
            SELECT cc.id, bigm_similarity(cc.text, :q) AS score
            FROM conversation_chunks cc
            JOIN conversations c ON c.id = cc.conversation_id AND c.deleted_at IS NULL
            WHERE cc.org_id = :org_id
              AND cc.workspace_id = :ws_id
              AND cc.creator_user_id = :user_id
              AND cc.text LIKE '%' || :q || '%' ESCAPE '\\'
            ORDER BY score DESC
            LIMIT {int(limit)}
        """
        return LexicalSqlBundle(sql=sql, bind_keys=["org_id", "ws_id", "user_id", "q"])
