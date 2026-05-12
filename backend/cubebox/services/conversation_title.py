"""Conversation auto-title generation service.

The frontend calls ``POST /conversations/{id}/generate-title`` in parallel
with the first user message. This service:

- Skips when the conversation already has a title (durable first-turn gate
  that is immune to ordering races against ``send_message``'s synchronous
  ``mark_active`` write — once any title exists, auto or manual, further
  calls are no-ops).
- Calls the default LLM with the few-shot prompt in
  ``cubebox.prompts.title``.
- Sanitises and validates the LLM output, including an echo-detector that
  rejects "the model just quoted the input" failures.
- Persists the new title via an atomic SQL compare-and-set so a concurrent
  manual rename is never clobbered.
- Swallows LLM/provider errors and returns the conversation unchanged.
"""

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.llm.factory import LLMFactory
from cubebox.models import Conversation
from cubebox.prompts.title import TITLE_GENERATION_PROMPT, TITLE_PROMPT_PLACEHOLDER
from cubebox.repositories import ConversationRepository

logger = logging.getLogger(__name__)

MAX_SNIPPET_CHARS: int = 1000
MAX_TITLE_CHARS: int = 80
# Big enough for reasoning models to finish a chain-of-thought and still
# produce the actual title text, small enough to stay under the
# ``> 1024`` threshold in ``LLMFactory.create`` that would otherwise
# allocate the entire budget to ``thinking.budget_tokens``.
LLM_MAX_TOKENS: int = 1024

# Strip wrapping quotes (English + Chinese pairs), a leading "Title:" marker
# that some models emit despite the prompt, and trailing punctuation.
_LEADING_LABEL_RE = re.compile(r"^\s*(?:title|标题)\s*[:：]\s*", flags=re.IGNORECASE)
_WRAPPING_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ("《", "》"),
    ("`", "`"),
)
_TRAILING_PUNCT = "。.,，;；:：!！?？ \t\r\n"


def _extract_text(content: Any) -> str:
    """Pull plain-text content out of a LangChain AIMessage payload.

    Reasoning-capable providers (e.g. DeepSeek's anthropic-compatible
    endpoint) return ``content`` as a list of blocks like
    ``["", {"type": "thinking", "thinking": "..."}, {"type": "text",
    "text": "Real answer"}]``. Stringifying the list would treat the
    thinking block as the title; instead, collect only string elements
    and ``{type: text}`` dicts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                # Skip thinking / tool_use / image / other non-text blocks.
        return "".join(parts)
    return str(content)


def _normalise_whitespace(text: str) -> str:
    """Collapse any run of whitespace (incl. newlines) to a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _strip_wrapping_quotes(text: str) -> str:
    for left, right in _WRAPPING_QUOTE_PAIRS:
        if text.startswith(left) and text.endswith(right) and len(text) >= 2:
            text = text[len(left) : -len(right)].strip()
    return text


def _clean_title(raw: Any) -> str:
    """Normalise raw model output into a single-line title."""
    text = _normalise_whitespace(_extract_text(raw))
    text = _LEADING_LABEL_RE.sub("", text).strip()
    text = _strip_wrapping_quotes(text)
    text = text.strip(_TRAILING_PUNCT)
    return text[:MAX_TITLE_CHARS]


def _looks_like_echo(title: str, snippet: str) -> bool:
    """Reject titles that are obvious echoes of the user's input.

    The LLM failure mode we have observed is the model returning the first
    ~30 chars of the input verbatim ("Use this skill when the user:\\n").
    If a normalised title of meaningful length is a prefix of the
    normalised input, it isn't a summary.
    """
    if not title or len(title) < 6:
        return False
    norm_t = _normalise_whitespace(title).lower()
    norm_s = _normalise_whitespace(snippet).lower()
    if not norm_t or not norm_s:
        return False
    # Compare a leading window — if the title is the first chunk of the
    # input, that's an echo no matter the language.
    prefix_len = min(len(norm_t), 24)
    return norm_s.startswith(norm_t[:prefix_len])


def _build_prompt(snippet: str) -> str:
    return TITLE_GENERATION_PROMPT.replace(TITLE_PROMPT_PLACEHOLDER, snippet)


async def generate_and_apply_title(
    *,
    repo: ConversationRepository,
    session: AsyncSession,
    org_id: str,
    encryption_backend: EncryptionBackend | None,
    conversation: Conversation,
    content: str,
) -> Conversation:
    """Generate and persist an auto-title for ``conversation``.

    Best-effort and idempotent. Returns the (possibly updated) conversation.
    """
    original_title = conversation.title

    # Durable first-turn gate. The frontend already restricts the call to
    # the first turn, but a retry, a direct API call, or a race against
    # ``send_message``'s ``mark_active`` could still arrive late. Gating on
    # the title state — rather than ``has_messages`` — avoids that race
    # while still preventing repeated auto-retitles of an already-named
    # conversation.
    if original_title != "":
        return conversation

    snippet = (content or "").strip()[:MAX_SNIPPET_CHARS]
    if not snippet:
        return conversation

    factory = LLMFactory(
        session=session,
        org_id=org_id,
        encryption_backend=encryption_backend,
    )
    try:
        llm = await factory.create_default(temperature=0, max_tokens=LLM_MAX_TOKENS)
    except Exception:
        logger.warning("Auto-title skipped: no usable LLM provider")
        return conversation

    try:
        # Single user message instead of system+human: prevents the "echo
        # the first chunk of the input as the title" failure we observed
        # with several OpenAI-compatible providers when the system block
        # was long and the user content was long.
        result = await llm.ainvoke([HumanMessage(content=_build_prompt(snippet))])
    except Exception:
        logger.warning("Auto-title skipped: LLM call failed", exc_info=True)
        return conversation

    title = _clean_title(result.content)
    if not title:
        return conversation

    if _looks_like_echo(title, snippet):
        logger.info(
            "Auto-title rejected as input echo: title=%r snippet_prefix=%r",
            title[:40],
            snippet[:40],
        )
        return conversation

    updated = await repo.update_title_if_current(conversation.id, title, original_title)
    return updated or conversation
