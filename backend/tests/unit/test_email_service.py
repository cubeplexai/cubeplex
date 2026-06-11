import pytest

from cubebox.services.email import EmailService, LogEmailBackend


@pytest.mark.asyncio
async def test_log_backend_does_not_raise(capsys: pytest.CaptureFixture[str]) -> None:
    svc = EmailService(backend=LogEmailBackend())
    await svc.send(
        to="test@example.com",
        subject="Hello",
        template="password_reset",
        context={"reset_url": "https://example.com/reset?token=abc", "email": "test@example.com"},
    )
    captured = capsys.readouterr()
    assert "test@example.com" in captured.out
    assert "Hello" in captured.out


@pytest.mark.asyncio
async def test_send_renders_template() -> None:
    svc = EmailService(backend=LogEmailBackend())
    await svc.send(
        to="user@test.com",
        subject="Reset",
        template="password_reset",
        context={"reset_url": "https://x.com/reset?token=t", "email": "user@test.com"},
    )
