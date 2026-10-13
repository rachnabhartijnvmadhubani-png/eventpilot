# EventPilot: current version guide

EventPilot has two separate portals. Organizations manage their own events, and attendees register for events and receive tickets. Team: gracebug.

## Open the app

1. Open http://3.110.130.114:8080/ and sign in with the organization's email and password. New organizations can choose **Sign up**. No organization access key is needed.
2. Start on **Events**, then open an event's **Overview**, **Plan** or **People** page.
3. Use **Overview** to review registrations, progress, recommendations and forecasts. Enter only activity from outside the portal in manual metrics; portal registrations are added automatically.
4. Use **Plan** to complete work and manage content, partners and operations. Completing a plan task queues an email checklist for the organization.
5. Use **People** to see registrations, check in tickets, start the event and publish updates. Starting an event and publishing an update notify its registered attendees by email.
6. Share the event's attendee link. Attendees can sign up or sign in at http://3.110.130.114:8080/attendee.html, register, see their tickets and read updates.
7. Use **Ask copilot** for Groq suggestions about the selected event. **Email this advice** sends a saved response to the organization's account email.

## API documentation

Open http://3.110.130.114:8080/docs. Organization login returns a token for **OrganizationBearer**. Attendee login returns a separate token for **AttendeeBearer**. These tokens are not interchangeable. Public event discovery, signup, login and health do not require authorization.

## Email delivery

Emails use a durable queue and a separate mail worker. Actions stay saved if the provider is unavailable. **People → Email delivery** shows queued, sent or failed messages and offers retry for failed deliveries. The SMTP sender is separate from organization and attendee account addresses.

Configure SMTP and Groq credentials privately in the server environment. Never commit real passwords, tokens, private SSH keys or database exports. The repository contains only a placeholder environment example.

## Important details

- New events start with zero activity. Delhi demo events are labeled fictional examples.
- Forecasts are estimates, not guaranteed registrations. Completing an ordinary task does not create actual attendees.
- Organization signup creates a separate private organization. It does not claim another organization's events.
- Archive hides an event from active lists and new registration while preserving its history and tickets.
- The public deployment uses HTTP. Camera scanning requires HTTPS or localhost; manual ticket entry and QR-image reading remain available.
- Announcements appear in the attendee portal as well as notification emails. Email verification, password recovery and SMS delivery are not implemented.

## Upgrade the server

Run `bash deploy/deploy.sh` from the project folder with the correct private SSH key configured. The script verifies the Sportforward instance before writing, backs up the current code and database, preserves protected settings, installs dependencies and checks readiness. It does not target the separate Sports Friends instance.

See `README.md` for local setup, deployment settings, backups and test commands.
