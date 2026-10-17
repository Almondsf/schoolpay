"""
Outgoing email. Callers never send directly: they enqueue the `email.send`
task through the outbox, so mail goes out only if their transaction commits.

Backends (EMAIL_BACKEND):
- console: logs that a message was sent (subject and recipient domain only, no body)
- memory: keeps messages in `sent_messages` (tests)
- smtp: sends via SMTP_HOST with STARTTLS
"""

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


sent_messages: list[OutgoingEmail] = []


def deliver(message: OutgoingEmail) -> None:
    settings = get_settings()
    if settings.email_backend == "memory":
        sent_messages.append(message)
        return
    if settings.email_backend == "console":
        # Bodies hold sign-in links: never log them
        logger.info("Email (console backend) to *@%s: %s", message.to.rpartition("@")[2], message.subject)
        return
    mail = EmailMessage()
    mail["From"] = settings.email_from
    mail["To"] = message.to
    mail["Subject"] = message.subject
    mail.set_content(message.body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(mail)
