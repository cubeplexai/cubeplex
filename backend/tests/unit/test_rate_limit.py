"""Tests for HTTP rate-limit client identification."""

from starlette.requests import Request

from cubeplex.api.middleware.rate_limit import limiter


def test_rate_limit_client_uses_original_forwarded_address() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [(b"x-forwarded-for", b"198.51.100.10, 10.0.0.5")],
            "client": ("10.0.0.5", 12345),
        }
    )

    assert limiter._key_func(request) == "198.51.100.10"


def test_rate_limit_client_uses_peer_address_without_forwarded_header() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": ("198.51.100.11", 12345),
        }
    )

    assert limiter._key_func(request) == "198.51.100.11"
