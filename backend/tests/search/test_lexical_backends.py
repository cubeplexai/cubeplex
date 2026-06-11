from cubebox.search.lexical.pgroonga import PgroongaBackend


def test_pgroonga_strips_disallowed_chars() -> None:
    b = PgroongaBackend()
    assert b.normalize_query('docling "(x)"') == "docling x"


def test_pgroonga_sql_has_expected_binds() -> None:
    b = PgroongaBackend()
    bundle = b.search_sql(limit=20)
    assert "pgroonga_score" in bundle.sql
    assert "&@~" in bundle.sql
    assert set(bundle.bind_keys) == {"org_id", "ws_id", "user_id", "q"}
