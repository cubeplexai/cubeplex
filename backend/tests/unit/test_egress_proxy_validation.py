"""Unit tests for egress_proxy URL validation in SandboxPolicyService."""

import pytest

from cubebox.services.sandbox_policy import (
    SandboxPolicyValidationError,
    _validate_egress_proxy,
)


def test_valid_http_proxy():
    _validate_egress_proxy("http://192.168.1.150:7892")


def test_valid_https_proxy():
    _validate_egress_proxy("https://proxy.internal:8443")


def test_rejects_socks():
    with pytest.raises(SandboxPolicyValidationError, match="http or https"):
        _validate_egress_proxy("socks5://proxy:1080")


def test_rejects_no_scheme():
    with pytest.raises(SandboxPolicyValidationError, match="http or https"):
        _validate_egress_proxy("192.168.1.150:7892")


def test_rejects_no_port():
    with pytest.raises(SandboxPolicyValidationError, match="port"):
        _validate_egress_proxy("http://192.168.1.150")


def test_rejects_no_host():
    with pytest.raises(SandboxPolicyValidationError, match="hostname"):
        _validate_egress_proxy("http://:7892")


def test_rejects_empty():
    with pytest.raises(SandboxPolicyValidationError):
        _validate_egress_proxy("")
