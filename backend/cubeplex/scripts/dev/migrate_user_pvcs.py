"""One-time migrator: rename pre-(workspace,user) PVCs to the new shape.

Run from the backend dir. Dry-run is the default; pass --apply to actually
rename. Ambiguous cases (user in multiple workspaces) are NOT touched and
are listed for operator manual cleanup.

    uv run python -m cubeplex.scripts.dev.migrate_user_pvcs            # dry-run
    uv run python -m cubeplex.scripts.dev.migrate_user_pvcs --apply    # do it

This script is intentionally simple and lives under cubeplex/scripts/dev/ —
it is a one-shot helper, not a long-term commitment. It's packaged under
``cubeplex`` (rather than the sibling top-level ``backend/scripts/dev``
folder) so the pure-function planner can be unit-tested via a normal
``from cubeplex.scripts.dev.migrate_user_pvcs import ...`` import.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Literal


@dataclass
class MigrationAction:
    user_id: str
    kind: Literal["rename", "manual_cleanup", "skip_no_pvc"]
    old_name: str | None = None
    new_name: str | None = None
    reason: str = ""


def build_migration_plan(
    *,
    existing_pvcs: list[str],
    memberships: dict[str, list[str]],
    pvc_prefix: str,
) -> list[MigrationAction]:
    """Return one action per user with a pre-#144 PVC claim.

    Old PVC claim shape: ``<prefix>-<sanitize(user_id)>`` (workspace-blind).
    New PVC claim shape: ``<prefix>-<sanitize("ws-{ws}-user-{user}")>``.

    Both shapes are derived from the same helpers SandboxManager uses
    (``build_legacy_user_pvc_name`` / ``build_user_pvc_name`` in
    ``cubeplex.sandbox.manager``) so we can't drift from the names the
    runtime actually mounts.

    - exactly one workspace  -> rename
    - multiple workspaces    -> manual_cleanup (ambiguous target)
    - no pre-rename PVC      -> omitted (nothing to do)
    """
    from cubeplex.sandbox.manager import build_legacy_user_pvc_name, build_user_pvc_name

    pvc_set = set(existing_pvcs)
    actions: list[MigrationAction] = []
    for user_id, workspaces in memberships.items():
        old_name = build_legacy_user_pvc_name(pvc_prefix, user_id)
        if old_name not in pvc_set:
            continue
        if len(workspaces) == 1:
            new_name = build_user_pvc_name(pvc_prefix, workspaces[0], user_id)
            actions.append(
                MigrationAction(
                    user_id=user_id,
                    kind="rename",
                    old_name=old_name,
                    new_name=new_name,
                )
            )
        else:
            actions.append(
                MigrationAction(
                    user_id=user_id,
                    kind="manual_cleanup",
                    old_name=old_name,
                    reason=(f"user belongs to {len(workspaces)} workspaces; pick one manually"),
                )
            )
    return actions


async def _fetch_memberships() -> dict[str, list[str]]:
    """Open a session, return {user_id: [workspace_id, ...]} for all users."""
    from sqlalchemy import text

    from cubeplex.db.engine import async_session_maker

    async with async_session_maker() as session:
        rows = (await session.execute(text("SELECT user_id, workspace_id FROM memberships"))).all()
    out: dict[str, list[str]] = {}
    for user_id, ws_id in rows:
        out.setdefault(user_id, []).append(ws_id)
    return out


# Operators MUST replace ``_list_pvcs`` with their own cluster-aware
# implementation before relying on this script's output. ``main_async``
# checks ``_LIST_PVCS_WIRED`` and prints a loud warning when False so a
# silent ``nothing to migrate`` can't mislead an operator into thinking
# their PVCs are already migrated.
_LIST_PVCS_WIRED = False


async def _list_pvcs() -> list[str]:
    """Return all PVC claim names in the configured namespace.

    STUB. Replace with a cluster-aware implementation (kubectl/k8s client,
    opensandbox helper, whatever your deployment uses) and flip
    ``_LIST_PVCS_WIRED = True`` so this script trusts the result. Until
    then the script announces it is unwired and refuses to act in
    ``--apply`` mode.
    """
    return []


def _apply_rename(action: MigrationAction) -> None:
    """Perform the rename in the cluster. IMPLEMENT against the same client.

    Most PVC backends don't support rename in-place — typical pattern is:
    create the new PVC bound to the same PV (reclaimPolicy=Retain), then
    delete the old PVC. Leave the concrete steps to whoever runs this; this
    is a one-shot script.
    """
    raise NotImplementedError("wire to your PVC client")


_UNWIRED_BANNER = (
    "WARNING: PVC discovery is not wired in this build (_list_pvcs returns []).\n"
    "         Replace _list_pvcs() with a cluster-aware implementation and set\n"
    "         _LIST_PVCS_WIRED = True before trusting this script's output.\n"
    "         A 'nothing to migrate' result with the stub does NOT mean your\n"
    "         old user-<id> PVCs are gone — it means the script can't see them."
)


async def main_async(*, apply: bool) -> int:
    if not _LIST_PVCS_WIRED:
        print(_UNWIRED_BANNER)
        if apply:
            print("refusing to --apply with an unwired _list_pvcs stub")
            return 2
    # Pull the PVC prefix from the same config the runtime reads, so the
    # plan's old/new claim names match what SandboxManager actually mounts.
    from cubeplex.config import config as _cfg

    pvc_prefix = _cfg.get("sandbox.volume.pvc_prefix", "cubeplex-user")
    memberships = await _fetch_memberships()
    pvcs = await _list_pvcs()
    plan = build_migration_plan(
        existing_pvcs=pvcs,
        memberships=memberships,
        pvc_prefix=pvc_prefix,
    )
    if not plan:
        print("nothing to migrate")
        return 0
    rename = [a for a in plan if a.kind == "rename"]
    manual = [a for a in plan if a.kind == "manual_cleanup"]
    print(f"plan: {len(rename)} renames, {len(manual)} manual-cleanup entries")
    for a in rename:
        print(f"  RENAME {a.old_name} -> {a.new_name}")
    for a in manual:
        print(f"  MANUAL {a.old_name} ({a.reason})")
    if not apply:
        print("dry-run: re-run with --apply to perform the renames")
        return 0
    for a in rename:
        _apply_rename(a)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="actually perform the renames")
    args = p.parse_args()
    return asyncio.run(main_async(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
