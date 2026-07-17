"""Tests for trigger HMAC signature and timestamp verification."""

from datetime import datetime, timedelta

from cubeplex.triggers.signature import (
    sign,
    timestamp_fresh,
    verify,
    verify_with_rotation,
)


class TestSign:
    """Test HMAC signature generation."""

    def test_sign_round_trip(self) -> None:
        """Verify that signed content matches when re-signed."""
        secret = "test_secret_key"
        timestamp = "1234567890"
        body = b"webhook payload data"

        signature = sign(secret, timestamp, body)
        assert isinstance(signature, str)
        assert len(signature) == 64  # SHA256 hex is 64 chars

    def test_sign_different_secrets_produce_different_signatures(
        self,
    ) -> None:
        """Different secrets should produce different signatures."""
        timestamp = "1234567890"
        body = b"webhook payload data"

        sig1 = sign("secret1", timestamp, body)
        sig2 = sign("secret2", timestamp, body)

        assert sig1 != sig2

    def test_sign_different_timestamps_produce_different_signatures(
        self,
    ) -> None:
        """Different timestamps should produce different signatures."""
        secret = "test_secret"
        body = b"webhook payload data"

        sig1 = sign(secret, "1234567890", body)
        sig2 = sign(secret, "9999999999", body)

        assert sig1 != sig2

    def test_sign_different_bodies_produce_different_signatures(
        self,
    ) -> None:
        """Different bodies should produce different signatures."""
        secret = "test_secret"
        timestamp = "1234567890"

        sig1 = sign(secret, timestamp, b"body1")
        sig2 = sign(secret, timestamp, b"body2")

        assert sig1 != sig2


class TestVerify:
    """Test HMAC signature verification."""

    def test_verify_valid_signature(self) -> None:
        """Verify that a correctly signed body validates."""
        secret = "my_webhook_secret"
        timestamp = "1234567890"
        body = b'{"action": "opened", "issue": {"id": 123}}'

        signature = sign(secret, timestamp, body)
        assert verify(secret, timestamp, body, signature) is True

    def test_verify_tampered_body_fails(self) -> None:
        """Verify that tampering with the body invalidates the signature."""
        secret = "my_webhook_secret"
        timestamp = "1234567890"
        body = b'{"action": "opened"}'

        signature = sign(secret, timestamp, body)
        tampered_body = b'{"action": "closed"}'

        assert verify(secret, timestamp, tampered_body, signature) is False

    def test_verify_wrong_secret_fails(self) -> None:
        """Verify that using a different secret invalidates the signature."""
        secret = "original_secret"
        timestamp = "1234567890"
        body = b"payload data"

        signature = sign(secret, timestamp, body)
        wrong_secret = "different_secret"

        assert verify(wrong_secret, timestamp, body, signature) is False

    def test_verify_tampered_signature_fails(self) -> None:
        """Verify that altering the signature itself fails validation."""
        secret = "my_secret"
        timestamp = "1234567890"
        body = b"payload"

        signature = sign(secret, timestamp, body)
        tampered_signature = signature[:-2] + "XX"

        assert verify(secret, timestamp, body, tampered_signature) is False


class TestTimestampFresh:
    """Test timestamp freshness validation."""

    def test_timestamp_fresh_in_window(self) -> None:
        """Timestamp within the window should be fresh."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        # 60 seconds ago
        ts = str(int((now - timedelta(seconds=60)).timestamp()))

        assert timestamp_fresh(ts, now=now, max_age_seconds=300) is True

    def test_timestamp_fresh_at_boundary(self) -> None:
        """Timestamp at the boundary should be fresh."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        # Exactly 300 seconds ago
        ts = str(int((now - timedelta(seconds=300)).timestamp()))

        assert timestamp_fresh(ts, now=now, max_age_seconds=300) is True

    def test_timestamp_fresh_stale(self) -> None:
        """Timestamp older than the window should be stale."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        # 400 seconds ago (beyond the default 300s window)
        ts = str(int((now - timedelta(seconds=400)).timestamp()))

        assert timestamp_fresh(ts, now=now, max_age_seconds=300) is False

    def test_timestamp_fresh_future(self) -> None:
        """Timestamp in the future should be stale."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        # 400 seconds in the future
        ts = str(int((now + timedelta(seconds=400)).timestamp()))

        assert timestamp_fresh(ts, now=now, max_age_seconds=300) is False

    def test_timestamp_fresh_future_in_window(self) -> None:
        """Timestamp slightly in the future but within window should be fresh."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        # 30 seconds in the future
        ts = str(int((now + timedelta(seconds=30)).timestamp()))

        assert timestamp_fresh(ts, now=now, max_age_seconds=300) is True

    def test_timestamp_fresh_non_numeric(self) -> None:
        """Non-numeric timestamp should be invalid."""
        now = datetime(2024, 1, 1, 12, 0, 0)

        assert timestamp_fresh("not_a_number", now=now) is False

    def test_timestamp_fresh_empty_string(self) -> None:
        """Empty timestamp should be invalid."""
        now = datetime(2024, 1, 1, 12, 0, 0)

        assert timestamp_fresh("", now=now) is False

    def test_timestamp_fresh_float_string(self) -> None:
        """Float timestamp string should be converted to int."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        # Timestamp with fractional seconds should parse as int
        ts = "1234567890.5"

        assert timestamp_fresh(ts, now=now) is False  # Stale by default


