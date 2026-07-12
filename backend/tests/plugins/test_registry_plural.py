from cubeplex.plugins.registry import GROUP_AUDIT, PluginRegistry


class _StubSink:
    name = "stub"

    async def record(self, event):  # type: ignore[no-untyped-def]
        return None


class _StubSink2:
    name = "stub2"

    async def record(self, event):  # type: ignore[no-untyped-def]
        return None


def _seed(reg: PluginRegistry, candidates: dict[str, type]) -> None:
    reg._candidates[GROUP_AUDIT] = dict(candidates)


def test_plural_with_no_external_returns_only_default() -> None:
    reg = PluginRegistry()
    default = _StubSink()
    out = reg.resolve_plural(GROUP_AUDIT, default=default, disabled=[])
    assert out == [default]


def test_plural_with_one_external_returns_default_plus_external() -> None:
    reg = PluginRegistry()
    default = _StubSink()
    _seed(reg, {"siem": _StubSink2})
    out = reg.resolve_plural(GROUP_AUDIT, default=default, disabled=[])
    assert default in out
    assert any(isinstance(o, _StubSink2) for o in out)
    assert len(out) == 2


def test_plural_disabled_builtin_excludes_default() -> None:
    reg = PluginRegistry()
    default = _StubSink()
    _seed(reg, {"siem": _StubSink2})
    out = reg.resolve_plural(GROUP_AUDIT, default=default, disabled=["builtin"])
    assert default not in out
    assert any(isinstance(o, _StubSink2) for o in out)
    assert len(out) == 1


def test_plural_disabled_external_excludes_it() -> None:
    reg = PluginRegistry()
    default = _StubSink()
    _seed(reg, {"siem": _StubSink2, "other": _StubSink})
    out = reg.resolve_plural(GROUP_AUDIT, default=default, disabled=["siem"])
    assert default in out
    assert not any(isinstance(o, _StubSink2) for o in out)


def test_plural_default_can_be_none() -> None:
    """For multi-instance protocols without a CE default (e.g. UserDirectorySyncer)."""
    reg = PluginRegistry()
    out = reg.resolve_plural(GROUP_AUDIT, default=None, disabled=[])
    assert out == []
