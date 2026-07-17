from cubeplex.sandbox_policy.rules import (
    build_network_policy,
    evaluate_command,
    split_shell_command,
)

DENY_RM = [{"action": "deny", "pattern": "rm *"}]
CONFIRM_PUSH = [{"action": "confirm", "pattern": "git push *"}]


def test_no_rules_allows() -> None:
    assert evaluate_command("rm -rf /workspace", []) == ("allow", None)


def test_deny_first_match_wins() -> None:
    action, pat = evaluate_command("rm -rf /workspace", DENY_RM)
    assert action == "deny"
    assert pat == "rm *"


def test_allow_when_no_pattern_matches() -> None:
    assert evaluate_command("ls -la", DENY_RM) == ("allow", None)


def test_precedence_deny_beats_confirm_beats_allow() -> None:
    rules = [
        {"action": "allow", "pattern": "git *"},
        {"action": "confirm", "pattern": "git push *"},
        {"action": "deny", "pattern": "git push --force *"},
    ]
    # deny wins even though allow + confirm also match, and is listed last.
    assert evaluate_command("git push --force origin main", rules)[0] == "deny"
    # confirm beats the broad allow.
    assert evaluate_command("git push origin main", rules)[0] == "confirm"
    # only the broad allow matches.
    assert evaluate_command("git status", rules)[0] == "allow"


def test_split_shell_command_operators() -> None:
    assert split_shell_command("safe && rm -rf /") == ["safe", "rm -rf /"]
    assert split_shell_command("safe; denied") == ["safe", "denied"]
    assert split_shell_command("a | b") == ["a", "b"]


def test_substitution_subcommands_are_extracted() -> None:
    assert "denied" in split_shell_command("echo $(denied)")
    assert "denied" in split_shell_command("echo `denied`")


def test_chaining_cannot_smuggle_a_denied_command() -> None:
    # Every sub-command must pass; a denied sub-command denies the whole call.
    assert evaluate_command("safe && rm -rf /", DENY_RM)[0] == "deny"
    assert evaluate_command("ls; rm x", DENY_RM)[0] == "deny"
    assert evaluate_command("echo $(rm x)", DENY_RM)[0] == "deny"


def test_shell_grouping_cannot_hide_a_denied_command() -> None:
    """Regression for codex P1 r3317630106: `(rm -rf /)`, `{ rm; }`,
    `if …; then rm …; fi` and friends must be normalized so the rule glob
    still matches the denied command underneath."""
    assert evaluate_command("(rm -rf /workspace)", DENY_RM)[0] == "deny"
    assert evaluate_command("{ rm -rf /workspace; }", DENY_RM)[0] == "deny"
    assert evaluate_command("if true; then rm -rf /workspace; fi", DENY_RM)[0] == "deny"
    assert evaluate_command("for f in *; do rm $f; done", DENY_RM)[0] == "deny"
    assert evaluate_command("exec rm -rf /workspace", DENY_RM)[0] == "deny"
    assert evaluate_command("time rm -rf /workspace", DENY_RM)[0] == "deny"
    # Sanity: the same glob still passes through innocuous compound commands.
    assert evaluate_command("(echo ok)", DENY_RM)[0] == "allow"
    assert evaluate_command("if true; then echo ok; fi", DENY_RM)[0] == "allow"


def test_confirm_subcommand_propagates_when_no_deny() -> None:
    assert evaluate_command("ls && git push origin", CONFIRM_PUSH)[0] == "confirm"


def _egress(p):
    return [(r.action, r.target) for r in (p.egress or [])]


def test_allow_mode_exact_allow_beats_wildcard_deny() -> None:
    # default allow; block all of github EXCEPT api.github.com.
    p = build_network_policy(
        admin_rules=[
            {"action": "deny", "target": "*.github.com"},
            {"action": "allow", "target": "api.github.com"},
        ],
        default_action="allow",
        force_allow_hosts=[],
    )
    assert p.default_action == "allow"
    # exact host (more specific) must be emitted before the wildcard so the
    # sidecar's first-match resolves api.github.com to allow.
    assert _egress(p)[0] == ("allow", "api.github.com")
    assert ("deny", "*.github.com") in _egress(p)


def test_deny_mode_exact_deny_beats_wildcard_allow() -> None:
    # default deny; allow all of github EXCEPT secret.github.com.
    p = build_network_policy(
        admin_rules=[
            {"action": "allow", "target": "*.github.com"},
            {"action": "deny", "target": "secret.github.com"},
        ],
        default_action="deny",
        force_allow_hosts=[],
    )
    assert p.default_action == "deny"
    assert _egress(p)[0] == ("deny", "secret.github.com")
    assert ("allow", "*.github.com") in _egress(p)


def test_deeper_wildcard_sorts_before_shallower() -> None:
    p = build_network_policy(
        admin_rules=[
            {"action": "allow", "target": "*.github.com"},
            {"action": "deny", "target": "*.api.github.com"},
        ],
        default_action="allow",
        force_allow_hosts=[],
    )
    assert _egress(p)[0] == ("deny", "*.api.github.com")


def test_force_allow_host_present_and_beats_wildcard_deny() -> None:
    p = build_network_policy(
        admin_rules=[{"action": "deny", "target": "*.internal"}],
        default_action="deny",
        force_allow_hosts=["egress-exchange.internal"],
    )
    # exact forced-allow host (1,2) beats the wildcard deny (0,1).
    assert _egress(p)[0] == ("allow", "egress-exchange.internal")


def test_force_allow_wins_over_admin_exact_deny_same_host() -> None:
    # An admin deny on the exchange host itself must not strand substitution.
    p = build_network_policy(
        admin_rules=[{"action": "deny", "target": "x.host.com"}],
        default_action="deny",
        force_allow_hosts=["x.host.com"],
    )
    assert _egress(p)[0] == ("allow", "x.host.com")


def test_emitted_targets_are_normalized_trailing_dot_and_case() -> None:
    # Trailing-dot / mixed-case targets must be normalized in the EMITTED rule,
    # not just the sort key — the sidecar matches patterns verbatim (lowercase
    # only, no trailing-dot strip), so a raw "api.github.com." would never match.
    p = build_network_policy(
        admin_rules=[
            {"action": "allow", "target": "API.GitHub.com."},
            {"action": "deny", "target": "*.EVIL.com."},
        ],
        default_action="deny",
        force_allow_hosts=[],
    )
    targets = {(r.action, r.target) for r in (p.egress or [])}
    assert ("allow", "api.github.com") in targets
    assert ("deny", "*.evil.com") in targets


def test_blank_targets_are_dropped() -> None:
    p = build_network_policy(
        admin_rules=[{"action": "deny", "target": ""}],
        default_action="allow",
        force_allow_hosts=[],
    )
    assert _egress(p) == []
