# OSS/EE Edition Foundation Implementation Plan

**Goal:** Land the edition/license foundation the OSS/EE split rests on: signed license-key
verification enforced at EE load, `edition`/`features` exposed via
`/api/v1/system/info`, the multi-org EE gate, frontend edition scaffolding
(`useEdition` + `<EEGate>` + nav filtering), and the Apache-2.0 + `ee/` repo licensing
skeleton.

**Architecture:** A new `cubeplex/plugins/license.py` module parses Ed25519-signed license
keys offline (public key embedded in OSS source; private key never enters the repo). App
startup refuses to boot when `cubeplex_ee` is installed without a valid license. The
backend is the single authority on edition: the frontend only reads
`/system/info.edition` and hides/gates EE surfaces — no license logic in the frontend.
Multi-org is gated at the only org-creation entrypoint (onboarding full mode) plus the
existing single-tenant startup consistency check.

**Prerequisite — stage 0 must land first:**
[`docs/dev/plans/2026-07-27-plugin-seam-simplification.md`](2026-07-27-plugin-seam-simplification.md).
Task 3 attaches the license check to the `import cubeplex_ee` gate that stage 0 (plugin seam
simplification, spec §9) introduces at `api/app.py:91`. Executing this plan before stage 0
means writing that check twice, against `PluginRegistry.discover()` and then again against
the import gate. Stage 0 also leaves the registry with the `register_*` methods that
`cubeplex_ee.register()` calls, and with `bind_defaults()` taking no `config=` argument —
Task 3 Step 4 below assumes both.

**Revised 2026-07-27** against the tree as it stands. Three changes from the 07-07 draft:
Task 3 moved from `PluginRegistry.discover()` to the import gate (above); Task 7 gates four
page files rather than five (`/admin/cost` is a bare `redirect()` stub — a gate there never
renders); and the superpowers sub-skill header was dropped, since this repo drives plans
through `/feature-workflow`.

**Tech Stack:** FastAPI, `cryptography` (Ed25519, already a backend dep), pydantic,
SQLModel/Postgres (no schema changes in this plan), Next.js + `@cubeplex/core` (SWR),
next-intl.

**Spec:** [`docs/dev/specs/2026-07-07-oss-ee-split-design.md`](../specs/2026-07-07-oss-ee-split-design.md)
— this plan is delivery stage 1 of 0–3 (spec §11).

**Decisions this implements (recorded in the spec §2):** multi-org = EE gate (OSS limited to
1 org) · IM all-OSS (connector matrix + IM admin stay in the OSS build; nothing IM gets
gated) · license-key validation before open-sourcing · Apache-2.0 core + monorepo `ee/`
(commercial EULA) · one `cubeplex-ee` wheel exposing a top-level `cubeplex_ee` package.

**Explicitly out of scope (follow-up plans):** SSO relocation into `ee/`; the
`cubeplex.cost_middleware` seam and moving the cost read path into `ee/`. Until those land,
backend EE routes still exist in the main package — this plan only makes the *frontend*
hide/gate them and builds the enforcement machinery. Also out of scope, and tracked in the
spec: collapsing the two parallel audit paths (§12.1) and the missing EE audit UI (§12.2).

## Global Constraints

- Backend: mypy strict, full type annotations, line length 100.
- Time: tz-aware datetimes only — `datetime.now(UTC)`; naive datetimes never cross a
  service boundary.
- Frontend: strict TS; **pnpm, never npm**; `@cubeplex/core` must build (`tsc`) before web
  sees API/type changes.
- Test placement: pure in-process tests → `backend/tests/unit/`; anything that opens an
  `AsyncSession`, runs alembic, or hits the FastAPI app → `backend/tests/e2e/`.
- No hand-editing `pyproject.toml`/`package.json` for *dependencies* (none are needed —
  `cryptography` is already present). Adding an `exports` entry to
  `frontend/packages/core/package.json` is config, not a dependency, and is required.
- Noisy commands (pytest, pnpm build, mypy) → pipe through `tee tmp/<task>.log`, read the
  tail.
- Execution happens in a worktree (`./scripts/new-worktree feat/2026-07-07-oss-ee-edition-foundation`
  from the main repo root); `cat .worktree.env` first — ports 8000/3000 are wrong inside
  worktrees. Plain `uv run pytest` from `backend/` is safe (conftest auto-routes to the
  per-slot test DB).
- Docs ship with the code: this plan adds `docs/site/docs/admin/editions.md` (sanctioned
  new page — editions are a new user-facing subsystem).

---

### Task 1: Repo licensing skeleton (Apache-2.0 root + `ee/` placeholder)

**Files:**
- Create: `LICENSE` (repo root)
- Create: `ee/LICENSE`
- Create: `ee/README.md`

**Interfaces:**
- Produces: the `ee/` directory later plans move EE code into; the licensing story every
  other task's headers assume.

- [ ] **Step 1: Fetch the canonical Apache-2.0 text**

```bash
curl -fsSL -o LICENSE https://www.apache.org/licenses/LICENSE-2.0.txt
head -3 LICENSE   # expect: "                                 Apache License"
```

- [ ] **Step 2: Create `ee/LICENSE`**

```text
The cubeplex Enterprise license (the "Enterprise License")
Copyright (c) 2026 cubeplex

With regard to the cubeplex software located in this "ee/" directory and any
software that this directory's build artifacts are distributed as part of:

This software and associated documentation files (the "Software") may only be
used in production if you (and any entity that you represent) hold a valid
cubeplex Enterprise license key for the correct number of user seats, and only
for the duration and scope of that license. Development of the Software itself
and evaluation in non-production environments are permitted.

You are NOT permitted to copy, modify, merge, publish, distribute, sublicense
or sell the Software except as allowed by the terms of your Enterprise license
agreement.

All third-party components incorporated into this Software are licensed under
the original license provided by the owner of the applicable component.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

(Modeled on n8n's `.ee` / GitLab `ee/LICENSE` pattern. A lawyer-reviewed EULA replaces
this before selling; the notice pins the boundary now.)

- [ ] **Step 3: Create `ee/README.md`**

```markdown
# cubeplex Enterprise (`ee/`)

Everything under `ee/` is **not** Apache-2.0. It is source-visible but governed by
[`ee/LICENSE`](./LICENSE): production use requires a valid cubeplex Enterprise license
key (delivered as `CUBEPLEX_LICENSE__KEY`; verified offline by
`backend/cubeplex/plugins/license.py`).

