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
from typing import Any, Literal

from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule

CommandAction = Literal["deny", "confirm", "allow"]
_ACTION_RANK: dict[str, int] = {"deny": 0, "confirm": 1, "allow": 2}

# Operators that chain or compose separate commands. Split on these to inspect
# each constituent command independently.
_CHAIN_SPLIT = re.compile(r"&&|\|\||[;\n|&]")
# $(...) and `...` command substitutions.
_SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def split_shell_command(command: str) -> list[str]:
    """Split a command line into the constituent commands a shell would run.

    Handles &&, ||, ;, |, & and newline chaining, plus $(...) / backtick
    substitutions. Returns trimmed non-empty fragments. Best-effort: the goal
    is "no denied command hides inside a chain", not a full shell parser.
    """
    pieces: list[str] = []
    remaining = command

    def _pull_substitutions(text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            if inner and inner.strip():
                pieces.append(inner.strip())
            return " "

        return _SUBST.sub(repl, text)

    remaining = _pull_substitutions(remaining)
    for frag in _CHAIN_SPLIT.split(remaining):
        frag = frag.strip()
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


def merge_network_rules(
    base: NetworkPolicy, admin_rules: list[dict[str, Any]] | None
) -> NetworkPolicy:
    """Compose the vault-derived NetworkPolicy with admin-authored rules.

    Union of allow targets; admin ``deny`` rules win (remove the target from
    allow and add an explicit deny). default_action stays ``deny``.
    """
    base_egress = base.egress or []
    allows: set[str] = {r.target for r in base_egress if r.action == "allow"}
    denies: set[str] = {r.target for r in base_egress if r.action == "deny"}
    for rule in admin_rules or []:
        action = str(rule.get("action", ""))
        target = str(rule.get("target", ""))
        if not target:
            continue
        if action == "deny":
            denies.add(target)
            allows.discard(target)
        elif action == "allow":
            if target not in denies:
                allows.add(target)
    # Emit DENY rules FIRST. The sidecar evaluates `egress` in order, so listing
    # denies ahead of allows makes deny win even when a deny and an allow target
    # OVERLAP via wildcards (e.g. allow=api.evil.com, deny=*.evil.com) — exact-set
    # removal above can't catch wildcard overlap, so ordering is what guarantees
    # deny-wins. Targets are FQDN/wildcard only (OpenSandbox `NetworkRule.target`);
    # the policy service must reject regex/`*`-only targets at write time
    # (validate_host_pattern already does — see Task 3 test
    # `test_service_rejects_bad_network_target`).
    egress = [NetworkRule(action="deny", target=t) for t in sorted(denies)]
    egress += [NetworkRule(action="allow", target=t) for t in sorted(allows)]
    return NetworkPolicy(defaultAction="deny", egress=egress)
