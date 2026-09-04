"""Versand. Drei Backends: Resend, SMTP, Konsole.

`console` ist der Trockenlauf — schreibt die Mail nach stdout, verschickt
nichts. Das ist der Default, damit ein halb konfiguriertes System niemandem
Post schickt.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import requests

from .config import MailConfig

log = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


class MailError(RuntimeError):
    pass


@dataclass
class Mail:
    subject: str
    text: str
    html: str


def send(mail: Mail, config: MailConfig, *, timeout: int = 30) -> str:
    """Verschickt und gibt das benutzte Backend zurueck."""
    if not config.recipients:
        log.warning("Kein MI_MAIL_TO gesetzt — Ausgabe geht auf die Konsole")
        return _send_console(mail, config)

    backend = config.backend
    if backend == "resend":
        return _send_resend(mail, config, timeout)
    if backend == "smtp":
        return _send_smtp(mail, config, timeout)
    if backend == "console":
        return _send_console(mail, config)
    raise MailError(f"Unbekanntes Mail-Backend: {backend!r}")


def _send_console(mail: Mail, config: MailConfig) -> str:
    print("=" * 72)
    print(f"An:      {', '.join(config.recipients) or '(kein Empfaenger)'}")
    print(f"Von:     {config.sender}")
    print(f"Betreff: {mail.subject}")
    print("=" * 72)
    print(mail.text)
    print("=" * 72)
    return "console"


def _send_resend(mail: Mail, config: MailConfig, timeout: int) -> str:
    if not config.resend_api_key:
        raise MailError("RESEND_API_KEY fehlt")
    response = requests.post(
        RESEND_ENDPOINT,
        headers={
            "Authorization": f"Bearer {config.resend_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": config.sender,
            "to": list(config.recipients),
            "subject": mail.subject,
            "text": mail.text,
            "html": mail.html,
        },
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise MailError(f"Resend {response.status_code}: {response.text[:300]}")
    return "resend"


def _send_smtp(mail: Mail, config: MailConfig, timeout: int) -> str:
    if not config.smtp_host:
        raise MailError("SMTP_HOST fehlt")

    message = EmailMessage()
    message["Subject"] = mail.subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(mail.text)
    message.add_alternative(mail.html, subtype="html")

    if config.smtp_port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            config.smtp_host, config.smtp_port, timeout=timeout
        )
    else:
        server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=timeout)
    try:
        if config.smtp_starttls and config.smtp_port != 465:
            server.starttls()
        if config.smtp_user:
            server.login(config.smtp_user, config.smtp_password or "")
        server.send_message(message)
    finally:
        server.quit()
    return "smtp"