This directory builds a single Python distribution, `cubeplex-ee`, exposing the
top-level package `cubeplex_ee`. It is installed alongside the OSS package and
implements the Protocols in `backend/cubeplex/plugins/protocols.py`; app startup
imports it if present and hands it the parsed license. Installing or removing it never
modifies OSS code. Planned residents: SSO (SAML/OIDC), fine-grained RBAC, persistent
audit sinks, trace viewer, cost reporting, and multi-org support.

The rest of this repository (outside `ee/`) is Apache-2.0 — see the root
[`LICENSE`](../LICENSE).
```

(No `ee/pyproject.toml` yet — there is no EE code to package until stage 2. Task 1 only
plants the license boundary. The packaging decision it will need, path source vs. uv
workspace, is spec §11.)

- [ ] **Step 4: Commit**

```bash
git add LICENSE ee/LICENSE ee/README.md
git commit -m "chore: Apache-2.0 root license + ee/ commercial boundary"
```

---

### Task 2: License-key module (`cubeplex/plugins/license.py`)

**Files:**
- Create: `backend/cubeplex/plugins/license.py`
- Create: `backend/scripts/dev/license_keygen.py`
- Test: `backend/tests/unit/test_license.py`

**Interfaces:**
- Consumes: config keys `license.key` and `license.public_key_hex`
  (env: `CUBEPLEX_LICENSE__KEY`, `CUBEPLEX_LICENSE__PUBLIC_KEY_HEX` — dynaconf).
- Produces (used by Tasks 3–5):
  - `LICENSE_PUBLIC_KEY_HEX: str` — embedded production public key (hex, 32-byte Ed25519).
  - `FEATURE_MULTI_ORG: str = "multi_org"`
  - `class LicenseError(Exception)`
  - `@dataclass(frozen=True) License(licensee: str, features: frozenset[str],
    issued_at: datetime, expires_at: datetime)`
  - `parse_license_key(key: str, *, public_key_hex: str | None = None,
    now: datetime | None = None) -> License` (raises `LicenseError`)
  - `load_license() -> License | None` (config-driven, cached; invalid key → warn + None)
  - `has_feature(name: str) -> bool`
  - `get_edition() -> Literal["oss", "ee"]`
  - `get_features() -> list[str]`
  - `reset_license_cache_for_tests() -> None`

Key format: `CBX1.<b64url(payload-json)>.<b64url(ed25519-sig-over-payload-bytes)>`.
The signature covers the exact decoded payload bytes, so no JSON canonicalization is
needed. Payload fields: `licensee: str`, `features: list[str]`, `issued_at`,
`expires_at` (ISO-8601 **with** offset).

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/test_license.py`:

```python
"""Unit: license-key parsing, signature/expiry validation, feature gates."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cubeplex.plugins.license import (
    License,
    LicenseError,
    parse_license_key,
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _make_key(
    private_key: Ed25519PrivateKey,
    *,
    licensee: str = "Acme Corp",
    features: list[str] | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "licensee": licensee,
        "features": features if features is not None else ["multi_org"],
        "issued_at": (issued_at or now).isoformat(),
        "expires_at": (expires_at or (now + timedelta(days=365))).isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return f"CBX1.{_b64url(raw)}.{_b64url(private_key.sign(raw))}"


@pytest.fixture
def keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes_raw().hex()
    return private_key, public_hex


def test_valid_key_parses(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    private_key, public_hex = keypair
    lic = parse_license_key(_make_key(private_key), public_key_hex=public_hex)
    assert isinstance(lic, License)
    assert lic.licensee == "Acme Corp"
    assert lic.features == frozenset({"multi_org"})
    assert lic.expires_at.tzinfo is not None


def test_wrong_signer_rejected(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    _, public_hex = keypair
    other = Ed25519PrivateKey.generate()
    with pytest.raises(LicenseError):
        parse_license_key(_make_key(other), public_key_hex=public_hex)


def test_tampered_payload_rejected(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    private_key, public_hex = keypair
    key = _make_key(private_key)
    prefix, payload_b64, sig_b64 = key.split(".")
    raw = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    raw["features"] = ["multi_org", "sso", "audit"]
    forged = _b64url(json.dumps(raw, separators=(",", ":")).encode())
    with pytest.raises(LicenseError):
        parse_license_key(f"{prefix}.{forged}.{sig_b64}", public_key_hex=public_hex)


def test_expired_key_rejected(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    private_key, public_hex = keypair
    key = _make_key(
        private_key,
        issued_at=datetime.now(UTC) - timedelta(days=400),
        expires_at=datetime.now(UTC) - timedelta(days=35),
    )
    with pytest.raises(LicenseError, match="expired"):
        parse_license_key(key, public_key_hex=public_hex)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "garbage",
        "CBX1.onlytwo",
        "CBX9.YQ.YQ",  # wrong prefix
        "CBX1.!!!.YQ",  # bad base64
    ],
)
def test_malformed_keys_rejected(bad: str, keypair: tuple[Ed25519PrivateKey, str]) -> None:
    _, public_hex = keypair
    with pytest.raises(LicenseError):
        parse_license_key(bad, public_key_hex=public_hex)


def test_load_license_missing_key_is_oss(monkeypatch: pytest.MonkeyPatch) -> None:
    import cubeplex.plugins.license as lic_mod

    monkeypatch.setattr(lic_mod, "_config_get", lambda key: None)
    lic_mod.reset_license_cache_for_tests()
    assert lic_mod.load_license() is None
    assert lic_mod.get_edition() == "oss"
    assert lic_mod.get_features() == []
    assert lic_mod.has_feature("multi_org") is False
    lic_mod.reset_license_cache_for_tests()


def test_load_license_valid_key_is_ee(
    monkeypatch: pytest.MonkeyPatch, keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    import cubeplex.plugins.license as lic_mod

    private_key, public_hex = keypair
    key = _make_key(private_key, features=["multi_org", "sso"])
    values = {"license.key": key, "license.public_key_hex": public_hex}
    monkeypatch.setattr(lic_mod, "_config_get", lambda k: values.get(k))
    lic_mod.reset_license_cache_for_tests()
    assert lic_mod.get_edition() == "ee"
    assert lic_mod.get_features() == ["multi_org", "sso"]
    assert lic_mod.has_feature("sso") is True
    lic_mod.reset_license_cache_for_tests()


def test_load_license_invalid_key_degrades_to_oss(
    monkeypatch: pytest.MonkeyPatch, keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    import cubeplex.plugins.license as lic_mod

    _, public_hex = keypair
    values = {"license.key": "CBX1.bogus.bogus", "license.public_key_hex": public_hex}
    monkeypatch.setattr(lic_mod, "_config_get", lambda k: values.get(k))
    lic_mod.reset_license_cache_for_tests()
    assert lic_mod.load_license() is None
    assert lic_mod.get_edition() == "oss"
    lic_mod.reset_license_cache_for_tests()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_license.py --no-cov 2>&1 | tee tmp/license.log | tail -3
```

Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.plugins.license'`.

