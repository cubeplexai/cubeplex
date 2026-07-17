"""Write-time screen for shared memory. Personal memory bypasses this.

Rule-based first pass — false positives are fine (user can rephrase),
false negatives are caught by render-time trust marking + execution-time
gates. See spec §Trust Model.
"""

import re


class MemoryScreenError(ValueError):
    """Raised when shared-memory content fails the adversarial screen."""


# Patterns are intentionally conservative.
_DESTRUCTIVE_CMD = re.compile(
    r"\b(rm\s+-rf|drop\s+table|truncate\s+table|mkfs\b|"
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;:|"
    r"format\s+[a-z]:|del\s+/[sf]|"
    r"shutdown\s|reboot\s|halt\s)",
    re.IGNORECASE,
)
_SECRET_EXFIL = re.compile(
    r"(\.env\b|credentials?\.|/\.aws/|secrets?\.|access[_\s]?token|"
    r"vault\s+(read|kv|get))",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"(ignore\s+(previous|prior|all)\s+(instructions?|rules?)|"
    r"you\s+are\s+now\b|"
    r"system\s*:|<\s*system\s*>|"
    r"forget\s+(everything|all|previous))",
    re.IGNORECASE,
)
_OTHER_USER_TARGETING = re.compile(
    r"when\s+(?:@?\w+|user|colleague|teammate)\s+(?:asks?|requests?|inquires?)",
    re.IGNORECASE,
)


def screen_shared_content(content: str) -> None:
    if _DESTRUCTIVE_CMD.search(content):
        raise MemoryScreenError(
            "content contains a destructive command pattern; reword without "
            "embedding the literal command"
        )
    if _SECRET_EXFIL.search(content):
        raise MemoryScreenError(
            "content references secret-bearing paths; do not put secret "
            "instructions in shared memory"
        )
    if _INJECTION.search(content):
        raise MemoryScreenError(
            "content matches a prompt-injection pattern; rephrase as a fact, "
            "not as an instruction to override behavior"
        )
    if _OTHER_USER_TARGETING.search(content):
        raise MemoryScreenError(
            "content appears to target other users; shared memory describes "
            "the workspace, not how to handle specific people's questions"
        )
