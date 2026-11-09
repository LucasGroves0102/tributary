"""Email for filing alerts (F6): a provider interface, OFF unless configured.

No provider is chosen and nothing here costs money: `email_from_env` returns None unless
ALERT_SMTP_HOST and ALERT_SMTP_FROM are set, and then speaks plain SMTP (any relay: a free
tier, a self-hosted server), so choosing one later is configuration, not code. A provider is
only half of it: alerts need a recipient address the user proved they own. No such address is
stored today (GitHub sign-ins carry GitHub's no-reply address, password accounts a placeholder),
so `recipients` is a function the job is given; the default `no_recipients` answers None for
everyone and no email is sent even with a provider configured. Adding a verified address (a
code sent to it, confirmed in Settings) is the next step; see the README, "Filing alerts".

What an email would say is `digest`: one message per user per run, the alerts that became
ready, in the words the webhook text uses (query_api.alerts.message), never another user's.
Secrets (ALERT_SMTP_PASSWORD) come from the environment only and are never logged."""

import logging
import smtplib
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger("query_api.alert_email")
TIMEOUT = 10.0


class EmailProvider(Protocol):
    def send(self, to: str, subject: str, text: str) -> None: ...


@dataclass(frozen=True)
class SmtpEmail:
    """Plain SMTP with TLS: STARTTLS on 587 (or any port), implicit TLS on 465."""
    host: str
    sender: str
    port: int = 587
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    connect: Callable[..., smtplib.SMTP] | None = None  # injected in tests

    def send(self, to: str, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(text)
        context = ssl.create_default_context()
        if self.connect is not None:
            server = self.connect(self.host, self.port, timeout=TIMEOUT)
        elif self.port == 465:
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=TIMEOUT)
        with server:
            if self.port != 465:
                server.starttls(context=context)
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(msg)


def email_from_env(env: Mapping[str, str]) -> EmailProvider | None:
    """None (email off) unless ALERT_SMTP_HOST and ALERT_SMTP_FROM are set."""
    host, sender = env.get("ALERT_SMTP_HOST", "").strip(), env.get("ALERT_SMTP_FROM", "").strip()
    if not host or not sender:
        return None
    return SmtpEmail(host=host, sender=sender, port=int(env.get("ALERT_SMTP_PORT", "587")),
                     username=env.get("ALERT_SMTP_USER") or None,
                     password=env.get("ALERT_SMTP_PASSWORD") or None)


def no_recipients(_user_id: str) -> str | None:
    """No user has a verified address yet (module docstring)."""
    return None


def digest(messages: list[str]) -> tuple[str, str]:
    """Subject and body of one user's alerts from one run."""
    n = len(messages)
    subject = f"Tributary: {n} new filing alert{'' if n == 1 else 's'}"
    body = "\n\n---\n\n".join(messages)
    return subject, body + "\n\nChoose which filings alert in Tributary: Settings, Filing alerts.\n"
