"""Generate license signing keypairs + sign license keys. Dev/founder tool.

Usage:
  uv run python scripts/dev/license_keygen.py genkey
  uv run python scripts/dev/license_keygen.py sign \
      --private-key-hex <hex> --licensee "Acme Corp" \
      --features multi_org,sso --days 365

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


def genkey() -> None:
    private_key = Ed25519PrivateKey.generate()
    print("private_key_hex:", private_key.private_bytes_raw().hex())
    print("public_key_hex: ", private_key.public_key().public_bytes_raw().hex())


def sign(private_key_hex: str, licensee: str, features: str, days: int) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    now = datetime.now(UTC)
    payload = {
        "licensee": licensee,
        "features": [f.strip() for f in features.split(",") if f.strip()],
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    print(f"CBX1.{_b64url(raw)}.{_b64url(private_key.sign(raw))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("genkey")
    p_sign = sub.add_parser("sign")
    p_sign.add_argument("--private-key-hex", required=True)
    p_sign.add_argument("--licensee", required=True)
    p_sign.add_argument("--features", default="")
    p_sign.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    if args.cmd == "genkey":
        genkey()
    else:
        sign(args.private_key_hex, args.licensee, args.features, args.days)


if __name__ == "__main__":
    main()
