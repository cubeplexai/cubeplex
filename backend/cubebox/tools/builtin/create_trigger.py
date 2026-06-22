"""create_trigger tool — cubepi.AgentTool with auto IM-origin detection.

Factory: ``make_create_trigger_tool(...)`` returns one ``cubepi.AgentTool``.
Mirrors ``create_scheduled_task`` but for webhook triggers. The trigger has
no "fixed conversation" mode — when the trigger fires, by default it spins
up a fresh conversation each time (``new_each_time``); inside an IM
conversation it instead posts back to that IM channel
(``conversation_policy='im_channel'``).

Triggers always require a webhook secret; the tool auto-generates one and
stores it in the credential vault, returning the trigger id and the ingest
URL so the agent can paste the webhook URL to the user.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Literal

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field
from sqlalchemy import select

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.engine import async_session_maker
from cubebox.models import Trigger
from cubebox.models.conversation import Conversation
from cubebox.models.im_connector import IMThreadLink
from cubebox.models.public_id import PREFIX_TRIGGER, generate_public_id
from cubebox.repositories import MembershipRepository, TriggerRepository
from cubebox.services.credential import CredentialService
from cubebox.services.schedule_target_spec import ScheduleTargetError, TriggerTargetSpec

_DEFAULT_SOURCE_CONFIG: dict[str, Any] = {
    "signature_header": "X-Signature",
    "timestamp_header": "X-Timestamp",
    "event_id_header": "X-Event-Id",
    "max_body_bytes": 1048576,
}


class CreateTriggerArgs(BaseModel):
    """Input schema for create_trigger."""

    name: str = Field(
        min_length=1,
        max_length=128,
        description="Short human-readable label for this trigger (shown in the trigger list).",
    )
    prompt_template: str = Field(
        min_length=1,
        description=(
            "Prompt template rendered when the trigger fires. May reference webhook payload "
            "fields with {{field_name}}."
        ),
    )
    payload_fields: list[str] | None = Field(
        default=None,
        description=(
            "Names of webhook payload top-level fields the template references. "
            "Defaults to an empty list."
        ),
    )
    conversation_policy: Literal["new_each_time", "im_channel"] | None = Field(
        default=None,
        description=(
            "How fired runs are routed. Leave None to auto-derive: "
            "IM origin → 'im_channel'; otherwise → 'new_each_time'."
        ),
    )
    topic_id: str | None = Field(
        default=None,
        description=(
            "Optional topic id when conversation_policy='new_each_time'. Inherited from the "
            "current conversation's topic when omitted."
        ),
    )


def _gen_secret() -> str:
    # 32-byte URL-safe token, matches the webhook-secret strength used elsewhere.
    return secrets.token_urlsafe(32)


def make_create_trigger_tool(
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
    conversation_id: str,
    encryption_backend: EncryptionBackend,
) -> AgentTool[CreateTriggerArgs]:
    """Build the create_trigger cubepi.AgentTool bound to a run.

    A fresh DB session is opened per call. org_id / workspace_id /
    user_id / conversation_id / encryption_backend are bound at construction
    (run-scoped). The encryption backend is needed to seal the auto-generated
    webhook secret into the credential vault.
    """

    async def _execute(
        tool_call_id: str,
        args: CreateTriggerArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        async with async_session_maker() as session:
            link_stmt = select(IMThreadLink).where(
                IMThreadLink.conversation_id == conversation_id,  # type: ignore[arg-type]
                IMThreadLink.org_id == org_id,  # type: ignore[arg-type]
                IMThreadLink.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
            link = (await session.execute(link_stmt)).scalar_one_or_none()

            conversation_policy = args.conversation_policy
            topic_id = args.topic_id
            im_account_id: str | None = None
            im_channel_id: str | None = None
            im_scope_key: str | None = None
            im_scope_kind: str | None = None

            if conversation_policy is None:
                if link is not None:
                    conversation_policy = "im_channel"
                    im_account_id = link.account_id
                    im_channel_id = link.channel_id
                    im_scope_key = link.scope_key
                    im_scope_kind = link.scope_kind
                else:
                    conversation_policy = "new_each_time"
            elif conversation_policy == "im_channel":
                if link is None:
                    return _error(
                        "im_channel target requires this conversation to be bound to an "
                        "IM channel; no IMThreadLink found for the current conversation."
                    )
                im_account_id = link.account_id
                im_channel_id = link.channel_id
                im_scope_key = link.scope_key
                im_scope_kind = link.scope_kind

            if conversation_policy == "new_each_time" and topic_id is None:
                current_conv = await session.get(Conversation, conversation_id)
                if current_conv is not None and current_conv.topic_id is not None:
                    topic_id = current_conv.topic_id

            try:
                TriggerTargetSpec(
                    conversation_policy=conversation_policy,
                    topic_id=topic_id,
                    im_account_id=im_account_id,
                    im_channel_id=im_channel_id,
                    im_scope_key=im_scope_key,
                    im_scope_kind=im_scope_kind,
                ).validate()
            except ScheduleTargetError as exc:
                return _error(str(exc))

            mem_repo = MembershipRepository(session)
            role = await mem_repo.get_role(user_id=user_id, workspace_id=workspace_id)
            if role is None:
                return _error("Current user is not a member of this workspace.")

            from cubebox.repositories.credential import CredentialRepository

            cred_repo = CredentialRepository(session, org_id=org_id)
            cred_service = CredentialService(
                cred_repo,
                encryption_backend,
                org_id=org_id,
                actor_user_id=user_id,
            )

            # Pre-generate the trigger id so the credential name is bounded
            # and unique. Mirrors api/routes/v1/ws_triggers.py:create_trigger.
            trigger_id_pre = generate_public_id(PREFIX_TRIGGER)
            webhook_secret = _gen_secret()
            cred_id = await cred_service.create(
                kind="webhook_secret",
                name=f"trigger:{trigger_id_pre}",
                plaintext=webhook_secret,
            )

            trigger = Trigger(
                id=trigger_id_pre,
                name=args.name,
                enabled=True,
                source_type="webhook",
                source_config=dict(_DEFAULT_SOURCE_CONFIG),
                filter=None,
                target_type="inline",
                target_ref={"prompt_template": args.prompt_template},
                payload_fields=args.payload_fields or [],
                conversation_policy=conversation_policy,
                topic_id=topic_id,
                im_account_id=im_account_id,
                im_channel_id=im_channel_id,
                im_scope_key=im_scope_key,
                im_scope_kind=im_scope_kind,
                run_as_user_id=user_id,
                max_runs_per_minute=10,
                rate_limit_burst=20,
                rate_limit_response="429",
                current_secret_cred_id=cred_id,
            )
            trig_repo = TriggerRepository(session, org_id=org_id, workspace_id=workspace_id)
            saved = await trig_repo.add(trigger)

            result = {
                "status": "created",
                "id": saved.id,
                "name": saved.name,
                "conversation_policy": saved.conversation_policy,
                "ingest_path": f"/api/v1/triggers/{saved.id}/ingest",
                "webhook_secret": webhook_secret,
            }
            return AgentToolResult(content=[TextContent(text=json.dumps(result))])

    return AgentTool(
        name="create_trigger",
        description=(
            "Create a webhook trigger that fires `prompt_template` whenever the trigger's "
            "webhook URL receives a signed event. By default, when called inside an IM "
            "conversation the trigger posts back to that IM channel; otherwise each fire "
            "creates a fresh conversation (under the current topic when applicable). "
            "Returns the trigger id, ingest path, and the generated webhook secret — "
            "the secret is only shown once; relay it to the user immediately."
        ),
        parameters=CreateTriggerArgs,
        execute=_execute,
    )


def _error(message: str) -> AgentToolResult:
    payload = {"status": "error", "error": message}
    return AgentToolResult(
        content=[TextContent(text=json.dumps(payload))],
        is_error=True,
    )
