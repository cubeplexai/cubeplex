#!/usr/bin/env python3
"""CLI tool to diff two capture directories produced by test_capture_runtime_requests.py.

Usage:

    uv run python tests/diagnostic/compare_runtimes.py <dir_a> <dir_b>

    # Example: compare langgraph vs cubepi for deepseek/anthropic
    uv run python tests/diagnostic/compare_runtimes.py \\
        /tmp/cubepi_runtime_capture/langgraph/deepseek_anthropic \\
        /tmp/cubepi_runtime_capture/cubepi/deepseek_anthropic

    # Example: compare langgraph vs cubepi for arkcode/openai
    uv run python tests/diagnostic/compare_runtimes.py \\
        /tmp/cubepi_runtime_capture/langgraph/arkcode_openai \\
        /tmp/cubepi_runtime_capture/cubepi/arkcode_openai

    # Example: compare cubepi turn 1 vs turn 2 (prefix stability)
    uv run python tests/diagnostic/compare_runtimes.py \\
        --files anthropic_001.json anthropic_002.json \\
        /tmp/cubepi_runtime_capture/cubepi/deepseek_anthropic \\
        /tmp/cubepi_runtime_capture/cubepi/deepseek_anthropic

Output: colored field-level diff highlighting all JSON body differences between
corresponding request files.

Exit code: 0 if all compared pairs are identical, 1 if any differences found.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# ANSI colour helpers (degrade gracefully when not a tty)
# ──────────────────────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()


def _red(s: str) -> str:
    return f"\033[91m{s}\033[0m" if _USE_COLOR else s


def _green(s: str) -> str:
    return f"\033[92m{s}\033[0m" if _USE_COLOR else s


def _yellow(s: str) -> str:
    return f"\033[93m{s}\033[0m" if _USE_COLOR else s


def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if _USE_COLOR else s


def _cyan(s: str) -> str:
    return f"\033[96m{s}\033[0m" if _USE_COLOR else s


# ──────────────────────────────────────────────────────────────────────────────
# Recursive JSON differ
# ──────────────────────────────────────────────────────────────────────────────


def _diff_values(path: str, a: Any, b: Any, diffs: list[str]) -> None:
    """Recursively walk two JSON values and collect field-level diffs."""
    if type(a) != type(b):  # noqa: E721
        diffs.append(
            f"  {_cyan(path)}\n"
            f"    {_red('< type=' + type(a).__name__)}: {json.dumps(a, ensure_ascii=False)[:200]}\n"
            f"    {_green('> type=' + type(b).__name__)}: {json.dumps(b, ensure_ascii=False)[:200]}"
        )
        return

    if isinstance(a, dict):
        all_keys = sorted(set(a) | set(b))
        for k in all_keys:
            child_path = f"{path}.{k}"
            if k not in a:
                diffs.append(
                    f"  {_cyan(child_path)}\n"
                    f"    {_red('< (missing)')}\n"
                    f"    {_green('> ' + json.dumps(b[k], ensure_ascii=False)[:200])}"
                )
            elif k not in b:
                diffs.append(
                    f"  {_cyan(child_path)}\n"
                    f"    {_red('< ' + json.dumps(a[k], ensure_ascii=False)[:200])}\n"
                    f"    {_green('> (missing)')}"
                )
            else:
                _diff_values(child_path, a[k], b[k], diffs)

    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(
                f"  {_cyan(path + '[]')}\n"
                f"    {_red('< len=' + str(len(a)))}\n"
                f"    {_green('> len=' + str(len(b)))}"
            )
            # Diff element-by-element up to min length
            for i, (ai, bi) in enumerate(zip(a, b, strict=False)):
                _diff_values(f"{path}[{i}]", ai, bi, diffs)
        else:
            for i, (ai, bi) in enumerate(zip(a, b, strict=True)):
                _diff_values(f"{path}[{i}]", ai, bi, diffs)

    else:
        # Scalar comparison
        if a != b:
            diffs.append(
                f"  {_cyan(path)}\n"
                f"    {_red('< ' + json.dumps(a, ensure_ascii=False)[:300])}\n"
                f"    {_green('> ' + json.dumps(b, ensure_ascii=False)[:300])}"
            )


def diff_bodies(body_a: Any, body_b: Any) -> list[str]:
    """Return a list of human-readable difference strings."""
    diffs: list[str] = []
    _diff_values("body", body_a, body_b, diffs)
    return diffs


# ──────────────────────────────────────────────────────────────────────────────
# File pairing logic
# ──────────────────────────────────────────────────────────────────────────────


def _list_json_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(directory.glob("*.json"))


def _pair_files(
    dir_a: pathlib.Path,
    dir_b: pathlib.Path,
    explicit_files: list[str] | None = None,
) -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Pair JSON files from two directories by position or explicit name."""
    if explicit_files:
        # Both dirs use the same file name
        return [(dir_a / f, dir_b / f) for f in explicit_files]

    files_a = _list_json_files(dir_a)
    files_b = _list_json_files(dir_b)

    if len(files_a) != len(files_b):
        print(
            _yellow(
                f"Warning: {dir_a} has {len(files_a)} files, "
                f"{dir_b} has {len(files_b)} files. "
                f"Comparing by position up to min({len(files_a)}, {len(files_b)})."
            )
        )

    pairs = []
    for fa, fb in zip(files_a, files_b, strict=False):
        pairs.append((fa, fb))
    return pairs


