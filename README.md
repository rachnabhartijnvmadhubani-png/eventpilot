# EventPilot — team gracebug

EventPilot helps an organization plan an event, see its progress and manage attendees. It has a FastAPI/MySQL backend and an HTML/CSS/JavaScript frontend. The Groq copilot gives suggestions for the selected event. Delhi demonstration events contain fictional data.

## Live app

- Organization dashboard: http://3.110.130.114:8080/
- Attendee portal: http://3.110.130.114:8080/attendee.html
- Swagger: http://3.110.130.114:8080/docs
- Health: http://3.110.130.114:8080/api/health

Port 80 serves the same app. Deployment targets only Sportforward instance `i-06b48111a6c256e97` in Mumbai. The separate Sports Friends instance must not be used.

## Organization signup and login

1. Choose **Sign up** and enter an organization name, email and password.
2. Sign in with that email and password. No organization access key is needed.
3. Only that organization's events, dashboard and attendees open.
4. Use **Account settings** to change the password. Use **Sign out** when finished.

A new signup creates a new private organization. It cannot claim an existing organization by entering its name. Existing EventPilot events are retained under their original organization; the server operator can privately enroll its owner using the migration tool below. An attendee account is separate from an organization account, even when their email addresses are the same.

Passwords are hashed. Organizer and attendee sessions use separate hashed tokens in MySQL, expire after 24 hours and survive restarts. Signing out revokes the current session. A password change revokes the organization's other sessions. The browser keeps its token only for the current tab session, with an in-memory fallback if storage is unavailable. Old organization access keys no longer authenticate dashboard users. The protected server maintenance secret is kept for administration and migration.

In Swagger, call organization signup or login, copy the returned token and put it in **Authorize → OrganizationBearer**. Personal attendee requests use a separate token in **AttendeeBearer**. Public event listing, health, signup and login do not require authorization.

## Event workflow

1. Start on **Events**. Create an event from its brief, or open an existing event.
2. **Overview** shows actual registrations, the target, event health and next actions. **Add numbers** records cumulative activity from outside the attendee portal.
3. **Plan** contains generated tasks. Filter by role, complete work and use the expandable sections for partners, content and operations.
4. **People** contains attendees, announcements and ticket check-in. Share the event's attendee link so people can register and receive a ticket.
5. **Ask copilot** uses Groq to suggest improvements for the selected event. It receives event progress and plans, rather than attendee passwords or ticket details.
6. Edit or reschedule an event without losing saved metrics, tickets or completed tasks. Changing its type or format regenerates an untouched plan; those fields are locked after activity starts.
7. Archive an event to hide it from active lists and new public registration. Restore it to use it again. Existing tickets remain saved.

The main workflow is **Events → Overview / Plan / People**. Account settings and the copilot are secondary controls.

## Real numbers and demos

Manual metrics represent activity outside this portal; portal activity is added separately. Do not enter totals that already include portal registrations. Saving the same date replaces that date's manual record. Dates use India calendar days.

New events start with zero activity. Forecasts need observations on at least two dates, unless the event has ended. They estimate results from recent growth and 1,000 simulations. Recovery task completion can add a labeled simulation uplift; ordinary venue or budget tasks do not invent registrations. Estimates and template assumptions are not observed attendee activity.

**Load demo** opens a labeled fictional Delhi hackathon and reuses the organization's active demo. Demo events are separate from real events and hidden from ordinary public discovery. Simulation controls are optional.

## Email notifications

Account settings has **Email my organization**. The People list and attendee tickets have **Email ticket**. Recipients come from the signed-in account or the selected event's saved registration, so the browser cannot send to an arbitrary address.

Registrations and check-in send attendee confirmations. **Publish update** queues a separate styled email for every registered attendee, and **Start event** sends the event-start message once. Completing an Event plan task emails the organization login address a checklist showing completed and remaining tasks. Each saved Groq response has **Email this advice**. Recipients come from saved accounts; the SMTP sender does not change anyone's login or notification address.

