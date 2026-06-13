from cubebox.services.conversation_search.lexical.pg_bigm import PgBigmBackend
from cubebox.services.conversation_search.lexical.pgroonga import PgroongaBackend


def test_pgroonga_strips_disallowed_chars() -> None:
    b = PgroongaBackend()
    assert b.normalize_query('docling "(x)"') == "docling x"


def test_pgroonga_sql_has_expected_binds() -> None:
    b = PgroongaBackend()
    bundle = b.search_sql(limit=20)
    assert "pgroonga_score" in bundle.sql
    assert "&@~" in bundle.sql
    assert set(bundle.bind_keys) == {"org_id", "ws_id", "user_id", "q"}


def test_pgbigm_escapes_like_wildcards() -> None:
    b = PgBigmBackend()
    assert b.normalize_query("50% off_now") == "50\\% off\\_now"


def test_pgbigm_sql_has_like_clause() -> None:
    b = PgBigmBackend()
    bundle = b.search_sql(limit=20)
    assert "LIKE" in bundle.sql
    assert "bigm_similarity" in bundle.sql
