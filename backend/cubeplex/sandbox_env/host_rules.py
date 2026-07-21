"""Validation + matching for env-vault host patterns.

A pattern is one of:
  - exact FQDN:   "api.github.com"
  - wildcard:     "*.example.com"  (matches subdomains of, not, example.com)
  - regex:        "/^...$/"        (must be fully anchored)

Every pattern must resolve within a single registrable domain (eTLD+1), so a
secret can never be widened to more than one registrable domain. See spec §7.
"""

from __future__ import annotations

import re

import tldextract

# offline (bundled snapshot) + no disk cache: cache_dir=None avoids writing a
# lock under ~/.cache, which would raise OSError in read-only containers.
_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

# Characters allowed in a DNS label segment (the `[A-Za-z0-9-]` from the
# suffix check below). Module-level so the linear scan avoids re-parsing.
_LABEL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")


class HostPatternError(ValueError):
    """Raised when a host pattern is malformed or too broad."""


def _registrable(host: str) -> str | None:
    ext = _extract(host)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


def _is_regex(pattern: str) -> bool:
    return len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/")


def validate_host_pattern(pattern: str) -> None:
    """Raise HostPatternError unless the pattern is well-formed and within one eTLD+1."""
    if not pattern or pattern == "*":
        raise HostPatternError(f"empty or too-broad pattern: {pattern!r}")

    if _is_regex(pattern):
        body = pattern[1:-1]
        if not (body.startswith("^") and body.endswith("$")):
            raise HostPatternError(f"regex must be anchored with ^...$: {pattern!r}")
        try:
            re.compile(body)
        except re.error as exc:
            raise HostPatternError(f"invalid regex {pattern!r}: {exc}") from exc
        core = body[1:-1]  # strip ^ and $
        # No alternation: a top-level (or any) `|` lets the regex match more than
        # one registrable domain (e.g. /^(api\.github\.com|api\.attacker\.net)$/),
        # which would defeat the substitution boundary. Reject it outright. Host
        # variation that stays within one registrable domain can be expressed with
        # char classes / quantifiers instead. (spec §7)
        if "|" in core:
            raise HostPatternError(
                f"alternation '|' not allowed in host regex (can span domains): {pattern!r}"
            )
        # eTLD+1 boundary: the regex must END with a literal \.domain.tld so it
        # cannot match more than one registrable domain. Scanned backward
        # char-by-char (O(n)) instead of a regex: `re.search` on
        # `(?:\\.X+)+$` and its unrolled form are polynomial under retry on
        # crafted input (`\.-` repeated + a non-matching tail), and this path
        # is reachable from the workspace sandbox-env route with no length cap
        # on the pattern, so a workspace member could DoS validation.
        i = len(core)
        suffix_start: int | None = None
        while i > 0:
            # label = run of [A-Za-z0-9-] immediately before i
            j = i
            while j > 0 and core[j - 1] in _LABEL_CHARS:
                j -= 1
            if j == i:
                break  # no label here -> stop
            # the label must be preceded by a literal \. (escaped dot)
            if j < 2 or core[j - 2 : j] != "\\.":
                break
            suffix_start = j - 2
            i = j - 2
        if suffix_start is None:
            raise HostPatternError(
                f"regex must end with a literal \\.domain.tld suffix: {pattern!r}"
            )
        literal = core[suffix_start:].replace("\\.", ".").lstrip(".")
        if _registrable(literal) is None:
            raise HostPatternError(
                f"regex literal suffix is not a single registrable domain: {pattern!r}"
            )
        return

    probe = pattern[2:] if pattern.startswith("*.") else pattern
    if "*" in probe or "/" in probe or " " in probe or "." not in probe:
        raise HostPatternError(f"malformed host pattern: {pattern!r}")
    reg = _registrable(probe)
    if reg is None:
        # e.g. "*.com" -> probe "com" -> not a registrable domain -> rejected here.
        raise HostPatternError(f"not a valid host / too broad: {pattern!r}")


def validate_hosts(hosts: list[str]) -> None:
    """Validate a host list for a secret entry.

    Each pattern must be valid; and a regex-only list is rejected because a
    regex cannot be expressed as an egress allow-list rule (FQDN/wildcard only),
    so at least one FQDN/wildcard companion is required (spec §6.4).
    """
    if not hosts:
        raise HostPatternError("secret entry requires at least one host")
    for pattern in hosts:
        validate_host_pattern(pattern)
    if all(_is_regex(p) for p in hosts):
        raise HostPatternError(
            "regex-only host list needs an FQDN/wildcard companion for the allow-list"
        )


def canon_host(host: str) -> str:
    """Canonical host form for comparison/storage: trimmed, no trailing dot, lowercase.

    Mirrors the sidecar's domain matching (case-insensitive, single trailing dot
    stripped). Use this anywhere a host/FQDN/wildcard target is compared or stored.
    """
    return host.strip().removesuffix(".").lower()


def host_matches(host: str, patterns: list[str]) -> bool:
    """True if ``host`` (already lowercased/port-stripped) matches any pattern."""
    host = host.lower()
    for pattern in patterns:
        if _is_regex(pattern):
            # DNS hostnames are case-insensitive; the exact/wildcard branches
            # already lowercase, so match regex case-insensitively too.
            if re.fullmatch(pattern[1:-1], host, re.IGNORECASE):
                return True
        elif pattern.startswith("*."):
            suffix = pattern[1:].lower()  # ".example.com"
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == pattern.lower():
            return True
    return False
