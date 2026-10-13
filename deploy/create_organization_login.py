"""Privately enroll an existing organization without changing its saved events.

Run on the server with the protected environment loaded. Public signup always
creates a new organization; this operator tool never accepts a public request.
"""
import argparse
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True, type=int)
    parser.add_argument("--organization-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--password-stdin", action="store_true", help="Read the initial password privately from stdin instead of generating one.")
    parser.add_argument("--environment", type=Path, default=Path("/etc/eventpilot/eventpilot.env"))
    parser.add_argument("--backend", type=Path, default=Path("/opt/eventpilot/app"))
    args = parser.parse_args()
    email = args.email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        parser.error("Provide a valid organization email.")
    settings = dotenv_values(args.environment)
    if not settings.get("DATABASE_URL"):
        parser.error("The protected environment must contain DATABASE_URL.")
    os.environ.update({key: value for key, value in settings.items() if value is not None})
    sys.path.insert(0, str(args.backend))
    import main

    main.Base.metadata.create_all(main.engine)
    password = sys.stdin.read().rstrip("\r\n") if args.password_stdin else secrets.token_urlsafe(24)
    if not 6 <= len(password) <= 128:
        parser.error("The initial password must contain 6 to 128 characters.")
    with main.Session() as db:
        organization = db.get(main.Organization, args.organization_id)
        if not organization or organization.name != args.organization_name:
            parser.error("Organization ID and name do not match. Nothing was changed.")
        if db.query(main.OrganizationAccount).filter_by(organization_id=organization.id).first():
            parser.error("This organization already has a login. Nothing was changed.")
        if db.query(main.OrganizationAccount).filter_by(email=email).first():
            parser.error("This email already has a login. Nothing was changed.")
        account = main.OrganizationAccount(
            organization_id=organization.id, email=email,
            password_hash=main._hash_password(password),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        descriptor = os.open(args.credentials_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w") as file:
                file.write(
                    "EventPilot organization login\n"
                    "Website: http://3.110.130.114:8080/\n"
                    f"Organization: {organization.name}\nEmail: {email}\n"
                    f"Temporary password: {password}\n\n"
                    "Log in and change this password in Account settings.\n"
                    "Keep this file private. No organization access key is needed.\n"
                )
                file.flush()
                os.fsync(file.fileno())
            db.add(account)
            db.commit()
        except Exception:
            db.rollback()
            args.credentials_file.unlink(missing_ok=True)
            raise
    print("Organization login created. Saved events and attendees were retained.")
    print("Temporary credentials were saved to the private file; no password was printed.")


if __name__ == "__main__":
    run()
