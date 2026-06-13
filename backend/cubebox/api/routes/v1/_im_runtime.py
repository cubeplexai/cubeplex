"""Shared list-output builder for ws_im + admin_im routes.

Lifted out so the same code populates `runtime` on every IMAccountOut
regardless of scope, and so neither route reaches into the service's
private session attribute.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.im_connector import IMAccountListOut, IMAccountOut
from cubebox.models.im_connector import IMConnectorAccount
from cubebox.repositories.im_connector import (
    _RuntimeAgg,
    collect_runtime_aggregates,
)
from cubebox.services.im_connector import IMConnectorService, compute_runtime


async def build_im_list_out(
    *,
    svc: IMConnectorService,
    session: AsyncSession,
    long_conns: dict[str, Any],
    accounts: list[IMConnectorAccount],
) -> IMAccountListOut:
    """Populate ``runtime`` on every IMAccountOut.

    Uses a single batched aggregate query for the list, plus one
    credential decrypt per account for ``bot_open_id``. The service is
    only used for ``load_bot_open_id`` — the session is passed in
    directly so we never poke at the service's private attributes.
    """
    aggs = await collect_runtime_aggregates(session, account_ids=[a.id for a in accounts])
    out_rows: list[IMAccountOut] = []
    for a in accounts:
        bot_open_id = await svc.load_bot_open_id(a)
        rt = compute_runtime(
            a,
            long_conns=long_conns,
            agg=aggs.get(a.id) or _RuntimeAgg(),
            bot_open_id=bot_open_id,
        )
        out_rows.append(
            IMAccountOut(
                id=a.id,
                platform=a.platform,
                external_account_id=a.external_account_id,
                workspace_id=a.workspace_id,
                acting_user_id=a.acting_user_id,
                delivery_mode=a.delivery_mode,
                enabled=a.enabled,
                runtime=rt,
            )
        )
    return IMAccountListOut(accounts=out_rows)
