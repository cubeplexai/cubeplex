#!/usr/bin/env python3
"""Phase-2 end-to-end: run ONE WildClawBench task through cubeplex and grade it.

Pure cubeplex HTTP — uses the new POST /ws/{ws}/sandbox/exec and
POST /ws/{ws}/sandbox/files/upload endpoints (authenticated by the workspace
API key), so no kubectl / opensandbox coupling.

Pipeline:
  1. set org default_image = wcb image (admin API; reverted at the end)
  2. exec: create persistent /workspace/.wcb/{input,results,gt}; symlink
     /tmp_workspace -> /workspace/.wcb  (the tasks hardcode /tmp_workspace paths,
     but /workspace is the only PVC-backed, recycle-surviving dir)
  3. upload task input/* into the sandbox
  4. drive the agent (SSE) with the task prompt; save cubeplex SSE + convert to
     an OpenClaw-JSONL transcript
  5. upload gt/* + the transcript + transcript_loader.py + a grade runner
  6. exec: install grade deps, run the task's grade() with the OpenRouter judge
     env -> parse score.json
  7. revert default_image

Env (source a shard-*.env): CUBEPLEX_BASE_URL, CUBEPLEX_TOKEN, CUBEPLEX_WS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
WCB = HERE.parent
sys.path.insert(0, str(WCB))
from wcb_harness.dataset import parse_task_md  # noqa: E402
from wcb_harness.transcript import sse_to_openclaw_records, write_openclaw_jsonl  # noqa: E402

WORK = "/workspace/.wcb"  # persistent (PVC) task dir; /tmp_workspace -> here
PROXY = "http://192.168.1.215:7892"  # egress proxy so the sandbox's pip reaches PyPI


def _openrouter_key(config_path: Path) -> str:
    import yaml

    c = yaml.safe_load(config_path.read_text())

    def find(d):
        if isinstance(d, dict):
            if isinstance(d.get("openrouter"), dict):
                return d["openrouter"]
            for v in d.values():
                r = find(v)
                if r:
                    return r
        return None

    blk = find(c) or {}
    return blk.get("api_key", "")


class Cube:
    def __init__(self) -> None:
        self.base = os.environ["CUBEPLEX_BASE_URL"].rstrip("/")
        self.ws = os.environ["CUBEPLEX_WS"]
        self.h = {"Authorization": f"Bearer {os.environ['CUBEPLEX_TOKEN']}"}

    def get_policy(self) -> dict:
        return requests.get(f"{self.base}/api/v1/admin/sandbox-policy", headers=self.h, timeout=20).json()

    def set_image(self, img: str, pol: dict) -> None:
        body = {
            "default_image": img,
            "network_default_action": pol.get("network_default_action", "allow"),
            "network_rules": pol.get("network_rules"),
            "command_rules": pol.get("command_rules") or None,
            "egress_proxy": pol.get("egress_proxy"),
        }
        r = requests.put(f"{self.base}/api/v1/admin/sandbox-policy", headers=self.h, json=body, timeout=20)
        r.raise_for_status()

    def exec(self, command: str, *, envs: dict | None = None, timeout: int = 300) -> dict:
        r = requests.post(
            f"{self.base}/api/v1/ws/{self.ws}/sandbox/exec",
            headers=self.h,
            json={"command": command, "timeout": timeout, "envs": envs},
            timeout=timeout + 30,
        )
        r.raise_for_status()
        return r.json()

    def upload(self, local: Path, dest: str) -> dict:
        with local.open("rb") as f:
            r = requests.post(
                f"{self.base}/api/v1/ws/{self.ws}/sandbox/files/upload",
                headers=self.h,
                params={"path": dest},
                files={"file": (local.name, f)},
                timeout=120,
            )
        r.raise_for_status()
        return r.json()

    def upload_bytes(self, content: bytes, name: str, dest: str) -> dict:
        r = requests.post(
            f"{self.base}/api/v1/ws/{self.ws}/sandbox/files/upload",
            headers=self.h,
            params={"path": dest},
            files={"file": (name, content)},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    def drive_agent(
        self, prompt: str, model_key: str, sse_out: Path, *, max_seconds: float = 900.0
    ) -> list[dict]:
        cid = requests.post(
            f"{self.base}/api/v1/ws/{self.ws}/conversations",
            headers=self.h,
            json={"title": "wcb-task"},
            timeout=20,
        ).json()["id"]
        events: list[dict] = []
        start = time.time()
        with requests.post(
            f"{self.base}/api/v1/ws/{self.ws}/conversations/{cid}/messages",
            headers={**self.h, "Accept": "text/event-stream"},
            json={"content": prompt, "reasoning": {"mode": "off"}, "model_key": model_key},
            stream=True,
            timeout=max_seconds + 120,
        ) as resp:
            resp.raise_for_status()
            with sse_out.open("w") as fout:
                for line in resp.iter_lines(decode_unicode=True):
                    if line and line.startswith("data: "):
                        fout.write(line[6:] + "\n")
                        try:
                            ev = json.loads(line[6:])
                        except json.JSONDecodeError:
                            ev = None
                        if ev is not None:
                            events.append(ev)
                            if ev.get("type") in ("done", "error"):
                                break
                    # Wall-clock cap: stop a thrashing agent and grade what it left.
                    if time.time() - start > max_seconds:
                        print(f"[wcb] agent wall-clock cap ({max_seconds:.0f}s) hit — stopping", flush=True)
                        break
        return events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="path to the task .md")
    ap.add_argument("--repo", required=True, help="WildClawBench repo root (for skills/workspace resolution)")
    ap.add_argument("--data", required=True, help="dir with this task's exec/ and gt/ data")
    ap.add_argument("--image", default="hub.sensedeal.vip/library/wildclawbench-ubuntu:v1.4")
    ap.add_argument("--model-key", default="max")
    ap.add_argument("--max-agent-seconds", type=float, default=900.0)
    ap.add_argument("--config", default=str(WCB.parents[1] / "backend/config.development.local.yaml"))
    ap.add_argument("--out", default=str(WCB / "runs"))
    args = ap.parse_args()

    task = parse_task_md(Path(args.task), repo_root=Path(args.repo))
    data = Path(args.data)
    out_dir = Path(args.out) / task.task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    or_key = _openrouter_key(Path(args.config))
    # Judge is overridable via env vars so the API key never lands in the repo.
    # Defaults: OpenRouter + claude-sonnet-4-6 (strong vision, accepts max_tokens;
    # WCB's default gpt-5.4 rejects max_tokens=256). Override WCB_JUDGE_* to point
    # at a local/self-hosted judge endpoint (key from the shell env, NOT committed).
    judge_env = {
        "OPENROUTER_API_KEY": os.environ.get("WCB_JUDGE_API_KEY", or_key),
        "OPENROUTER_BASE_URL": os.environ.get(
            "WCB_JUDGE_BASE_URL", "https://openrouter.ai/api/v1"
        ),
        "JUDGE_MODEL": os.environ.get("WCB_JUDGE_MODEL", "anthropic/claude-sonnet-4-6"),
        "TMP_WORKSPACE": WORK,
    }
    jkey = judge_env["OPENROUTER_API_KEY"]
    print(
        f"[wcb] task={task.task_id} model={args.model_key} "
        f"judge={judge_env['JUDGE_MODEL']} @ {judge_env['OPENROUTER_BASE_URL']} "
        f"judge_key={'set' if jkey else 'MISSING'}"
    )

    cube = Cube()
    pol = cube.get_policy()
    saved_image = pol["default_image"]
    score: dict = {}
    try:
        # 1. image
        cube.set_image(args.image, pol)
        print(f"[wcb] default_image -> {args.image}")

        # 2. persistent workspace + /tmp_workspace symlink (exec also spins sandbox up).
        #    The wcb image ships /tmp_workspace as a REAL directory; `ln -sfn` on an
        #    existing dir creates the link INSIDE it (→ /tmp_workspace/.wcb) instead of
        #    replacing it, so grade's /tmp_workspace/gt/gt.png wouldn't resolve. Remove
        #    the dir first, then symlink. Also point pip at the egress proxy (the sandbox
        #    has no proxy env, so the agent's pip can't reach PyPI otherwise — it
        #    thrashes retrying). pip reads /etc/pip.conf regardless of shell.
        proxy = PROXY
        cube.exec(
            f"rm -rf {WORK} /tmp_workspace && mkdir -p {WORK}/input {WORK}/results {WORK}/gt && "
            f"ln -s {WORK} /tmp_workspace && "
            f"printf '[global]\\nproxy = {proxy}\\n' > /etc/pip.conf && echo ready",
            timeout=180,
        )
        print("[wcb] workspace prepared + /tmp_workspace symlink + pip proxy")

        # 2b. pre-warm the packages this task family typically needs, so the agent
        #     doesn't burn its budget pip-installing (belt-and-suspenders w/ pip.conf).
        cube.exec(
            "pip install -q numpy scipy pillow opencv-python-headless 2>&1 | tail -1 || true",
            envs={"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy},
            timeout=420,
        )
        print("[wcb] pre-warmed numpy/scipy/pillow/opencv")

        # 3. upload the task's exec/ tree → /tmp_workspace (WildClawBench maps
        #    <task>/exec/* to the agent's /tmp_workspace). If there's no exec/,
        #    upload every task file except the gt/ ground truth.
        exec_dir = data / "exec"
        src_root = exec_dir if exec_dir.is_dir() else data
        n_up = 0
        for f in sorted(src_root.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(src_root)
            if rel.parts and rel.parts[0] == "gt":
                continue  # ground truth is injected later, after the agent finishes
            cube.upload(f, f"{WORK}/{rel.as_posix()}")
            n_up += 1
        print(f"[wcb] uploaded {n_up} input file(s)")

        # 4. drive the agent
        print("[wcb] driving agent (this can take minutes)...")
        t0 = time.time()
        events = cube.drive_agent(
            task.prompt, args.model_key, out_dir / "sse.jsonl", max_seconds=args.max_agent_seconds
        )
        n_tools = sum(1 for e in events if e.get("type") == "tool_call")
        print(f"[wcb] agent done in {time.time()-t0:.0f}s, {len(events)} events, {n_tools} tool calls")

        # 5. transcript + gt + grade scaffolding.
        #    Re-upload gt RIGHT BEFORE grading (not with the input): the agent may
        #    have moved/deleted files under /tmp_workspace while solving, so gt
        #    uploaded at step 3 isn't guaranteed present at grade time. Also
        #    ensure the results dir exists (grade reads results/ from it).
        records = sse_to_openclaw_records(events, prompt=task.prompt)
        tpath = out_dir / "transcript.jsonl"
        write_openclaw_jsonl(records, tpath)
        cube.upload(tpath, f"{WORK}/transcript.jsonl")
        cube.exec(f"mkdir -p {WORK}/gt {WORK}/results", timeout=60)
        for f in sorted((data / "gt").rglob("*")):
            if f.is_file():
                rel = f.relative_to(data / "gt")
                cube.upload(f, f"{WORK}/gt/{rel}")
        loader = (Path(args.repo) / "src/utils/transcript_loader.py").read_text()
        cube.upload_bytes(loader.encode(), "_transcript_loader.py", f"{WORK}/_transcript_loader.py")
        # Judge-compat shim: WildClawBench's grade() calls the OpenAI client in
        # two shapes that break on reasoning judges (gpt-5.x, o1/o3/o4):
        #   (a) it passes max_tokens — reasoning models reject it (400) and want
        #       max_completion_tokens. Remap for reasoning models only.
        #   (b) some grade() calls pass NEITHER max_tokens nor max_completion_tokens
        #       (e.g. fuzzy_search). On reasoning models via litellm this can
        #       intermittently return HTTP 200 with empty message.content — the
        #       reasoning trace consumes the (small/default) completion budget and
        #       leaves nothing for the final answer. Supply a default
        #       max_completion_tokens so the final answer always has room.
        # Both branches touch reasoning models only; the default claude-sonnet
        # path (which accepts max_tokens and doesn't intermittently empty out) is
        # untouched. Verified fuzzy_search judge returns score 1 with this shim.
        judge_shim = (
            "import re, openai.resources.chat.completions as _oc\n"
            "_REASONING = re.compile(r'^(gpt-5|o[134]\\b|o[134]-)', re.I)\n"
            "_orig_create = _oc.Completions.create\n"
            "def _patched_create(self, *a, **kw):\n"
            "    _m = kw.get('model') or (a[0] if a else '')\n"
            "    if isinstance(_m, str) and _REASONING.search(_m):\n"
            "        if 'max_tokens' in kw and 'max_completion_tokens' not in kw:\n"
            "            kw['max_completion_tokens'] = kw.pop('max_tokens')\n"
            "        elif 'max_completion_tokens' not in kw:\n"
            "            kw['max_completion_tokens'] = 4096\n"
            "    return _orig_create(self, *a, **kw)\n"
            "_oc.Completions.create = _patched_create\n"
        )
        grade_runner = (
            "import json\n"
            "from _transcript_loader import load_transcript\n"
            f"_transcript = load_transcript({json.dumps(f'{WORK}/transcript.jsonl')})\n\n"
            f"{judge_shim}\n\n"
            f"{task.automated_checks}\n\n"
            f"result = grade(transcript=_transcript, workspace_path={json.dumps(WORK)})\n"
            "print(json.dumps(result))\n"
        )
        cube.upload_bytes(grade_runner.encode(), "_grade_runner.py", f"{WORK}/_grade_runner.py")
        print("[wcb] uploaded gt + transcript + grade runner")

        # 6. install grade deps + run grade().
        #    The sandbox ships a default http_proxy/https_proxy (opensandbox-injected,
        #    100.104.x:7897) that can't reach OpenRouter — and httpx honors the
        #    lowercase vars. Set BOTH cases to our working proxy so the LLM judge
        #    doesn't hang until timeout (→ keyword fallback → understated score).
        #    NO_PROXY keeps the LAN judge endpoint (192.168.1.215:4000) direct
        #    instead of looping through the clash proxy on the same host; remote
        #    judges (openrouter.ai) still go through the proxy.
        NO_PROXY = "localhost,127.0.0.1,10.0.0.0/8,192.168.0.0/16,100.104.0.0/16"
        grade_proxy_env = {
            "HTTP_PROXY": PROXY, "HTTPS_PROXY": PROXY,
            "http_proxy": PROXY, "https_proxy": PROXY,
            "NO_PROXY": NO_PROXY, "no_proxy": NO_PROXY,
        }
        print("[wcb] installing grade deps + grading...")
        cube.exec(
            "pip install -q openai pillow numpy 2>&1 | tail -1 || true",
            envs={**grade_proxy_env},
            timeout=300,
        )
        r = cube.exec(
            f"cd {WORK} && python3 _grade_runner.py",
            envs={**judge_env, **grade_proxy_env},
            timeout=600,
        )
        raw = r.get("output", "")
        (out_dir / "grade_stdout.txt").write_text(raw)
        for line in reversed(raw.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    score = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if not score:
            print(f"[wcb] WARN: no JSON score parsed. grade stdout tail:\n{raw[-800:]}")
    finally:
        cube.set_image(saved_image, pol)
        print(f"[wcb] reverted default_image -> {saved_image}")

    (out_dir / "score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False))
    print("\n========== SCORE ==========")
    print(json.dumps(score, indent=2, ensure_ascii=False))
    print("===========================")
    print(f"overall_score = {score.get('overall_score')}")
    return 0 if score else 1


if __name__ == "__main__":
    sys.exit(main())