- [ ] **Step 3: Implement `backend/cubeplex/plugins/license.py`**

```python
"""Offline license-key verification + feature gates for EE.

Key format: ``CBX1.<b64url(payload-json)>.<b64url(ed25519 signature)>``.
The signature covers the exact payload bytes, so no JSON canonicalization is
required. The embedded public key is the production signer; ``license.public_key_hex``
config exists so tests/dev can use their own keypair — a production deployment never
sets it.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

# Production signing public key (raw Ed25519, 32 bytes, hex). The private half
# never enters this repo.
LICENSE_PUBLIC_KEY_HEX = "REPLACED_IN_STEP_6"

_KEY_PREFIX = "CBX1"

FEATURE_MULTI_ORG = "multi_org"


class LicenseError(Exception):
    """Invalid, tampered, malformed, or expired license key."""


@dataclass(frozen=True)
class License:
    licensee: str
    features: frozenset[str]
    issued_at: datetime
    expires_at: datetime


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + pad)
    except (binascii.Error, ValueError) as exc:
        raise LicenseError("malformed base64 segment") from exc


def _parse_ts(payload: dict[str, object], field: str) -> datetime:
    raw = payload.get(field)
    if not isinstance(raw, str):
        raise LicenseError(f"missing {field}")
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise LicenseError(f"bad {field}") from exc
    if ts.tzinfo is None:
        raise LicenseError(f"{field} must be timezone-aware")
    return ts


def parse_license_key(
    key: str,
    *,
    public_key_hex: str | None = None,
    now: datetime | None = None,
) -> License:
    """Verify signature + expiry and return the License. Raises LicenseError."""
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != _KEY_PREFIX:
        raise LicenseError("malformed license key")
    payload_raw = _b64url_decode(parts[1])
    signature = _b64url_decode(parts[2])

    pub_hex = public_key_hex or LICENSE_PUBLIC_KEY_HEX
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    except ValueError as exc:
        raise LicenseError("bad public key") from exc
    try:
        public_key.verify(signature, payload_raw)
    except InvalidSignature as exc:
        raise LicenseError("signature verification failed") from exc

    try:
        payload = json.loads(payload_raw)
    except ValueError as exc:
        raise LicenseError("payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LicenseError("payload is not an object")

    licensee = payload.get("licensee")
    features = payload.get("features")
    if not isinstance(licensee, str) or not isinstance(features, list):
        raise LicenseError("payload missing licensee/features")

    issued_at = _parse_ts(payload, "issued_at")
    expires_at = _parse_ts(payload, "expires_at")
    if (now or datetime.now(UTC)) >= expires_at:
        raise LicenseError("license expired")

    return License(
        licensee=licensee,
        features=frozenset(str(f) for f in features),
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _config_get(key: str) -> object | None:
    from cubeplex.config import config

    return config.get(key, None)


_license_loaded = False
_license: License | None = None


def load_license() -> License | None:
    """Parse the configured license key once; invalid/missing → None (OSS)."""
    global _license_loaded, _license
    if _license_loaded:
        return _license
    key = _config_get("license.key")
    pub = _config_get("license.public_key_hex")
    _license = None
    if isinstance(key, str) and key.strip():
        try:
            _license = parse_license_key(
                key.strip(),
                public_key_hex=pub.strip() if isinstance(pub, str) and pub.strip() else None,
            )
            logger.info(
                "license loaded: licensee=%s features=%s expires=%s",
                _license.licensee,
                sorted(_license.features),
                _license.expires_at.isoformat(),
            )
        except LicenseError as exc:
            logger.warning("configured license.key is invalid, running as OSS: %s", exc)
    _license_loaded = True
    return _license


def reset_license_cache_for_tests() -> None:
    global _license_loaded, _license
    _license_loaded = False
    _license = None


def has_feature(name: str) -> bool:
    lic = load_license()
    return lic is not None and name in lic.features


def get_edition() -> Literal["oss", "ee"]:
    return "ee" if load_license() is not None else "oss"


def get_features() -> list[str]:
    lic = load_license()
    return sorted(lic.features) if lic else []
```

- [ ] **Step 4: Write the keygen/signing dev script**

`backend/scripts/dev/license_keygen.py`:

```python
"""Generate license signing keypairs + sign license keys. Dev/founder tool.

Usage:
  uv run python scripts/dev/license_keygen.py genkey
  uv run python scripts/dev/license_keygen.py sign \
      --private-key-hex <hex> --licensee "Acme Corp" \
      --features multi_org,sso --days 365
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
```

- [ ] **Step 5: Run the unit tests — expect all green except none touching the constant**

```bash
cd backend && uv run pytest tests/unit/test_license.py --no-cov 2>&1 | tee tmp/license.log | tail -3
```