# ──────────────────────────────────────────────────────────────────────────────
# Main comparison
# ──────────────────────────────────────────────────────────────────────────────


def compare_dirs(
    dir_a: pathlib.Path,
    dir_b: pathlib.Path,
    label_a: str,
    label_b: str,
    explicit_files: list[str] | None = None,
) -> bool:
    """Compare two capture directories. Returns True if all pairs are identical."""
    pairs = _pair_files(dir_a, dir_b, explicit_files)
    if not pairs:
        print(_yellow("No files to compare."))
        return True

    all_identical = True

    for fa, fb in pairs:
        print()
        print(_bold(f"{'─' * 72}"))
        print(_bold(f"Comparing {label_a}/{fa.name}  vs  {label_b}/{fb.name}"))
        print(_bold(f"{'─' * 72}"))

        try:
            rec_a = json.loads(fa.read_text())
        except Exception as exc:
            print(_red(f"  Failed to read {fa}: {exc}"))
            all_identical = False
            continue

        try:
            rec_b = json.loads(fb.read_text())
        except Exception as exc:
            print(_red(f"  Failed to read {fb}: {exc}"))
            all_identical = False
            continue

        body_a = rec_a.get("body") or {}
        body_b = rec_b.get("body") or {}

        # Show metadata
        print(f"  {label_a}: {rec_a.get('url', '?')}  method={rec_a.get('method', '?')}")
        print(f"  {label_b}: {rec_b.get('url', '?')}  method={rec_b.get('method', '?')}")

        diffs = diff_bodies(body_a, body_b)

        if not diffs:
            print(_green("  ✓ Bodies are IDENTICAL"))
        else:
            all_identical = False
            print(_red(f"  ✗ {len(diffs)} field-level difference(s) found:"))
            for d in diffs:
                print(d)

    return all_identical


def _summarise_captures(capture_root: pathlib.Path) -> None:
    """Print a summary table of all captures found under capture_root."""
    print()
    print(_bold("Capture summary:"))
    for runtime_dir in sorted(capture_root.iterdir()):
        if not runtime_dir.is_dir():
            continue
        for provider_dir in sorted(runtime_dir.iterdir()):
            if not provider_dir.is_dir():
                continue
            files = sorted(provider_dir.glob("*.json"))
            sizes = [f.stat().st_size for f in files]
            print(
                f"  {runtime_dir.name}/{provider_dir.name}:  "
                f"{len(files)} files  "
                f"({', '.join(str(s) + 'B' for s in sizes)})"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("dir_a", type=pathlib.Path, help="First capture directory (label: A)")
    parser.add_argument("dir_b", type=pathlib.Path, help="Second capture directory (label: B)")
    parser.add_argument(
        "--label-a",
        default=None,
        help="Label for dir_a (defaults to last two path components)",
    )
    parser.add_argument(
        "--label-b",
        default=None,
        help="Label for dir_b (defaults to last two path components)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=None,
        help="Specific filenames to compare (same name in both dirs)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of all captures under /tmp/cubepi_runtime_capture",
    )
    args = parser.parse_args()

    if args.summary:
        capture_root = pathlib.Path("/tmp/cubepi_runtime_capture")
        if capture_root.exists():
            _summarise_captures(capture_root)
        else:
            print(_yellow("No captures found at /tmp/cubepi_runtime_capture"))
        return

    dir_a: pathlib.Path = args.dir_a
    dir_b: pathlib.Path = args.dir_b

    if not dir_a.exists():
        print(_red(f"Error: directory not found: {dir_a}"))
        sys.exit(1)
    if not dir_b.exists():
        print(_red(f"Error: directory not found: {dir_b}"))
        sys.exit(1)

    label_a = args.label_a or "/".join(dir_a.parts[-2:])
    label_b = args.label_b or "/".join(dir_b.parts[-2:])

    print(_bold(f"\nDiffing: {label_a}  vs  {label_b}"))
    print(f"  A: {dir_a}")
    print(f"  B: {dir_b}")

    identical = compare_dirs(dir_a, dir_b, label_a, label_b, args.files)
    print()
    if identical:
        print(_green(_bold("Result: ALL IDENTICAL — request bodies match exactly.")))
        sys.exit(0)
    else:
        print(_red(_bold("Result: DIFFERENCES FOUND — see details above.")))
        sys.exit(1)


if __name__ == "__main__":
    main()
