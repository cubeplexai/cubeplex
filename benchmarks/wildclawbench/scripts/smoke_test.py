#!/usr/bin/env python3
"""Phase-1 smoke test: can cubeplex spin up & drive a sandbox on the WildClawBench image?

Verifies the core integration assumption: opensandbox injects execd into an
ARBITRARY image (here wildclawbench-ubuntu), so cubeplex can drive an agent whose
sandbox == the WildClawBench tool environment.

Steps (uses a workspace API key, which is org-admin in single_tenant):
  1. GET  /admin/sandbox-policy  → save current default_image
  2. PUT  default_image = <wcb image>
  3. create a conversation, post a message that runs a few shell commands via the
     agent's `execute` tool, stream SSE, collect the tool_result(s)
  4. revert default_image to the saved value (always, even on error)

Env (source a shard-*.env first): CUBEPLEX_BASE_URL, CUBEPLEX_TOKEN, CUBEPLEX_WS.
Usage: smoke_test.py <wcb-image-ref> [--model-key lite]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def _req(method: str, base: str, path: str, token: str, body=None, accept=None, timeout=180):
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if accept:
        headers["Accept"] = accept
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="image ref to set as sandbox default_image")
    ap.add_argument("--model-key", default="lite", help="cheap tier for the smoke (default lite)")
    args = ap.parse_args()

    base = os.environ["CUBEPLEX_BASE_URL"].rstrip("/")
    token = os.environ["CUBEPLEX_TOKEN"]
    ws = os.environ["CUBEPLEX_WS"]

    # 1. current policy
    pol = json.loads(_req("GET", base, "/api/v1/admin/sandbox-policy", token).read())
    saved_image = pol["default_image"]
    print(f"[smoke] current default_image = {saved_image}")

    def put_image(img: str) -> None:
        body = {
            "default_image": img,
            "network_default_action": pol.get("network_default_action", "allow"),
            "network_rules": pol.get("network_rules"),
            "command_rules": pol.get("command_rules") or None,
            "egress_proxy": pol.get("egress_proxy"),
        }
        _req("PUT", base, "/api/v1/admin/sandbox-policy", token, body=body).read()

    ok = False
    try:
        # 2. switch image
        put_image(args.image)
        print(f"[smoke] set default_image = {args.image}")

        # 3. drive one execute call
        cid = json.loads(
            _req("POST", base, f"/api/v1/ws/{ws}/conversations", token, {"title": "wcb-smoke"}).read()
        )["id"]
        probe_cmd = (
            "uname -a; echo '---'; (cat /etc/os-release 2>/dev/null | grep PRETTY); echo '---'; "
            "for t in agent-browser openclaw python3 node ffmpeg; do printf '%s: ' $t; "
            "command -v $t || echo MISSING; done; echo '---EXECD_OK---'"
        )
        msg = (
            "Use your execute tool to run EXACTLY this one shell command and then report "
            "its full stdout verbatim, nothing else:\n\n" + probe_cmd
        )
        tool_outputs: list[str] = []
        sandbox_err = None
        stream = _req(
            "POST", base, f"/api/v1/ws/{ws}/conversations/{cid}/messages", token,
            {"content": msg, "thinking": "off", "model_key": args.model_key},
            accept="text/event-stream",
        )
        for raw in stream:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            et = ev.get("type")
            d = ev.get("data") or {}
            if et == "tool_result":
                content = d.get("content", "")
                tool_outputs.append(content if isinstance(content, str) else json.dumps(content))
            elif et == "model_failover":
                print(f"[smoke] model_failover: {str(d.get('reason'))[:160]}")
            elif et in ("done", "error"):
                if et == "error":
                    sandbox_err = d
                break

        print("\n========== TOOL OUTPUT(S) ==========")
        for o in tool_outputs:
            print(o)
        print("====================================")
        joined = "\n".join(tool_outputs)
        ok = "EXECD_OK" in joined
        print(f"\n[smoke] execd/execute reachable: {ok}")
        print(f"[smoke] Ubuntu 22.04 image: {'22.04' in joined}")
        print(f"[smoke] agent-browser present: {'agent-browser: /' in joined}")
        if sandbox_err:
            print(f"[smoke] error event: {json.dumps(sandbox_err)[:300]}")
    finally:
        # 4. always revert
        try:
            put_image(saved_image)
            print(f"[smoke] reverted default_image = {saved_image}")
        except Exception as e:  # noqa: BLE001
            print(f"[smoke] WARNING: revert failed: {e} — set it back manually to {saved_image}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