Expected: PASS (all tests pass `public_key_hex` explicitly, so the placeholder constant
doesn't matter yet).

- [ ] **Step 6: Generate the production keypair and embed the public key**

```bash
cd backend && uv run python scripts/dev/license_keygen.py genkey
```

- Replace `LICENSE_PUBLIC_KEY_HEX = "REPLACED_IN_STEP_6"` in
  `backend/cubeplex/plugins/license.py` with the printed `public_key_hex` value.
- Write the printed `private_key_hex` to `local/license-signing-key.txt` (the `local/`
  directory is gitignored — verify with `git check-ignore local/license-signing-key.txt`).
- **Flag to the founder in the task report:** back this key up outside the machine
  (password manager); it is the only thing that can sign customer licenses.

- [ ] **Step 7: mypy + full unit run, then commit**

```bash
cd backend && uv run mypy cubeplex/plugins/license.py 2>&1 | tail -2
uv run pytest tests/unit/test_license.py --no-cov 2>&1 | tee tmp/license.log | tail -3
git add cubeplex/plugins/license.py scripts/dev/license_keygen.py tests/unit/test_license.py
git commit -m "feat(license): Ed25519 license-key module + keygen tool"
```

---

### Task 3: Refuse to boot when `cubeplex_ee` is installed unlicensed

**Files:**
- Create: `backend/cubeplex/plugins/ee.py`
- Modify: `backend/cubeplex/api/app.py` (the EE load point stage 0 leaves at ~line 91)
- Test: `backend/tests/unit/test_ee_license_enforcement.py`

**Interfaces:**
- Consumes: `load_license()` from Task 2; the `PluginRegistry` binding surface.
- Produces:
  - `EE_MODULE: str = "cubeplex_ee"`
  - `load_ee(registry: PluginRegistry) -> bool` — imports the EE distribution if
    installed, verifies the license, hands the parsed `License` to
    `cubeplex_ee.register()`, and returns whether EE loaded.
- Startup behavior: `cubeplex_ee` importable without a valid license → `RuntimeError`
  at boot. Fail-fast beats silently disabling EE: a silently dropped SSO `AuthProvider`
  would brick logins in a far more confusing way.

Why a module rather than a bare `try: import` inline in `app.py`: the gate needs a unit
test, and a function whose import is monkeypatchable gives one without building a
synthetic wheel.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_ee_license_enforcement.py`:

```python
"""Unit: startup refuses to load cubeplex_ee without a valid license."""

from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace

import pytest

import cubeplex.plugins.license as lic_mod
from cubeplex.plugins.ee import EE_MODULE, load_ee


def _fake_ee_module(calls: list[object]) -> ModuleType:
    module = ModuleType(EE_MODULE)

    def register(registry: object, *, license: object) -> None:
        calls.append((registry, license))

    module.register = register  # type: ignore[attr-defined]
    return module


def _valid_license() -> "lic_mod.License":
    now = datetime.now(UTC)
    return lic_mod.License(
        licensee="Acme Corp",
        features=frozenset({"multi_org"}),
        issued_at=now,
        expires_at=now + timedelta(days=30),
    )


def test_ee_absent_runs_as_oss(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_import(name: str) -> ModuleType:
        raise ImportError(name)

    monkeypatch.setattr("cubeplex.plugins.ee.importlib.import_module", raise_import)
    monkeypatch.setattr(lic_mod, "load_license", lambda: None)
    assert load_ee(SimpleNamespace()) is False


def test_ee_present_without_license_refuses_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "cubeplex.plugins.ee.importlib.import_module", lambda name: _fake_ee_module(calls)
    )
    monkeypatch.setattr(lic_mod, "load_license", lambda: None)
    with pytest.raises(RuntimeError, match="license"):
        load_ee(SimpleNamespace())
    assert calls == []


def test_ee_present_with_license_registers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "cubeplex.plugins.ee.importlib.import_module", lambda name: _fake_ee_module(calls)
    )
    lic = _valid_license()
    monkeypatch.setattr(lic_mod, "load_license", lambda: lic)
    registry = SimpleNamespace()
    assert load_ee(registry) is True
    assert calls == [(registry, lic)]
```

- [ ] **Step 2: Run to verify the tests fail**

```bash
cd backend && uv run pytest tests/unit/test_ee_license_enforcement.py --no-cov 2>&1 | tee tmp/ee.log | tail -3
```

Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.plugins.ee'`.

- [ ] **Step 3: Implement `backend/cubeplex/plugins/ee.py`**

```python
"""Optional load of the single EE distribution (`cubeplex-ee`).

The OSS build never imports EE statically: if the distribution isn't installed the
import fails and CE defaults stay bound. When it *is* installed, a valid license is
mandatory — see docs/dev/specs/2026-07-07-oss-ee-split-design.md §8.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cubeplex.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

EE_MODULE = "cubeplex_ee"


def load_ee(registry: PluginRegistry) -> bool:
    """Import EE if installed and hand it the license. Returns whether EE loaded."""
    from cubeplex.plugins.license import load_license

    try:
        module = importlib.import_module(EE_MODULE)
    except ImportError:
        return False  # OSS build

    lic = load_license()
    if lic is None:
        raise RuntimeError(
            f"{EE_MODULE} is installed but no valid cubeplex license key is "
            "configured; set license.key (CUBEPLEX_LICENSE__KEY) or uninstall the "
            "cubeplex-ee wheel"
        )

    register = getattr(module, "register", None)
    if register is None:
        raise RuntimeError(f"{EE_MODULE} does not expose register(registry, *, license)")
    register(registry, license=lic)
    logger.info(
        "EE loaded: licensee=%s features=%s expires=%s",
        lic.licensee,
        sorted(lic.features),
        lic.expires_at.isoformat(),
    )
    return True
```

(The `load_license` import stays local so `lic_mod.load_license` monkeypatching works and
module import stays cycle-free.)

- [ ] **Step 4: Call it from app startup**

In `backend/cubeplex/api/app.py`, at the EE load point stage 0 leaves behind (where
`await _reg.discover()` used to sit, ~line 91), before `bind_defaults`:

```python
    from cubeplex.plugins.ee import load_ee

    load_ee(_reg)
```

Order matters: EE registers its implementations on the registry, then `bind_defaults`
resolves each Protocol slot, falling back to a CE default wherever EE registered nothing.

- [ ] **Step 5: Run tests to verify all pass**

```bash
cd backend && uv run pytest tests/unit/test_ee_license_enforcement.py --no-cov 2>&1 | tee tmp/ee.log | tail -3
```

Expected: 3 passed.

- [ ] **Step 6: mypy + commit**

```bash
cd backend && uv run mypy cubeplex/plugins/ee.py cubeplex/api/app.py 2>&1 | tail -2
git add cubeplex/plugins/ee.py cubeplex/api/app.py tests/unit/test_ee_license_enforcement.py
git commit -m "feat(license): refuse boot when cubeplex_ee is installed unlicensed"
```

---

### Task 4: `edition` + `features` in `/api/v1/system/info`

**Files:**
- Modify: `backend/cubeplex/api/schemas/system.py`
- Modify: `backend/cubeplex/api/routes/v1/system.py`
- Test: `backend/tests/e2e/test_system_info.py` (extend existing)

**Interfaces:**
- Consumes: `get_edition()`, `get_features()` from Task 2.
- Produces: `SystemInfoResponse.edition: Literal["oss","ee"]` and
  `SystemInfoResponse.features: list[str]` — the only edition source the frontend
  (Tasks 6–8) reads.

- [ ] **Step 1: Extend the e2e test (failing first)**

Append to `backend/tests/e2e/test_system_info.py`:

```python
async def test_system_info_reports_edition(unauthenticated_memory_client):
    """No license configured in the test env → OSS edition, empty features."""
    resp = await unauthenticated_memory_client.get("/api/v1/system/info")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["edition"] == "oss"
    assert data["features"] == []
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && uv run pytest tests/e2e/test_system_info.py --no-cov 2>&1 | tee tmp/sysinfo.log | tail -3
```

