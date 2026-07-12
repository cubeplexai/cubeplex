"""Unit tests for filename sanitization in the attachment service."""

from cubeplex.services.attachments import _safe_basename


def test_strips_posix_path_traversal() -> None:
    assert _safe_basename("../../etc/passwd") == "passwd"


def test_strips_absolute_posix_path() -> None:
    assert _safe_basename("/etc/passwd") == "passwd"


def test_strips_windows_separators() -> None:
    assert _safe_basename("..\\..\\Windows\\System32\\evil.exe") == "evil.exe"


def test_strips_mixed_separators() -> None:
    assert _safe_basename("a/b\\c/../d.txt") == "d.txt"


def test_empty_falls_back_to_upload() -> None:
    assert _safe_basename("") == "upload"


def test_dot_falls_back_to_upload() -> None:
    assert _safe_basename(".") == "upload"


def test_double_dot_falls_back_to_upload() -> None:
    assert _safe_basename("..") == "upload"


def test_only_dots_and_spaces_falls_back_to_upload() -> None:
    # After basename + strip(" .") nothing usable remains
    assert _safe_basename("   ...   ") == "upload"


def test_strips_nul_and_control_bytes() -> None:
    assert _safe_basename("a\x00b\x01c.png") == "abc.png"


def test_clean_filename_passes_through() -> None:
    assert _safe_basename("chart.png") == "chart.png"


def test_long_name_truncated_preserving_extension() -> None:
    raw = "x" * 300 + ".png"
    out = _safe_basename(raw)
    assert len(out) <= 255
    assert out.endswith(".png")


def test_long_name_no_extension_clamped() -> None:
    raw = "y" * 400
    out = _safe_basename(raw)
    assert len(out) <= 255


def test_strips_windows_disallowed_chars() -> None:
    assert _safe_basename('a<b>c:d"e|f?g*h.txt') == "abcdefgh.txt"
