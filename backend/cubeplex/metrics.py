"""Prometheus metrics for the backend process and its execution paths."""

from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator, metrics
from prometheus_fastapi_instrumentator.metrics import Info
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "cubeplex_http_requests_in_flight",
    "HTTP requests currently being served by this process.",
)
SSE_CONNECTIONS_ACTIVE = Gauge(
    "cubeplex_sse_connections_active",
    "SSE responses currently open in this process.",
)
SSE_CONNECTIONS_OPENED = Counter(
    "cubeplex_sse_connections_opened_total",
    "SSE responses opened by this process.",
)
AGENT_RUN_ATTEMPTS_ACTIVE = Gauge(
    "cubeplex_agent_run_attempts_active",
    "Agent execution tasks active in this process, including their cleanup.",
)
AGENT_RUN_ATTEMPTS_STARTED = Counter(
    "cubeplex_agent_run_attempts_started_total",
    "Agent execution tasks started in this process.",
    ("trigger", "phase"),
)
AGENT_RUN_ATTEMPT_DURATION = Histogram(
    "cubeplex_agent_run_attempt_duration_seconds",
    "Time from scheduling an agent task through cleanup, excluding time paused for HITL.",
    buckets=(1, 2, 5, 10, 30, 60, 120, 300, 900, 1800, 3600),
)
AGENT_RUNS_FINISHED = Counter(
    "cubeplex_agent_runs_finished_total",
    "Durable terminal run outcomes recorded by this process.",
    ("outcome",),
)
AGENT_HITL_PAUSES = Counter(
    "cubeplex_agent_hitl_pauses_total",
    "Agent run attempts that reached a HITL pause.",
)
LLM_CALLS = Counter(
    "cubeplex_llm_calls_total",
    "LLM billing events committed by this process.",
    ("outcome",),
)
LLM_TOKENS = Counter(
    "cubeplex_llm_tokens_total",
    "Token usage recorded in committed LLM billing events.",
    ("direction",),
)
TOOL_CALLS = Counter(
    "cubeplex_tool_calls_total",
    "Completed tool calls, grouped into fixed categories.",
    ("category", "outcome"),
)


def run_trigger_label(trigger: str) -> str:
    return trigger if trigger in {"interactive", "im", "automated"} else "other"


def record_llm_call(outcome: str, usage: dict[str, int] | None = None) -> None:
    if outcome not in {"success", "fallback_failed"}:
        raise ValueError(f"unsupported LLM outcome: {outcome}")
    LLM_CALLS.labels(outcome=outcome).inc()
    if usage is None:
        return
    for direction in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        count = usage.get(direction, 0)
        if count > 0:
            LLM_TOKENS.labels(direction=direction.removesuffix("_tokens")).inc(count)


def record_tool_call(name: str, *, is_error: bool) -> None:
    if name in {"execute", "monitor", "kill_execute", "read", "write", "edit", "sandbox_config"}:
        category = "sandbox"
    elif name == "subagent":
        category = "subagent"
    elif name == "load_skill":
        category = "skill"
    elif name.startswith("mcp__"):
        category = "mcp"
    else:
        category = "other"
    TOOL_CALLS.labels(category=category, outcome="error" if is_error else "success").inc()


class ConnectionMetricsMiddleware:
    """Count in-flight requests and SSE streams without buffering their bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if path == "/metrics" or path.startswith("/health/"):
            await self.app(scope, receive, send)
            return

        HTTP_REQUESTS_IN_FLIGHT.inc()
        streaming = False

        async def send_wrapper(message: Message) -> None:
            nonlocal streaming
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                streaming = any(
                    key.lower() == b"content-type" and value.startswith(b"text/event-stream")
                    for key, value in headers
                )
                if streaming:
                    SSE_CONNECTIONS_ACTIVE.inc()
                    SSE_CONNECTIONS_OPENED.inc()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if streaming:
                SSE_CONNECTIONS_ACTIVE.dec()
            HTTP_REQUESTS_IN_FLIGHT.dec()


_http_requests = metrics.requests(metric_namespace="cubeplex")
_http_latency = metrics.latency(metric_namespace="cubeplex", should_include_status=False)


def _record_http_latency(info: Info) -> None:
    if _http_latency is None:
        return
    content_type = info.response.headers.get("content-type", "") if info.response else ""
    if not content_type.startswith("text/event-stream"):
        _http_latency(info)


instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    excluded_handlers=[r"^/metrics$", r"^/health/"],
)
if _http_requests is not None:
    instrumentator.add(_http_requests)
instrumentator.add(_record_http_latency)