Expected: new test FAILS with `KeyError: 'edition'`.

- [ ] **Step 3: Add the schema fields**

In `backend/cubeplex/api/schemas/system.py`, add to `SystemInfoResponse`:

```python
    # Edition is computed server-side from the configured license key; the
    # frontend never holds license logic — it only mirrors these two fields.
    edition: Literal["oss", "ee"] = "oss"
    features: list[str] = []
```

- [ ] **Step 4: Populate in the route**

In `backend/cubeplex/api/routes/v1/system.py`, inside `get_system_info` add the import and
fields:

```python
    from cubeplex.plugins.license import get_edition, get_features

    return SystemInfoResponse(
        deployment_mode=mode,  # type: ignore[arg-type]
        version=_CUBEPLEX_VERSION,
        sandbox_enabled=config.get("sandbox.enabled", False),
        password_policy=get_password_policy(),  # type: ignore[arg-type]
        edition=get_edition(),
        features=get_features(),
    )
```

- [ ] **Step 5: Run tests to verify they pass, commit**

```bash
cd backend && uv run pytest tests/e2e/test_system_info.py --no-cov 2>&1 | tee tmp/sysinfo.log | tail -3
git add cubeplex/api/schemas/system.py cubeplex/api/routes/v1/system.py tests/e2e/test_system_info.py
git commit -m "feat(system): expose edition + licensed features in /system/info"
```

---

### Task 5: Multi-org EE gate

**Files:**
- Modify: `backend/cubeplex/auth/singleton_org.py`
- Modify: `backend/cubeplex/api/routes/v1/onboarding.py` (full-mode branch, around line 84-97)
- Modify: `backend/cubeplex/api/app.py:316-333` (startup consistency check)
- Test: `backend/tests/e2e/test_multi_org_gate.py`

**Interfaces:**
- Consumes: `has_feature`, `FEATURE_MULTI_ORG` from Task 2; existing
  `org_count(session)` in `singleton_org.py`.
- Produces:
  - `class MultiOrgNotLicensedError(Exception)` in `cubeplex/auth/singleton_org.py`
  - `async def ensure_additional_org_allowed(session: AsyncSession) -> None` — no-op when
    0 orgs exist or the license has `multi_org`; raises `MultiOrgNotLicensedError`
    otherwise.
  - HTTP contract: onboarding full-mode in `single_tenant` returns
    **403 `multi_org_requires_license`** when an org already exists and no
    `multi_org`-licensed key is configured.

Scope note: the gate applies in `single_tenant` mode only. `multi_tenant` (hosted cloud)
creates one org per user by design and is our own licensed deployment — gating it would
break every multi-tenant e2e flow for zero enforcement value. The OSS story is
"self-host = 1 shared org; client/department isolation = EE".

- [ ] **Step 1: Read the existing single-tenant e2e flow first**

Read `backend/tests/e2e/test_auth_needs_onboarding.py` and
`backend/tests/e2e/test_onboarding.py`. Reuse their login/CSRF helpers if they differ from
the `_login` helper shown below (which mirrors `test_onboarding.py`).

- [ ] **Step 2: Write the failing e2e test**

`backend/tests/e2e/test_multi_org_gate.py`:

```python
"""E2E: single_tenant caps orgs at 1 unless the license has multi_org."""

import secrets

import httpx
import pytest

from tests.e2e.helpers import csrf_cookie_name

pytestmark = pytest.mark.e2e


async def _login(client: httpx.AsyncClient, email: str, password: str) -> None:
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(csrf_cookie_name()) or ""
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 204), f"login failed: {r.status_code} {r.text}"
    client.headers["X-CSRF-Token"] = client.cookies.get(csrf_cookie_name()) or csrf


async def _register(client: httpx.AsyncClient, email: str, password: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code == 201, resp.text


def _onboard_body(tag: str) -> dict[str, str]:
    return {
        "org_name": f"Org {tag}",
        "org_slug": f"org-{tag}",
        "workspace_name": f"WS {tag}",
    }


@pytest.mark.asyncio
async def test_second_org_blocked_without_license(
    fresh_db_unauth_client_single_tenant: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = fresh_db_unauth_client_single_tenant
    monkeypatch.setattr(
        "cubeplex.auth.email_otp.is_email_verification_enabled", lambda: False
    )
    password = "StrongPass1!"
    email1 = f"gate1-{secrets.token_hex(4)}@example.com"
    email2 = f"gate2-{secrets.token_hex(4)}@example.com"

    # Both users register before any org exists (the race the gate closes).
    await _register(client, email1, password)
    await _register(client, email2, password)

    await _login(client, email1, password)
    resp = await client.post("/api/v1/onboarding", json=_onboard_body(secrets.token_hex(4)))
    assert resp.status_code == 201, resp.text

    client.cookies.clear()
    await _login(client, email2, password)
    resp = await client.post("/api/v1/onboarding", json=_onboard_body(secrets.token_hex(4)))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "multi_org_requires_license"


@pytest.mark.asyncio
async def test_second_org_allowed_with_multi_org_license(
    fresh_db_unauth_client_single_tenant: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = fresh_db_unauth_client_single_tenant
    monkeypatch.setattr(
        "cubeplex.auth.email_otp.is_email_verification_enabled", lambda: False
    )
    monkeypatch.setattr(
        "cubeplex.plugins.license.has_feature", lambda name: name == "multi_org"
    )
    password = "StrongPass1!"
    email1 = f"lic1-{secrets.token_hex(4)}@example.com"
    email2 = f"lic2-{secrets.token_hex(4)}@example.com"

    await _register(client, email1, password)
    await _register(client, email2, password)

    await _login(client, email1, password)
    resp = await client.post("/api/v1/onboarding", json=_onboard_body(secrets.token_hex(4)))
    assert resp.status_code == 201, resp.text

    client.cookies.clear()
    await _login(client, email2, password)
    resp = await client.post("/api/v1/onboarding", json=_onboard_body(secrets.token_hex(4)))
    assert resp.status_code == 201, resp.text
```

Note: if `_on_register_single_tenant` auto-joins user2 to an existing org at registration
time, that doesn't affect this test — both users register while 0 orgs exist, so both hit
full-mode onboarding. If the register flow rejects the second pending-owner registration
outright, adapt registration order per what `test_auth_needs_onboarding.py` shows, keeping
the invariant under test: **the second org creation in single_tenant must 403 without a
license**. (License semantics via monkeypatching `has_feature` is deliberate here: key
parsing is unit-tested in Task 2; this test covers the gate wiring.)

