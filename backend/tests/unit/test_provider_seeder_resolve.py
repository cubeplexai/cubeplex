from cubebox.seeders.provider_seeder import _merge_cost


def test_merge_cost_partial_override_inherits_other_legs():
    catalog = {"input": 0.27, "output": 1.10, "cache_read": 0.07, "cache_write": 0.0}
    override = {"input": 0.5}
    assert _merge_cost(catalog, override) == {
        "input": 0.5,
        "output": 1.10,
        "cache_read": 0.07,
        "cache_write": 0.0,
    }


def test_merge_cost_no_override_returns_catalog():
    catalog = {"input": 1.0, "output": 2.0, "cache_read": 0.0, "cache_write": 0.0}
    assert _merge_cost(catalog, None) == catalog
