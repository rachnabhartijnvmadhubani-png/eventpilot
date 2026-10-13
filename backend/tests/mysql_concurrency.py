"""Integration checks restricted to a fresh, explicitly provisioned scratch MySQL DB.

Run: TEST_MYSQL_URL=<mysql+pymysql URL ending in eventpilot_test_SUFFIX>
     /opt/eventpilot/venv/bin/python tests/mysql_concurrency.py
Does not create/drop databases or modify production schemas. Root owns cleanup.
"""
import os
from pathlib import Path
import re
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from cryptography.fernet import Fernet

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url


def run():
    raw_url = os.environ.get("TEST_MYSQL_URL", "")
    if not raw_url:
        raise SystemExit("Set TEST_MYSQL_URL to a freshly provisioned eventpilot_test_* database")
    parsed = make_url(raw_url)
    if parsed.drivername != "mysql+pymysql" or not re.fullmatch(r"eventpilot_test_[a-z0-9_]+", parsed.database or ""):
        raise SystemExit("Refusing to run: TEST_MYSQL_URL must use mysql+pymysql and an eventpilot_test_* database")
    check_engine = create_engine(parsed, connect_args={"connect_timeout": 5})
    try:
        if inspect(check_engine).get_table_names():
            raise SystemExit("Refusing to run: scratch database must be empty")
    finally:
        check_engine.dispose()

    os.environ["DATABASE_URL"] = raw_url
    os.environ["GROQ_API_KEY"] = ""
    test_key = secrets.token_urlsafe(32)
    os.environ["ORGANIZER_API_KEY"] = test_key
    os.environ["ORGANIZATION_KEY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with patch("dotenv.load_dotenv"):
        import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200
        schema = client.get("/openapi.json").json()
        assert {"OrganizationBearer", "AttendeeBearer"}.issubset(schema["components"]["securitySchemes"])
        organizer = client.post("/api/organizations/signup", json={"organization_name": "Scratch race organization", "email": "race-organization@example.test", "password": "scratch-password-123"})
        assert organizer.status_code == 201, organizer.status_code
        client.headers["Authorization"] = "Bearer " + organizer.json()["token"]
        attendee_headers = []
        for index in range(2):
            response = client.post("/api/attendees/register", json={"name": "Scratch race attendee", "email": f"race{index}@example.test", "password": "scratch-password-123"})
            assert response.status_code == 200, response.status_code
            attendee_headers.append({"Authorization": "Bearer " + response.json()["token"]})

        event_response = client.post("/api/events", json={"name": "Scratch last-seat race", "date": (main.today_in_delhi()+timedelta(days=3)).isoformat(), "target_regs": 3})
        assert event_response.status_code == 201, event_response.status_code
        eid = event_response.json()["id"]
        metric = {"date": main.today_in_delhi().isoformat(), "reach": 1000, "clicks": 100, "registrations": 2, "confirmations": 2, "attendance": 0}
        assert client.post(f"/api/events/{eid}/metrics", json=metric).status_code == 200
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda headers: client.post("/api/attendees/registrations", json={"event_id": eid}, headers=headers), attendee_headers))
        assert sorted(r.status_code for r in responses) == [200, 409], [r.status_code for r in responses]
        ticket = next(r.json()["ticket_code"] for r in responses if r.status_code == 200)
        with main.Session() as db:
            assert db.query(main.Registration).filter_by(event_id=eid).count() == 1
        assert client.get(f"/api/events/{eid}/dashboard").json()["actual"] == 3
        print("PASS: concurrent last-seat admission counts manual baseline and admits exactly one attendee")

        payload = {"ticket_code": ticket, "event_id": eid}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post("/api/check-in", json=payload), range(2)))
        assert sorted(r.status_code for r in responses) == [200, 409], [r.status_code for r in responses]
        print("PASS: concurrent check-in succeeds once and rejects the duplicate")

        second_event = client.post("/api/events", json={"name": "Scratch duplicate admission race", "date": (main.today_in_delhi()+timedelta(days=3)).isoformat(), "target_regs": 10}).json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post("/api/attendees/registrations", json={"event_id": second_event["id"]}, headers=attendee_headers[0]), range(2)))
        assert sorted(r.status_code for r in responses) == [200, 409], [r.status_code for r in responses]
        with main.Session() as db:
            assert db.query(main.Registration).filter_by(event_id=second_event["id"]).count() == 1
        print("PASS: concurrent same-attendee admission creates one ticket")
    print("Scratch schema retained for caller cleanup; no production database was used")


if __name__ == "__main__":
    run()
