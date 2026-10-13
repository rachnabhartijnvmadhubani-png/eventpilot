"""SMTP delivery checks use mocks only; no message is sent."""

import os
import smtplib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import email_service


class EmailServiceTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "SMTP_USERNAME": "sender@gmail.com",
            "SMTP_APP_PASSWORD": "abcdefghijklmnop",
        }
        self.environment = patch.dict(os.environ, self.settings, clear=True)
        self.environment.start()
        self.smtp = patch.object(email_service.smtplib, "SMTP")
        self.factory = self.smtp.start()
        self.connection = self.factory.return_value.__enter__.return_value
        self.connection.send_message.return_value = {}

    def tearDown(self):
        self.smtp.stop()
        self.environment.stop()

    def send(self, recipient="attendee@example.com", subject="Event details", body="Your event details."):
        return email_service.send_message(recipient, subject, body)

    def test_default_gmail_delivery_uses_starttls_and_one_recipient(self):
        self.assertIsNone(self.send())
        self.factory.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
        self.connection.starttls.assert_called_once()
        self.connection.login.assert_called_once_with("sender@gmail.com", "abcdefghijklmnop")
        message = self.connection.send_message.call_args.args[0]
        self.assertEqual(message["From"], "sender@gmail.com")
        self.assertEqual(message["To"], "attendee@example.com")
        self.assertEqual(message["Subject"], "Event details")
        self.assertEqual(message.get_content_type(), "text/plain")
        self.assertIn("Your event details.", message.get_content())
        self.assertEqual(self.connection.send_message.call_args.kwargs,
                         {"from_addr": "sender@gmail.com", "to_addrs": ["attendee@example.com"]})
        methods = [call[0] for call in self.connection.method_calls]
        self.assertEqual(methods, ["ehlo", "starttls", "ehlo", "login", "send_message"])

    def test_gmail_grouped_app_password_and_explicit_sender(self):
        with patch.dict(os.environ, {"SMTP_APP_PASSWORD": "abcd efgh ijkl mnop", "SMTP_FROM_EMAIL": "events@gmail.com"}):
            self.send()
        self.connection.login.assert_called_once_with("sender@gmail.com", "abcdefghijklmnop")
        self.assertEqual(self.connection.send_message.call_args.args[0]["From"], "events@gmail.com")

    def test_html_delivery_retains_accessible_plain_text_alternative(self):
        email_service.send_message("attendee@example.com", "Event details", "Your event details.",
                                   html_body="<html><body><h1>Event details</h1><p>Your event details.</p></body></html>")
        message = self.connection.send_message.call_args.args[0]
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertEqual([part.get_content_type() for part in message.iter_parts()], ["text/plain", "text/html"])
        self.assertIn("Your event details.", message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("<h1>Event details</h1>", message.get_body(preferencelist=("html",)).get_content())

    def test_html_must_be_nonempty_text_when_supplied(self):
        for html_body in ["", "   ", 123]:
            with self.subTest(html_body=html_body), self.assertRaises(email_service.EmailDeliveryFailed):
                email_service.send_message("attendee@example.com", "Event details", "Body", html_body=html_body)
        self.factory.assert_not_called()

    def test_configuration_check_never_connects_and_tracks_missing_settings(self):
        self.assertTrue(email_service.is_configured())
        self.assertTrue(email_service.config_available())
        with patch.dict(os.environ, {"SMTP_APP_PASSWORD": "CHANGE_ME_APP_PASSWORD"}):
            self.assertFalse(email_service.is_configured())
        self.factory.assert_not_called()

    def test_custom_port_and_explicit_starttls_disable(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.company.org", "SMTP_PORT": "2525", "SMTP_STARTTLS": "false"}):
            self.send()
        self.factory.assert_called_once_with("smtp.company.org", 2525, timeout=10)
        self.connection.starttls.assert_not_called()

    def test_missing_and_placeholder_settings_fail_before_network(self):
        for field, value in [("SMTP_USERNAME", ""), ("SMTP_APP_PASSWORD", ""),
                             ("SMTP_USERNAME", "CHANGE_ME_GMAIL_ADDRESS"),
                             ("SMTP_FROM_EMAIL", "CHANGE_ME_GMAIL_ADDRESS"),
                             ("SMTP_APP_PASSWORD", "CHANGE_ME_APP_PASSWORD"),
                             ("SMTP_APP_PASSWORD", "your-app-password"),
                             ("SMTP_APP_PASSWORD", "123456")]:
            with self.subTest(field=field, value=value), patch.dict(os.environ, {field: value}):
                with self.assertRaises(email_service.EmailNotConfigured):
                    self.send()
        self.factory.assert_not_called()

    def test_invalid_configuration_fails_before_network(self):
        for field, value in [("SMTP_PORT", "bad"), ("SMTP_PORT", "0"), ("SMTP_PORT", "70000"),
                             ("SMTP_HOST", "bad\r\nhost"), ("SMTP_USERNAME", "not-an-email"),
                             ("SMTP_FROM_EMAIL", "sender@gmail.com\r\nBcc: other@example.com"),
                             ("SMTP_STARTTLS", "uncertain")]:
            with self.subTest(field=field, value=value), patch.dict(os.environ, {field: value}):
                with self.assertRaises(email_service.EmailNotConfigured):
                    self.send()
        self.factory.assert_not_called()

    def test_recipient_must_be_one_valid_address_without_header_injection(self):
        for recipient in ["", "not-an-email", "a@example.com,b@example.com", "a@example.com\r\nBcc:b@example.com",
                          "Name <a@example.com>", "a..b@example.com", "a@bad..example.com"]:
            with self.subTest(recipient=recipient):
                with self.assertRaises(email_service.EmailDeliveryFailed):
                    self.send(recipient=recipient)
        self.factory.assert_not_called()

    def test_subject_cannot_inject_headers_and_body_must_be_text(self):
        for subject, body in [("", "Body"), ("Title\r\nBcc:other@example.com", "Body"), ("Title", ""), ("Title", None)]:
            with self.subTest(subject=subject):
                with self.assertRaises(email_service.EmailDeliveryFailed):
                    self.send(subject=subject, body=body)
        self.factory.assert_not_called()

    def test_authentication_error_does_not_expose_provider_details(self):
        self.connection.login.side_effect = smtplib.SMTPAuthenticationError(535, b"PRIVATE-PROVIDER-DETAILS")
        with self.assertRaises(email_service.EmailDeliveryFailed) as caught:
            self.send()
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.connection.send_message.assert_not_called()

    def test_network_failure_returns_only_generic_error(self):
        self.factory.side_effect = OSError("PRIVATE-CONNECTION-DETAILS")
        with self.assertRaises(email_service.EmailDeliveryFailed) as caught:
            self.send()
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_refused_recipient_is_reported_as_delivery_failure(self):
        self.connection.send_message.return_value = {"attendee@example.com": (550, b"PRIVATE-DELIVERY-DETAILS")}
        with self.assertRaises(email_service.EmailDeliveryFailed) as caught:
            self.send()
        self.assertNotIn("PRIVATE", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
