"""Approximate token counting for messages.

IMPORTANT: callers must pass the *view* they intend to send to the LLM
(i.e. the post-compaction projection [summary, *recent]), NOT the raw
state["messages"]. Passing raw history breaks scaling accuracy because
historical AIMessage.usage_metadata reflects the compressed view the
LLM actually saw — comparing it against an approx walked over the full
history yields a scale_factor < 1 (clamped to 1.0, scaling effectively
disabled) and also triggers needless re-compaction on stable convos.
"""

from __future__ import annotations

from langchain_core.messages import AnyMessage
from langchain_core.messages.utils import count_tokens_approximately

# 2.0 chars/token is a deliberate conservative override of the 4.0 default.
# 4.0 underestimates Chinese / CJK by ~3-4x; with our threshold of
# context_window * 0.7, underestimating means compacting too late → overflow.
# Once usage_metadata scaling kicks in (turn 2+), the value is self-corrected
# anyway — this just protects the cold start.
_CHARS_PER_TOKEN = 2.0


def approx_tokens(messages: list[AnyMessage]) -> int:
    """Approximate total tokens for a list of messages.

    Uses langchain_core.count_tokens_approximately with usage_metadata
    self-scaling enabled, so historical AIMessages with real token counts
    auto-calibrate the estimate (scale factor clamped to [1.0, 1.25]).
    """
    if not messages:
        return 0
    return int(
        count_tokens_approximately(
            messages,
            chars_per_token=_CHARS_PER_TOKEN,
            use_usage_metadata_scaling=True,
        )
    )