The durable email queue is delivered by `eventpilot-mail.service`. Registration, task completion and publishing succeed even when the email provider is unavailable. Open **People → Email delivery** to view queued, sent or failed messages and retry failed deliveries. Repeated start, completion and advice-email clicks reuse the same notification. An interrupted send is marked failed after 15 minutes; check the mailbox before retrying because the provider may already have accepted it. Existing announcements and completed tasks are not emailed retroactively.

Set `SMTP_USERNAME`, `SMTP_FROM_EMAIL` and `SMTP_APP_PASSWORD` in the protected server environment, with `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587` and `SMTP_STARTTLS=true`. Set `PUBLIC_APP_URL` to the public frontend URL, then restart `eventpilot` and `eventpilot-mail`. With missing SMTP settings, notifications stay queued and the app explains why. The app password stays on the server and is never sent to the frontend. Deployment preserves existing SMTP settings. Every message has escaped HTML, inline email CSS and a plain-text alternative.

## Local setup

Use Python 3.11 or newer and MySQL 8. For a new local database, edit the example password in `setup.sql` before running it. Do not reset an existing database with that file.

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

Set `DATABASE_URL` in `backend/.env` and optionally `GROQ_API_KEY`. Keep an existing installation's protected settings when upgrading. Start the backend:

```bash
cd backend
.venv/bin/uvicorn main:app --reload --port 8000
```

From the project folder, serve the frontend in a second terminal:

```bash
python3 -m http.server 5500 --directory frontend
```

Open http://localhost:5500/ and http://localhost:8000/docs. Other local frontend ports must be included in `CORS_ORIGINS`. Charts, Markdown, sanitization, QR and Swagger assets are included locally.

## Deploy

```bash
bash deploy/deploy.sh
```

The script verifies the Sportforward instance ID before writing. It uses the existing SSH key path by default; override it with `EVENTPILOT_SSH_KEY` if needed. Never upload a private SSH key with application source.

Before replacing code, the installer backs up code, settings and MySQL under `/var/backups/eventpilot/<UTC timestamp>/`. It preserves database and Groq credentials, installs dependencies, adds tables without resetting existing records, restarts the service and checks readiness.

- Backend: `/opt/eventpilot/app`
- Python environment: `/opt/eventpilot/venv`
- Frontend: `/var/www/eventpilot`
- Protected settings: `/etc/eventpilot/eventpilot.env`
- Service: `eventpilot`

To give an existing organization its first email login, the server operator can run the private migration tool with its exact ID and name. This refuses existing accounts or duplicate emails and never reassigns events. It generates a temporary password into a new private file; change it after first login.

```bash
sudo /opt/eventpilot/venv/bin/python /opt/eventpilot/create_organization_login.py \
  --organization-id EXISTING_ID --organization-name EXISTING_NAME \
  --email OWNER_EMAIL --credentials-file /root/eventpilot-initial-login.txt
```

## Verify

```bash
backend/.venv/bin/python -m unittest discover -s backend/tests -v
sudo systemctl status eventpilot --no-pager
curl --fail http://127.0.0.1:8001/api/health
sudo nginx -t
```

Regression tests use isolated SQLite and mocked AI calls. `backend/tests/mysql_concurrency.py` checks races only in a fresh `eventpilot_test_*` MySQL database; never point it at live data.

## Current limits

This is a hackathon prototype. The deployment remains HTTP as requested; HTTPS is needed to encrypt passwords in transit. Camera scanning needs localhost or HTTPS, so the public app supports manual ticket entry and QR-image reading. Email verification, password recovery, login rate limits and SMS delivery are not implemented. Email delivery needs valid SMTP configuration and provider acceptance does not guarantee inbox placement. Announcements also appear in the attendee portal. Groq requires its protected API configuration.
