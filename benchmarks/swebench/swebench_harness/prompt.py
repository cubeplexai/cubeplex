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

TASK_TEMPLATE = r"""You are an autonomous software engineer fixing a single SWE-bench Verified task.

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

1. Initialise sandbox directories and (optionally) configure outbound proxy:
     mkdir -p /workspace/swebench/.cache /workspace/swebench/runs /workspace/swebench/venvs
{proxy_setup_block}

2. Initialise (or reuse) a shallow bare cache for {repo}. The cache only
   stores SHAs we have fetched so far — far smaller than a full clone,
   typically <50 MB total even for django/astropy:
     CACHE=/workspace/swebench/.cache/{repo_slug}.git
     if [ ! -f "$CACHE/HEAD" ]; then
         rm -rf "$CACHE"
         git init --bare "$CACHE"
         git -C "$CACHE" remote add origin https://github.com/{repo}
     fi

3. Fetch the target commit into the bare cache. Shallow + idempotent —
   if the SHA is already present, this is effectively a no-op. GitHub
   accepts fetches of any reachable SHA on a public repo:
     git -C "$CACHE" fetch --depth 1 --no-tags origin \
         {base_commit}:refs/swebench/{instance_id}

4. Create a fresh worktree at the target commit. Use --force in case
   a prior attempt left a partial worktree:
     rm -rf {workdir}
     git --git-dir="$CACHE" worktree add --force {workdir} {base_commit}
     cd {workdir}

5. Set up an isolated Python venv OUTSIDE the worktree so it never
   ends up in `git diff`. CRITICAL: the sandbox image bakes in
   PYTHONPATH (pointing at /opt/venv + /workspace user site-packages)
   and PIP_PREFIX (/workspace/.python-packages). If you don't clear
   these, `pip install -e .` installs into the wrong prefix (the venv
   stays empty) AND PYTHONPATH shadows the venv's packages with the
   base image's — so `import <project>` finds the wrong version. Always
   unset them for this venv:
     unset PYTHONPATH PIP_PREFIX NPM_CONFIG_PREFIX
     VENV=/workspace/swebench/venvs/{instance_id}
     python3 -m venv "$VENV"
     . "$VENV/bin/activate"
     # setuptools+wheel: many older projects need distutils, which
     # Python 3.12 removed; setuptools ships the shim.
     pip install --quiet --upgrade pip setuptools wheel
     if [ -f setup.py ] || [ -f pyproject.toml ]; then
         pip install --quiet -e . || true
     fi
   Whenever you open a NEW shell for this task, re-run the `unset` and
   re-`activate` the venv first — otherwise the baked-in env returns.

6. Run the targeted failing tests to confirm they currently fail.
7. Read the source, write a fix, re-run the targeted tests until they pass.
   Inspect related tests to avoid regressions. Edit only PRODUCT code
   inside {workdir}; do NOT edit any test file under tests/, test/, or
   *_test.py.
8. Stage the diff. Belt-and-suspenders exclusion — even though the venv
   lives outside {workdir}, exclude __pycache__/*.pyc/build/dist in case
   the project's install drops other artefacts:
     cd {workdir}
     git add -A -- . ':(exclude)__pycache__' ':(exclude)*.pyc' \
                   ':(exclude)*.egg-info' ':(exclude).pytest_cache' \
                   ':(exclude).tox' ':(exclude)build' ':(exclude)dist'
     git diff --cached -- . ':(exclude)__pycache__' ':(exclude)*.pyc' \
                          ':(exclude)*.egg-info' ':(exclude).pytest_cache' \
                          ':(exclude).tox' ':(exclude)build' ':(exclude)dist' \
                          > patch.diff
     wc -c patch.diff
     ! grep -q '^diff --git a/\.venv/' patch.diff || (echo "ERROR: venv leaked into patch" && exit 1)

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


_PROXY_BLOCK_TEMPLATE = """\
   Configure git + pip to route through the egress HTTPS proxy
   (cubebox's SandboxPolicy.egress_proxy is not auto-injected into the
   sandbox env yet; this prompt sets it explicitly). Idempotent:
     git config --global http.proxy  {egress_proxy}
     git config --global https.proxy {egress_proxy}
"""


def render_prompt(
    instance: SWEBenchInstance,
    *,
    workdir: str | None = None,
    egress_proxy: str | None = None,
) -> str:
    """Render the per-task user message that drives the agent.

    ``egress_proxy``: if given, the prompt instructs the agent to set
    git http(s).proxy to this URL before fetching. Use when the sandbox
    cannot reach github.com directly but can reach a proxy host.
    """
    work = workdir or f"/workspace/swebench/runs/{instance.instance_id}"
    repo_slug = instance.repo.replace("/", "_")
    failing = "\n".join(f"  - {name}" for name in instance.fail_to_pass) or "  (none listed)"
    if egress_proxy:
        proxy_block = _PROXY_BLOCK_TEMPLATE.format(egress_proxy=egress_proxy)
    else:
        proxy_block = ""
    return TASK_TEMPLATE.format(
        instance_id=instance.instance_id,
        repo=instance.repo,
        repo_slug=repo_slug,
        base_commit=instance.base_commit,
        problem_statement=instance.problem_statement.strip(),
        failing_tests_block=failing,
        workdir=work,
        proxy_setup_block=proxy_block,
    )
