"""Unit tests for cubebox.mcp.disclosure — config + threshold gate."""

from __future__ import annotations

from cubebox.mcp.disclosure import DisclosureSettings, disclosure_active


class TestDisclosureSettings:
    def test_defaults(self) -> None:
        s = DisclosureSettings()
        assert s.enabled == "auto"
        assert s.threshold_pct == 10.0
        assert s.min_servers == 2


class TestDisclosureActive:
    def test_disabled_never_active(self) -> None:
        s = DisclosureSettings(enabled="off")
        assert (
            disclosure_active(s, server_count=100, total_tool_tokens=50_000, context_window=100_000)
            is False
        )

    def test_on_always_active(self) -> None:
        s = DisclosureSettings(enabled="on")
        assert disclosure_active(s, server_count=0) is True

    def test_auto_below_min_servers(self) -> None:
        s = DisclosureSettings(enabled="auto", min_servers=3)
        assert (
            disclosure_active(
                s,
                server_count=2,
                total_tool_tokens=50_000,
                context_window=100_000,
            )
            is False
        )

    def test_auto_below_threshold_pct(self) -> None:
        s = DisclosureSettings(enabled="auto", threshold_pct=10.0, min_servers=2)
        assert (
            disclosure_active(
                s,
                server_count=3,
                total_tool_tokens=5_000,
                context_window=100_000,
            )
            is False
        )

    def test_auto_above_both_thresholds(self) -> None:
        s = DisclosureSettings(enabled="auto", threshold_pct=10.0, min_servers=2)
        assert (
            disclosure_active(
                s,
                server_count=3,
                total_tool_tokens=15_000,
                context_window=100_000,
            )
            is True
        )

    def test_auto_no_context_window(self) -> None:
        s = DisclosureSettings(enabled="auto", min_servers=2)
        assert disclosure_active(s, server_count=3, context_window=0) is True
        assert disclosure_active(s, server_count=1, context_window=0) is False
