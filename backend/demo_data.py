"""Fictional Delhi presentation data; writes only new, explicitly marked events.

This module does not import the application or connect to a database. ORM classes
are supplied by the caller, so it is safe to import from both the API and CLI.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def today_in_delhi() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


@dataclass(frozen=True)
class DemoModels:
    Event: type
    Task: type
    Metric: type
    Uplift: type
    Decision: type
    Partner: type
    Content: type
    Logistics: type
    Escalation: type


@dataclass(frozen=True)
class DemoProfile:
    key: str
    name: str
    kind: str
    format: str
    venue: str
    target: int
    planning_days: int
    days_left: int
    registrations: int
    reach_per_reg: int
    clicks_per_reg: float
    confirmation_rate: float
    growth: float
    partner_stages: tuple[str, ...]
    completed_tasks: frozenset[int]
    missed_content: frozenset[int]


PROFILES = (
    DemoProfile(
        "delhi-innovators-v1", "Delhi Innovators Hackathon", "Hackathon", "Hybrid",
        "Demo Innovation Hall, Dwarka, New Delhi (fictional venue)",
        750, 30, 12, 400, 125, 4.1, 0.69, 1.0,
        ("Activated",) * 3 + ("Confirmed",) * 5 + ("Contacted",) * 4,
        frozenset({0, 5, 8, 11, 14}), frozenset({3, 5}),
    ),
    DemoProfile(
        "delhi-campus-impact-v1", "Delhi Campus Impact Hackathon", "Hackathon", "Offline",
        "Demo Maker Commons, Saket, New Delhi (fictional venue)",
        450, 28, 7, 132, 72, 4.8, 0.55, 1.45,
        ("Activated",) + ("Confirmed",) * 2 + ("Contacted",) * 6,
        frozenset({0, 14}), frozenset({2, 3, 4, 6}),
    ),
    DemoProfile(
        "delhi-event-growth-v1", "Delhi Event Growth Webinar", "Webinar", "Online",
        "Online from New Delhi — fictional demonstration stream",
        300, 21, 0, 327, 120, 3.4, 0.88, 1.55,
        ("Activated",) * 3 + ("Confirmed",) * 2 + ("Contacted",),
        frozenset(set(range(16)) - {3, 4}), frozenset(),
    ),
)

TASKS = (
    ("Publish event announcement", "Marketing", 28, 25),
    ("Launch college-ambassador push", "Marketing", 20, 60),
    ("Improve registration conversion", "Marketing", 18, 48),
    ("Launch targeted social campaign", "Marketing", 15, 35),
    ("Send confirmation reminders", "Marketing", 3, 25),
    ("Contact partners", "Partnerships", 25, 25),
    ("Confirm partners", "Partnerships", 14, 30),
    ("Activate partner promotions", "Partnerships", 10, 35),
    ("Confirm venue", "Operations", 24, 25),
    ("Confirm AV and internet", "Operations", 12, 25),
    ("Prepare check-in", "Operations", 4, 20),
    ("Invite judges", "Programme", 22, 25),
    ("Confirm speakers", "Programme", 16, 25),
    ("Publish agenda", "Programme", 8, 20),
    ("Approve budget", "Leadership", 27, 25),
    ("Readiness review", "Leadership", 5, 30),
)

PARTNERS = (
    "Yamuna Spark Labs", "Capital Bridge Ventures", "Aravali Makers Guild",
    "Nayi Disha Robotics", "MetroByte Studio", "Campus Relay Network",
    "Lotus Circuit Collective", "Northstar Student Fund", "Dilli Data Cooperative",
    "Greenlane Cloud Works", "SevaTech Sandbox", "Kiteframe Design Lab",
)

CONTENT = (
    "Demo event announcement", "Student builder FAQ", "Fictional mentor reveal",
    "Challenge-track spotlight", "Ambassador outreach kit", "Registration walkthrough",
    "Fictional partner spotlight", "Speaker and agenda preview", "Travel or joining guide",
    "Team-formation reminder", "Final registration call", "Event-day checklist",
)


def create_demo_event(db, models: DemoModels, reference_date: date | None = None,
                      profile: DemoProfile = PROFILES[0]):
    """Add a complete fictional scenario to the caller's transaction; never commit."""
    today = reference_date or today_in_delhi()
    end = today + timedelta(days=profile.days_left)
    start = end - timedelta(days=profile.planning_days)
    marker = f"[seed:{profile.key}]"
    event = models.Event(
        name=profile.name, type=profile.kind, date=end, format=profile.format,
        audience="Delhi NCR students, campus ambassadors and first-time event organisers",
        target_regs=profile.target, venue=profile.venue, start_date=start,
        objectives=(f"[DEMO] {marker} Entirely fictional Delhi presentation scenario. "
                    "All partners, sponsors, venues, metrics and decisions are synthetic; "
                    "no real affiliations or attendance claims. "
                    f"Target {profile.target} registrations; demonstrate student-led "
                    "innovation, accessible event operations and practical outreach."),
    )
    db.add(event)
    db.flush()

    elapsed = (today - start).days
    initial_registrations = max(3, round(profile.registrations * 0.1))
    for index in range(elapsed + 1):
        day = start + timedelta(days=index)
        progress = index / max(1, elapsed)
        registrations = initial_registrations + round((profile.registrations - initial_registrations) * progress ** profile.growth)
        confirmations = round(registrations * profile.confirmation_rate)
        db.add(models.Metric(
            event_id=event.id, date=day, reach=max(100, registrations * profile.reach_per_reg),
            clicks=max(registrations, round(registrations * profile.clicks_per_reg)),
            registrations=registrations, confirmations=confirmations,
            attendance=round(confirmations * 0.86) if day == end else 0,
        ))

    tasks = []
    for index, (title, role, offset, impact) in enumerate(TASKS):
        if profile.kind == "Webinar" and title == "Confirm venue":
            continue
        due = max(start, end - timedelta(days=offset))
        if profile.kind == "Webinar":
            title = {"Invite judges": "Invite webinar facilitators",
                     "Confirm AV and internet": "Test webinar stream and captions",
                     "Prepare check-in": "Prepare webinar joining instructions",
                     "Launch targeted social campaign": "Launch session-recap social campaign"}.get(title, title)
            if index == 3:
                due = end + timedelta(days=1)
            elif index == 4:
                due = end
        task = models.Task(event_id=event.id, title=title, role=role, due_date=due,
                           priority="Critical" if offset >= 20 else "Important",
                           status="Done" if index in profile.completed_tasks else "Open", impact=impact)
        db.add(task)
        tasks.append(task)

    extras = (
        (("Recruit student volunteer captains", "Operations", -4),
         ("Validate Wi-Fi failover plan", "Operations", 2),
         ("Review privacy and accessibility checklist", "Leadership", -2),
         ("Publish Delhi prototype challenge tracks", "Programme", 1))
        if profile.kind == "Hackathon" else
        (("Upload recording and slides", "Programme", 1),
         ("Send attendee feedback form", "Marketing", 1),
         ("Publish attendance summary", "Operations", 2),
         ("Review speaker feedback", "Leadership", 3))
    )
    for title, role, offset in extras:
        task = models.Task(event_id=event.id, title=title, role=role,
                           due_date=today + timedelta(days=offset), priority="Important", status="Open", impact=18)
        db.add(task)
        tasks.append(task)
    db.flush()

    for index, task in enumerate(t for t in tasks if t.status == "Done"):
        if index >= 6:
            break
        before = min(88, 36 + index * 7 + (12 if profile.kind == "Webinar" else 0))
        db.add(models.Decision(event_id=event.id, actor=task.role,
                               action=f"Demo simulation: {task.title}",
                               before_value=before, after_value=before + 4 + index % 3))
        db.add(models.Uplift(event_id=event.id, task_id=task.id, gain=task.impact * 0.8 / 7,
                            start_date=start + timedelta(days=3 + index), days=7))

    overdue = [task for task in tasks if task.status == "Open" and task.due_date < today]
    for index, task in enumerate(overdue):
        age = (today - task.due_date).days
        closed = index == len(overdue) - 1
        db.add(models.Escalation(task_id=task.id,
                                 level="Leadership" if age >= 5 else "Team Lead" if age >= 3 else "Owner",
                                 status="Closed" if closed else "Open", decision="Approved" if closed else "Pending"))

    for name, stage in zip(PARTNERS, profile.partner_stages):
        db.add(models.Partner(event_id=event.id, name=f"{name} (Demo)", stage=stage))

    for index, topic in enumerate(CONTENT):
        slot_date = start + timedelta(days=round(profile.planning_days * (index + 1) / len(CONTENT)))
        status = "Approved" if slot_date <= today and index not in profile.missed_content else "Pending"
        db.add(models.Content(event_id=event.id, date=slot_date, topic=topic,
                              channel=("Instagram", "Email", "LinkedIn", "WhatsApp")[(index + 1) % 4], status=status))

    items = (
        ("Fictional venue booking", "AV and internet", "Volunteer coordination", "Demo catering plan",
         "Accessibility support", "Wi-Fi failover", "Safety desk", "Registration desks")
        if profile.kind == "Hackathon" else
        ("Webinar room setup", "Speaker audio check", "Live caption test", "Stream backup", "Attendance export", "Recording release")
    )
    done_indices = {0, 2, 3} if profile.key == PROFILES[0].key else {0, 3}
    if profile.kind == "Webinar":
        done_indices = {0, 1, 2, 3}
    for index, item in enumerate(items):
        db.add(models.Logistics(event_id=event.id, item=item,
                                status="Done" if index in done_indices else "Open",
                                due_date=today + timedelta(days=index - 3 if profile.kind == "Hackathon" else max(0, index - 3))))
    db.flush()
    return event


def seed_demo_events(db, models: DemoModels, reference_date: date | None = None):
    """Atomically create missing marked scenarios, preserving all existing records.

    Call with a session that has no active transaction. Existing demo scenarios
    are reused unchanged, including presentation interactions and edited metrics.
    """
    today = reference_date or today_in_delhi()
    result = []
    with db.begin():
        # Reverse insertion makes the primary hackathon the newest list item.
        for profile in reversed(PROFILES):
            marker = f"[seed:{profile.key}]"
            event = db.query(models.Event).filter(models.Event.objectives.contains(marker)).order_by(models.Event.id).first()
            created = event is None
            if created:
                event = create_demo_event(db, models, today, profile)
            result.append({"id": event.id, "name": event.name, "created": created,
                           "date": event.date.isoformat(), "start_date": event.start_date.isoformat()})
    return list(reversed(result))
