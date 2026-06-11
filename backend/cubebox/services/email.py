"""Pluggable email service with log/smtp/resend backends."""

from __future__ import annotations

import abc
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from loguru import logger

from cubebox.config import config

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


class EmailBackend(abc.ABC):
    @abc.abstractmethod
    async def send(self, *, to: str, subject: str, html: str, text: str) -> None: ...


class LogEmailBackend(EmailBackend):
    async def send(self, *, to: str, subject: str, html: str, text: str) -> None:
        logger.info("Email to={} subject={}", to, subject)
        print(f"--- EMAIL to={to} subject={subject} ---\n{text}\n--- END ---")


class SmtpEmailBackend(EmailBackend):
    async def send(self, *, to: str, subject: str, html: str, text: str) -> None:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        import aiosmtplib

        from_addr = config.get("email.from_address", "noreply@cubebox.local")
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        await aiosmtplib.send(
            msg,
            hostname=config.get("email.smtp_host", "localhost"),
            port=config.get("email.smtp_port", 587),
            username=config.get("email.smtp_user", None),
            password=config.get("email.smtp_password", None),
            use_tls=config.get("email.smtp_tls", True),
        )


class EmailService:
    def __init__(self, backend: EmailBackend | None = None) -> None:
        if backend is None:
            kind = config.get("email.backend", "log")
            backend = SmtpEmailBackend() if kind == "smtp" else LogEmailBackend()
        self._backend = backend
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=True,
        )

    async def send(
        self,
        *,
        to: str,
        subject: str,
        template: str,
        context: dict[str, str],
    ) -> None:
        html = self._env.get_template(f"{template}.html").render(**context)
        text = self._env.get_template(f"{template}.txt").render(**context)
        await self._backend.send(to=to, subject=subject, html=html, text=text)


def get_email_service() -> EmailService:
    return EmailService()
