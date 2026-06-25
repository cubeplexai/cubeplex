"""Single source of truth for where a skill's files live inside the sandbox.

Both the sandbox file-sync (``cubebox.sandbox.lazy._sync_skills``) and the
``load_skill`` tool import ``sandbox_skill_dir`` so the directory the files are
written to is exactly the directory the agent is told to read from — the agent
never has to construct the path itself.

Canonical skill names can contain a colon (``<org>:<skill>`` for
registry-installed skills). A colon is hostile to filesystem paths and the LLM
reliably mis-renders it as a path separator (and drops the version segment),
so reads of bundled scripts/templates fail. We normalise ``:`` to ``__`` and
hand the resolved path back to the agent via ``load_skill``.

Skills live UNDER ``/workspace`` so they survive sandbox pause/resume and
kill+recreate via the (workspace_id, user_id)-scoped PVC. The sync layer reads
``/workspace/.skills/manifest.json`` to short-circuit "already up to date".
"""

from __future__ import annotations

SKILLS_ROOT = "/workspace/.skills"


def safe_skill_name(name: str) -> str:
    """Normalise canonical skill name to a filesystem-safe directory name.

    Colons in ``<org>:<skill>`` registry names are replaced with double
    underscores; plain preinstalled names pass through unchanged.
    """
    return name.replace(":", "__")


def sandbox_skill_dir(name: str, version: str) -> str:
    """Absolute directory a skill's sibling files are mounted at in the sandbox.

    Returns a path with no trailing slash, e.g.
    ``/workspace/.skills/acme__my-skill/1.2.0``. Preinstalled skills have plain
    names (no colon) and are unaffected by the normalisation.
    """
    return f"{SKILLS_ROOT}/{safe_skill_name(name)}/{version}"
