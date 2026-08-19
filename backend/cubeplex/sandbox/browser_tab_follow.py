"""Keep the Neko live view on the tab agent-browser is operating.

Neko streams the Chromium window, not individual CDP targets. agent-browser
can drive a background tab while the user still sees a different one. After
each CLI command we activate the session's current page via CDP
``/json/activate/<id>``.

The helper is installed into the sandbox at ``start_browser`` time so it
applies to already-built images (no sandbox-image rebuild required).
"""

from __future__ import annotations

import base64
import json
import shlex
from typing import Any

FOCUS_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
import subprocess
import urllib.error
import urllib.request

CDP = "http://127.0.0.1:9222"


def list_pages():
    with urllib.request.urlopen(f"{CDP}/json", timeout=2) as resp:
        data = json.loads(resp.read().decode())
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict) and p.get("type") == "page"]


def current_url(real_bin):
    env = os.environ.copy()
    env["AGENT_BROWSER_WRAPPER"] = "1"
    try:
        out = subprocess.run(
            [real_bin, "get", "url"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else None


def _norm(u):
    u = str(u or "").strip()
    if not u:
        return ""
    return u.rstrip("/") or u


def choose_target_id(pages, url):
    wanted = _norm(url)
    if not pages or not wanted:
        return None
    exact = [p for p in pages if _norm(p.get("url")) == wanted]
    if not exact:
        return None
    tid = exact[0].get("id")
    return str(tid) if tid else None


def activate(target_id):
    urllib.request.urlopen(f"{CDP}/json/activate/{target_id}", timeout=2).read()


def main():
    real = os.environ.get("AGENT_BROWSER_REAL", "/usr/bin/agent-browser")
    try:
        pages = list_pages()
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return 0
    tid = choose_target_id(pages, current_url(real))
    if not tid:
        return 0
    try:
        activate(tid)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

WRAPPER_SCRIPT = r"""#!/bin/sh
# CUBEPLEX_TAB_FOLLOW=1
REAL="${AGENT_BROWSER_REAL:-__REAL_DEFAULT__}"
if [ "${AGENT_BROWSER_WRAPPER:-}" = "1" ]; then
    exec "$REAL" "$@"
fi
export AGENT_BROWSER_WRAPPER=1
export AGENT_BROWSER_REAL="$REAL"
"$REAL" "$@"
status=$?
case "${1:-}" in
    --help|-h|help|install|upgrade|skills) exit "$status" ;;
esac
if [ "$status" -eq 0 ]; then
    /usr/local/bin/cubeplex-focus-browser-tab >/dev/null 2>&1 || true
fi
exit "$status"
"""

_INSTALLER = r"""
import stat
from pathlib import Path
import json, base64
files = json.loads(base64.b64decode("@@PAYLOAD@@").decode())
focus_path = Path("/usr/local/bin/cubeplex-focus-browser-tab")
wrap_path = Path("/usr/local/bin/agent-browser")
real_path = Path("/usr/local/bin/agent-browser.real")

def write_exec(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

if wrap_path.exists():
    existing = wrap_path.read_text(encoding="utf-8", errors="replace")
    if "CUBEPLEX_TAB_FOLLOW=1" not in existing:
        # Always keep the current real CLI as .real, even if an older copy exists.
        wrap_path.replace(real_path)

real = "/usr/local/bin/agent-browser.real"
if not Path(real).exists():
    real = "/usr/bin/agent-browser"

wrapper = files["wrapper"].replace("__REAL_DEFAULT__", real)
write_exec(focus_path, files["focus"])
write_exec(wrap_path, wrapper)
"""


def _normalize_url(url: str | None) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    return text.rstrip("/") or text


def choose_target_id(pages: list[dict[str, Any]], url: str | None) -> str | None:
    """Pick the CDP target id whose URL matches the agent's current page.

    Exact match only (trailing slash ignored). Prefix matching would activate
    ``https://ex.com`` when the agent is on ``https://ex.com/login``.
    """
    wanted = _normalize_url(url)
    if not pages or not wanted:
        return None
    exact = [p for p in pages if _normalize_url(str(p.get("url"))) == wanted]
    if not exact:
        return None
    tid = exact[0].get("id")
    return str(tid) if tid else None


def tab_follow_install_command() -> str:
    """Root command that writes the agent-browser wrapper into the sandbox."""
    payload = base64.b64encode(
        json.dumps({"focus": FOCUS_SCRIPT, "wrapper": WRAPPER_SCRIPT}).encode()
    ).decode()
    src = _INSTALLER.replace("@@PAYLOAD@@", payload)
    return "python3 -c " + shlex.quote(src)
