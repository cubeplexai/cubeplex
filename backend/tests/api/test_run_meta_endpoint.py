"""Tests for the API exposure of RunMeta error fields."""

from __future__ import annotations

import json

from cubeplex.api.routes.v1.conversations import _parse_error_params


def test_parse_error_params_returns_dict_when_valid_json() -> None:
    raw = json.dumps({"model": "kimi-k2.6", "tokens_in": 262014})
    parsed = _parse_error_params(raw)
    assert parsed == {"model": "kimi-k2.6", "tokens_in": 262014}


def test_parse_error_params_returns_none_for_none_input() -> None:
    assert _parse_error_params(None) is None


def test_parse_error_params_returns_none_for_empty_string() -> None:
    assert _parse_error_params("") is None


def test_parse_error_params_returns_none_for_invalid_json() -> None:
    assert _parse_error_params("not json {") is None


def test_parse_error_params_returns_none_for_non_dict_json() -> None:
    # A JSON list is valid JSON but not a dict.
    assert _parse_error_params("[1, 2, 3]") is None
