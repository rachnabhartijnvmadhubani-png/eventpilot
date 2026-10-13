"""EventPilot email layouts: escaped HTML and matching plain-text content.

Templates accept mappings or ORM-like records and only select the fields needed
for the recipient. They never include passwords, access keys, or session tokens.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from datetime import timezone
from html import escape
import os
import re
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo


DEFAULT_APP_URL = "http://3.110.130.114:8080/"
_GREEN = "#17644c"
_TEXT = "#182b24"
_MUTED = "#62746b"
_LABELS = {
    "organization": "ORGANIZATION ACCOUNT",
    "ticket": "YOUR EVENT TICKET",
    "registration": "REGISTRATION DETAILS",
    "check-in": "CHECK-IN DETAILS",
    "event-start": "EVENT-DAY INFORMATION",
    "announcement": "EVENT UPDATE",
    "checklist": "EVENT PLAN",
    "groq": "EVENT ADVICE",
}


@dataclass(frozen=True)
class EmailContent:
    subject: str
    body: str
    html_body: str

    @property
    def plain(self):
        return self.body

    @property
    def html(self):
        return self.html_body


def _get(record, key, default=""):
    if record is None:
        return default
    value = record.get(key, default) if isinstance(record, Mapping) else getattr(record, key, default)
    return default if value is None else value


def _text(value):
    return "" if value is None else str(value)


def _subject(value):
    return " ".join(_text(value).split())[:240] or "EventPilot update"


def _date_text(value):
    if isinstance(value, (date, datetime)):
        return value.strftime("%d %b %Y")
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return _text(value) or "To be confirmed"


def _timestamp_text(value):
    try:
        timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
    except (TypeError, ValueError):
        return _text(value)


def _app_url(app_url=None):
    configured = app_url or os.getenv("EVENTPILOT_PUBLIC_URL") or os.getenv("PUBLIC_APP_URL") or DEFAULT_APP_URL
    try:
        parsed = urlsplit(configured.strip())
        # A bad configuration must not produce executable or credential-bearing links.
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password
                or any(character in configured for character in "\r\n\t<>\" ")
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            return DEFAULT_APP_URL
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", ""))
    except (AttributeError, ValueError):
        return DEFAULT_APP_URL


def _cta_url(path, app_url=None):
    base = _app_url(app_url)
    path = _text(path).lstrip("/")
    if (path.startswith(("http:", "https:", "javascript:", "data:"))
            or any(character in path for character in "\r\n<>\"")):
        path = ""
    return base + path


def _rows_html(rows):
    return "".join(
        '<tr><td style="width:34%;padding:10px 12px 10px 0;vertical-align:top;'
        f'font-size:12px;line-height:18px;color:{_MUTED};">{escape(_text(label))}</td>'
        '<td style="padding:10px 0;vertical-align:top;font-size:14px;line-height:20px;'
        f'color:{_TEXT};word-break:break-word;">{escape(_text(value)).replace(chr(10), "<br>")}</td></tr>'
        for label, value in rows
    )


def _section_html(title, rows):
    if not rows:
        return ""
    return (f'<h2 style="margin:26px 0 10px;font-size:15px;line-height:22px;color:{_TEXT};">{escape(_text(title))}</h2>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;border-collapse:collapse;background:#f5f8f6;border:1px solid #e1e9e4;">'
            '<tr><td style="padding:4px 16px;"><table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">{_rows_html(rows)}</table></td></tr></table>')


def _inline_markdown(value):
    safe = escape(value)
    safe = re.sub(r"`([^`\n]+)`", r'<code style="padding:2px 4px;background:#eff3f0;font-family:monospace;">\1</code>', safe)
    safe = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", safe)
    return safe


def _markdown_html(value):
    """Small safe subset: paragraphs, headings, lists and code; raw HTML is text."""
    result, in_code, code = [], False, []
    for line in _text(value).splitlines():
        if line.strip().startswith("```"):
            if in_code:
                result.append('<pre style="margin:12px 0;padding:14px;background:#f5f8f6;white-space:pre-wrap;'
                              f'word-break:break-word;font-size:12px;line-height:18px;">{escape(chr(10).join(code))}</pre>')
                code = []
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        if not line.strip():
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        bullet = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if heading:
            result.append(f'<h3 style="margin:22px 0 8px;font-size:16px;line-height:23px;color:{_GREEN};">{_inline_markdown(heading.group(1))}</h3>')
        elif bullet:
            result.append('<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
                          f'<tr><td style="width:18px;vertical-align:top;padding:3px 0;color:{_GREEN};">&#8226;</td>'
                          f'<td style="padding:3px 0;font-size:14px;line-height:23px;color:{_TEXT};">{_inline_markdown(bullet.group(1))}</td></tr></table>')
        else:
            result.append(f'<p style="margin:9px 0;font-size:14px;line-height:23px;color:{_TEXT};">{_inline_markdown(line)}</p>')
    if code:
        result.append('<pre style="padding:14px;background:#f5f8f6;white-space:pre-wrap;word-break:break-word;">'
                      + escape("\n".join(code)) + "</pre>")
    return "".join(result)


def _plain_markdown(value):
    lines = []
    for line in _text(value).splitlines():
        if line.strip().startswith("```"):
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
        line = re.sub(r"`([^`\n]+)`", r"\1", line)
        lines.append(line)
    return "\n".join(lines)


def _event_rows(event):
    if event is None:
        return []
    rows = [("Event", _get(event, "name", "Your event")), ("Date", _date_text(_get(event, "date")))]
    for label, key in [("Format", "format"), ("Venue / joining location", "venue"),
                       ("Audience", "audience"), ("Organization", "organization_name")]:
        value = _get(event, key)
        if value:
            rows.append((label, value))
    return rows


def _task_content(tasks):
    tasks = list(tasks or [])
    completed = sum(_get(task, "status") == "Done" for task in tasks)
    open_count = len(tasks) - completed
    plain = [f"Task checklist: {len(tasks)} total | {completed} completed | {open_count} remaining"]
    html = (f'<h2 style="margin:26px 0 10px;font-size:15px;line-height:22px;color:{_TEXT};">Full task checklist</h2>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background:#f5f8f6;border:1px solid #e1e9e4;width:100%;"><tr>'
            f'<td style="padding:16px;font-size:13px;color:{_MUTED};"><strong style="font-size:24px;color:{_GREEN};">{completed}</strong><br>Completed</td>'
            f'<td style="padding:16px;font-size:13px;color:{_MUTED};"><strong style="font-size:24px;color:{_TEXT};">{open_count}</strong><br>Remaining</td></tr></table>')
    if not tasks:
        return "No tasks have been recorded for this event yet.", html + '<p style="font-size:14px;line-height:23px;">No tasks have been recorded for this event yet.</p>'
    html += ('<table aria-label="Event task checklist" width="100%" cellpadding="0" cellspacing="0" border="0" '
             'style="width:100%;border-collapse:collapse;margin-top:16px;table-layout:fixed;">'
             '<thead><tr>' + "".join(f'<th scope="col" style="padding:10px 5px;border-bottom:2px solid #dce6df;'
                                   f'text-align:left;font-size:11px;color:{_MUTED};width:{width};">{label}</th>'
                                   for label, width in [("Task", "42%"), ("Owner", "20%"), ("Due", "20%"), ("Status", "18%")])
             + '</tr></thead><tbody>')
    for task in tasks:
        title, role = _get(task, "title", "Untitled task"), _get(task, "role", "Unassigned")
        due, status = _date_text(_get(task, "due_date")), _get(task, "status", "Open")
        color = _GREEN if status == "Done" else _TEXT
        plain.append(f"[{status}] {title} | Owner: {role} | Due: {due}")
        html += '<tr>' + "".join(
            f'<td style="padding:12px 5px;vertical-align:top;border-bottom:1px solid #e5ece7;'
            f'font-size:12px;line-height:18px;word-break:break-word;color:{color if index == 3 else _TEXT};">{escape(_text(value))}</td>'
            for index, value in enumerate((title, role, due, status))) + '</tr>'
    return "\n".join(plain), html + '</tbody></table>'


def render_email(kind, title, lines=None, data=None, *, subject=None, cta_label=None, cta_path=None, app_url=None):
    """Build a full email; data supports event, sections, rows, tasks and advice.

    sections is [{title: str, rows: [(label, value), ...]}]. Records may be
    dictionaries or ORM objects. Arbitrary data keys are ignored.
    """
    data = data or {}
    kind = _text(kind).lower().replace("_", "-")
    if isinstance(lines, str):
        lines = [lines]
    lines = list(lines or [])
    sections = []
    event = _get(data, "event")
    if event:
        sections.append(("Event summary", _event_rows(event)))
    if _get(data, "rows"):
        rows = _get(data, "rows")
        sections.append((_get(data, "section_title", "Details"), list(rows.items()) if isinstance(rows, Mapping) else list(rows)))
    for section in _get(data, "sections", []):
        rows = _get(section, "rows", [])
        sections.append((_get(section, "title", "Details"), list(rows.items()) if isinstance(rows, Mapping) else list(rows)))
    attendee_kind = kind in {"ticket", "registration", "check-in", "event-start", "announcement"}
    cta_path = cta_path if cta_path is not None else "attendee.html" if attendee_kind else ""
    cta_label = cta_label or ("Open attendee portal" if attendee_kind else "Open organizer portal")
    url = _cta_url(cta_path, app_url)
    preheader = _text(lines[0] if lines else title)[:170]
    content = "".join(f'<p style="margin:0 0 14px;font-size:15px;line-height:25px;color:{_TEXT};">'
                      + escape(_text(line)).replace("\n", "<br>") + '</p>' for line in lines)
    plain = [_text(title), "", *(_text(line) for line in lines)]
    for section_title, rows in sections:
        content += _section_html(section_title, rows)
        plain.extend(["", _text(section_title), *(f"{label}: {value}" for label, value in rows)])
    if "tasks" in data:
        task_plain, task_html = _task_content(_get(data, "tasks", []))
        content += task_html
        plain.extend(["", task_plain])
    advice = _get(data, "advice")
    if advice:
        content += '<div style="margin-top:24px;border-top:1px solid #e1e9e4;padding-top:8px;">' + _markdown_html(advice) + '</div>'
        plain.extend(["", _plain_markdown(advice)])
    plain.extend(["", f"{cta_label}: {url}", "", "EventPilot | Team gracebug"])
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(_text(title))}</title><style>@media only screen and (max-width:600px){{.email-padding{{padding:24px 18px!important;}}.email-container{{width:100%!important;}}}}</style></head>
<body style="margin:0;padding:0;background:#eef3ef;font-family:Arial,Helvetica,sans-serif;color:{_TEXT};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background:#eef3ef;">
<tr><td align="center" style="padding:28px 10px;">
<!--[if mso]><table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" class="email-container" width="640" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;background:#ffffff;border:1px solid #dae5dd;border-collapse:collapse;">
<tr><td style="padding:25px 32px;border-bottom:1px solid #e5ece7;background:#ffffff;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td style="font-size:24px;line-height:30px;font-weight:bold;letter-spacing:-0.5px;color:{_GREEN};">EventPilot</td><td align="right" style="font-size:11px;line-height:17px;color:{_MUTED};">by team gracebug</td></tr></table>
</td></tr><tr><td class="email-padding" style="padding:32px;">
<p style="margin:0 0 10px;font-size:10px;line-height:16px;letter-spacing:1.4px;font-weight:bold;color:{_GREEN};">{escape(_LABELS.get(kind, "EVENTPILOT UPDATE"))}</p>
<h1 style="margin:0 0 18px;font-size:27px;line-height:35px;letter-spacing:-0.5px;color:{_TEXT};">{escape(_text(title))}</h1>
{content}
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px;"><tr><td bgcolor="{_GREEN}" style="background:{_GREEN};border-radius:5px;">
<a href="{escape(url, quote=True)}" style="display:inline-block;padding:14px 22px;font-size:14px;line-height:19px;font-weight:bold;color:#ffffff;text-decoration:none;">{escape(_text(cta_label))}</a>
</td></tr></table>
</td></tr><tr><td style="padding:22px 32px;background:#f8faf8;border-top:1px solid #e5ece7;font-size:11px;line-height:18px;color:{_MUTED};">
EventPilot &middot; Team gracebug<br>Event details and updates in one place.
</td></tr></table><!--[if mso]></td></tr></table><![endif]--></td></tr></table></body></html>'''
    return EmailContent(_subject(subject or title), "\n".join(plain), html)


def organization_details(organization, email, *, app_url=None):
    return render_email("organization", "Your organization is ready", [
        f"Welcome to EventPilot, {_get(organization, 'name', 'your team')}.",
        "Use your organizer email to sign in, plan events and manage participants."],
        {"rows": [("Organization", _get(organization, "name")), ("Organizer email", email)],
         "section_title": "Account details"}, subject="Your EventPilot organization account", app_url=app_url)


def _ticket_rows(registration):
    rows = [("Ticket code", _get(registration, "ticket_code", "Not available")),
            ("Registration status", _get(registration, "status", "Not recorded"))]
    if _get(registration, "checked_in_at"):
        rows.append(("Checked in", _timestamp_text(_get(registration, "checked_in_at"))))
    return rows


def ticket(event, registration, attendee=None, *, app_url=None):
    name = _get(attendee, "name", "there")
    return render_email("ticket", "Your event ticket", [
        f"Hello {name}, here are your registration details.",
        "Keep your ticket code private and show it to the organizer at check-in."],
        {"event": event, "sections": [{"title": "Ticket details", "rows": _ticket_rows(registration)}]},
        subject=f"Your ticket for {_get(event, 'name', 'the event')}", app_url=app_url)


def registration_confirmation(event, registration, attendee=None, *, app_url=None):
    confirmed = _get(registration, "status") in {"Confirmed", "Attended"}
    return render_email("registration", "Your registration is confirmed" if confirmed else "Your registration details", [
        f"Hello {_get(attendee, 'name', 'there')}, your registration details are below.",
        "You can find your ticket and the latest event updates in the attendee portal."],
        {"event": event, "sections": [{"title": "Registration details", "rows": _ticket_rows(registration)}]},
        subject=f"Registration: {_get(event, 'name', 'your event')}", app_url=app_url)


def check_in_confirmation(event, registration, attendee=None, *, app_url=None):
    checked_in = bool(_get(registration, "checked_in_at"))
    return render_email("check-in", "You are checked in" if checked_in else "Your check-in details", [
        f"Hello {_get(attendee, 'name', 'there')}.",
        "Your arrival has been recorded. Enjoy the event." if checked_in else "Check-in has not been recorded for this registration yet."],
        {"event": event, "sections": [{"title": "Check-in details", "rows": _ticket_rows(registration)}]},
        subject=f"Check-in: {_get(event, 'name', 'your event')}", app_url=app_url)


def event_start(event, attendee=None, registration=None, *, app_url=None):
    data = {"event": event}
    if registration is not None:
        data["sections"] = [{"title": "Your registration", "rows": _ticket_rows(registration)}]
    return render_email("event-start", "Your event-day information", [
        f"Hello {_get(attendee, 'name', 'there')}, here are the details for {_get(event, 'name', 'your event')}.",
        "Check the venue or joining location below and keep your ticket ready."], data,
        subject=f"Event-day details: {_get(event, 'name', 'your event')}", app_url=app_url)


def announcement(event, announcement, attendee=None, *, app_url=None):
    return render_email("announcement", _get(announcement, "title", "An update from your organizer"), [
        f"An update for {_get(event, 'name', 'your event')}.", _get(announcement, "message")],
        {"event": event}, subject=f"{_get(event, 'name', 'EventPilot')}: {_get(announcement, 'title', 'Event update')}", app_url=app_url)


def task_checklist(event, tasks, organizer_email=None, *, app_url=None):
    return render_email("checklist", "Your event plan at a glance", [
        f"Here is the current task checklist for {_get(event, 'name', 'your event')}.",
        "Review completed work, owners and upcoming tasks in one place."],
        {"event": event, "tasks": list(tasks or [])},
        subject=f"Task checklist: {_get(event, 'name', 'your event')}", app_url=app_url)


def groq_advice(event, advice, organizer_email=None, *, app_url=None):
    return render_email("groq", "Advice for your event", [
        f"Here is the advice requested for {_get(event, 'name', 'your event')}.",
        "Review the suggestions against your latest event numbers before taking action."],
        {"event": event, "advice": advice},
        subject=f"Event advice: {_get(event, 'name', 'your event')}", app_url=app_url)
