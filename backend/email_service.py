"""Single-recipient email delivery using server-owned SMTP settings."""

import os
import re
import smtplib
import ssl
from email.message import EmailMessage


class EmailNotConfigured(Exception):
    """The server still has missing or placeholder SMTP settings."""


class EmailDeliveryFailed(Exception):
    """Delivery failed; the public error deliberately excludes provider details."""


_CONFIGURATION_ERROR = "Email is not configured. Set the SMTP settings on the server."
_DELIVERY_ERROR = "The email could not be sent. Please try again later."
_ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\Z")
_PLACEHOLDERS = {
    "password", "apppassword", "secret", "test", "test123", "dummy",
    "123456", "12345678", "none", "null",
}


def _placeholder(value):
    compact = re.sub(r"[\s_-]", "", value).casefold()
    return (not compact or compact in _PLACEHOLDERS
            or compact.startswith(("changeme", "replaceme", "placeholder", "yourgmail", "yourapp", "youremail", "yourpassword")))


def _valid_address(value):
    if not isinstance(value, str) or len(value) > 254 or not _ADDRESS.fullmatch(value):
        return False
    local, domain = value.rsplit("@", 1)
    return (len(local) <= 64 and not local.startswith(".") and not local.endswith(".")
            and ".." not in local and ".." not in domain
            and all(label and not label.startswith("-") and not label.endswith("-")
                    and len(label) <= 63 for label in domain.split(".")))


def _settings():
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    sender = os.getenv("SMTP_FROM_EMAIL", username).strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    starttls = os.getenv("SMTP_STARTTLS", "true").strip().casefold()
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except (TypeError, ValueError):
        raise EmailNotConfigured(_CONFIGURATION_ERROR) from None
    if (not 1 <= port <= 65535 or _placeholder(host) or _placeholder(username)
            or _placeholder(sender) or _placeholder(password)
            or not _valid_address(username) or not _valid_address(sender)
            or any(character.isspace() for character in host)
            or any(character in password for character in "\r\n")
            or starttls not in {"true", "false", "1", "0", "yes", "no", "on", "off"}):
        raise EmailNotConfigured(_CONFIGURATION_ERROR)
    # Gmail displays app passwords in four groups; copying those spaces is safe.
    if host.casefold() == "smtp.gmail.com":
        password = password.replace(" ", "")
    return host, port, username, sender, password, starttls in {"true", "1", "yes", "on"}


def is_configured():
    """Check SMTP configuration without making a connection or exposing secrets."""
    try:
        _settings()
    except EmailNotConfigured:
        return False
    return True


config_available = is_configured


def send_message(recipient, subject, body, html_body=None):
    """Send one email with plain text and optional HTML, or raise a safe error.

    Callers select the recipient from authorized database records and bound the
    message body before calling. Credentials never appear in returned errors.
    """
    host, port, username, sender, password, starttls = _settings()
    if (not _valid_address(recipient) or not isinstance(subject, str)
            or not subject.strip() or any(character in subject for character in "\r\n")
            or not isinstance(body, str) or not body.strip()
            or html_body is not None and (not isinstance(html_body, str) or not html_body.strip())):
        raise EmailDeliveryFailed(_DELIVERY_ERROR)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject.strip()
    message.set_content(body)
    if html_body is not None:
        message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(host, port, timeout=10) as connection:
            connection.ehlo()
            if starttls:
                connection.starttls(context=ssl.create_default_context())
                connection.ehlo()
            connection.login(username, password)
            refused = connection.send_message(message, from_addr=sender, to_addrs=[recipient])
            if refused:
                raise EmailDeliveryFailed(_DELIVERY_ERROR)
    except (smtplib.SMTPException, OSError, ValueError, EmailDeliveryFailed):
        raise EmailDeliveryFailed(_DELIVERY_ERROR) from None
