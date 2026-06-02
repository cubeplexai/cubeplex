"""System prompt fragment for the end-of-run memory reflection turn."""

REFLECTION_PROMPT: str = (
    "This run is complete. Before we finish: briefly review what happened in "
    "this conversation turn. Did the user express a preference, correction, "
    "opinion, or important fact worth remembering?\n\n"
    "If yes: call memory_save or memory_update. Check memory_search first to "
    "avoid duplicating an existing item. Use scope=personal unless the user "
    "explicitly asked to share with their team.\n\n"
    'If nothing is worth saving, reply with "done" and stop.'
)