- [ ] **Step 3: Run to verify the first test fails**

```bash
cd backend && uv run pytest tests/e2e/test_multi_org_gate.py --no-cov 2>&1 | tee tmp/multiorg.log | tail -5
```

Expected: `test_second_org_blocked_without_license` FAILS (second onboarding returns 201,
creating org #2 — today's latent hole). The licensed test may already pass.

- [ ] **Step 4: Add the gate helper to `singleton_org.py`**

Append to `backend/cubeplex/auth/singleton_org.py`:

```python
class MultiOrgNotLicensedError(Exception):
    """A second org was requested but the license lacks the multi_org feature."""


async def ensure_additional_org_allowed(session: AsyncSession) -> None:
    """Gate org creation beyond the first (single_tenant callers only)."""
    from cubeplex.plugins.license import FEATURE_MULTI_ORG, has_feature

    if await org_count(session) == 0:
        return
    if not has_feature(FEATURE_MULTI_ORG):
        raise MultiOrgNotLicensedError
```

- [ ] **Step 5: Enforce in the onboarding route**

In `backend/cubeplex/api/routes/v1/onboarding.py`, add to the imports:

```python
from cubeplex.auth.singleton_org import (
    MultiOrgNotLicensedError,
    ensure_additional_org_allowed,
)
```

Then in `complete_onboarding`, inside the `if not org_rows:` full-mode branch, directly
before the `_bootstrap_org_and_workspace(...)` call (after the `org_name`/`org_slug`
validation raise):

```python
            mode = getattr(request.app.state, "deployment_mode", "single_tenant")
            if mode == "single_tenant":
                try:
                    await ensure_additional_org_allowed(session)
                except MultiOrgNotLicensedError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="multi_org_requires_license",
                    ) from exc
```

- [ ] **Step 6: Make the startup consistency check license-aware**

In `backend/cubeplex/api/app.py` (currently lines 316-333), replace the `if int(_count) > 1:`
block body:

```python
        if int(_count) > 1:
            from cubeplex.plugins.license import FEATURE_MULTI_ORG, has_feature

            if not has_feature(FEATURE_MULTI_ORG):
                raise RuntimeError(
                    f"single_tenant requires exactly 0 or 1 orgs in DB; found "
                    f"{int(_count)}. Multiple orgs need an EE license with the "
                    "multi_org feature. Install a license key, switch to "
                    "multi_tenant, or clean up the DB before starting."
                )
```

- [ ] **Step 7: Run the gate tests + neighbors to verify green**

```bash
cd backend && uv run pytest tests/e2e/test_multi_org_gate.py tests/e2e/test_onboarding.py \
  tests/e2e/test_auth_needs_onboarding.py --no-cov 2>&1 | tee tmp/multiorg.log | tail -3
```

Expected: all pass (multi_tenant onboarding flows untouched).

- [ ] **Step 8: mypy + commit**

```bash
cd backend && uv run mypy cubeplex/auth/singleton_org.py cubeplex/api/routes/v1/onboarding.py 2>&1 | tail -2
git add cubeplex/auth/singleton_org.py cubeplex/api/routes/v1/onboarding.py cubeplex/api/app.py \
  tests/e2e/test_multi_org_gate.py
git commit -m "feat(license): gate second org in single_tenant behind multi_org feature"
```

---

### Task 6: `@cubeplex/core` — edition fields + `useEdition` hook

**Files:**
- Modify: `frontend/packages/core/src/api/system.ts`
- Create: `frontend/packages/core/src/hooks/useEdition.ts`
- Modify: `frontend/packages/core/package.json` (add `./hooks/useEdition` exports entry)

**Interfaces:**
- Consumes: Task 4's `/system/info` fields.
- Produces (used by Tasks 7–8):
  - `SystemInfoResponse.edition?: 'oss' | 'ee'`, `SystemInfoResponse.features?: string[]`
  - `useEdition(): { edition: 'oss' | 'ee'; features: string[];
    hasFeature: (name: string) => boolean; loading: boolean }` imported via subpath
    `@cubeplex/core/hooks/useEdition` (client hooks are intentionally not in the barrel —
    see the comment in `frontend/packages/core/src/index.ts:13-20`).

No test in this task: the hook is a thin SWR read over an endpoint already e2e-tested in
Task 4, and DOM-presence tests are forbidden by `docs/testing.md`. `tsc` strict is the
verification.

- [ ] **Step 1: Extend `SystemInfoResponse`**

In `frontend/packages/core/src/api/system.ts`:

```typescript
export interface SystemInfoResponse {
  deployment_mode: 'single_tenant' | 'multi_tenant'
  version: string
  sandbox_enabled?: boolean
  password_policy?: 'low' | 'high'
  edition?: 'oss' | 'ee'
  features?: string[]
}
```

- [ ] **Step 2: Create the hook**

`frontend/packages/core/src/hooks/useEdition.ts`:

```typescript
'use client'

import useSWR from 'swr'

import { createApiClient } from '../api/client'
import { fetchSystemInfo, type SystemInfoResponse } from '../api/system'

/**
 * Backend-computed edition ('oss' | 'ee') + licensed feature flags.
 * Shares the SWR key with useDeploymentMode, so it costs no extra request.
 * Defaults to 'oss' while loading — gate on `loading` where flicker matters.
 */
export function useEdition() {
  const { data, isLoading } = useSWR<SystemInfoResponse>(
    '/api/v1/system/info',
    () => fetchSystemInfo(createApiClient('')),
    { revalidateOnFocus: false, revalidateIfStale: false, shouldRetryOnError: false },
  )
  const features = data?.features ?? []
  return {
    edition: data?.edition ?? 'oss',
    features,
    hasFeature: (name: string) => features.includes(name),
    loading: isLoading,
  }
}
```

- [ ] **Step 3: Add the subpath export**

In `frontend/packages/core/package.json` `"exports"`, add alongside the existing
`./hooks/useDeploymentMode` entry (mirror its exact shape — `types`/`source`/`import`/
`default` all pointing to `./src/hooks/useEdition.ts`):

```json
    "./hooks/useEdition": {
      "types": "./src/hooks/useEdition.ts",
      "source": "./src/hooks/useEdition.ts",
      "import": "./src/hooks/useEdition.ts",
      "default": "./src/hooks/useEdition.ts"
    }
```

- [ ] **Step 4: Build core to verify strict TS passes**

```bash
cd frontend && pnpm --filter @cubeplex/core build 2>&1 | tee ../tmp/core-build.log | tail -3
```

Expected: clean `tsc` exit.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/api/system.ts packages/core/src/hooks/useEdition.ts packages/core/package.json
git commit -m "feat(core): edition/features in SystemInfo + useEdition hook"
```

---

### Task 7: `<EEGate>` component + gate the EE admin pages

**Files:**
- Create: `frontend/packages/web/components/admin/EEGate.tsx`
- Modify: `frontend/packages/web/app/admin/authentication/page.tsx`
- Modify: `frontend/packages/web/app/admin/insights/page.tsx`
- Modify: `frontend/packages/web/app/admin/traces/page.tsx`
- Modify: `frontend/packages/web/app/admin/traces/[traceId]/page.tsx`
- Modify: `frontend/packages/web/messages/en.json` (namespace `adminLayout`, ~line 1529)
- Modify: `frontend/packages/web/messages/zh.json` (same namespace)

**Interfaces:**
- Consumes: `useEdition` from Task 6.
- Produces: `EEGate({ children }: { children: ReactNode })` — renders children when
  edition is `'ee'`, an enterprise-license card otherwise, `null` while loading.

- [ ] **Step 1: Add i18n strings**

In `frontend/packages/web/messages/en.json`, inside the existing `"adminLayout"` object
(next to `"comingSoon"`):

```json
    "eeOnlyTitle": "Enterprise feature",
    "eeOnlyDescription": "This page requires a cubeplex Enterprise license."
```

In `frontend/packages/web/messages/zh.json`, same namespace:

```json
    "eeOnlyTitle": "企业版功能",
    "eeOnlyDescription": "此页面需要 cubeplex 企业版 license。"
```

- [ ] **Step 2: Create the component**

`frontend/packages/web/components/admin/EEGate.tsx` (styling mirrors
`components/admin/ComingSoonCard.tsx`):

```tsx
'use client'

import type { ReactNode } from 'react'
import { Lock } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useEdition } from '@cubeplex/core/hooks/useEdition'

