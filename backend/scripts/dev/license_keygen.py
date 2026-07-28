"""Generate license signing keypairs + sign license keys. Dev/founder tool.

Usage:
  uv run python scripts/dev/license_keygen.py genkey --kid prod-2026
  uv run python scripts/dev/license_keygen.py sign \
      --private-key-hex <hex> --kid prod-2026 --licensee "Acme Corp" \
      --features multi_org,sso --days 365

  uv run python scripts/dev/license_keygen.py dev-env --features multi_org

The kid names which signing key produced a license. Keep it stable for the life
of a key; mint a new one to rotate, and trust both ids during the window.

`dev-env` is for test harnesses: it mints a throwaway keypair and prints the two
env vars that put a backend into EE mode. Nothing is persisted, so there is no
committed secret and no key that expires out from under CI.

The private key produced by `genkey` is the only thing that can sign customer
licenses. Keep it outside the repo (see docs/dev/specs/2026-07-07-oss-ee-split-design.md §7).
"""

from __future__ import annotations

import argparse
import base64
import json
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def genkey(kid: str) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes_raw().hex()
    print("private_key_hex:", private_key.private_bytes_raw().hex())
    print()
    print("Add to LICENSE_PUBLIC_KEYS in cubeplex/plugins/license.py:")
    print(f'    "{kid}": "{public_hex}",')


def sign(private_key_hex: str, kid: str, licensee: str, features: str, days: int) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    now = datetime.now(UTC)
    payload = {
        "kid": kid,
        "licensee": licensee,
        "features": [f.strip() for f in features.split(",") if f.strip()],
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    print(f"CPX1.{_b64url(raw)}.{_b64url(private_key.sign(raw))}")


def dev_env(features: str, days: int) -> None:
    """Print CUBEPLEX_LICENSE__* env lines for a throwaway EE-mode backend."""
    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes_raw().hex()
    now = datetime.now(UTC)
    payload = {
        "kid": "dev-ephemeral",
        "licensee": "dev-harness",
        "features": [f.strip() for f in features.split(",") if f.strip()],
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    # public_key_hex bypasses the kid table, so the ephemeral kid needs no entry.
    print(f"CUBEPLEX_LICENSE__PUBLIC_KEY_HEX={public_hex}")
    print(f"CUBEPLEX_LICENSE__KEY=CPX1.{_b64url(raw)}.{_b64url(private_key.sign(raw))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_genkey = sub.add_parser("genkey")
    p_genkey.add_argument("--kid", required=True, help="e.g. prod-2026")
    p_sign = sub.add_parser("sign")
    p_sign.add_argument("--private-key-hex", required=True)
    p_sign.add_argument("--kid", required=True)
    p_sign.add_argument("--licensee", required=True)
    p_sign.add_argument("--features", default="")
    p_sign.add_argument("--days", type=int, default=365)
    p_dev = sub.add_parser("dev-env")
    p_dev.add_argument("--features", default="multi_org")
    p_dev.add_argument("--days", type=int, default=1)
    args = parser.parse_args()
    if args.cmd == "genkey":
        genkey(args.kid)
    elif args.cmd == "dev-env":
        dev_env(args.features, args.days)
    else:
        sign(args.private_key_hex, args.kid, args.licensee, args.features, args.days)


if __name__ == "__main__":
    main()