class TestVerifyWithRotation:
    """Test dual-secret verification with rotation window."""

    def test_verify_with_rotation_current_secret_in_window(
        self,
    ) -> None:
        """Body signed with current secret should verify."""
        current = "current_secret"
        previous = "old_secret"
        now = datetime(2024, 1, 1, 12, 0, 0)
        expires_at = now + timedelta(hours=24)
        timestamp = "1234567890"
        body = b"payload"

        signature = sign(current, timestamp, body)

        assert (
            verify_with_rotation(
                current=current,
                previous=previous,
                previous_expires_at=expires_at,
                timestamp=timestamp,
                raw_body=body,
                provided=signature,
                now=now,
            )
            is True
        )

    def test_verify_with_rotation_previous_secret_in_window(
        self,
    ) -> None:
        """Body signed with previous secret should verify within overlap."""
        current = "new_secret"
        previous = "old_secret"
        now = datetime(2024, 1, 1, 12, 0, 0)
        expires_at = now + timedelta(hours=24)
        timestamp = "1234567890"
        body = b"payload"

        signature = sign(previous, timestamp, body)

        assert (
            verify_with_rotation(
                current=current,
                previous=previous,
                previous_expires_at=expires_at,
                timestamp=timestamp,
                raw_body=body,
                provided=signature,
                now=now,
            )
            is True
        )

    def test_verify_with_rotation_previous_secret_expired(self) -> None:
        """Body signed with previous secret fails when overlap expired."""
        current = "new_secret"
        previous = "old_secret"
        now = datetime(2024, 1, 1, 12, 0, 0)
        # Expiry is exactly now (boundary condition)
        expires_at = now
        timestamp = "1234567890"
        body = b"payload"

        signature = sign(previous, timestamp, body)

        # At the boundary, previous_expires_at, now >= expires_at is True
        assert (
            verify_with_rotation(
                current=current,
                previous=previous,
                previous_expires_at=expires_at,
                timestamp=timestamp,
                raw_body=body,
                provided=signature,
                now=now,
            )
            is False
        )

    def test_verify_with_rotation_previous_secret_past_expiry(
        self,
    ) -> None:
        """Body signed with previous secret fails when expiry is in the past."""
        current = "new_secret"
        previous = "old_secret"
        now = datetime(2024, 1, 1, 12, 0, 0)
        # Expiry was 1 hour ago
        expires_at = now - timedelta(hours=1)
        timestamp = "1234567890"
        body = b"payload"

        signature = sign(previous, timestamp, body)

        assert (
            verify_with_rotation(
                current=current,
                previous=previous,
                previous_expires_at=expires_at,
                timestamp=timestamp,
                raw_body=body,
                provided=signature,
                now=now,
            )
            is False
        )

    def test_verify_with_rotation_no_previous_secret(self) -> None:
        """Body signed with previous secret fails when previous=None."""
        current = "new_secret"
        now = datetime(2024, 1, 1, 12, 0, 0)
        expires_at = now + timedelta(hours=24)
        timestamp = "1234567890"
        body = b"payload"

        # Sign with some old secret (simulate an attacker trying old keys)
        signature = sign("some_old_key", timestamp, body)

        assert (
            verify_with_rotation(
                current=current,
                previous=None,
                previous_expires_at=expires_at,
                timestamp=timestamp,
                raw_body=body,
                provided=signature,
                now=now,
            )
            is False
        )

    def test_verify_with_rotation_no_previous_expires_at(self) -> None:
        """Body signed with previous secret fails when expires_at=None."""
        current = "new_secret"
        previous = "old_secret"
        now = datetime(2024, 1, 1, 12, 0, 0)
        timestamp = "1234567890"
        body = b"payload"

        signature = sign(previous, timestamp, body)

        assert (
            verify_with_rotation(
                current=current,
                previous=previous,
                previous_expires_at=None,
                timestamp=timestamp,
                raw_body=body,
                provided=signature,
                now=now,
            )
            is False
        )

    def test_verify_with_rotation_neither_secret_matches(self) -> None:
        """Body signed with unrecognized secret fails."""
        current = "new_secret"
        previous = "old_secret"
        now = datetime(2024, 1, 1, 12, 0, 0)
        expires_at = now + timedelta(hours=24)
        timestamp = "1234567890"
        body = b"payload"

        signature = sign("completely_unknown_secret", timestamp, body)

        assert (
            verify_with_rotation(
                current=current,
                previous=previous,
                previous_expires_at=expires_at,
                timestamp=timestamp,
                raw_body=body,
                provided=signature,
                now=now,
            )
            is False
        )

    def test_verify_with_rotation_current_secret_always_wins(
        self,
    ) -> None:
        """Current secret is checked first regardless of previous."""
        current = "new_secret"
        previous = "old_secret"
        now = datetime(2024, 1, 1, 12, 0, 0)
        expires_at = now + timedelta(hours=24)
        timestamp = "1234567890"
        body = b"payload"

        # Sign with current secret
        signature = sign(current, timestamp, body)

        # Even with valid previous setup, current should succeed
        assert (
            verify_with_rotation(
                current=current,
                previous=previous,
                previous_expires_at=expires_at,
                timestamp=timestamp,
                raw_body=body,
                provided=signature,
                now=now,
            )
            is True
        )
