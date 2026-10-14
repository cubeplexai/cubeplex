"""Configuration-bound runtime observations from actual provider calls.

A separate best-effort transaction ignores obsolete calls and preserves full
probe evidence. Provider response listeners identify the actual fallback member;
agent tool errors are not model-health observations.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlmodel import col

from cubeplex.db.engine import async_session_maker
from cubeplex.llm.readiness import configuration_fingerprint, provider_configuration_fingerprint
from cubeplex.models.credential import Credential
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


@dataclass(frozen=True)
class RuntimeObservation:
    org_id: str | None
    provider_id: str
    model_db_id: str
    configuration_fingerprint: str
    provider_fingerprint: str
    provider_evidence_revision: int
    model_evidence_revision: int
    provider_probe_generation: int
    model_probe_generation: int


async def _do_writeback(*, observation: RuntimeObservation, outcome: RuntimeOutcome) -> None:
    async with async_session_maker() as session:
        provider = await session.scalar(
            select(Provider)
            .where(
                col(Provider.id) == observation.provider_id,
            )
            .with_for_update()
        )
        if provider is None or provider.org_id != observation.org_id:
            return
        credential = None
        if provider.credential_id:
            credential = await session.scalar(
                select(Credential)
                .where(
                    col(Credential.id) == provider.credential_id,
                )
                .with_for_update()
            )
        revision = credential.value_revision if credential else 0
        model = await session.scalar(
            select(Model)
            .where(
                col(Model.id) == observation.model_db_id,
                col(Model.provider_id) == provider.id,
            )
            .with_for_update()
        )
        if model is None:
            return
        if (
            configuration_fingerprint(provider, model, revision)
            != observation.configuration_fingerprint
            or provider_configuration_fingerprint(provider, revision)
            != observation.provider_fingerprint
            or provider.evidence_revision != observation.provider_evidence_revision
            or model.evidence_revision != observation.model_evidence_revision
            or provider.probe_generation != observation.provider_probe_generation
            or model.probe_generation != observation.model_probe_generation
        ):
            return
        now = datetime.now(UTC)
        if outcome == "auth_error":
            provider.evidence_revision += 1
            provider.last_liveness_status = "auth_error"
            provider.last_liveness_at = now
            provider.last_liveness_summary = {
                "source": "runtime",
                "detail": "Provider credential rejected",
                "configuration_fingerprint": observation.provider_fingerprint,
                "evidence_revision": provider.evidence_revision,
            }
            session.add(provider)
        elif outcome == "model_not_found":
            model.evidence_revision += 1
            model.last_test_summary = {
                **(model.last_test_summary or {}),
                "runtime_observation": {
                    "source": "runtime",
                    "status": "unavailable",
                    "observed_at": now.isoformat(),
                    "configuration_fingerprint": observation.configuration_fingerprint,
                },
            }
            session.add(model)
        else:
            if provider.last_liveness_status in {"fail", "auth_error"}:
                provider.evidence_revision += 1
                provider.last_liveness_status = "ok"
                provider.last_liveness_at = now
                provider.last_liveness_summary = {
                    "source": "runtime",
                    "detail": "Runtime call succeeded",
                    "configuration_fingerprint": observation.provider_fingerprint,
                    "evidence_revision": provider.evidence_revision,
                }
                session.add(provider)
            runtime = (model.last_test_summary or {}).get("runtime_observation", {})
            if runtime.get("status") == "unavailable":
                model.evidence_revision += 1
                model.last_test_summary = {
                    **(model.last_test_summary or {}),
                    "runtime_observation": {
                        "source": "runtime",
                        "status": "ok",
                        "observed_at": now.isoformat(),
                        "configuration_fingerprint": observation.configuration_fingerprint,
                    },
                }
                session.add(model)
        await session.commit()


def schedule_runtime_status_writeback(
    *,
    observation: RuntimeObservation,
    exc: BaseException | None,
) -> asyncio.Task[None] | None:
    try:
        outcome: RuntimeOutcome = "other" if exc is None else classify_runtime_error(exc)
        if exc is not None and outcome == "other":
            return None

        async def runner() -> None:
            try:
                await _do_writeback(observation=observation, outcome=outcome)
            except Exception:
                logger.warning(
                    "Runtime status writeback failed for provider %s",
                    observation.provider_id,
                    exc_info=True,
                )

        task = asyncio.create_task(runner())
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)
        return task
    except Exception:
        logger.warning("Could not schedule runtime status writeback", exc_info=True)
        return None
