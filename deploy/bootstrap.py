"""Provision the app-only MySQL account and protected service environment."""
import os
import secrets
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from cryptography.fernet import Fernet

source = dotenv_values(Path(sys.argv[1]) / "source.env")
target = Path("/etc/eventpilot/eventpilot.env")
existing = dotenv_values(target) if target.exists() else {}
database_url = existing.get("DATABASE_URL")
pending = target.with_name("provisioning.env")
if not database_url:
    if pending.exists():
        database_url = dotenv_values(pending)["DATABASE_URL"]
        password = urlsplit(database_url).password
    else:
        account_count = subprocess.check_output(
            ["mysql", "--batch", "--skip-column-names", "-e",
             "SELECT COUNT(*) FROM mysql.user WHERE user='eventpilot' AND host='localhost'"],
            text=True,
        ).strip()
        if account_count != "0":
            raise RuntimeError("An EventPilot MySQL account already exists without saved deployment credentials; configure DATABASE_URL before deploying")
        password = secrets.token_urlsafe(36)
        database_url = f"mysql+pymysql://eventpilot:{password}@127.0.0.1:3306/eventpilot"
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(f"DATABASE_URL={database_url}\n")
            file.flush()
            os.fsync(file.fileno())
    sql = (
        "CREATE DATABASE IF NOT EXISTS eventpilot CHARACTER SET utf8mb4 "
        "COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS 'eventpilot'@'localhost' IDENTIFIED BY '{password}';\n"
        "GRANT ALL PRIVILEGES ON eventpilot.* TO 'eventpilot'@'localhost';\n"
    )
    subprocess.run(["mysql"], input=sql, text=True, check=True)

organizer_key = source.get("ORGANIZER_API_KEY") or existing.get("ORGANIZER_API_KEY") or secrets.token_urlsafe(48)
if organizer_key.startswith("CHANGE_ME") or len(organizer_key) < 32:
    raise ValueError("ORGANIZER_API_KEY must be a random secret of at least 32 characters")

organization_encryption_key = (
    source.get("ORGANIZATION_KEY_ENCRYPTION_KEY")
    or existing.get("ORGANIZATION_KEY_ENCRYPTION_KEY")
    or Fernet.generate_key().decode("ascii")
)
Fernet(organization_encryption_key.encode("ascii"))

def smtp_value(name, default):
    # Example placeholders must never overwrite working server email settings.
    for mapping in (existing, source):
        value = mapping.get(name) or ""
        if value and not value.upper().startswith(("CHANGE_ME", "REPLACE_ME", "YOUR_")):
            return value
    return default


environment = {
    "DATABASE_URL": database_url,
    "GROQ_API_KEY": source.get("GROQ_API_KEY") or existing.get("GROQ_API_KEY") or "",
    "GROQ_BASE_URL": source.get("GROQ_BASE_URL") or existing.get("GROQ_BASE_URL") or "https://api.groq.com/openai/v1",
    "GROQ_MODEL": source.get("GROQ_MODEL") or existing.get("GROQ_MODEL") or "openai/gpt-oss-120b",
    "ORGANIZER_API_KEY": organizer_key,
    "ORGANIZATION_KEY_ENCRYPTION_KEY": organization_encryption_key,
    "SMTP_HOST": smtp_value("SMTP_HOST", "smtp.gmail.com"),
    "SMTP_PORT": smtp_value("SMTP_PORT", "587"),
    "SMTP_USERNAME": smtp_value("SMTP_USERNAME", "CHANGE_ME_GMAIL_ADDRESS"),
    "SMTP_FROM_EMAIL": smtp_value("SMTP_FROM_EMAIL", "CHANGE_ME_GMAIL_ADDRESS"),
    "SMTP_APP_PASSWORD": smtp_value("SMTP_APP_PASSWORD", "CHANGE_ME_APP_PASSWORD"),
    "SMTP_STARTTLS": smtp_value("SMTP_STARTTLS", "true"),
    "PUBLIC_APP_URL": smtp_value("PUBLIC_APP_URL", "http://3.110.130.114:8080"),
    "CORS_ORIGINS": source.get("CORS_ORIGINS") or existing.get("CORS_ORIGINS") or "http://3.110.130.114,http://3.110.130.114:8080",
}
temporary = target.with_suffix(".tmp")
with temporary.open("w") as file:
    for key, value in environment.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"Invalid newline in {key}")
        file.write(f"{key}={shlex.quote(value)}\n")
os.chmod(temporary, 0o600)
os.replace(temporary, target)
pending.unlink(missing_ok=True)
print("Database and protected application environment configured.")
