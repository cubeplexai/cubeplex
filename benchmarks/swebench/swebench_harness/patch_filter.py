"""Filter known-noise sections out of a unified-diff patch.

Defense-in-depth: even when the prompt template tells the agent to
exclude `.venv/`, an `edit_file` happy path could still slip in
`__pycache__/`, `*.pyc`, build artefacts, etc. SWE-bench's official
scorer applies the patch with `git apply --check`, which rejects
non-existent paths — one stray binary section can fail an otherwise
correct submission.

Apply this on the way OUT of the harness (when writing predictions.jsonl)
and on the way IN to the scorer (before subprocess invocation), so a
broken upstream can't poison a downstream we control.
"""

from __future__ import annotations

import re

# Path prefixes we never want in a SWE-bench patch. Match against the
# `a/<path>` side of `diff --git`.
NOISE_PREFIXES: tuple[str, ...] = (
    ".venv/",
    "venv/",
    ".env/",
    "__pycache__/",
    ".pytest_cache/",
    ".tox/",
    "build/",
    "dist/",
    ".eggs/",
    "node_modules/",
)
NOISE_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
)
# Anything that looks like `*.egg-info/` somewhere in the path.
NOISE_RE = re.compile(r"(^|/)[^/]+\.egg-info/")

# Match the start of a diff section. Each git-style hunk begins with this.
DIFF_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/\S+\s*$", re.MULTILINE)


def _is_noisy(path: str) -> bool:
    if path.startswith(NOISE_PREFIXES):
        return True
    if path.endswith(NOISE_SUFFIXES):
        return True
    if NOISE_RE.search(path):
        return True
    return False


def clean_patch(text: str) -> str:
    """Return ``text`` with all known-noise diff sections removed.

    Sections that don't start with `diff --git` (e.g. an empty input or a
    leading hunk for a single anonymous file) are kept verbatim.
    """
    if not text:
        return text
    # Split into sections by `^diff --git` while keeping the headers.
    headers = list(DIFF_HEADER_RE.finditer(text))
    if not headers:
        return text
    out_parts: list[str] = []
    # Preserve any preamble before the first diff header.
    if headers[0].start() > 0:
        out_parts.append(text[: headers[0].start()])
    for i, m in enumerate(headers):
        start = m.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        path = m.group(1)
        if _is_noisy(path):
            continue
        out_parts.append(text[start:end])
    return "".join(out_parts)


def count_sections(text: str) -> int:
    return len(DIFF_HEADER_RE.findall(text))
