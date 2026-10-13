"""Template checks use fictional records and do not send mail."""

from datetime import date
from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import email_templates as templates


class Elements(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class EmailTemplateTests(unittest.TestCase):
    def setUp(self):
        self.event = {"id": 12, "name": "Delhi Builders Hackathon", "date": "2026-11-06",
                      "format": "Hybrid", "venue": "Demo hall, New Delhi", "audience": "Student builders"}
        self.registration = {"id": 9, "ticket_code": "EP-FICTIONAL-TICKET", "status": "Confirmed", "checked_in_at": None}
        self.attendee = {"name": "Asha"}

    def test_each_template_has_coherent_html_plain_text_and_public_cta(self):
        functions = [
            lambda: templates.organization_details({"name": "Gracebug"}, "organizer@example.com"),
            lambda: templates.ticket(self.event, self.registration, self.attendee),
            lambda: templates.registration_confirmation(self.event, self.registration, self.attendee),
            lambda: templates.check_in_confirmation(self.event, self.registration, self.attendee),
            lambda: templates.event_start(self.event, self.attendee, self.registration),
            lambda: templates.announcement(self.event, {"title": "Arrival details", "message": "Please arrive at 9 AM."}),
            lambda: templates.task_checklist(self.event, []),
            lambda: templates.groq_advice(self.event, "## Next steps\n- Review **outreach** results."),
        ]
        for function in functions:
            content = function()
            with self.subTest(subject=content.subject):
                self.assertIsInstance(content, templates.EmailContent)
                self.assertTrue(content.subject)
                self.assertIn("EventPilot | Team gracebug", content.body)
                self.assertIn("Team gracebug", content.html_body)
                self.assertEqual(content.plain, content.body)
                self.assertEqual(content.html, content.html_body)
                elements = Elements(content.html_body).elements
                self.assertTrue(any(tag == "h1" for tag, _ in elements))
                self.assertTrue(any(tag == "table" and attrs.get("role") == "presentation" for tag, attrs in elements))
                links = [attrs["href"] for tag, attrs in elements if tag == "a"]
                self.assertEqual(len(links), 1)
                self.assertTrue(links[0].startswith("http://3.110.130.114:8080/"))
                self.assertFalse(any(tag in {"script", "iframe", "img"} for tag, _ in elements))

    def test_event_announcements_and_recipient_names_are_escaped(self):
        malicious = '<script>alert("unsafe")</script>'
        event = {**self.event, "name": malicious, "venue": '\" onmouseover=\"unsafe'}
        content = templates.announcement(event, {"title": '<img src=x onerror="unsafe">', "message": malicious})
        elements = Elements(content.html_body).elements
        self.assertFalse(any(tag in {"script", "img"} for tag, _ in elements))
        self.assertFalse(any(key.startswith("on") for _, attrs in elements for key in attrs))
        self.assertIn("&lt;script&gt;", content.html_body)
        self.assertIn(malicious, content.body)
        ticket = templates.ticket(self.event, self.registration, {"name": '<svg onload="unsafe">'})
        self.assertNotIn("<svg", ticket.html_body)

    def test_subjects_remove_header_control_lines(self):
        event = {**self.event, "name": "Demo\r\nBcc: other@example.com"}
        content = templates.ticket(event, self.registration)
        self.assertNotIn("\r", content.subject)
        self.assertNotIn("\n", content.subject)

    def test_account_details_never_select_password_key_or_token_fields(self):
        organization = {"name": "Private organization", "password": "PRIVATE-PASSWORD",
                        "ciphertext": "PRIVATE-CIPHERTEXT", "api_key_hash": "PRIVATE-KEY", "token": "PRIVATE-TOKEN"}
        content = templates.organization_details(organization, "organizer@example.com")
        for secret in ["PRIVATE-PASSWORD", "PRIVATE-CIPHERTEXT", "PRIVATE-KEY", "PRIVATE-TOKEN"]:
            self.assertNotIn(secret, content.body)
            self.assertNotIn(secret, content.html_body)

    def test_checklist_contains_all_tasks_and_only_done_is_completed(self):
        tasks = [
            {"title": "Publish announcement", "role": "Marketing", "due_date": "2026-11-04", "status": "Done"},
            {"title": "Confirm venue", "role": "Operations", "due_date": "2026-11-01", "status": "Open"},
            {"title": "Confirm speaker", "role": "Programme", "due_date": "2026-11-03", "status": "Blocked"},
            {"title": "Review metrics", "role": "Leadership", "due_date": "2026-11-05", "status": "Completed"},
        ]
        content = templates.task_checklist(self.event, tasks)
        self.assertIn("4 total | 1 completed | 3 remaining", content.body)
        for task in tasks:
            self.assertIn(task["title"], content.body)
            self.assertIn(task["title"], content.html_body)
            self.assertIn(f'[{task["status"]}]', content.body)
        self.assertIn('aria-label="Event task checklist"', content.html_body)
        self.assertIn('scope="col"', content.html_body)

    def test_checkin_and_registration_headings_follow_recorded_state(self):
        pending = {**self.registration, "status": "Pending"}
        self.assertNotIn("registration is confirmed", templates.registration_confirmation(self.event, pending).body)
        unchecked = templates.check_in_confirmation(self.event, self.registration)
        self.assertIn("has not been recorded", unchecked.body)
        checked = templates.check_in_confirmation(self.event, {**self.registration, "status": "Attended",
                                                  "checked_in_at": "2026-11-06T04:00:00+00:00"})
        self.assertIn("You are checked in", checked.body)
        self.assertIn("06 Nov 2026, 09:30 AM IST", checked.body)

    def test_advice_basic_markdown_is_formatted_and_raw_html_never_runs(self):
        advice = '## Outreach\n- Improve **registration** copy.\n<script>unsafe()</script>\n[bad](javascript:unsafe())\n```\n<img src=x>\n```'
        content = templates.groq_advice(self.event, advice)
        elements = Elements(content.html_body).elements
        self.assertTrue(any(tag == "h3" for tag, _ in elements))
        self.assertIn("<strong>registration</strong>", content.html_body)
        self.assertFalse(any(tag in {"script", "img"} for tag, _ in elements))
        self.assertFalse(any(attrs.get("href", "").startswith("javascript:") for _, attrs in elements))
        self.assertIn("&lt;script&gt;", content.html_body)
        self.assertNotIn("**registration**", content.body)

    def test_bad_cta_configuration_cannot_create_executable_or_external_link(self):
        for url in ["javascript:unsafe()", "https://user:password@example.com/", 'https://example.com/\"><img src=x>', "https://example.com:invalid/"]:
            content = templates.ticket(self.event, self.registration, app_url=url)
            links = [attrs["href"] for tag, attrs in Elements(content.html_body).elements if tag == "a"]
            self.assertEqual(links, [templates.DEFAULT_APP_URL + "attendee.html"])
        content = templates.render_email("update", "Details", ["Details"], cta_path="javascript:unsafe()")
        self.assertNotIn('href="javascript:', content.html_body)

    def test_configured_base_url_preserves_project_path_and_clears_query(self):
        content = templates.ticket(self.event, self.registration, app_url="https://events.example.org/project/?secret=ignored")
        self.assertIn("https://events.example.org/project/attendee.html", content.body)
        self.assertNotIn("secret=ignored", content.body)

    def test_orm_like_records_and_date_objects_are_supported(self):
        class Event:
            name = "Delhi event"
            date = date(2026, 11, 6)
            format = "Online"
            venue = "Joining details"
        content = templates.ticket(Event(), self.registration)
        self.assertIn("06 Nov 2026", content.body)
        self.assertIn("Joining details", content.html_body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
