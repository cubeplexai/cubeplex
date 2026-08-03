# CubePlex Enterprise (`backend/ee/`)

Everything under `backend/ee/` is **not** Apache-2.0. It is source-visible but governed by
[`ee/LICENSE`](./LICENSE): production use requires a valid CubePlex Enterprise license
key (delivered as `CUBEPLEX_LICENSE__KEY`; verified offline by
`backend/cubeplex/plugins/license.py`).

This directory builds a single Python distribution, `cubeplex-ee`, exposing the
top-level package `cubeplex_ee`. It is installed alongside the OSS package and
implements the Protocols in `backend/cubeplex/plugins/protocols.py`; app startup
imports it if present and hands it the parsed license. Installing or removing it never
modifies OSS code. Planned residents: SSO (SAML/OIDC), fine-grained RBAC, persistent
audit sinks, trace viewer, cost reporting, and multi-org support.

The rest of this repository (outside `backend/ee/`) is Apache-2.0 — see the root
[`LICENSE`](../../LICENSE).

## Status

No EE code lives here yet. This directory currently carries only the license boundary,
planted ahead of the relocations so that every later move lands on a settled licensing
story rather than establishing one mid-surgery. The distribution's `pyproject.toml`
arrives with the first EE feature (stage 2 — cost extraction).

Design and staging: [`docs/dev/specs/2026-07-07-oss-ee-split-design.md`](../../docs/dev/specs/2026-07-07-oss-ee-split-design.md).

## Installing it for development

```bash
cd backend && uv pip install -e ee
```

Declared as the `licensed` dependency group in `backend/pyproject.toml`, not as
an optional extra — `uv sync --all-extras` must not pull it in, or the import
gate in `cubeplex/plugins/ee.py` has nothing left to gate. Details in
`backend/docs/quick-reference.md`.
