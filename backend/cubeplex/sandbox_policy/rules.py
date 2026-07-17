"""Pure matchers for sandbox command + network policy rules.

These functions have no I/O and no DB access: they are the single reuse
boundary shared by the admin route (validation), the manager (network merge),
and the exec middleware (command enforcement). Keep them side-effect free.

Command-rule semantics mirror Claude Code permissions: an ordered rule list is
evaluated with precedence deny > confirm > allow, first matching rule per
action-tier wins, and shell chaining cannot smuggle a denied command past an
allow rule — every sub-command of a chained/substituted command line must pass.
"""

from __future__ import annotations

import re
import shlex
from fnmatch import fnmatchcase
from typing import Any, Literal, cast

from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule

from cubeplex.sandbox_env.host_rules import canon_host

CommandAction = Literal["deny", "confirm", "allow"]
_ACTION_RANK: dict[str, int] = {"deny": 0, "confirm": 1, "allow": 2}

# Operators that chain or compose separate commands. Split on these to inspect
# each constituent command independently. Includes shell grouping characters
# `(`, `)`, `{`, `}`, `[`, `]` so commands hidden inside `(rm -rf /)` or
# `{ rm -rf /; }` get extracted as their own fragments rather than treated as
# opaque single-token strings the rule globs can never match.
_CHAIN_SPLIT = re.compile(r"&&|\|\||[;\n|&(){}\[\]]")
# $(...) and `...` command substitutions.
_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
# Shell control / loop / function-definition keywords. After splitting, a
# fragment like ``then rm -rf /`` should be reduced to ``rm -rf /`` before
# matching against the rule glob so `if … ; then rm … ; fi`,
# `for x in …; do rm …; done`, etc. can't smuggle a denied command past an
# allow rule.
_LEADING_CONTROL_KEYWORDS = (
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "for",
    "do",
    "done",
    "while",
    "until",
    "case",
    "esac",
    "select",
    "function",
    "in",
    "time",
    "exec",
    "command",
    "eval",
)
_LEADING_CONTROL = re.compile(r"^(?:" + "|".join(_LEADING_CONTROL_KEYWORDS) + r")\b\s*")


def _strip_leading_control(fragment: str) -> str:
    """Iteratively peel shell control keywords off the front of a fragment.

    ``then rm -rf /`` → ``rm -rf /``;
    ``exec time rm -rf /`` → ``rm -rf /``.
    """
    prev = ""
    cur = fragment
    while cur and cur != prev:
        prev = cur
        cur = _LEADING_CONTROL.sub("", cur).lstrip()
    return cur


def split_shell_command(command: str) -> list[str]:
    """Split a command line into the constituent commands a shell would run.

    Handles &&, ||, ;, |, & and newline chaining, $(...) / backtick
    substitutions, shell grouping (``()`` / ``{}`` / ``[]``), and peels
    leading control keywords (``if``/``then``/``do``/``exec`` …) from each
    fragment. Returns trimmed non-empty fragments. Best-effort: the goal is
    "no denied command hides inside a chain or compound command", not a
    full shell parser.
    """
    pieces: list[str] = []
    remaining = command

    def _pull_substitutions(text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            if inner and inner.strip():
                pieces.append(_strip_leading_control(inner.strip()))
            return " "

        return _SUBST.sub(repl, text)

    remaining = _pull_substitutions(remaining)
    for frag in _CHAIN_SPLIT.split(remaining):
        frag = _strip_leading_control(frag.strip())
        if frag:
            pieces.append(frag)
    return pieces


def _matches(command: str, pattern: str) -> bool:
    """Glob-match a single command against a rule pattern.

    Match against the whole command line and against the argv form so that
    both ``rm *`` (string glob) and tokenized intent are covered.
    """
    if fnmatchcase(command, pattern):
        return True
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    return bool(argv) and fnmatchcase(" ".join(argv), pattern)


def _eval_single(command: str, rules: list[dict[str, Any]]) -> tuple[CommandAction, str | None]:
    best: tuple[CommandAction, str | None] = ("allow", None)
    best_rank = _ACTION_RANK["allow"]
    for rule in rules:
        action = str(rule.get("action", "allow"))
        pattern = str(rule.get("pattern", ""))
        if action not in _ACTION_RANK or not pattern:
            continue
        if _matches(command, pattern) and _ACTION_RANK[action] < best_rank:
            best = (action, pattern)  # type: ignore[assignment]
            best_rank = _ACTION_RANK[action]
    return best


def evaluate_command(command: str, rules: list[dict[str, Any]]) -> tuple[CommandAction, str | None]:
    """Return the strictest (action, matched_pattern) over every sub-command.

    deny > confirm > allow. No rule match → ("allow", None).
    """
    subcommands = split_shell_command(command) or [command.strip()]
    strongest: tuple[CommandAction, str | None] = ("allow", None)
    strongest_rank = _ACTION_RANK["allow"]
    for sub in subcommands:
        action, pattern = _eval_single(sub, rules)
        if _ACTION_RANK[action] < strongest_rank:
            strongest = (action, pattern)
            strongest_rank = _ACTION_RANK[action]
    return strongest


def _host_specificity(target: str) -> tuple[int, int]:
    """Sort key — higher value = more specific = emitted earlier.

    The sidecar evaluates egress first-match-wins, so emitting the most
    specific rule first makes "most-specific match wins" in BOTH allow and
    deny modes. Exact FQDN beats any wildcard; among the same kind, more
    labels is more specific (``*.api.github.com`` > ``*.github.com``).
    Normalize the trailing dot / case first so it matches the sidecar's own
    host matching (and the contradiction check's canonicalization).
    """
    target = canon_host(target)
    if target.startswith("*."):
        return (0, target[2:].count(".") + 1)
    return (1, target.count(".") + 1)


def build_network_policy(
    *,
    admin_rules: list[dict[str, Any]] | None,
    default_action: str,
    force_allow_hosts: list[str],
) -> NetworkPolicy:
    """Assemble the egress policy from admin rules + default action.

    ``force_allow_hosts`` (the credential-exchange host) are prepended as
    ``allow`` rules so they survive sorting and, on an exact-specificity tie
    with an admin rule on the same host, win first-match (stable sort keeps
    the prepended rule ahead). Vault hosts are NOT included here — network
    reachability is independent of credential substitution.
    """
    rules: list[dict[str, Any]] = [{"action": "allow", "target": h} for h in force_allow_hosts]
    rules += list(admin_rules or [])
    ordered = sorted(
        (r for r in rules if str(r.get("target", "")).strip()),
        key=lambda r: _host_specificity(str(r["target"])),
        reverse=True,
    )
    egress = [
        NetworkRule(
            action=cast(Literal["allow", "deny"], str(r["action"])),
            target=canon_host(str(r["target"])),
        )
        for r in ordered
    ]
    return NetworkPolicy(
        defaultAction=cast(Literal["allow", "deny"], default_action), egress=egress
    )
