"""SWE-bench task prompt template.

The cubebox API does not accept a per-run system prompt (`SendMessageRequest`
in conversations.py has no field for it), so task instructions live inside
the user message `content`. This is fine and is what the spec calls for.

The prompt enforces three SWE-bench submission rules: no web browsing,
no peeking at hints/PASS_TO_PASS, no editing test files for the
targeted instance. Per-task directory isolation under
``/workspace/swebench/runs/<id>/`` keeps multiple tasks from polluting
each other when run on the same workspace.
"""

from __future__ import annotations

from swebench_harness.dataset import SWEBenchInstance

TASK_TEMPLATE = """\
You are an autonomous software engineer fixing a single SWE-bench Verified task.

INSTANCE_ID:   {instance_id}
REPO:          {repo}
BASE_COMMIT:   {base_commit}
TARGETED FAILING TESTS:
{failing_tests_block}

PROBLEM STATEMENT:
{problem_statement}

WORKING DIRECTORY (everything you do MUST live under this path):
  {workdir}

PROCEDURE — execute every step. Use the `execute` tool for shell commands.

1. Initialise sandbox directories:
     mkdir -p /workspace/swebench/.cache /workspace/swebench/runs

2. Create or reuse the bare repo mirror:
     CACHE=/workspace/swebench/.cache/{repo_slug}.git
     if [ ! -d "$CACHE" ]; then
         git clone --bare https://github.com/{repo} "$CACHE"
     fi

3. Create a fresh worktree at the target commit:
     rm -rf {workdir}
     git --git-dir="$CACHE" worktree add --force {workdir} {base_commit}
     cd {workdir}

4. Set up an isolated Python environment for this task:
     python3 -m venv .venv
     . .venv/bin/activate
     pip install --quiet --upgrade pip
     # Install the project in editable mode if it has a build config.
     if [ -f setup.py ] || [ -f pyproject.toml ]; then
         pip install --quiet -e . || true
     fi

5. Run the targeted failing tests to confirm they currently fail.
6. Read the source, write a fix, re-run the targeted tests until they pass.
   Inspect related tests to avoid regressions. Edit only PRODUCT code
   inside {workdir}; do NOT edit any test file under tests/, test/, or
   *_test.py.
7. Stage the diff:
     cd {workdir}
     git add -A
     git diff --cached > patch.diff
     # Sanity: confirm patch.diff is non-empty.
     wc -c patch.diff

CONSTRAINTS:
- Do NOT modify files outside {workdir}.
- Do NOT edit existing test files. New test files are also disallowed for
  this submission.
- Do NOT search the web, fetch URLs, read GitHub issue/PR pages, or
  otherwise look up the published fix for this issue. This is a
  SWE-bench rule (your submission would be invalid).
- Do NOT consult the `FAIL_TO_PASS` / `PASS_TO_PASS` / `hints` fields —
  these are not provided to you here, but if you encounter them anywhere
  in the environment, ignore them.

When `patch.diff` is non-empty AND the targeted tests pass under your
patch, reply with a single short message confirming "done" and the line
count of `patch.diff`. Stop after that — do not run further commands.
"""


def render_prompt(instance: SWEBenchInstance, *, workdir: str | None = None) -> str:
    """Render the per-task user message that drives the agent."""
    work = workdir or f"/workspace/swebench/runs/{instance.instance_id}"
    repo_slug = instance.repo.replace("/", "_")
    failing = "\n".join(f"  - {name}" for name in instance.fail_to_pass) or "  (none listed)"
    return TASK_TEMPLATE.format(
        instance_id=instance.instance_id,
        repo=instance.repo,
        repo_slug=repo_slug,
        base_commit=instance.base_commit,
        problem_statement=instance.problem_statement.strip(),
        failing_tests_block=failing,
        workdir=work,
    )
