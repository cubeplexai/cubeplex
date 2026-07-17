"""Parse WildClawBench task .md files.

Mirrors WildClawBench's own `src/utils/task_parser.py::parse_task_md` field-for-
field, so a task parsed here is identical to what their grader/runner sees. We
keep our own copy (rather than importing theirs) so this harness can develop and
test standalone, before the WildClawBench repo is wired in as a dependency.

The authoritative source is the cloned repo at ~/benchmarks/wildclawbench/repo;
if their parser changes, re-sync this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WcbTask:
    task_id: str
    prompt: str
    workspace_path: str          # absolute path to the task's input workspace
    skills_path: str             # absolute path to the repo's bundled skills/ root
    automated_checks: str        # python source defining grade(transcript, workspace_path)
    env: str                     # newline-separated env var NAMES (values from .env)
    skills: str                  # newline-separated ClawHub skill names (may be empty)
    warmup: str                  # shell commands to run before the agent starts
    timeout_seconds: int
    file_path: str
    category: str

    def skill_names(self) -> list[str]:
        return [s.strip() for s in self.skills.splitlines() if s.strip() and not s.strip().startswith("#")]

    def env_names(self) -> list[str]:
        return [e.strip() for e in self.env.splitlines() if e.strip() and not e.strip().startswith("#")]

    def warmup_commands(self) -> list[str]:
        return [w for w in (ln.rstrip() for ln in self.warmup.splitlines()) if w.strip()]


def _strip_codeblock(raw: str) -> str:
    s = re.sub(r"^```[^\n]*\n?", "", raw.strip())
    s = re.sub(r"\n?```$", "", s).strip()
    return s


def parse_task_md(task_file: Path, *, repo_root: Path) -> WcbTask:
    """Parse one task .md. `repo_root` resolves task-relative workspace/skills paths.

    WildClawBench resolves these from the repo root (parents[2] of their
    task_parser.py); we pass it explicitly so this works from any cwd.
    """
    content = task_file.read_text(encoding="utf-8")

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not fm_match:
        raise ValueError(f"YAML frontmatter not found: {task_file}")

    import yaml  # local import: keep module import cheap

    metadata = yaml.safe_load(fm_match.group(1)) or {}
    body = fm_match.group(2)

    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.split("\n"):
        header = re.match(r"^##\s+(.+)$", line)
        if header:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = header.group(1)
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()

    raw_workspace = sections.get("Workspace Path", "").strip()
    workspace_path = _strip_codeblock(raw_workspace)
    if not workspace_path:
        raise ValueError(f"Missing ## Workspace Path in {task_file}")

    def abspath(rel: str) -> str:
        p = Path(rel)
        return str(p if p.is_absolute() else (repo_root / p).resolve())

    return WcbTask(
        task_id=metadata.get("id", task_file.stem),
        prompt=sections.get("Prompt", "").strip(),
        workspace_path=abspath(workspace_path),
        skills_path=abspath("skills"),
        automated_checks=_strip_codeblock(sections.get("Automated Checks", "")),
        env=_strip_codeblock(sections.get("Env", "")),
        skills=_strip_codeblock(sections.get("Skills", "")),
        warmup=_strip_codeblock(sections.get("Warmup", "")),
        timeout_seconds=int(metadata.get("timeout_seconds", 120)),
        file_path=str(task_file.resolve()),
        category=task_file.parent.name,
    )


def load_tasks(repo_root: Path, *, category: str | None = None) -> list[WcbTask]:
    """Load all task .md files under <repo_root>/tasks, optionally one category.

    Skips the `task0_template.md` template. Returns tasks sorted by id.
    """
    tasks_root = repo_root / "tasks"
    tasks: list[WcbTask] = []
    cat_dirs = (
        [tasks_root / category] if category else sorted(p for p in tasks_root.iterdir() if p.is_dir())
    )
    for cat_dir in cat_dirs:
        for md in sorted(cat_dir.glob("*.md")):
            if md.stem.startswith("task0_template"):
                continue
            tasks.append(parse_task_md(md, repo_root=repo_root))
    return sorted(tasks, key=lambda t: t.task_id)