/**
 * Wraps EE-only admin pages. The backend is authoritative (edition comes from
 * /system/info); this only prevents OSS users from hitting bare API errors on
 * direct navigation to EE routes.
 */
export function EEGate({ children }: { children: ReactNode }) {
  const { edition, loading } = useEdition()
  const t = useTranslations('adminLayout')

  if (loading) return null
  if (edition === 'ee') return <>{children}</>

  return (
    <div className="max-w-2xl mx-auto mt-16 px-6">
      <div className="rounded-xl border border-dashed border-border bg-muted/20 px-6 py-10 text-center">
        <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Lock className="size-4" />
        </div>
        <p className="text-sm font-medium mb-1">{t('eeOnlyTitle')}</p>
        <p className="text-xs text-muted-foreground">{t('eeOnlyDescription')}</p>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Wrap each of the four page files**

Two deliberate exclusions. `/admin/im` is not gated — IM connectors are OSS per spec §2.
`/admin/cost` is not gated either: it is a five-line `redirect('/admin/insights')` stub, so
a gate there would never render (the redirect fires first). Leave that file untouched; its
destination, `/admin/insights`, carries the gate. See spec §6 and §10.

Uniform mechanical pattern — do NOT restructure page internals. For each page file:
rename the existing default-export component by appending `Content`, drop its
`export default`, and add a new default export that wraps it. Example for
`app/admin/authentication/page.tsx` (component `AdminAuthenticationPage`, line 33):

```tsx
import { EEGate } from '@/components/admin/EEGate'

function AdminAuthenticationPageContent() {
  // ...existing body unchanged...
}

export default function AdminAuthenticationPage() {
  return (
    <EEGate>
      <AdminAuthenticationPageContent />
    </EEGate>
  )
}
```

Apply identically in the other three files (whatever each page's current default component
is named). If a page is a server component (no `'use client'`), the wrapper still works —
`EEGate` is itself a client component; keep the page's own directives untouched.

- [ ] **Step 4: Lint + typecheck the web package**

```bash
cd frontend && pnpm --filter web lint 2>&1 | tee ../tmp/eegate.log | tail -3
pnpm --filter web exec tsc --noEmit 2>&1 | tee -a ../tmp/eegate.log | tail -3
```

Expected: clean on both.

- [ ] **Step 5: Manual verification against the local backend (no license configured)**

Start backend + web (worktree ports from `.worktree.env`), log in as admin, navigate to
`/admin/authentication` — expect the Enterprise card, no SSO API calls fired, no console
errors. Capture a screenshot for the PR.

- [ ] **Step 6: Commit**

```bash
git add packages/web/components/admin/EEGate.tsx packages/web/app/admin packages/web/messages
git commit -m "feat(web): EEGate component; gate SSO/insights/traces admin pages"
```

---

### Task 8: AdminSubNav edition filtering

**Files:**
- Modify: `frontend/packages/web/components/admin/AdminSubNav.tsx:31-48` (types) and
  `:120-201` (ENTRIES + render)

**Interfaces:**
- Consumes: `useEdition` from Task 6.
- Produces: OSS deployments render no EE nav entries; EE entries appear only when
  `/system/info.edition === 'ee'`.

- [ ] **Step 1: Extend the nav types**

In `AdminSubNav.tsx`, add the optional flag to both shapes (note: the `/admin/im` entry
is NOT tagged — IM is OSS):

```tsx
type NavLeaf = {
  href: string
  label: string
  icon: LucideIcon
  ee?: boolean
}

type NavGroup = {
  key: string
  label: string
  icon: LucideIcon
  children: NavLeaf[]
  ee?: boolean
}
```

- [ ] **Step 2: Mark the EE entries and filter**

Inside `AdminSubNav()`: add the hook import
`import { useEdition } from '@cubeplex/core/hooks/useEdition'` (top of file), call
`const { edition } = useEdition()` next to the other hooks, tag these three ENTRIES with
`ee: true`:

- `{ href: '/admin/authentication', ... }`
- `{ href: '/admin/insights', ... }`
- `{ href: '/admin/traces', ... }`

then filter before rendering:

```tsx
  const visibleEntries = ENTRIES.filter((entry) => edition === 'ee' || !entry.ee)
```

and change the render loop from `ENTRIES.map(...)` to `visibleEntries.map(...)`.
(While `edition` is loading it defaults to `'oss'`, so EE deployments see the three items
pop in after the `/system/info` fetch resolves — the fetch is shared/cached with
`useDeploymentMode`, so in practice this is one initial round-trip; acceptable.)

- [ ] **Step 3: Lint + typecheck**

```bash
cd frontend && pnpm --filter web lint 2>&1 | tail -3 && pnpm --filter web exec tsc --noEmit 2>&1 | tail -3
```

Expected: clean.

- [ ] **Step 4: Manual verification + commit**

Reload `/admin` against the unlicensed local backend: Authentication, Insights, and
Traces entries absent; IM still present; remaining nav works. Screenshot for the PR.

```bash
git add packages/web/components/admin/AdminSubNav.tsx
git commit -m "feat(web): hide EE nav entries outside ee edition"
```

---

### Task 9: Docs + dev-environment notes

**Files:**
- Create: `docs/site/docs/admin/editions.md`
- Modify: `backend/docs/quick-reference.md` (env-var/config section)

**Interfaces:**
- Consumes: everything above (documents the shipped behavior).

- [ ] **Step 1: Read the placeholder format**

Read `docs/dev/plans/2026-06-23-docs-overhaul.md` for the screenshot-placeholder block
format and the code-area→page mapping; use that placeholder format below verbatim if it
differs from what's shown.

- [ ] **Step 2: Write `docs/site/docs/admin/editions.md`**

```markdown
# Editions & Licensing

cubeplex ships in two editions:

- **OSS (Apache-2.0)** — the default when you self-host. Includes the full agent
  runtime, sandboxes, skills, artifacts, memory, MCP, IM connectors, and one
  shared organization with admin/member roles. Your data never leaves your
  deployment.
- **Enterprise (EE)** — adds the governance layer security reviews ask for: SSO
  (SAML/OIDC), fine-grained RBAC, persistent audit logs, trace viewer, cost
  insights, and multiple isolated organizations in one deployment.

## How the edition is determined

Set your license key in the backend configuration:

​```yaml
license:
  key: "CBX1...."
​```

or via the environment variable `CUBEPLEX_LICENSE__KEY`. The key is verified
offline (Ed25519 signature; no phone-home). With a valid key, `GET
/api/v1/system/info` reports `"edition": "ee"` plus the licensed `features`,
and the admin UI unlocks the Enterprise pages. Without one, cubeplex runs as
OSS: Enterprise pages show an "Enterprise feature" notice, and if the
Enterprise package is installed the server refuses to start rather than
running it unlicensed.

## Multiple organizations

OSS deployments in `single_tenant` mode host exactly one organization. Creating
a second organization (for example, one isolated org per client or department)
requires a license that includes the `multi_org` feature; the API returns
`403 multi_org_requires_license` otherwise.

<!-- SCREENSHOT-PLACEHOLDER: admin page showing the Enterprise feature card -->
```

- [ ] **Step 3: Add the config keys to `backend/docs/quick-reference.md`**

In the env-vars/config-keys section, add:

```markdown
- `license.key` (`CUBEPLEX_LICENSE__KEY`) — EE license key (`CBX1.…`); absent → OSS
  edition. Verified offline in `cubeplex/plugins/license.py`.
- `license.public_key_hex` (`CUBEPLEX_LICENSE__PUBLIC_KEY_HEX`) — override the embedded
  license-signing public key. **Tests/dev only** (pair with
  `scripts/dev/license_keygen.py`); never set in production.
```

- [ ] **Step 4: Commit**

```bash
git add docs/site/docs/admin/editions.md backend/docs/quick-reference.md
git commit -m "docs: editions & licensing page + license config reference"
```

---

### Task 10: Pre-PR sweep

- [ ] **Step 1: Full backend check**

```bash
cd backend && uv run mypy cubeplex 2>&1 | tee tmp/sweep-mypy.log | tail -2
uv run pytest tests/unit tests/e2e --no-cov 2>&1 | tee tmp/sweep-pytest.log | tail -5
```

Expected: mypy clean; test suite green. Known watch-items: any existing test asserting the
exact `/system/info` response shape, and any Playwright admin spec that exercises the now
EE-gated pages against an unlicensed local backend (`admin-insights.spec.ts` is the likely
one). If frontend e2e needs EE mode locally: generate a test keypair + key with
`scripts/dev/license_keygen.py` and set `CUBEPLEX_LICENSE__KEY` +
`CUBEPLEX_LICENSE__PUBLIC_KEY_HEX` in the backend env used for Playwright runs; note the
two env vars in the PR description so other machines can do the same.

- [ ] **Step 2: Frontend check**

```bash
cd frontend && pnpm --filter @cubeplex/core build 2>&1 | tail -2
pnpm --filter web lint 2>&1 | tail -2 && pnpm --filter web exec tsc --noEmit 2>&1 | tail -2
```

- [ ] **Step 3: Verification evidence, then PR**

Follow `/verification-before-completion`: paste the sweep tails into the task report,
attach the two screenshots (EEGate card, filtered nav). PR title: a brief description
(e.g. "Edition foundation: license keys, EE gating, multi-org cap"). One concern, one PR.
Then run the `/pr-codex-review-loop`.

---

## Self-Review (done at plan time; re-checked 2026-07-27)

- **Spec coverage** vs the OSS/EE doc checklist: license enforcement ✓ (Tasks 2–3),
  edition scaffolding `/system/info` + `useEdition` + nav filter + `EEGate` ✓ (Tasks 4,
  6–8), multi-org gate ✓ (Task 5), Apache-2.0 + `ee/` structure ✓ (Task 1), docs ✓
  (Task 9). Deferred by design: the plugin seam simplification (stage 0 — a *prerequisite*,
  see the header), SSO relocation, cost extraction, and the two audit defects in spec §12
  (follow-up plans; noted in header). IM is untouched everywhere — all-OSS per spec §2.
- **Anchors re-verified 2026-07-27:** `plugins/registry.py:54` `discover()`,
  `SystemInfoResponse` in `api/schemas/system.py:8`, `auth/singleton_org.py`
  (`org_count` present), the four admin pages, `AdminSubNav.tsx` ENTRIES array, and
  `models/sso_connection.py` / `models/external_identity.py` all still exist as this plan
  assumes. Nothing in the plan had landed at that point.
- **Known open risk:** the exact single-tenant register/onboard interplay in Task 5's
  test is verified against `test_auth_needs_onboarding.py` at execution time (Step 1
  exists for this); the invariant under test is fixed even if setup choreography needs
  adapting.
- **Type consistency:** `License`/`LicenseError`/`parse_license_key`/`load_license`/
  `has_feature`/`get_edition`/`get_features` names match across Tasks 2–5;
  `useEdition` return shape matches usage in Tasks 7–8; i18n namespace `adminLayout`
  matches `EEGate`'s `useTranslations('adminLayout')`.
