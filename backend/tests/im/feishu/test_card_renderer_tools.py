"""Tests for per-tool icon and one-liner args summarization."""

from cubebox.im.feishu.card_renderer import (
    TOOL_DISPLAY,
    default_display,
    summarize_args,
)


def test_unknown_tool_uses_default_display() -> None:
    disp = default_display("frobnicate")
    assert disp.icon == "⚙️"
    assert disp.summarize({"x": 1}) == '{"x": 1}'


def test_summarize_args_truncates_long_values() -> None:
    long = "a" * 200
    out = summarize_args({"text": long})
    assert len(out) <= 90  # 80 cap + ellipsis budget
    assert out.endswith("…")


def test_read_file_summary_shows_path() -> None:
    disp = TOOL_DISPLAY["read_file"]
    assert "src/foo.py" in disp.summarize({"path": "src/foo.py"})
    assert disp.icon  # any non-empty icon


def test_bash_summary_shows_command_head() -> None:
    disp = TOOL_DISPLAY["bash"]
    out = disp.summarize({"cmd": "ls -la /tmp/very/long/path/with/lots/of/extra"})
    assert out.startswith("ls -la ")


def test_web_fetch_summary_shows_url() -> None:
    disp = TOOL_DISPLAY["web_fetch"]
    out = disp.summarize({"url": "https://example.com/x"})
    assert "https://example.com/x" in out


def test_update_memory_summary_shows_key() -> None:
    disp = TOOL_DISPLAY["update_memory"]
    out = disp.summarize({"key": "feedback_x", "content": "..."})
    assert "feedback_x" in out


def test_summarize_args_handles_no_args() -> None:
    assert summarize_args({}) == ""
