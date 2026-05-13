"""SubAgentMiddlewarePi — cubepi port of SubAgentMiddleware (M3.c.3).

Injects a ``subagent`` AgentTool that, when called by the main cubepi
agent, spawns an ephemeral inner cubepi.Agent (via
``create_cubebox_cubepi_agent``), subscribes to its event stream, translates
cubepi AgentEvents to cubebox SSE dicts via
``convert_cubepi_agent_event_to_sse``, and forwards tagged events to the
parent's ``subagent_event_queue`` ContextVar.

Hooks (per Spec B): ``tools`` only — the middleware injects one AgentTool.

CostMiddlewarePi cloning is handled lazily inside ``_execute``; the import
guard keeps this module self-contained until M3.d.1 (cost_pi) lands.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, cast

from cubepi import AgentTool, AgentToolResult, TextContent
from cubepi.middleware.base import Middleware
from loguru import logger

from cubebox.middleware.subagents import SubAgent, _SubAgentSchema, subagent_event_queue
from cubebox.prompts.subagents import SUBAGENT_PROMPT


class SubAgentMiddlewarePi(Middleware):
    """cubepi port of SubAgentMiddleware (M3.c.3).

    Hooks:
    - ``tools``: a single ``subagent`` AgentTool whose ``execute`` spawns
      an inner cubepi.Agent and streams its events back to the parent's
      event queue.

    Constructor args:
        subagent_map:
            Dict mapping subagent type keys to ``SubAgent`` spec dicts.
            A ``"general-purpose"`` fallback is always registered.
        default_provider:
            cubepi Provider to use when the spec does not specify one.
        default_model_id:
            Model ID string to pass to the inner agent when the spec does
            not override it.
        default_provider_name:
            Provider name label for the inner agent's Model object.
        shared_tools:
            Tools shared from the parent agent (excluding ``subagent`` and
            ``load_skill`` to prevent recursive spawning).
        inherited_middleware:
            Middleware list from the parent; CostMiddlewarePi entries are
            cloned with incremented depth for billing attribution.
    """

    def __init__(
        self,
        *,
        subagent_map: dict[str, SubAgent],
        default_provider: Any,
        default_model_id: str,
        default_provider_name: str,
        shared_tools: Sequence[AgentTool[Any]] = (),
        inherited_middleware: Sequence[Any] = (),
    ) -> None:
        # Ensure general-purpose fallback is always available
        if "general-purpose" not in subagent_map:
            subagent_map = dict(subagent_map)
            subagent_map["general-purpose"] = SubAgent(
                name="general-purpose",
                description="A general-purpose AI assistant",
                system_prompt="You are a helpful AI assistant.",
            )

        self._subagent_map = subagent_map
        self._default_provider = default_provider
        self._default_model_id = default_model_id
        self._default_provider_name = default_provider_name

        # Exclude self-referential tools to prevent recursive spawning
        _excluded = {"subagent", "load_skill"}
        self._shared_tools: tuple[AgentTool[Any], ...] = tuple(
            t for t in shared_tools if t.name not in _excluded
        )
        self._inherited_middleware: tuple[Any, ...] = tuple(inherited_middleware)

        self.tools: list[AgentTool[Any]] = [self._make_subagent_tool()]

    def _make_subagent_tool(self) -> AgentTool[_SubAgentSchema]:
        """Build the cubepi AgentTool that spawns inner cubepi.Agent runs."""
        available = ", ".join(f'"{k}"' for k in self._subagent_map)

        async def _execute(
            tool_call_id: str,
            args: _SubAgentSchema,
            *,
            signal: Any = None,
            on_update: Any = None,
        ) -> AgentToolResult:
            spec = self._subagent_map.get(
                args.subagent_type,
                self._subagent_map["general-purpose"],
            )
            # Use explicit casts: SubAgent is a TypedDict whose optional fields
            # return `object` from `.get()`; cast to the concrete types we need.
            from cubepi.providers.base import Provider  # noqa: PLC0415

            provider: Provider = cast(Provider, spec.get("provider") or self._default_provider)
            model_id: str = cast(str, spec.get("model_id") or self._default_model_id)
            provider_name: str = cast(str, spec.get("provider_name") or self._default_provider_name)
            system_prompt: str = spec.get("system_prompt") or ""

            # SubAgent.tools are BaseTool (langgraph); shared_tools are AgentTool (cubepi).
            # For the cubepi path only AgentTools are used — spec-level tools may be
            # AgentTool already (caller's responsibility) or absent.  Cast to Any to
            # keep mypy happy while cross-runtime tools lists evolve in M3.f.
            tools: list[AgentTool[Any]] = list(self._shared_tools) + cast(
                list[AgentTool[Any]], list(spec.get("tools", []))
            )
            middleware: list[Any] = list(self._inherited_middleware) + list(
                spec.get("middleware", [])
            )

            # Clone CostMiddlewarePi for billing depth attribution (M3.d.1).
            # Lazy import: if cost_pi is not yet defined, the isinstance check
            # simply finds nothing and cost cloning is skipped.
            try:
                from cubebox.middleware.cost_pi import CostMiddlewarePi  # noqa: PLC0415

                _cost_mw = next((m for m in middleware if isinstance(m, CostMiddlewarePi)), None)
                if _cost_mw is not None:
                    child_cost = CostMiddlewarePi(
                        org_id=_cost_mw._org_id,
                        workspace_id=_cost_mw._workspace_id,
                        user_id=_cost_mw._user_id,
                        conversation_id=_cost_mw._conversation_id,
                        parent_billing_id=_cost_mw._last_billing_id,
                        subagent_depth=_cost_mw._subagent_depth + 1,
                    )
                    middleware = [m for m in middleware if not isinstance(m, CostMiddlewarePi)] + [
                        child_cost
                    ]
            except ImportError:
                pass  # M3.d.1 not yet landed — skip cost cloning

            # Build inner agent
            from cubebox.agents.graph_pi import create_cubebox_cubepi_agent  # noqa: PLC0415

            inner = create_cubebox_cubepi_agent(
                provider=provider,
                model_id=model_id,
                provider_name=provider_name,
                system_prompt=system_prompt,
                tools=tools or None,
                # No checkpointer: subagents are ephemeral
            )
            if middleware:
                # Agent accepts middleware= at construction, not post-hoc;
                # rebuild with middleware if any are present.
                from cubepi import Agent, Model  # noqa: PLC0415

                inner = Agent(
                    provider=provider,
                    model=Model(id=model_id, provider=provider_name),
                    system_prompt=system_prompt,
                    tools=tools or [],
                    middleware=middleware,
                )

            # Subscribe to inner agent events; collect + optionally forward
            from cubebox.agents.stream_pi import (  # noqa: PLC0415
                convert_cubepi_agent_event_to_sse,
            )

            queue = subagent_event_queue.get(None)
            sa_agent_id = f"subagent:{tool_call_id}"
            subagent_events: list[dict[str, Any]] = []
            last_ai_text: list[str] = []

            def _listener(evt: Any, _signal: Any = None) -> None:
                sse_events = convert_cubepi_agent_event_to_sse(evt)
                for sse in sse_events:
                    tagged: dict[str, Any] = {**sse, "agent_id": sa_agent_id}
                    subagent_events.append(tagged)
                    if queue is not None:
                        try:
                            queue.put_nowait(("subagent", sa_agent_id, tagged))
                        except asyncio.QueueFull:
                            logger.warning(
                                "subagent_event_queue full — dropping event for {}",
                                sa_agent_id,
                            )
                        except Exception as exc:
                            logger.debug(
                                "subagent_event_queue put failed for {}: {}",
                                sa_agent_id,
                                exc,
                            )
                    if sse.get("type") == "text_delta":
                        last_ai_text.append(sse.get("delta", ""))

            inner.subscribe(_listener)

            try:
                await inner.prompt(args.prompt)
            except Exception as exc:
                logger.error(
                    "Subagent '{}' failed (tool_call_id={}): {}",
                    args.subagent_type,
                    tool_call_id,
                    exc,
                )
                return AgentToolResult(
                    content=[TextContent(text=f"[error: {exc}]")],
                    is_error=True,
                )

            final_content = "".join(last_ai_text) or "[subagent produced no output]"
            return AgentToolResult(
                content=[TextContent(text=final_content)],
                details={"subagent_events": subagent_events},
            )

        return AgentTool(
            name="subagent",
            description=(
                f"Delegate a task to a subagent. Available subagent types: {available}. "
                "Provide a name (short label), role (subagent's expertise), task (what to do), "
                "and a self-contained prompt — the subagent has no conversation context.\n\n"
                f"{SUBAGENT_PROMPT}"
            ),
            parameters=_SubAgentSchema,
            execute=_execute,
        )
