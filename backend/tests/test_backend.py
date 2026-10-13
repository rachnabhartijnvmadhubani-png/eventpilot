"""Deployment regression checks; uses an isolated SQLite database, no live AI calls."""
import os
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet
from unittest.mock import patch

scratch = tempfile.TemporaryDirectory(prefix="eventpilot-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{scratch.name}/test.db"
os.environ["GROQ_API_KEY"] = ""
os.environ["CORS_ORIGINS"] = "http://3.110.130.114,http://localhost:5173"
os.environ["ORGANIZER_API_KEY"] = "test-organizer-secret"
os.environ["ORGANIZATION_KEY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

with patch("dotenv.load_dotenv"):
    import main
import demo_data
import httpx
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError


class FakeAIClient:
    response = None
    request_payload = None

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        type(self).request_payload = kwargs.get("json")
        return self.response


class BackendTests(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(main.engine)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.admin = TestClient(main.app, headers={"X-Organizer-Key": "test-organizer-secret"})
        with main.Session.begin() as db:
            organization = db.query(main.Organization).filter_by(name="EventPilot").one()
            db.add(main.OrganizationAccount(organization_id=organization.id, email="default@example.test",
                                           password_hash=main._hash_password("password123"), created_at=main._now_utc().isoformat()))
        login = self.client.post("/api/organizations/login", json={"email": "default@example.test", "password": "password123"}).json()
        self.client.headers["Authorization"] = "Bearer " + login["token"]

    def tearDown(self):
        self.admin.close()
        self.client.__exit__(None, None, None)

    def create_event(self):
        result = self.client.post("/api/events", json={
            "name": "Integration test", "date": (date.today() + timedelta(days=30)).isoformat(),
        })
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def test_docs_schema_and_readiness(self):
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertIn("/openapi.json", self.client.get("/docs").text)
        self.assertEqual(self.client.get("/redoc").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["info"]["title"], "EventPilot API")
        self.assertTrue({"/api/health", "/api/events", "/api/events/{eid}/dashboard", "/api/ai"}.issubset(schema["paths"]))
        brief = schema["components"]["schemas"]["Brief"]["properties"]
        self.assertEqual(brief["target_regs"]["minimum"], 1)
        self.assertEqual(brief["format"]["enum"], ["Hybrid", "Online", "Offline"])
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok", "database": "ok"})

    def test_create_metrics_forecast_and_intelligence(self):
        event = self.create_event()
        eid = event["id"]
        metric = {"date": date.today().isoformat(), "reach": 1000, "clicks": 100,
                  "registrations": 20, "confirmations": 15, "attendance": 0}
        self.assertEqual(self.client.post(f"/api/events/{eid}/metrics", json=metric).status_code, 200)
        metric["registrations"] = 25
        self.assertEqual(self.client.post(f"/api/events/{eid}/metrics", json=metric).status_code, 200)
        dashboard = self.client.get(f"/api/events/{eid}/dashboard").json()
        self.assertEqual(dashboard["actual"], 25)
        self.assertEqual(len(dashboard["metrics"]), 1)
        self.assertEqual(len(dashboard["tasks"]), len(main.TEMPLATES))
        self.assertTrue(0 <= dashboard["risk"] <= 100)
        self.assertTrue(dashboard["forecast"]["low"] <= dashboard["forecast"]["median"] <= dashboard["forecast"]["high"])
        intel = self.client.get(f"/api/events/{eid}/intelligence").json()
        self.assertEqual(len(intel["heatmap"]), 6)
        self.assertEqual(len(intel["timeline"]), len(main.TEMPLATES))
        self.assertEqual(self.client.get("/api/events").json()[0]["id"], eid)
        self.assertEqual(self.client.get("/api/events/9999/dashboard").status_code, 404)

    def test_swagger_loads_only_local_assets_with_valid_distribution_content(self):
        docs = self.client.get("/docs")
        self.assertEqual(docs.status_code, 200)
        assets = re.findall(r'(?:src|href)="([^"]+)"', docs.text)
        self.assertEqual(set(assets), {
            "/docs-assets/swagger-ui-bundle.js", "/docs-assets/swagger-ui.css", "/docs-assets/favicon-32x32.png",
        })
        self.assertIn('"validatorUrl": null', docs.text)
        self.assertIn("/openapi.json", docs.text)
        metadata = self.client.get("/docs-assets/swagger-ui-dist.metadata.json").json()
        self.assertEqual(metadata["version"], "5.33.1")
        expected_types = {".js": "javascript", ".css": "text/css", ".png": "image/png"}
        for path in assets:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            name = path.rsplit("/", 1)[-1]
            self.assertEqual(hashlib.sha256(response.content).hexdigest(), metadata["sha256"][name])
            self.assertIn(expected_types["." + name.rsplit(".", 1)[-1]], response.headers["content-type"])
        self.assertIn("SwaggerUIBundle", self.client.get("/docs-assets/swagger-ui-bundle.js").text)
        self.assertIn("Apache License", self.client.get("/docs-assets/LICENSE").text)
        self.assertEqual(self.client.get("/docs-assets/NOTICE").status_code, 200)
        self.assertEqual(self.client.get("/docs-assets/swagger-ui-bundle.js.LICENSE.txt").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        operations = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
        self.assertEqual(sum(method in operations for path in schema["paths"].values() for method in path), 39)
        self.assertNotIn("/docs", schema["paths"])
        self.assertFalse(any(path.startswith("/docs-assets") for path in schema["paths"]))

    def test_swagger_asset_paths_include_proxy_root_path(self):
        with TestClient(main.app, root_path="/gateway") as client:
            docs = client.get("/docs")
            self.assertEqual(docs.status_code, 200)
            self.assertIn('src="/gateway/docs-assets/swagger-ui-bundle.js"', docs.text)
            self.assertIn('href="/gateway/docs-assets/swagger-ui.css"', docs.text)
            self.assertIn("/gateway/openapi.json", docs.text)
            self.assertEqual(client.get("/gateway/docs-assets/swagger-ui-bundle.js").status_code, 200)

    def test_all_success_response_schemas_describe_actual_payload_shapes(self):
        schema = self.client.get("/openapi.json").json()
        definitions = schema["components"]["schemas"]
        expected_models = {
            ("/api/health", "get", "200"): "HealthResponse",
            ("/api/events", "post", "201"): "EventResponse",
            ("/api/demo", "post", "201"): "EventResponse",
            ("/api/events/{eid}/metrics", "post", "200"): "MetricResponse",
            ("/api/tasks/{tid}/done", "post", "200"): "TaskImpactResponse",
            ("/api/events/{eid}/escalate", "post", "200"): "EscalationResponse",
            ("/api/ai", "post", "200"): "AIResponse",
        }
        for (path, method, status), model in expected_models.items():
            result = schema["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]
            self.assertEqual(result["$ref"], f"#/components/schemas/{model}")
            self.assertEqual(definitions[model]["type"], "object")
        events = schema["paths"]["/api/events"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(events["type"], "array")
        self.assertEqual(events["items"]["$ref"], "#/components/schemas/EventResponse")
        for path, method in (("/api/events/{eid}/dashboard", "get"), ("/api/events/{eid}/intelligence", "get"), ("/api/{kind}/{item_id}", "patch")):
            result = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
            self.assertEqual(result["type"], "object")
        self.assertEqual(definitions["HealthResponse"]["properties"]["status"]["const"], "ok")
        self.assertEqual(definitions["EventResponse"]["properties"]["start_date"]["format"], "date")
        self.assertEqual(definitions["AIResponse"]["properties"]["text"]["type"], "string")

    def test_invalid_input_is_rejected_before_database_writes(self):
        for changes in ({"name": "   "}, {"name": "x" * 201}, {"target_regs": 0},
                        {"date": "bad-date"}, {"format": "other"}, {"type": "other"}):
            payload = {"name": "Valid", "date": "2026-11-18", **changes}
            self.assertEqual(self.client.post("/api/events", json=payload).status_code, 422)
        self.assertEqual(self.client.get("/api/events").json(), [])
        event = self.create_event()
        metric = {"date": date.today().isoformat(), "reach": -1, "clicks": 0,
                  "registrations": 0, "confirmations": 0, "attendance": 0}
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/metrics', json=metric).status_code, 422)
        self.assertEqual(self.client.post("/api/ai", json={"prompt": "  "}).status_code, 422)

    def test_task_completion_clamps_date_and_does_not_repeat(self):
        event = self.create_event()
        dashboard = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        tid = dashboard["tasks"][0]["id"]
        payload = {"actor": "Operations", "sim_date": "2099-01-01"}
        self.assertEqual(self.client.post(f"/api/tasks/{tid}/done", json=payload).status_code, 200)
        self.assertEqual(self.client.post(f"/api/tasks/{tid}/done", json=payload).status_code, 409)
        with main.Session() as db:
            self.assertEqual(db.query(main.Uplift).one().start_date.isoformat(), event["date"])
            self.assertEqual(db.query(main.Decision).count(), 1)

    def test_demo_update_and_closed_escalations_are_stable(self):
        response = self.client.post("/api/demo")
        self.assertEqual(response.status_code, 201)
        eid = response.json()["id"]
        day = response.json()["date"]
        dashboard = self.client.get(f"/api/events/{eid}/dashboard", params={"sim_date": day}).json()
        self.assertEqual(len(dashboard["partners"]), 12)
        self.assertEqual(len(dashboard["metrics"]), 19)
        partner_id = dashboard["partners"][0]["id"]
        self.assertEqual(self.client.patch(f"/api/partners/{partner_id}", json={"status": "Confirmed"}).status_code, 200)
        self.assertEqual(self.client.patch(f"/api/partners/{partner_id}", json={"status": "Done"}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/other/{partner_id}", json={"status": "Done"}).status_code, 422)
        escalation = dashboard["escalations"][0]
        self.assertEqual(self.client.patch(f'/api/escalations/{escalation["id"]}', json={"status": "Approved"}).status_code, 200)
        response = self.client.post(f"/api/events/{eid}/escalate", params={"sim_date": day})
        self.assertEqual(response.json(), {"created": 0})
        with main.Session() as db:
            self.assertEqual(db.query(main.Escalation).filter_by(task_id=escalation["task_id"]).count(), 1)

    def test_demo_is_populated_today_and_attendance_is_not_premature(self):
        event = self.client.post("/api/demo").json()
        dashboard = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        self.assertEqual(event["name"], "Delhi Innovators Hackathon")
        self.assertIn("[DEMO]", event["objectives"])
        self.assertEqual(dashboard["actual"], 400)
        self.assertEqual(dashboard["days_left"], 12)
        self.assertEqual(dashboard["metrics"][-1]["date"], demo_data.today_in_delhi().isoformat())
        self.assertEqual(dashboard["funnel"]["Attendance"], 0)
        self.assertNotEqual(dashboard["bottleneck"], "Attendance")
        self.assertGreater(dashboard["forecast"]["probability"], 0)
        self.assertLess(dashboard["forecast"]["probability"], 100)
        self.assertTrue(dashboard["decisions"])
        self.assertTrue(dashboard["escalations"])

    def test_seed_is_idempotent_preserves_existing_data_and_has_coherent_metrics(self):
        original = self.create_event()
        reference = date(2026, 10, 11)
        with main.Session() as db:
            seeded = demo_data.seed_demo_events(db, main.DEMO_MODELS, reference)
        self.assertEqual(sum(item["created"] for item in seeded), 3)
        self.assertEqual([item["name"] for item in seeded], [profile.name for profile in demo_data.PROFILES])
        risks = []
        all_models = tuple(vars(main.DEMO_MODELS).values())
        with main.Session() as db:
            counts = {model.__tablename__: db.query(model).count() for model in all_models}
            for item in seeded:
                event = db.get(main.Event, item["id"])
                snapshot = main.state(db, event, reference)
                risks.append(snapshot["risk"])
                self.assertIn("[DEMO]", event.objectives)
                self.assertIn("Delhi", event.venue)
                self.assertGreater(snapshot["actual"], 0)
                self.assertGreaterEqual(len(snapshot["metrics"]), 10)
                self.assertGreaterEqual(len(snapshot["tasks"]), 12)
                self.assertGreaterEqual(len(snapshot["partners"]), 4)
                self.assertGreaterEqual(len(snapshot["content"]), 4)
                self.assertGreaterEqual(len(snapshot["decisions"]), 1)
                self.assertEqual(len(main.analyze(snapshot, reference)["heatmap"]), 6)
                self.assertTrue(all("(Demo)" in partner["name"] for partner in snapshot["partners"]))
                for left, right in zip(snapshot["metrics"], snapshot["metrics"][1:]):
                    for field in ("reach", "clicks", "registrations", "confirmations", "attendance"):
                        self.assertLessEqual(left[field], right[field])
                for metric in snapshot["metrics"]:
                    self.assertLessEqual(metric["date"], reference.isoformat())
                    values = [metric[key] for key in ("reach", "clicks", "registrations", "confirmations", "attendance")]
                    self.assertEqual(values, sorted(values, reverse=True))
            self.assertEqual(db.get(main.Event, original["id"]).name, "Integration test")
            task = db.query(main.Task).filter_by(event_id=seeded[0]["id"], status="Open").first()
            task.status = "Done"
            edited_task_id = task.id
            db.commit()
        with main.Session() as db:
            repeated = demo_data.seed_demo_events(db, main.DEMO_MODELS, reference + timedelta(days=1))
            self.assertEqual(sum(item["created"] for item in repeated), 0)
            self.assertEqual([item["id"] for item in repeated], [item["id"] for item in seeded])
            self.assertEqual({model.__tablename__: db.query(model).count() for model in all_models}, counts)
            self.assertEqual(db.get(main.Task, edited_task_id).status, "Done")
        self.assertTrue(risks[1] > risks[0] > risks[2], risks)

    def test_seed_rolls_back_every_new_scenario_on_failure(self):
        original = self.create_event()
        build = demo_data.create_demo_event
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("Synthetic test failure")
            return build(*args, **kwargs)

        with main.Session() as db, patch.object(demo_data, "create_demo_event", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "Synthetic test failure"):
                demo_data.seed_demo_events(db, main.DEMO_MODELS, date(2026, 10, 11))
        with main.Session() as db:
            self.assertEqual(db.query(main.Event).count(), 1)
            self.assertEqual(db.get(main.Event, original["id"]).name, "Integration test")
            for model in (main.Metric, main.Partner, main.Uplift, main.Decision, main.Escalation):
                self.assertEqual(db.query(model).count(), 0)

    def test_seed_cli_reference_date_and_repeat_run(self):
        with tempfile.TemporaryDirectory(prefix="eventpilot-cli-") as directory:
            # Execute the actual CLI in this checkout, with dotenv disabled in
            # the child too, so tests never load local/server secret files.
            cli_runner = "import sys,runpy,dotenv; from pathlib import Path; target=sys.argv.pop(1); sys.path.insert(0,str(Path(target).parent)); dotenv.load_dotenv=lambda *a,**k:False; runpy.run_path(target,run_name='__main__')"
            command = [sys.executable, "-c", cli_runner, str(BACKEND_DIR / "seed_demo.py"), "--reference-date", "2026-10-11"]
            env = {**os.environ, "DATABASE_URL": f"sqlite:///{directory}/cli.db"}
            first = json.loads(subprocess.run(command, env=env, check=True, capture_output=True, text=True).stdout)
            second = json.loads(subprocess.run(command, env=env, check=True, capture_output=True, text=True).stdout)
            self.assertEqual(first["reference_date"], "2026-10-11")
            self.assertEqual(first["created"], 3)
            self.assertEqual(second["created"], 0)
            self.assertEqual(second["reused"], 3)
            self.assertEqual([item["id"] for item in first["events"]], [item["id"] for item in second["events"]])

    def test_cors_allows_configured_origin(self):
        response = self.client.options("/api/events", headers={
            "Origin": "http://3.110.130.114", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://3.110.130.114")

    def test_database_errors_are_safe_and_return_unhealthy(self):
        with patch.object(main.engine, "connect", side_effect=SQLAlchemyError("private database credential")):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Database unavailable; try again later"})
        self.assertNotIn("private", response.text)

    def test_ai_configuration_success_and_provider_error_are_safe(self):
        response = self.client.post("/api/ai", json={"prompt": "Give event advice"})
        self.assertEqual(response.status_code, 503)
        request = httpx.Request("POST", "https://example.invalid/chat/completions")
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(main.httpx, "AsyncClient", FakeAIClient):
            FakeAIClient.response = httpx.Response(200, json={"choices": [{"message": {"content": "Useful advice"}}]}, request=request)
            answer = self.client.post("/api/ai", json={"prompt": "Help"}).json()
            self.assertEqual(answer["text"], "Useful advice")
            self.assertIsInstance(answer["response_id"], int)
            FakeAIClient.response = httpx.Response(401, text="private upstream details", request=request)
            response = self.client.post("/api/ai", json={"prompt": "Help"})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("private", response.text)
            FakeAIClient.response = httpx.Response(200, json={"choices": [{"message": {"content": {"invalid": 1}}}]}, request=request)
            self.assertEqual(self.client.post("/api/ai", json={"prompt": "Help"}).status_code, 502)

    def account(self, email="one@example.test"):
        response = self.client.post("/api/attendees/register", json={"name": "Test attendee", "email": email,
                                                                  "password": "test-password-123"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), {"Authorization": "Bearer " + response.json()["token"]}

    def registration(self, eid, headers):
        response = self.client.post("/api/attendees/registrations", json={"event_id": eid}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_public_readiness_and_organizer_auth_are_documented_and_enforced(self):
        with TestClient(main.app) as anonymous:
            self.assertEqual(anonymous.get("/api/health").status_code, 200)
            self.assertEqual(anonymous.get("/api/public/events").status_code, 200)
            self.assertEqual(anonymous.get("/api/events").status_code, 401)
            self.assertEqual(anonymous.post("/api/demo").status_code, 401)
            with patch.dict(os.environ, {"ORGANIZER_API_KEY": ""}):
                self.assertEqual(anonymous.get("/api/events").status_code, 401)
            schema = anonymous.get("/openapi.json").json()
            schemes = schema["components"]["securitySchemes"]
            self.assertEqual(schemes["OrganizationBearer"]["scheme"], "bearer")
            self.assertEqual(schemes["AttendeeBearer"]["scheme"], "bearer")
            self.assertNotIn("security", schema["paths"]["/api/health"]["get"])
            self.assertEqual(schema["paths"]["/api/events"]["get"]["security"], [{"OrganizationBearer": []}])
            self.assertEqual(schema["paths"]["/api/attendees/me"]["get"]["security"], [{"AttendeeBearer": []}])
            for path in schema["paths"].values():
                for operation in path.values():
                    success = next(v for k, v in operation["responses"].items() if k.startswith("2"))
                    self.assertTrue(success["content"]["application/json"]["schema"])

    def test_accounts_normalize_email_hash_password_and_reject_invalid_inputs(self):
        account, headers = self.account(" ONE@EXAMPLE.TEST ")
        self.assertEqual(account["attendee"]["email"], "one@example.test")
        self.assertEqual(self.client.get("/api/attendees/me", headers=headers).json(), account["attendee"])
        self.assertEqual(self.client.post("/api/attendees/register", json={"name": "Other", "email": "one@example.test", "password": "test-password-123"}).status_code, 409)
        for payload in ({"name": " ", "email": "x@example.test", "password": "password123"},
                        {"name": "X", "email": "bad", "password": "password123"},
                        {"name": "X", "email": "x@example.test", "password": "short"},
                        {"name": "X", "email": "x@example.test", "password": "x" * 129}):
            self.assertEqual(self.client.post("/api/attendees/register", json=payload).status_code, 422)
        with main.Session() as db:
            attendee = db.query(main.Attendee).one()
            self.assertNotIn("test-password-123", attendee.password_hash)
            session = db.query(main.AttendeeSession).one()
            self.assertNotEqual(session.token_hash, account["token"])
            self.assertEqual(session.token_hash, hashlib.sha256(account["token"].encode()).hexdigest())
        self.assertEqual(self.client.post("/api/attendees/login", json={"email": "one@example.test", "password": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/attendees/login", json={"email": "ONE@example.test", "password": "test-password-123"}).status_code, 200)

    def test_persistent_sessions_expire_and_logout_revokes_only_that_token(self):
        account, headers = self.account()
        main.engine.dispose()
        with TestClient(main.app) as fresh_client:
            self.assertEqual(fresh_client.get("/api/attendees/me", headers=headers).status_code, 200)
            self.assertEqual(fresh_client.get("/api/events", headers=headers).status_code, 401)
        login = self.client.post("/api/attendees/login", json={"email": "one@example.test", "password": "test-password-123"}).json()
        other_headers = {"Authorization": "Bearer " + login["token"]}
        self.assertEqual(self.client.post("/api/attendees/logout", headers=headers).json(), {"ok": True})
        self.assertEqual(self.client.get("/api/attendees/me", headers=headers).status_code, 401)
        self.assertEqual(self.client.get("/api/attendees/me", headers=other_headers).status_code, 200)
        with main.Session.begin() as db:
            db.query(main.AttendeeSession).update({"expires_at": datetime(2000, 1, 1)})
        self.assertEqual(self.client.get("/api/attendees/me", headers=other_headers).status_code, 401)

    def test_registration_capacity_includes_manual_baseline_and_duplicate_is_rejected(self):
        event = self.client.post("/api/events", json={"name": "Small", "date": (date.today() + timedelta(days=3)).isoformat(), "target_regs": 2}).json()
        metric = {"date": date.today().isoformat(), "reach": 100, "clicks": 10, "registrations": 1, "confirmations": 1, "attendance": 0}
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/metrics', json=metric).status_code, 200)
        _, first = self.account()
        _, second = self.account("two@example.test")
        self.registration(event["id"], first)
        self.assertEqual(self.client.post("/api/attendees/registrations", json={"event_id": event["id"]}, headers=first).status_code, 409)
        response = self.client.post("/api/attendees/registrations", json={"event_id": event["id"]}, headers=second)
        self.assertEqual(response.status_code, 409)
        self.assertIn("capacity", response.json()["detail"])
        self.assertEqual(self.client.get(f'/api/events/{event["id"]}/dashboard').json()["actual"], 2)

    def test_registration_privacy_and_event_scoped_announcements(self):
        event = self.create_event()
        other = self.create_event()
        _, first = self.account()
        _, second = self.account("two@example.test")
        ticket = self.registration(event["id"], first)
        self.registration(other["id"], second)
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/announcements', json={"title": "Arrival", "message": "Bring your demo ticket"}).status_code, 200)
        mine = self.client.get("/api/attendees/registrations", headers=first).json()
        theirs = self.client.get("/api/attendees/registrations", headers=second).json()
        self.assertEqual(mine[0]["ticket_code"], ticket["ticket_code"])
        self.assertEqual(mine[0]["announcements"][0]["title"], "Arrival")
        self.assertEqual(theirs[0]["announcements"], [])
        self.assertNotIn(ticket["ticket_code"], json.dumps(theirs))
        self.assertNotIn("password", json.dumps(self.client.get(f'/api/events/{event["id"]}/attendees').json()))
        with TestClient(main.app) as public:
            self.assertEqual(public.get(f'/api/events/{event["id"]}/attendees', headers=first).status_code, 401)

    def test_checkin_rejects_wrong_event_and_is_atomic_under_two_requests(self):
        event = self.create_event()
        other = self.create_event()
        _, headers = self.account()
        ticket = self.registration(event["id"], headers)
        wrong = self.client.post("/api/check-in", json={"ticket_code": ticket["ticket_code"], "event_id": other["id"]})
        self.assertEqual(wrong.status_code, 409)
        self.assertIsNone(self.client.get("/api/attendees/registrations", headers=headers).json()[0]["checked_in_at"])
        payload = {"ticket_code": ticket["ticket_code"], "event_id": event["id"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(lambda _: self.client.post("/api/check-in", json=payload).status_code, range(2)))
        self.assertEqual(statuses, [200, 409])
        snapshot = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        self.assertEqual(snapshot["actual"], 1)
        self.assertEqual(snapshot["funnel"]["Confirmation"], 1)
        self.assertEqual(snapshot["funnel"]["Attendance"], 1)

    def test_historical_portal_growth_and_checkins_follow_india_calendar_days(self):
        event = self.client.post("/api/demo").json()
        _, headers = self.account()
        ticket = self.registration(event["id"], headers)
        today = demo_data.today_in_delhi()
        with main.Session.begin() as db:
            row = db.get(main.Registration, ticket["id"])
            # 19:00 UTC is the following day in India.
            row.created_at = f"{(today - timedelta(days=2)).isoformat()}T19:00:00+00:00"
            row.checked_in_at = f"{(today - timedelta(days=1)).isoformat()}T19:00:00+00:00"
            row.status = "Attended"
        early = self.client.get(f'/api/events/{event["id"]}/dashboard', params={"sim_date": (today-timedelta(days=2)).isoformat()}).json()
        middle = self.client.get(f'/api/events/{event["id"]}/dashboard', params={"sim_date": (today-timedelta(days=1)).isoformat()}).json()
        now = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        self.assertEqual(early["registration_sources"]["portal"], 0)
        self.assertEqual(middle["registration_sources"]["portal"], 1)
        self.assertEqual(middle["funnel"]["Attendance"], 0)
        self.assertEqual(now["funnel"]["Attendance"], 1)
        self.assertEqual(now["actual"], 401)
        self.assertEqual(now["registration_trajectory"][-1]["registrations"], now["actual"])
        self.assertEqual(now["funnel"]["Confirmation"], now["metrics"][-1]["confirmations"] + 1)

    def test_completed_forecast_has_no_future_uplift_and_late_metrics_remain_visible(self):
        today = demo_data.today_in_delhi()
        with main.Session.begin() as db:
            e = main.Event(name="Completed", type="Webinar", date=today-timedelta(days=1), start_date=today-timedelta(days=5),
                           format="Online", audience="Test", target_regs=100, venue="Test", objectives="Test")
            db.add(e); db.flush(); eid = e.id
            organization = db.query(main.Organization).filter_by(name="EventPilot").one()
            db.add(main.EventOrganization(event_id=eid, organization_id=organization.id))
            db.add(main.Metric(event_id=eid, date=today, reach=10000, clicks=400, registrations=80, confirmations=70, attendance=60))
            db.add(main.Uplift(event_id=eid, gain=10, days=7, start_date=today-timedelta(days=2)))
        snapshot = self.client.get(f"/api/events/{eid}/dashboard").json()
        self.assertEqual(snapshot["sim_date"], today.isoformat())
        self.assertEqual(snapshot["days_left"], 0)
        self.assertEqual(snapshot["forecast"], {"available": True, "median": 80, "low": 80, "high": 80, "probability": 0})
        self.assertEqual(snapshot["funnel"]["Attendance"], 60)
        self.assertEqual(self.client.post("/api/attendees/registrations", json={"event_id": eid}, headers=self.account()[1]).status_code, 400)
        self.assertNotIn(eid, [x["id"] for x in self.client.get("/api/public/events").json()])

    def test_recommendation_history_validation_and_relationships(self):
        event = self.create_event()
        payload = {"event_id": event["id"], "recommendation": "Call demo partners", "status": "Accepted", "notes": "Synthetic action"}
        self.assertEqual(self.client.post("/api/recommendation-actions", json=payload).status_code, 200)
        history = self.client.get(f'/api/events/{event["id"]}/recommendation-actions').json()
        self.assertEqual(history[0]["notes"], payload["notes"])
        for changes in ({"status": "Other"}, {"recommendation": " "}, {"notes": "x"*2001}):
            self.assertEqual(self.client.post("/api/recommendation-actions", json={**payload, **changes}).status_code, 422)
        self.assertEqual(self.client.post("/api/recommendation-actions", json={**payload, "event_id": 9999}).status_code, 404)
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/announcements', json={"title": "x"*161, "message": "Message"}).status_code, 422)
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/metrics', json={"date": date.today().isoformat(), "reach": 0, "clicks": 1, "registrations": 0, "confirmations": 0, "attendance": 0}).status_code, 422)

    def test_additive_startup_preserves_legacy_events_and_requires_no_existing_column_changes(self):
        main.Base.metadata.drop_all(main.engine)
        legacy_names = {"events", "tasks", "daily_metrics", "uplifts", "decision_log", "partners", "content_slots", "logistics", "escalations"}
        legacy_tables = [table for table in main.Base.metadata.sorted_tables if table.name in legacy_names]
        main.Base.metadata.create_all(main.engine, tables=legacy_tables)
        with main.Session.begin() as db:
            db.add(main.Event(id=77, name="Preserved legacy event", type="Hackathon", date=date(2026, 12, 1), start_date=date(2026, 10, 1),
                              format="Hybrid", audience="Legacy", target_regs=750, venue="Delhi", objectives="Existing user data"))
        with TestClient(main.app, headers={"X-Organizer-Key": "test-organizer-secret"}) as updated:
            with main.Session.begin() as db:
                organization = db.query(main.Organization).filter_by(name="EventPilot").one()
                db.add(main.OrganizationAccount(organization_id=organization.id, email="restored@example.test",
                                               password_hash=main._hash_password("password123"), created_at=main._now_utc().isoformat()))
            login = updated.post("/api/organizations/login", json={"email": "restored@example.test", "password": "password123"}).json()
            event = updated.get("/api/events", headers={"Authorization": "Bearer " + login["token"]}).json()[0]
            self.assertEqual(event["id"], 77)
            self.assertEqual(event["objectives"], "Existing user data")
            self.assertEqual(updated.get("/api/health").status_code, 200)
        with main.Session() as db:
            self.assertEqual(db.query(main.Event).count(), 1)
            self.assertEqual(db.query(main.AttendeeSession).count(), 0)

    def test_event_archive_hides_public_admissions_but_keeps_history(self):
        event = self.create_event()
        attendee, headers = self.account()
        registered = self.client.post("/api/attendees/registrations", json={"event_id": event["id"]}, headers=headers)
        self.assertEqual(registered.status_code, 200)
        archive = self.client.patch(f'/api/events/{event["id"]}/archive', json={"archived": True})
        self.assertEqual(archive.status_code, 200, archive.text)
        self.assertTrue(archive.json()["archived"])
        self.assertEqual(self.client.get("/api/events").json(), [])
        self.assertEqual(self.client.get("/api/events?include_archived=true").json()[0]["id"], event["id"])
        self.assertEqual(self.client.get("/api/public/events").json(), [])
        other, other_headers = self.account("other@example.test")
        closed = self.client.post("/api/attendees/registrations", json={"event_id": event["id"]}, headers=other_headers)
        self.assertEqual(closed.status_code, 400)
        history = self.client.get("/api/attendees/registrations", headers=headers).json()
        self.assertEqual(history[0]["ticket_code"], registered.json()["ticket_code"])
        self.assertFalse(self.client.patch(f'/api/events/{event["id"]}/archive', json={"archived": False}).json()["archived"])
        self.assertEqual(len(self.client.get("/api/public/events").json()), 1)

    def test_archived_history_is_readonly_until_restored(self):
        event = self.create_event()
        eid = event["id"]
        attendee, headers = self.account()
        ticket = self.client.post("/api/attendees/registrations", json={"event_id": eid}, headers=headers).json()
        snapshot = self.client.get(f'/api/events/{eid}/dashboard').json()
        partner = self.client.post(f'/api/events/{eid}/partners', json={"name": "Actual partner"}).json()
        self.client.patch(f'/api/events/{eid}/archive', json={"archived": True})
        metric = {"date": main.today_in_delhi().isoformat(), "reach": 1000, "clicks": 100, "registrations": 10, "confirmations": 7, "attendance": 0}
        calls = [
            self.client.post(f'/api/events/{eid}/metrics', json=metric),
            self.client.post(f'/api/events/{eid}/partners', json={"name": "Another partner"}),
            self.client.post(f'/api/tasks/{snapshot["tasks"][0]["id"]}/done', json={"actor": "Marketing"}),
            self.client.patch(f'/api/partners/{partner["id"]}', json={"status": "Confirmed"}),
            self.client.patch(f'/api/content/{snapshot["content"][0]["id"]}', json={"status": "Approved"}),
            self.client.post(f'/api/events/{eid}/announcements', json={"title": "Update", "message": "New message"}),
            self.client.post(f'/api/events/{eid}/escalate', params={"sim_date": event["date"]}),
            self.client.post('/api/check-in', json={"event_id": eid, "ticket_code": ticket["ticket_code"]}),
            self.client.post('/api/recommendation-actions', json={"event_id": eid, "recommendation": "New action"}),
        ]
        self.assertTrue(all(response.status_code == 409 for response in calls), [response.text for response in calls])
        archived = self.client.get(f'/api/events/{eid}/dashboard', params={"sim_date": event["date"]}).json()
        self.assertEqual(archived["escalations"], snapshot["escalations"])
        self.assertEqual(archived["decisions"], [])
        self.client.patch(f'/api/events/{eid}/archive', json={"archived": False})
        self.assertEqual(self.client.post(f'/api/events/{eid}/partners', json={"name": "Another partner"}).status_code, 201)

    def test_event_edit_preserves_progress_and_shifts_deadlines(self):
        event = self.create_event()
        before = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        tid = before["tasks"][0]["id"]
        self.client.post(f'/api/tasks/{tid}/done', json={"actor": "Marketing"})
        payload = {key: event[key] for key in ("name", "type", "date", "format", "audience", "target_regs", "venue", "objectives", "organization_id")}
        payload.update(name="Updated event", date=(date.fromisoformat(event["date"])+timedelta(days=5)).isoformat())
        updated = self.client.patch(f'/api/events/{event["id"]}', json=payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        after = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        self.assertEqual(after["tasks"][0]["id"], tid)
        self.assertEqual(after["tasks"][0]["status"], "Done")
        self.assertEqual(date.fromisoformat(after["tasks"][0]["due_date"])-date.fromisoformat(before["tasks"][0]["due_date"]), timedelta(days=5))
        self.assertEqual(len(after["decisions"]), 1)
        rejected = self.client.patch(f'/api/events/{event["id"]}', json={**payload, "type": "Webinar"})
        self.assertEqual(rejected.status_code, 409)

    def test_partial_edit_preserves_unsupplied_brief_fields_and_capacity(self):
        event = self.client.post("/api/events", json={"name": "Online webinar", "type": "Webinar", "format": "Online", "date": (main.today_in_delhi()+timedelta(days=20)).isoformat(), "venue": "A user supplied link"}).json()
        updated = self.client.patch(f'/api/events/{event["id"]}', json={"name": "Renamed webinar"})
        self.assertEqual(updated.status_code, 200, updated.text)
        for field in ("type", "format", "date", "venue", "target_regs", "start_date"):
            self.assertEqual(updated.json()[field], event[field])
        self.assertEqual(self.client.patch(f'/api/events/{event["id"]}', json={"date": None}).status_code, 422)
        metric = {"date": main.today_in_delhi().isoformat(), "reach": 100, "clicks": 10, "registrations": 5, "confirmations": 3, "attendance": 0}
        self.client.post(f'/api/events/{event["id"]}/metrics', json=metric)
        self.assertEqual(self.client.patch(f'/api/events/{event["id"]}', json={"target_regs": 4}).status_code, 409)

    def test_forecast_requires_distinct_recorded_days_and_goal_met_has_no_growth_actions(self):
        event = self.create_event()
        prior = date.fromisoformat(event["start_date"]) + timedelta(days=1)
        metric = {"date": prior.isoformat(), "reach": 1000, "clicks": 100, "registrations": 10, "confirmations": 7, "attendance": 0}
        self.client.post(f'/api/events/{event["id"]}/metrics', json=metric)
        first = self.client.get(f'/api/events/{event["id"]}/dashboard', params={"sim_date": (prior+timedelta(days=1)).isoformat()}).json()
        self.assertFalse(first["forecast"]["available"])
        metric.update(date=(prior+timedelta(days=2)).isoformat(), reach=10000, clicks=2000, registrations=500, confirmations=350)
        self.client.post(f'/api/events/{event["id"]}/metrics', json=metric)
        second = self.client.get(f'/api/events/{event["id"]}/dashboard', params={"sim_date": metric["date"]}).json()
        self.assertTrue(second["forecast"]["available"])
        self.assertEqual(second["recommendations"], [])

    def test_real_plan_has_no_fictional_records_and_matches_online_webinar(self):
        event = self.client.post("/api/events", json={"name": "My webinar", "type": "Webinar", "format": "Online", "date": (main.today_in_delhi()+timedelta(days=5)).isoformat()}).json()
        dashboard = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        titles = " ".join(task["title"] for task in dashboard["tasks"])
        self.assertNotIn("judges", titles.lower())
        self.assertNotIn("Confirm venue", titles)
        self.assertNotIn("targeted social campaign", titles)
        self.assertEqual(dashboard["partners"], [])
        self.assertEqual(dashboard["metrics"], [])
        self.assertFalse(event["is_demo"])
        self.assertTrue(dashboard["plan_summary"]["compressed"])
        self.assertLess(len(dashboard["tasks"]), len(main.TEMPLATES))
        self.assertTrue(all(event["start_date"] <= task["due_date"] <= event["date"] for task in dashboard["tasks"]))
        self.assertTrue(any("Online room" in item["item"] for item in dashboard["logistics"]))

    def test_empty_dashboard_does_not_invent_registration_growth(self):
        event = self.create_event()
        dashboard = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        self.assertEqual(dashboard["data_state"]["status"], "no_activity")
        self.assertFalse(dashboard["forecast"]["available"])
        self.assertEqual(dashboard["forecast"]["median"], 0)
        self.assertEqual(dashboard["forecast"]["high"], 0)
        self.assertEqual(dashboard["bottleneck"], "Not enough data")
        heatmap = self.client.get(f'/api/events/{event["id"]}/intelligence').json()["heatmap"]
        self.assertEqual([cell["level"] for cell in heatmap[:2]], ["Unknown", "Unknown"])

    def test_recovery_ranking_keeps_detected_confirmation_bottleneck_relevant(self):
        event = self.create_event()
        day = date.fromisoformat(event["start_date"]) + timedelta(days=15)
        metric = {"date": day.isoformat(), "reach": 100000, "clicks": 1000, "registrations": 100, "confirmations": 0, "attendance": 0}
        self.client.post(f'/api/events/{event["id"]}/metrics', json=metric)
        snapshot = self.client.get(f'/api/events/{event["id"]}/dashboard', params={"sim_date": day.isoformat()}).json()
        self.assertEqual(snapshot["bottleneck"], "Confirmation")
        intelligence = self.client.get(f'/api/events/{event["id"]}/intelligence', params={"sim_date": day.isoformat()}).json()
        self.assertIn("reminder", intelligence["ranked_actions"][0]["title"].lower())

    def test_ordinary_task_completion_does_not_invent_registrations(self):
        event = self.create_event()
        dashboard = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        task = next(task for task in dashboard["tasks"] if task["title"] == "Confirm venue")
        completed = self.client.post(f'/api/tasks/{task["id"]}/done', json={"actor": "Operations"})
        self.assertEqual(completed.status_code, 200)
        self.assertEqual({key: completed.json()[key] for key in ("before", "after")}, {"before": None, "after": None})
        with main.Session() as db:
            self.assertEqual(db.query(main.Uplift).count(), 0)
            self.assertEqual(db.query(main.Decision).one().actor, "Operations")
            self.assertIsNone(db.query(main.Decision).one().before_value)
            self.assertIsNone(db.query(main.Decision).one().after_value)
        self.assertEqual(self.client.get(f'/api/events/{event["id"]}/dashboard').json()["actual"], 0)

    def test_task_history_retains_unavailable_forecast_after_more_data_arrives(self):
        event = self.create_event()
        dashboard = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        task = next(task for task in dashboard["tasks"] if task["title"] == "Improve registration conversion")
        completed = self.client.post(f'/api/tasks/{task["id"]}/done', json={"actor": "Marketing"})
        self.assertEqual({key: completed.json()[key] for key in ("before", "after")}, {"before": None, "after": None})
        start = date.fromisoformat(event["start_date"])
        for offset, registrations in ((1, 20), (2, 35)):
            metric = {"date": (start+timedelta(days=offset)).isoformat(), "reach": 1000, "clicks": 100, "registrations": registrations, "confirmations": 15, "attendance": 0}
            self.client.post(f'/api/events/{event["id"]}/metrics', json=metric)
        later = self.client.get(f'/api/events/{event["id"]}/dashboard', params={"sim_date": (start+timedelta(days=2)).isoformat()}).json()
        self.assertTrue(later["forecast"]["available"])
        self.assertIsNone(later["decisions"][0]["before_value"])
        self.assertIsNone(later["decisions"][0]["after_value"])

    def test_recommendations_are_actionable_and_completed_once(self):
        event = self.create_event()
        recommendation = self.client.get(f'/api/events/{event["id"]}/dashboard').json()["recommendations"][0]
        payload = {"event_id": event["id"], "task_id": recommendation["id"], "recommendation": recommendation["title"]}
        accepted = self.client.post("/api/recommendation-actions", json=payload)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["task_id"], recommendation["id"])
        completed = self.client.post("/api/recommendation-actions", json={**payload, "status": "Completed"})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(self.client.post("/api/recommendation-actions", json={**payload, "status": "Completed"}).status_code, 409)
        with main.Session() as db:
            self.assertEqual(db.query(main.Uplift).count(), 1)
            self.assertEqual(db.query(main.Decision).count(), 1)
            self.assertEqual(db.get(main.Task, recommendation["id"]).status, "Done")
        other = self.create_event()
        self.assertEqual(self.client.post("/api/recommendation-actions", json={**payload, "event_id": other["id"]}).status_code, 404)

    def test_demo_creation_is_explicit_reused_and_not_public_by_default(self):
        first = self.client.post("/api/demo").json()
        second = self.client.post("/api/demo").json()
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["is_demo"])
        self.assertEqual(self.client.get("/api/public/events").json(), [])
        self.assertEqual(self.client.get("/api/public/events?include_demo=true").json()[0]["id"], first["id"])
        self.assertNotEqual(self.client.post("/api/demo?new_copy=true").json()["id"], first["id"])

    def test_real_partner_can_be_added_with_scoped_access(self):
        event = self.create_event()
        result = self.client.post(f'/api/events/{event["id"]}/partners', json={"name": "User supplied partner"})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["stage"], "Contacted")
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/partners', json={"name": "User supplied partner"}).status_code, 409)
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/partners', json={"name": "  "}).status_code, 422)
        organization, headers = self.organization("Other organization")
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/partners', json={"name": "Forbidden"}, headers=headers).status_code, 404)
        self.assertEqual(self.client.patch(f'/api/events/{event["id"]}/archive', json={"archived": True}, headers=headers).status_code, 404)

    def signup_organization(self, name="New organization", email="organization@example.test", password="password123456"):
        response = self.client.post("/api/organizations/signup", json={"organization_name": name, "email": email, "password": password})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), {"Authorization": "Bearer " + response.json()["token"]}

    def test_organization_signup_normalizes_and_atomically_rejects_duplicates_or_claims(self):
        signed, headers = self.signup_organization("  New   organization  ", " ORGANIZATION@EXAMPLE.TEST ")
        self.assertEqual(signed["organization"]["name"], "New organization")
        self.assertEqual(signed["organizer"]["email"], "organization@example.test")
        self.assertEqual(self.client.get("/api/organizations/me", headers=headers).json()["organization"], signed["organization"])
        with main.Session() as db:
            account = db.get(main.OrganizationAccount, signed["organizer"]["id"])
            self.assertNotIn("password123456", account.password_hash)
            self.assertTrue(main._password_matches("password123456", account.password_hash))
            stored_session = db.get(main.OrganizationSession, hashlib.sha256(signed["token"].encode()).hexdigest())
            self.assertIsNotNone(stored_session)
            self.assertNotEqual(stored_session.token_hash, signed["token"])
            organizations_before = db.query(main.Organization).count()
        for changes in ({"email": "ORGANIZATION@example.test", "organization_name": "Different name"},
                        {"email": "different@example.test", "organization_name": "new organization"},
                        {"email": "claim@example.test", "organization_name": "EventPilot"}):
            response = self.client.post("/api/organizations/signup", json={"password": "password123456", **changes})
            self.assertEqual(response.status_code, 409, response.text)
        injected = self.client.post("/api/organizations/signup", json={"organization_name": "Injected", "email": "inject@example.test", "password": "password123456", "organization_id": 1})
        self.assertEqual(injected.status_code, 422)
        with main.Session() as db:
            self.assertEqual(db.query(main.Organization).count(), organizations_before)
            self.assertEqual(db.query(main.OrganizationAccount).count(), 2)

    def test_new_organization_signup_does_not_require_old_key_encryption(self):
        with patch.dict(os.environ, {"ORGANIZATION_KEY_ENCRYPTION_KEY": "", "ORGANIZER_API_KEY": ""}):
            signed, headers = self.signup_organization()
            event = self.client.post("/api/events", json={"name": "Own event", "date": (main.today_in_delhi()+timedelta(days=10)).isoformat()}, headers=headers)
            self.assertEqual(event.status_code, 201, event.text)
            self.assertEqual(event.json()["organization_id"], signed["organization"]["id"])
            self.assertEqual(len(self.client.get("/api/events", headers=headers).json()), 1)

    def test_organization_sessions_persist_expire_and_logout_revokes_only_current(self):
        signed, first = self.signup_organization()
        login = self.client.post("/api/organizations/login", json={"email": " ORGANIZATION@EXAMPLE.TEST ", "password": "password123456"})
        self.assertEqual(login.status_code, 200)
        second = {"Authorization": "Bearer " + login.json()["token"]}
        with TestClient(main.app) as fresh:
            self.assertEqual(fresh.get("/api/organizations/me", headers=first).status_code, 200)
        self.assertEqual(self.client.post("/api/organizations/logout", headers=first).json(), {"ok": True})
        self.assertEqual(self.client.get("/api/organizations/me", headers=first).status_code, 401)
        self.assertEqual(self.client.get("/api/organizations/me", headers=second).status_code, 200)
        with main.Session.begin() as db:
            db.get(main.OrganizationSession, hashlib.sha256(login.json()["token"].encode()).hexdigest()).expires_at = main._now_utc().replace(tzinfo=None)-timedelta(seconds=1)
        self.assertEqual(self.client.get("/api/events", headers=second).status_code, 401)
        self.assertEqual(self.client.post("/api/organizations/login", json={"email": "organization@example.test", "password": "incorrect"}).status_code, 401)

    def test_organization_change_password_revokes_other_sessions_and_preserves_current(self):
        signed, first = self.signup_organization()
        second_login = self.client.post("/api/organizations/login", json={"email": "organization@example.test", "password": "password123456"}).json()
        second = {"Authorization": "Bearer " + second_login["token"]}
        self.assertEqual(self.client.post("/api/organizations/change-password", json={"current_password": "incorrect", "new_password": "123456"}, headers=first).status_code, 400)
        self.assertEqual(self.client.get("/api/organizations/me", headers=first).status_code, 200)
        changed = self.client.post("/api/organizations/change-password", json={"current_password": "password123456", "new_password": "123456"}, headers=first)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.client.get("/api/organizations/me", headers=first).status_code, 200)
        self.assertEqual(self.client.get("/api/organizations/me", headers=second).status_code, 401)
        self.assertEqual(self.client.post("/api/organizations/login", json={"email": "organization@example.test", "password": "password123456"}).status_code, 401)
        self.assertEqual(self.client.post("/api/organizations/login", json={"email": "organization@example.test", "password": "123456"}).status_code, 200)

    def test_organization_and_attendee_sessions_are_separate_even_for_same_email(self):
        organization, org_headers = self.signup_organization(email="shared@example.test")
        attendee, attendee_headers = self.account("shared@example.test")
        self.assertEqual(self.client.get("/api/attendees/me", headers=org_headers).status_code, 401)
        self.assertEqual(self.client.get("/api/organizations/me", headers=attendee_headers).status_code, 401)
        self.assertEqual(self.client.get("/api/events", headers=attendee_headers).status_code, 401)
        with TestClient(main.app) as public:
            self.assertEqual(public.get("/api/events", headers={"X-Organizer-Key": "test-organizer-secret"}).status_code, 401)
        self.assertEqual(self.client.get("/api/events", headers=org_headers).json(), [])
        self.assertNotIn("password_hash", json.dumps(organization))

    def test_ai_uses_server_event_context_and_rejects_another_organization(self):
        own = self.create_event()
        organization, other_headers = self.signup_organization()
        foreign = self.client.post("/api/events", json={"name": "Other private event", "date": (main.today_in_delhi()+timedelta(days=10)).isoformat()}, headers=other_headers).json()
        denied = self.client.post("/api/ai", json={"event_id": foreign["id"], "prompt": "Summarize"})
        self.assertEqual(denied.status_code, 404)
        request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(main.httpx, "AsyncClient", FakeAIClient):
            FakeAIClient.response = httpx.Response(200, json={"choices": [{"message": {"content": "Useful advice"}}]}, request=request)
            answer = self.client.post("/api/ai", json={"event_id": own["id"], "prompt": "What should I do next?"})
            self.assertEqual(answer.status_code, 200, answer.text)
            system = FakeAIClient.request_payload["messages"][0]["content"]
            self.assertIn("Verified current event summary from the server", system)
            self.assertIn(own["name"], system)
            self.assertNotIn(foreign["name"], system)
            self.assertNotIn("ticket_code", system)

    def test_email_routes_queue_owned_addresses_and_preserve_ticket_scope(self):
        import email_service
        event = self.create_event()
        account, attendee_headers = self.account("mail-attendee@example.test")
        ticket = self.registration(event["id"], attendee_headers)
        other_event = self.create_event()
        other_account, other_attendee = self.account("other-mail@example.test")
        with patch.object(email_service, "send_message") as send:
            orgmail = self.client.post("/api/organizations/email", json={"recipient": "injected@example.test"})
            self.assertEqual(orgmail.status_code, 200, orgmail.text)
            self.assertEqual(orgmail.json()["email_status"]["status"], "queued")
            ownmail = self.client.post(f'/api/attendees/registrations/{ticket["id"]}/email', headers=attendee_headers)
            self.assertEqual(ownmail.status_code, 200, ownmail.text)
            self.assertEqual(self.client.post(f'/api/events/{event["id"]}/attendees/{ticket["id"]}/email').status_code, 200)
            with main.Session() as db:
                rows = db.query(main.EmailOutbox).all()
                self.assertTrue(any(row.recipient == "default@example.test" and "organization:" in row.trigger_key for row in rows))
                ticket_mail = next(row for row in rows if row.trigger_key == f'ticket:{ticket["id"]}')
                self.assertEqual(ticket_mail.recipient, "mail-attendee@example.test")
                self.assertIn(ticket["ticket_code"], ticket_mail.body)
                self.assertNotIn("injected@example.test", " ".join(row.recipient for row in rows))
                count = len(rows)
            self.assertEqual(self.client.post(f'/api/attendees/registrations/{ticket["id"]}/email', headers=other_attendee).status_code, 404)
            self.assertEqual(self.client.post(f'/api/events/{other_event["id"]}/attendees/{ticket["id"]}/email').status_code, 404)
            with main.Session() as db:
                self.assertEqual(db.query(main.EmailOutbox).count(), count)
            send.assert_not_called()

    def test_missing_email_configuration_keeps_queue_pending_and_worker_failures_are_truthful(self):
        import email_service
        with patch.object(email_service, "is_configured", return_value=False), patch.object(email_service, "send_message") as send:
            queued = self.client.post("/api/organizations/email")
            self.assertEqual(queued.status_code, 200)
            self.assertIn("Waiting", queued.json()["email_status"]["message"])
            self.assertEqual(main.process_email_outbox()["claimed"], 0)
            send.assert_not_called()
            with main.Session() as db:
                self.assertEqual(db.query(main.EmailOutbox).one().status, "Pending")
        with patch.object(email_service, "is_configured", return_value=True), patch.object(email_service, "send_message", side_effect=email_service.EmailDeliveryFailed("secret provider details")):
            outcome = main.process_email_outbox()
            self.assertEqual(outcome["failed"], 1)
        with main.Session() as db:
            row = db.query(main.EmailOutbox).one()
            self.assertEqual(row.status, "Failed")
            self.assertNotIn("secret", row.last_error)
        retry = self.client.post("/api/organizations/email")
        self.assertEqual(retry.json()["email_status"]["status"], "queued")

    def test_registration_and_checkin_automatically_queue_once_without_sending_in_http(self):
        import email_service
        event = self.create_event()
        account, headers = self.account()
        with patch.object(email_service, "send_message") as send:
            ticket = self.registration(event["id"], headers)
            self.assertEqual(ticket["email_status"]["status"], "queued")
            duplicate = self.client.post("/api/attendees/registrations", json={"event_id": event["id"]}, headers=headers)
            self.assertEqual(duplicate.status_code, 409)
            checkin = self.client.post("/api/check-in", json={"event_id": event["id"], "ticket_code": ticket["ticket_code"]})
            self.assertEqual(checkin.status_code, 200, checkin.text)
            self.assertEqual(checkin.json()["email_status"]["queued"], 1)
            self.assertEqual(self.client.post("/api/check-in", json={"event_id": event["id"], "ticket_code": ticket["ticket_code"]}).status_code, 409)
            with main.Session() as db:
                rows = db.query(main.EmailOutbox).order_by(main.EmailOutbox.id).all()
                self.assertEqual([row.trigger_key for row in rows], [f'registration:{ticket["id"]}', f'check-in:{ticket["id"]}'])
                self.assertTrue(all(row.html_body and row.body and row.recipient == account["attendee"]["email"] for row in rows))
            send.assert_not_called()

    def test_announcement_publish_and_event_start_queue_every_attendee_and_start_is_idempotent(self):
        event = self.create_event()
        first, headers = self.account()
        second, other = self.account("second@example.test")
        self.registration(event["id"], headers)
        self.registration(event["id"], other)
        published = self.client.post(f'/api/events/{event["id"]}/announcements', json={"title": "Important update", "message": "Doors open at 9"})
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["email_status"]["queued"], 2)
        started = self.client.post(f'/api/events/{event["id"]}/start')
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["email_status"]["queued"], 2)
        repeated = self.client.post(f'/api/events/{event["id"]}/start')
        self.assertEqual(repeated.json()["started_at"], started.json()["started_at"])
        self.assertEqual(self.client.get("/api/events").json()[0]["lifecycle_status"], "Started")
        self.assertEqual(self.client.get("/api/public/events").json()[0]["lifecycle_status"], "Started")
        with main.Session() as db:
            rows = db.query(main.EmailOutbox).filter_by(trigger_key=f'event:{event["id"]}:started').all()
            self.assertEqual(len(rows), 2)
            self.assertEqual({row.recipient for row in rows}, {first["attendee"]["email"], second["attendee"]["email"]})
            self.assertEqual(db.query(main.EventLifecycle).count(), 1)

    def test_completed_task_queues_full_checklist_to_owning_organization(self):
        event = self.create_event()
        snapshot = self.client.get(f'/api/events/{event["id"]}/dashboard').json()
        task = next(task for task in snapshot["tasks"] if task["role"] == "Operations")
        completed = self.client.post(f'/api/tasks/{task["id"]}/done', json={"actor": "Operations"})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["email_status"]["queued"], 1)
        with main.Session() as db:
            row = db.query(main.EmailOutbox).filter_by(trigger_key=f'task:{task["id"]}:done').one()
            self.assertEqual(row.recipient, "default@example.test")
            self.assertTrue(all(item["title"] in row.body for item in snapshot["tasks"]))
            self.assertIn("Done", row.body)
            self.assertIn("Open", row.body)
        self.assertEqual(self.client.post(f'/api/tasks/{task["id"]}/done', json={"actor": "Operations"}).status_code, 409)
        with main.Session() as db:
            self.assertEqual(db.query(main.EmailOutbox).filter_by(trigger_key=f'task:{task["id"]}:done').count(), 1)

    def test_outbox_worker_sends_html_once_and_scoped_retry_only_retries_failures(self):
        import email_service
        event = self.create_event()
        account, headers = self.account()
        self.registration(event["id"], headers)
        with patch.object(email_service, "is_configured", return_value=True), patch.object(email_service, "send_message") as send:
            outcome = main.process_email_outbox(limit=1)
            self.assertEqual(outcome["sent"], 1)
            self.assertTrue(send.call_args.kwargs["html_body"])
            self.assertEqual(main.process_email_outbox(limit=1)["claimed"], 0)
            self.assertEqual(send.call_count, 1)
        delivery = self.client.get(f'/api/events/{event["id"]}/emails').json()
        self.assertEqual(delivery["email_status"]["sent"], 1)
        self.assertEqual(delivery["emails"][0]["status"], "Sent")
        retry = self.client.post(f'/api/events/{event["id"]}/emails/retry')
        self.assertEqual(retry.json()["email_status"]["queued"], 0)
        announcement = self.client.post(f'/api/events/{event["id"]}/announcements', json={"title": "Follow up", "message": "A new update"})
        with patch.object(email_service, "is_configured", return_value=True), patch.object(email_service, "send_message", side_effect=email_service.EmailDeliveryFailed()):
            self.assertEqual(main.process_email_outbox()["failed"], 1)
        retry = self.client.post(f'/api/events/{event["id"]}/emails/retry')
        self.assertEqual(retry.json()["email_status"]["sent"], 1)
        self.assertEqual(retry.json()["email_status"]["queued"], 1)
        foreign, foreign_headers = self.signup_organization()
        self.assertEqual(self.client.get(f'/api/events/{event["id"]}/emails', headers=foreign_headers).status_code, 404)
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/emails/retry', headers=foreign_headers).status_code, 404)
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/start', headers=foreign_headers).status_code, 404)

    def test_interrupted_outbox_requires_explicit_retry_and_does_not_resend_automatically(self):
        import email_service
        self.client.post("/api/organizations/email")
        with main.Session.begin() as db:
            row = db.query(main.EmailOutbox).one()
            row.status = "Sending"
            row.last_attempt_at = main._now_utc().replace(tzinfo=None)-timedelta(minutes=16)
        with patch.object(email_service, "is_configured", return_value=True), patch.object(email_service, "send_message") as send:
            self.assertEqual(main.process_email_outbox()["claimed"], 0)
            send.assert_not_called()
        with main.Session() as db:
            self.assertEqual(db.query(main.EmailOutbox).one().status, "Failed")
            self.assertIn("interrupted", db.query(main.EmailOutbox).one().last_error)

    def test_ai_email_uses_saved_response_and_organization_address_not_client_input(self):
        import email_service
        event = self.create_event()
        request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), patch.object(main.httpx, "AsyncClient", FakeAIClient):
            FakeAIClient.response = httpx.Response(200, json={"choices": [{"message": {"content": "Saved useful advice"}}]}, request=request)
            advice = self.client.post("/api/ai", json={"event_id": event["id"], "prompt": "Help me"}).json()
        with patch.object(email_service, "send_message") as send:
            mailed = self.client.post(f'/api/ai/responses/{advice["response_id"]}/email', json={"recipient": "injected@example.test", "html": "Bad content"})
            self.assertEqual(mailed.status_code, 200, mailed.text)
            self.assertEqual(mailed.json()["email_status"]["queued"], 1)
            repeated = self.client.post(f'/api/ai/responses/{advice["response_id"]}/email')
            self.assertEqual(repeated.json()["email_status"]["queued"], 1)
            foreign, foreign_headers = self.signup_organization()
            self.assertEqual(self.client.post(f'/api/ai/responses/{advice["response_id"]}/email', headers=foreign_headers).status_code, 404)
            with main.Session() as db:
                row = db.query(main.EmailOutbox).filter_by(trigger_key=f'advice:{advice["response_id"]}').one()
                self.assertEqual(row.recipient, "default@example.test")
                self.assertIn("Saved useful advice", row.body)
                self.assertNotIn("Bad content", row.html_body)
            send.assert_not_called()

    def test_event_handoff_hides_and_cancels_former_organization_notifications(self):
        import email_service
        event = self.create_event()
        attendee, headers = self.account()
        self.registration(event["id"], headers)
        new_org, new_headers = self.signup_organization()
        moved = self.admin.put(f'/api/organizations/{new_org["organization"]["id"]}/events/{event["id"]}')
        self.assertEqual(moved.status_code, 200)
        visible = self.client.get(f'/api/events/{event["id"]}/emails', headers=new_headers).json()
        self.assertEqual(visible["emails"], [])
        self.assertEqual(self.client.get(f'/api/events/{event["id"]}/emails').status_code, 404)
        with patch.object(email_service, "is_configured", return_value=True), patch.object(email_service, "send_message") as send:
            self.assertEqual(main.process_email_outbox()["failed"], 1)
            send.assert_not_called()
        with main.Session() as db:
            self.assertIn("ownership changed", db.query(main.EmailOutbox).one().last_error)
        self.assertEqual(self.client.post(f'/api/events/{event["id"]}/emails/retry', headers=new_headers).json()["email_status"]["queued"], 0)

    def test_maximum_valid_announcement_has_no_html_storage_truncation(self):
        from sqlalchemy.dialects import mysql
        quoted = '"' * 200
        event = self.client.post("/api/events", json={"name": quoted, "venue": quoted, "audience": quoted, "date": (main.today_in_delhi()+timedelta(days=10)).isoformat()}).json()
        attendee, headers = self.account()
        self.registration(event["id"], headers)
        published = self.client.post(f'/api/events/{event["id"]}/announcements', json={"title": '"'*160, "message": '"'*10000})
        self.assertEqual(published.status_code, 200, published.text)
        with main.Session() as db:
            row = db.query(main.EmailOutbox).filter_by(trigger_key=f'announcement:{published.json()["id"]}').one()
            self.assertGreater(len(row.html_body.encode()), 65535)
            self.assertIn('"'*10000, row.body)
        self.assertEqual(main.EmailOutbox.__table__.c.html_body.type.compile(dialect=mysql.dialect()), "LONGTEXT")

    def organization(self, name="Organization A"):
        response = self.admin.post("/api/organizations", json={"name": name})
        self.assertEqual(response.status_code, 201)
        row = response.json()
        email = f'organization-{row["id"]}@example.test'
        with main.Session.begin() as db:
            db.add(main.OrganizationAccount(organization_id=row["id"], email=email,
                                           password_hash=main._hash_password("password123"), created_at=main._now_utc().isoformat()))
        login = self.client.post("/api/organizations/login", json={"email": email, "password": "password123"})
        self.assertEqual(login.status_code, 200, login.text)
        return row, {"Authorization": "Bearer " + login.json()["token"]}

    def test_legacy_organization_keys_are_retained_but_no_longer_authenticate(self):
        organization, headers = self.organization()
        listing = self.admin.get("/api/organizations")
        self.assertEqual(listing.headers["cache-control"], "no-store")
        self.assertEqual(set(listing.json()[0]), {"id", "name", "key_preview", "created_at", "event_count"})
        reveal = self.admin.get(f'/api/organizations/{organization["id"]}/key')
        legacy_key = reveal.json()["key"]
        with main.Session() as db:
            stored = db.get(main.Organization, organization["id"])
            self.assertNotIn(legacy_key, stored.ciphertext)
            self.assertEqual(stored.api_key_hash, hashlib.sha256(legacy_key.encode()).hexdigest())
            self.assertEqual(Fernet(os.environ["ORGANIZATION_KEY_ENCRYPTION_KEY"].encode()).decrypt(stored.ciphertext.encode()).decode(), legacy_key)
        with TestClient(main.app) as public:
            self.assertEqual(public.get("/api/events", headers={"X-Organizer-Key": legacy_key}).status_code, 401)
            self.assertEqual(public.get("/api/events", headers={"X-Organizer-Key": "test-organizer-secret"}).status_code, 401)
            self.assertEqual(public.get(f'/api/organizations/{organization["id"]}/key').status_code, 401)
        access = self.client.get("/api/access", headers=headers)
        self.assertEqual(access.json(), {"role": "organizer", "organization": {"id": organization["id"], "name": organization["name"]}})
        self.assertEqual(access.headers["cache-control"], "no-store")
        # Normal sessions have no legacy administration access.
        self.assertEqual(self.client.get("/api/organizations", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/organizations", headers=headers, json={"name": "Not allowed"}).status_code, 403)
        self.assertEqual(self.client.get(f'/api/organizations/{organization["id"]}/key', headers=headers).status_code, 403)
        schema = self.client.get("/openapi.json").json()
        self.assertNotIn("/api/organizations/{oid}/key", schema["paths"])
        self.assertNotIn("/api/organizations", schema["paths"])

    def test_owner_assignment_moves_event_access_without_changing_event_data(self):
        a, a_headers = self.organization()
        b, b_headers = self.organization("Organization B")
        event = self.create_event()
        default = next(row for row in self.admin.get("/api/organizations").json() if row["name"] == "EventPilot")
        self.assertEqual(event["organization_id"], default["id"])
        self.assertEqual(self.admin.put(f'/api/organizations/{a["id"]}/events/{event["id"]}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/events/{event["id"]}/dashboard', headers=a_headers).status_code, 200)
        self.assertEqual(self.client.get(f'/api/events/{event["id"]}/dashboard', headers=b_headers).status_code, 404)
        self.assertEqual(self.client.put(f'/api/organizations/{b["id"]}/events/{event["id"]}', headers=a_headers).status_code, 403)
        self.assertEqual(self.admin.put(f'/api/organizations/{b["id"]}/events/{event["id"]}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/events/{event["id"]}/dashboard', headers=a_headers).status_code, 404)
        snapshot = self.client.get(f'/api/events/{event["id"]}/dashboard', headers=b_headers).json()
        self.assertEqual(snapshot["event"]["name"], event["name"])
        self.assertEqual(snapshot["event"]["organization_name"], "Organization B")
        self.assertEqual(len(snapshot["tasks"]), len(main.TEMPLATES))
        self.assertEqual(self.admin.put(f'/api/organizations/{b["id"]}/events/99999').status_code, 404)
        self.assertEqual(self.admin.put(f'/api/organizations/99999/events/{event["id"]}').status_code, 404)

    def test_organization_creation_and_demo_are_bound_atomically_to_caller(self):
        a, headers = self.organization()
        b, _ = self.organization("Organization B")
        payload = {"name": "Own event", "date": (date.today()+timedelta(days=10)).isoformat()}
        own = self.client.post("/api/events", json=payload, headers=headers)
        self.assertEqual(own.status_code, 201)
        self.assertEqual(own.json()["organization_id"], a["id"])
        denied = self.client.post("/api/events", json={**payload, "organization_id": b["id"]}, headers=headers)
        self.assertEqual(denied.status_code, 403)
        demo = self.client.post("/api/demo", headers=headers)
        self.assertEqual(demo.status_code, 201)
        self.assertEqual(demo.json()["organization_id"], a["id"])
        self.assertEqual(len(self.client.get("/api/events", headers=headers).json()), 2)
        self.assertEqual(self.client.post("/api/events", json={**payload, "organization_id": 99999}).status_code, 403)
        with patch.object(main, "EventOrganization", side_effect=RuntimeError("simulated membership failure")):
            with self.assertRaises(RuntimeError):
                self.client.post("/api/events", json=payload, headers=headers)
        with main.Session() as db:
            self.assertEqual(db.query(main.Event).count(), 2)
            self.assertEqual(db.query(main.EventOrganization).count(), 2)

    def test_every_organizer_resource_route_enforces_tenant_ownership_before_mutation(self):
        a, a_headers = self.organization()
        b, b_headers = self.organization("Organization B")
        own = self.client.post("/api/demo", headers=a_headers).json()
        foreign = self.client.post("/api/demo", headers=b_headers).json()
        eid = foreign["id"]
        snapshot = self.client.get(f"/api/events/{eid}/dashboard", headers=b_headers).json()
        self.assertEqual([e["id"] for e in self.client.get("/api/events", headers=a_headers).json()], [own["id"]])
        for suffix in ("dashboard", "intelligence", "attendees", "recommendation-actions"):
            self.assertEqual(self.client.get(f"/api/events/{eid}/{suffix}", headers=a_headers).status_code, 404)
        metric = {"date": date.today().isoformat(), "reach": 1000, "clicks": 100, "registrations": 20, "confirmations": 15, "attendance": 0}
        self.assertEqual(self.client.post(f"/api/events/{eid}/metrics", json=metric, headers=a_headers).status_code, 404)
        task = next(t for t in snapshot["tasks"] if t["status"] == "Open")
        self.assertEqual(self.client.post(f'/api/tasks/{task["id"]}/done', json={}, headers=a_headers).status_code, 404)
        for kind, rows, status in (("partners", snapshot["partners"], "Activated"), ("logistics", snapshot["logistics"], "Done"),
                                    ("content", snapshot["content"], "Approved"), ("escalations", snapshot["escalations"], "Rejected")):
            self.assertTrue(rows)
            self.assertEqual(self.client.patch(f'/api/{kind}/{rows[0]["id"]}', json={"status": status}, headers=a_headers).status_code, 404)
        self.assertEqual(self.client.post(f"/api/events/{eid}/escalate", params={"sim_date": foreign["date"]}, headers=a_headers).status_code, 404)
        self.assertEqual(self.client.post(f"/api/events/{eid}/announcements", json={"title": "No", "message": "No"}, headers=a_headers).status_code, 404)
        self.assertEqual(self.client.post("/api/recommendation-actions", json={"event_id": eid, "recommendation": "No"}, headers=a_headers).status_code, 404)
        _, attendee_headers = self.account()
        ticket = self.registration(eid, attendee_headers)
        for extra in ({}, {"event_id": own["id"]}, {"event_id": eid}):
            self.assertEqual(self.client.post("/api/check-in", json={"ticket_code": ticket["ticket_code"], **extra}, headers=a_headers).status_code, 404)
        with main.Session() as db:
            self.assertEqual(db.get(main.Task, task["id"]).status, "Open")
            self.assertEqual(db.query(main.Announcement).filter_by(event_id=eid).count(), 0)
            self.assertEqual(db.query(main.RecommendationAction).filter_by(event_id=eid).count(), 0)
            self.assertIsNone(db.get(main.Registration, ticket["id"]).checked_in_at)
        self.assertEqual(self.client.post("/api/check-in", json={"ticket_code": ticket["ticket_code"]}, headers=b_headers).status_code, 200)

    def test_default_membership_bootstrap_preserves_legacy_and_is_idempotent(self):
        event = self.create_event()
        with main.Session.begin() as db:
            db.query(main.EventOrganization).filter_by(event_id=event["id"]).delete()
            db.add(main.Event(id=77, name="Another legacy event", type="Hackathon", date=date(2026, 12, 1), start_date=date(2026, 10, 1),
                              format="Hybrid", audience="Legacy", target_regs=750, venue="Delhi", objectives="Preserve"))
        main.bootstrap_organizations()
        main.bootstrap_organizations()
        with main.Session() as db:
            self.assertEqual(db.query(main.Organization).count(), 1)
            self.assertEqual(db.query(main.EventOrganization).count(), 2)
            self.assertEqual(db.get(main.Event, 77).objectives, "Preserve")
            self.assertEqual(db.query(main.Task).filter_by(event_id=event["id"]).count(), len(main.TEMPLATES))

    def test_missing_or_changed_encryption_does_not_disclose_keys_or_break_owner_reads(self):
        organization, headers = self.organization()
        with patch.dict(os.environ, {"ORGANIZATION_KEY_ENCRYPTION_KEY": ""}):
            with TestClient(main.app, headers={"X-Organizer-Key": "test-organizer-secret"}) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                self.assertEqual(self.client.get("/api/events").status_code, 200)
                self.assertEqual(self.client.get("/api/access").status_code, 200)
                self.assertEqual(client.get("/api/organizations").status_code, 503)
                self.assertEqual(client.post("/api/organizations", json={"name": "No"}).status_code, 503)
        with patch.dict(os.environ, {"ORGANIZATION_KEY_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            response = self.admin.get(f'/api/organizations/{organization["id"]}/key')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(headers["Authorization"], response.text)
            self.assertNotIn("InvalidToken", response.text)

    def test_public_catalogue_and_personal_tickets_have_org_labels_without_secrets(self):
        organization, org_headers = self.organization()
        event = self.client.post("/api/events", json={"name": "Public", "date": (date.today()+timedelta(days=3)).isoformat()}, headers=org_headers).json()
        catalogue = self.client.get("/api/public/events")
        self.assertEqual(catalogue.json()[0]["organization_name"], organization["name"])
        _, headers = self.account()
        ticket = self.registration(event["id"], headers)
        history = self.client.get("/api/attendees/registrations", headers=headers)
        self.assertEqual(ticket["event"]["organization_id"], organization["id"])
        for response in (catalogue, history, self.client.get("/api/events", headers=org_headers), self.client.get(f'/api/events/{event["id"]}/dashboard', headers=org_headers)):
            self.assertNotIn(org_headers["Authorization"], response.text)
            self.assertNotIn("ciphertext", response.text)
            self.assertNotIn("api_key_hash", response.text)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        scratch.cleanup()
