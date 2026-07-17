"""Runtime status writeback — self-healing provider/model status (spec §4.4a).

Probes (Task 10) are point-in-time: a key gets revoked or a model retired
between probes, so the persisted ``providers.last_liveness_*`` /
``models.last_test_*`` columns drift from reality. This module flips them back
to the truth observed during a *real* agent LLM call, so the readiness helper
(Task 5) self-heals the UI without a manual re-test.

Discipline (mirrors MCP's "refresh failure flips authed=false"):

- **Out-of-band.** Every write is scheduled via :func:`asyncio.create_task` on
  a SEPARATE DB session (``async_session_maker``), never the live request's
  session. The live request is never blocked, delayed, or failed by a write.
- **Best-effort.** Any error in the background task is swallowed + logged.
- **Provider liveness is provider-grain; model unavailability is model-grain.**
  An auth (401/403) error flips the provider; a model_not_found (404) error
  flips only that one model, leaving siblings untouched.
- **Success clears via a guarded conditional UPDATE** — only a currently-failed
  provider is flipped back to "ok"; otherwise a cheap no-op (no read needed).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db.engine import async_session_maker
from cubeplex.models.provider import Model, Provider
from cubeplex.services.provider_probe import (
    _error_says_auth_failure,
    _error_says_model_not_found,
    _probe_error,
)

logger = logging.getLogger(__name__)

# What a classified runtime exception maps to.
RuntimeOutcome = Literal["auth_error", "model_not_found", "other"]

# Strong refs to in-flight background tasks. asyncio only keeps a weak ref to a
# task, so a fire-and-forget task with no other referent can be GC'd mid-run.
_inflight: set[asyncio.Task[None]] = set()


def classify_runtime_error(exc: BaseException) -> RuntimeOutcome:
    """Map a raised exception to a writeback outcome.

    Reuses ``provider_probe``'s classifiers (status-code + marker checks) so the
    auth / model-not-found marker lists live in exactly one place. Auth is
    checked first: a 401/403 is a provider-credential problem regardless of any
    model name in the message.
    """
    if not isinstance(exc, Exception):
        return "other"
    err = _probe_error(exc)
    if _error_says_auth_failure(err):
        return "auth_error"
    if _error_says_model_not_found(err):
        return "model_not_found"
    return "other"


async def _write_auth_failure(session: AsyncSession, *, provider_id: str, summary: str) -> None:
    """Flip a provider's liveness to "fail" (provider-grain)."""
    now = datetime.now(UTC)
    await session.execute(
        update(Provider)
        .where(Provider.id == provider_id)  # type: ignore[arg-type]
        .values(
            last_liveness_status="fail",
            last_liveness_at=now,
            last_liveness_summary={"source": "runtime", "detail": summary[:200]},
        )
    )
    await session.commit()


async def _write_model_unavailable(
    session: AsyncSession, *, provider_id: str, model_id: str, summary: str
) -> None:
    """Flip one model's test status to "unavailable" (model-grain)."""
    now = datetime.now(UTC)
    await session.execute(
        update(Model)
        .where(
            Model.provider_id == provider_id,  # type: ignore[arg-type]
            Model.model_id == model_id,  # type: ignore[arg-type]
        )
        .values(
            last_test_status="unavailable",
            last_test_at=now,
            last_test_summary={"source": "runtime", "detail": summary[:200]},
        )
    )
    await session.commit()


async def _clear_provider_liveness(session: AsyncSession, *, provider_id: str) -> None:
    """Guarded conditional UPDATE: flip a *currently-failed* provider back to "ok".

    Idempotent and cheap — the ``last_liveness_status == 'fail'`` predicate makes
    this a no-op for healthy providers, so no read is needed before writing.
    """
    now = datetime.now(UTC)
    await session.execute(
        update(Provider)
        .where(
            Provider.id == provider_id,  # type: ignore[arg-type]
            Provider.last_liveness_status == "fail",  # type: ignore[arg-type]
        )
        .values(
            last_liveness_status="ok",
            last_liveness_at=now,
            last_liveness_summary={"source": "runtime", "detail": "runtime call succeeded"},
        )
    )
    await session.commit()


async def _resolve_provider_id(
    session: AsyncSession, *, org_id: str, provider_slug: str
) -> str | None:
    """Resolve ``provider_slug`` (slug / ``Provider.slug``) to a DB id.

    Scoped to the org plus system-level (org_id IS NULL) providers — the same
    visibility rule the run path used to load the provider config.
    """
    from cubeplex.repositories.provider import ProviderRepository

    repo = ProviderRepository(session, org_id=org_id)
    provider = await repo.get_by_slug(provider_slug)
    return provider.id if provider is not None else None


async def _do_writeback(
    *,
    org_id: str,
    provider_slug: str,
    model_id: str,
    outcome: RuntimeOutcome,
    summary: str,
) -> None:
    """The actual background work. Opens its own session; swallows nothing here —
    the caller's task wrapper logs + swallows so failures never escape."""
    async with async_session_maker() as session:
        provider_id = await _resolve_provider_id(
            session, org_id=org_id, provider_slug=provider_slug
        )
        if provider_id is None:
            # Config-only provider (no DB row) — nothing to write back to.
            return
        if outcome == "auth_error":
            await _write_auth_failure(session, provider_id=provider_id, summary=summary)
        elif outcome == "model_not_found":
            await _write_model_unavailable(
                session, provider_id=provider_id, model_id=model_id, summary=summary
            )
        else:  # "other" — a successful call clears a stale liveness fail.
            await _clear_provider_liveness(session, provider_id=provider_id)


def schedule_runtime_status_writeback(
    *,
    org_id: str,
    provider_slug: str,
    model_id: str,
    exc: BaseException | None,
) -> asyncio.Task[None] | None:
    """Fire-and-forget the status writeback for a real LLM call.

    Pass ``exc=None`` on the success path (schedules the guarded liveness clear);
    pass the raised exception on the failure path (auth → provider fail,
    model_not_found → model unavailable; any other error is ignored).

    Returns the scheduled task (for tests) or ``None`` when there's nothing to do.
    NEVER raises — scheduling and the work itself are fully insulated from the
    live request.
    """
    try:
        if exc is None:
            outcome: RuntimeOutcome = "other"
            summary = "runtime call succeeded"
        else:
            outcome = classify_runtime_error(exc)
            if outcome == "other":
                # Not an auth / model error — don't touch persisted status on a
                # transient network/timeout/server error.
                return None
            summary = f"{type(exc).__name__}: {exc}"

        async def _runner() -> None:
            try:
                await _do_writeback(
                    org_id=org_id,
                    provider_slug=provider_slug,
                    model_id=model_id,
                    outcome=outcome,
                    summary=summary,
                )
            except Exception:  # noqa: BLE001 — best-effort, never propagate
                logger.warning(
                    "runtime status writeback failed (provider=%s model=%s outcome=%s)",
                    provider_slug,
                    model_id,
                    outcome,
                    exc_info=True,
                )

        task = asyncio.create_task(_runner())
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)
        return task
    except Exception:  # noqa: BLE001 — scheduling itself must never break the request
        logger.warning("failed to schedule runtime status writeback", exc_info=True)
        return None
