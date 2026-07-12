import pytest

from cubeplex.sandbox_env.host_rules import (
    HostPatternError,
    host_matches,
    validate_host_pattern,
)


@pytest.mark.parametrize(
    "pattern",
    ["api.github.com", "*.example.com", r"/^api[0-9]+\.foo\.com$/"],
)
def test_valid_patterns_accepted(pattern):
    validate_host_pattern(pattern)  # no raise


@pytest.mark.parametrize(
    "pattern",
    [
        "*.com",
        "*.co.uk",
        "*",
        r"/github\.com/",
        r"/^.*$/",
        r"/^(api\.github\.com|api\.attacker\.net)$/",  # alternation spans two domains
        "not a host",
        "",
    ],
)
def test_invalid_patterns_rejected(pattern):
    with pytest.raises(HostPatternError):
        validate_host_pattern(pattern)


def test_regex_only_list_rejected():
    from cubeplex.sandbox_env.host_rules import validate_hosts

    with pytest.raises(HostPatternError):
        validate_hosts([r"/^api[0-9]+\.foo\.com$/"])  # no FQDN/wildcard companion
    validate_hosts([r"/^api[0-9]+\.foo\.com$/", "api.foo.com"])  # ok with companion


def test_exact_match():
    assert host_matches("api.github.com", ["api.github.com"])
    assert not host_matches("evil.com", ["api.github.com"])


def test_wildcard_matches_subdomain_only():
    assert host_matches("api.example.com", ["*.example.com"])
    assert not host_matches("example.com", ["*.example.com"])
    assert not host_matches("api.evil.com", ["*.example.com"])


def test_anchored_regex_does_not_overmatch():
    pats = [r"/^api[0-9]+\.foo\.com$/"]
    assert host_matches("api1.foo.com", pats)
    assert not host_matches("api1.foo.com.attacker.net", pats)


def test_wildcard_case_insensitive():
    """Stored pattern with uppercase letters should match lowercase request host."""
    assert host_matches("api.example.com", ["*.Example.com"])
    assert host_matches("api.EXAMPLE.COM", ["*.Example.com"])
    assert not host_matches("api.evil.com", ["*.Example.com"])


def test_regex_case_insensitive():
    """DNS is case-insensitive; an uppercase regex must still match the host."""
    assert host_matches("api.example.com", [r"/^API\.Example\.COM$/"])
    assert host_matches("API.EXAMPLE.COM", [r"/^api\.example\.com$/"])
