"""Password policy — pure functions, single source of truth.

The backend is authoritative for password strength. The frontend mirrors
these rules for pre-submit UX only (see @cubeplex/core passwordPolicy).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PasswordPolicy(StrEnum):
    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True)
class PasswordRules:
    min_length: int
    require_uppercase: bool = False
    require_lowercase: bool = False
    require_digit: bool = False
    require_symbol: bool = False


@dataclass(frozen=True)
class PasswordValidationResult:
    ok: bool
    errors: list[str]


LOW_RULES = PasswordRules(min_length=8)
HIGH_RULES = PasswordRules(
    min_length=10,
    require_uppercase=True,
    require_lowercase=True,
    require_digit=True,
    require_symbol=True,
)

_RULES = {
    PasswordPolicy.LOW: LOW_RULES,
    PasswordPolicy.HIGH: HIGH_RULES,
}


def _is_symbol(ch: str) -> bool:
    # Visible non-alphanumeric ASCII (0x21..0x7e excluding alnum). Space excluded.
    return 33 <= ord(ch) <= 126 and not ch.isalnum()


def validate_password(password: str, policy: PasswordPolicy) -> PasswordValidationResult:
    rules = _RULES[policy]
    errors: list[str] = []
    if len(password) < rules.min_length:
        errors.append("password_too_short")
    if rules.require_uppercase and not any(c.isupper() for c in password):
        errors.append("password_no_uppercase")
    if rules.require_lowercase and not any(c.islower() for c in password):
        errors.append("password_no_lowercase")
    if rules.require_digit and not any(c.isdigit() for c in password):
        errors.append("password_no_digit")
    if rules.require_symbol and not any(_is_symbol(c) for c in password):
        errors.append("password_no_symbol")
    return PasswordValidationResult(ok=not errors, errors=errors)


def get_password_policy() -> PasswordPolicy:
    from cubeplex.config import config

    raw = str(config.get("auth.password_policy", "high")).lower()
    try:
        return PasswordPolicy(raw)
    except ValueError:
        return PasswordPolicy.HIGH


def validate_password_from_config(password: str) -> PasswordValidationResult:
    return validate_password(password, get_password_policy())
