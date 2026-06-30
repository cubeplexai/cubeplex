from cubebox.auth.password_policy import (
    HIGH_RULES,
    LOW_RULES,
    PasswordPolicy,
    validate_password,
)


def test_low_only_checks_length():
    assert validate_password("12345678", PasswordPolicy.LOW).ok is True
    assert validate_password("1234567", PasswordPolicy.LOW).ok is False
    # low does NOT require character classes
    assert validate_password("alllowercase", PasswordPolicy.LOW).ok is True


def test_high_requires_all_classes():
    ok = "Aa1!longenough"
    assert validate_password(ok, PasswordPolicy.HIGH).ok is True

    short = validate_password("Aa1!short", PasswordPolicy.HIGH)
    assert short.ok is False
    assert "password_too_short" in short.errors

    no_upper = validate_password("aa1!longenough", PasswordPolicy.HIGH)
    assert no_upper.ok is False
    assert "password_no_uppercase" in no_upper.errors

    no_lower = validate_password("AA1!LONGENOUGH", PasswordPolicy.HIGH)
    assert "password_no_lowercase" in no_lower.errors

    no_digit = validate_password("Aa!!longenough", PasswordPolicy.HIGH)
    assert "password_no_digit" in no_digit.errors

    no_symbol = validate_password("Aa1longenough", PasswordPolicy.HIGH)
    assert "password_no_symbol" in no_symbol.errors


def test_symbol_is_visible_non_alphanumeric_ascii():
    # space is not a symbol; punctuation is
    assert validate_password("Aa1 longenough", PasswordPolicy.HIGH).ok is False
    assert validate_password("Aa1.longenough", PasswordPolicy.HIGH).ok is True


def test_empty_and_overlong():
    assert validate_password("", PasswordPolicy.LOW).ok is False
    long_pw = "Aa1!" + "x" * 200
    assert validate_password(long_pw, PasswordPolicy.HIGH).ok is True


def test_rules_constants():
    assert LOW_RULES.min_length == 8
    assert HIGH_RULES.min_length == 10
    assert HIGH_RULES.require_symbol is True
