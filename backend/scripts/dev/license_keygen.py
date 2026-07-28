"""Generate license signing keypairs + sign license keys. Dev/founder tool.

Usage:
  uv run python scripts/dev/license_keygen.py genkey --kid prod-2026
  uv run python scripts/dev/license_keygen.py sign \
      --private-key-hex <hex> --kid prod-2026 --licensee "Acme Corp" \
      --features multi_org,sso --days 365

The kid names which signing key produced a license. Keep it stable for the life
of a key; mint a new one to rotate, and trust both ids during the window.

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
    args = parser.parse_args()
    if args.cmd == "genkey":
        genkey(args.kid)
    else:
        sign(args.private_key_hex, args.kid, args.licensee, args.features, args.days)


if __name__ == "__main__":
    main()
