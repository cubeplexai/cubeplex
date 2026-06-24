#!/usr/bin/env python3
"""Bootstrap N benchmark workspaces for a sharded full run.

Writes shard-0.env … shard-{N-1}.env into --out-dir, each a fresh cubebox
user/workspace whose SandboxPolicy is set to the build image + allow-egress
+ proxy. Idempotent-ish: skips a shard whose env file already exists and
still authenticates.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bootstrap import bootstrap  # noqa: E402


def _still_valid(env_path: Path, base_url: str) -> bool:
    if not env_path.exists():
        return False
    env = {}
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    token = env.get("CUBEBOX_TOKEN")
    if not token:
        return False
    try:
        r = requests.get(f"{base_url}/api/v1/auth/me",
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bootstrap N benchmark workspaces.")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--shards", type=int, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--image",
                    default="hub.sensedeal.vip/library/cubebox-sandbox:24.04-20260623-build")
    ap.add_argument("--egress-proxy", default="http://192.168.1.215:7892")
    args = ap.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for s in range(args.shards):
        env_path = args.out_dir / f"shard-{s}.env"
        if _still_valid(env_path, args.base_url):
            print(f"[bootstrap-many] shard {s}: reusing existing valid {env_path}")
            continue
        email = f"bench-s{s}-{int(time.time())}@example.com"
        password = secrets.token_urlsafe(18)
        print(f"[bootstrap-many] shard {s}: registering {email}")
        bootstrap(
            base_url=args.base_url,
            email=email,
            password=password,
            out_path=env_path,
            open_egress=True,
            key_label=f"swebench-shard-{s}",
            image=args.image,
            egress_proxy=args.egress_proxy,
        )
    print(f"[bootstrap-many] {args.shards} shard env files in {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
