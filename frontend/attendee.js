const API = window.EventPilotClient.apiBase;
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const portalParameters = new URLSearchParams(window.location.search);
const demoMode = portalParameters.get('demo') === '1';
const focusedEventId = Number(portalParameters.get('event')) || null;
let token = EventPilotClient.readSession('eventpilot_attendee_token');
let publicEvents = [], myRegistrations = [], currentAttendee = null, portalVersion = 0, authBusy = false;

function clearSession() {
  token = ''; currentAttendee = null; ++portalVersion;
  publicEvents = []; myRegistrations = [];
  EventPilotClient.writeSession('eventpilot_attendee_token', '');
  $('eventGrid').textContent = ''; $('registrationList').textContent = '';
  $('attendeeName').textContent = 'there'; $('eventCount').textContent = '0 events';
}
async function request(path, method = 'GET', body, authenticated = false) {
  try {
    return await EventPilotClient.request(path, { method, body, headers: authenticated && token ? { Authorization: `Bearer ${token}` } : {} });
  } catch (error) {
    if (authenticated && error.status === 401) { clearSession(); showAuth(); message('authMessage', 'Your session expired. Please sign in again.', true); }
    throw error;
  }
}
function message(id, text, isError = false) { const element = $(id); element.textContent = text || ''; element.classList.toggle('error', !!isError); }
function showAuth(which = 'login') {
  $('auth').hidden = false; $('portal').hidden = true; $('logout').hidden = true;
  document.querySelectorAll('[data-auth-tab]').forEach(button => button.classList.toggle('active', button.dataset.authTab === which));
  $('loginForm').hidden = which !== 'login'; $('registerForm').hidden = which !== 'register';
}
function showPortal() { $('auth').hidden = true; $('portal').hidden = false; $('logout').hidden = false; }
function dateText(value) { return value ? new Date(`${value}T12:00:00`).toLocaleDateString('en-IN', { year: 'numeric', month: 'short', day: 'numeric' }) : 'Date to be announced'; }

function eventStarted(event) { return event.lifecycle_status === 'Started'; }
function notificationText(result) {
  const mail = result && result.email_status;
  if (!mail) return 'Email status is not available yet.';
  const counts = [['sent', 'sent'], ['queued', 'queued'], ['failed', 'failed']].filter(([field]) => Number(mail[field]) > 0).map(([field, label]) => `${Number(mail[field])} ${label}`);
  if (counts.length) {
    let explanation = String(mail.message || '').replace(/^\d+\s+email(?:\(s\)|s)?\s+(?:queued|sent)(?:;\s*\d+\s+failed)?\.\s*/i, '').trim();
    if (!explanation && Number(mail.queued) > 0) explanation = 'Delivery is pending.';
    if (!explanation && Number(mail.failed) > 0) explanation = 'You can retry failed emails.';
    return `Email: ${counts.join(', ')}.${explanation ? ` ${explanation}` : ''}`;
  }
  return mail.message || ({ queued: 'Ticket email queued. Delivery is pending.', sent: 'Email sent.', failed: 'Email could not be sent. Your ticket is saved.', none: 'No email notifications needed.' })[mail.status] || 'Email status is not available yet.';
}
function renderEvents() {
  const visibleEvents = focusedEventId ? publicEvents.filter(event => event.id === focusedEventId) : publicEvents;
  $('eventCount').textContent = `${visibleEvents.length} event${visibleEvents.length === 1 ? '' : 's'}`;
  $('eventContext').hidden = !demoMode && !focusedEventId;
  $('eventContext').innerHTML = `${demoMode ? '<strong>Demo mode</strong><p>These sample events use fictional data.</p>' : ''}${focusedEventId ? '<p>You are viewing the event shared by your organizer.</p>' : ''}<a href="attendee.html">Browse all active events</a>`;
  const registeredIds = new Set(myRegistrations.map(registration => registration.event.id));
  $('eventGrid').innerHTML = visibleEvents.map(event => `<article class="event-card"><p class="eyebrow">${escapeHTML(event.type || 'EVENT')} · ${escapeHTML(event.format || '')}</p><h3>${escapeHTML(event.name)}</h3>${event.is_demo ? '<span class="status">Fictional demo event</span>' : ''}${eventStarted(event) ? '<span class="status started">Event started</span>' : ''}${event.organization_name ? `<p class="event-organization">Organized by ${escapeHTML(event.organization_name)}</p>` : ''}<div class="event-meta"><span>📅 ${escapeHTML(dateText(event.date))}</span><span>📍 ${escapeHTML(event.venue || 'Venue details coming soon')}</span><span>🎯 ${escapeHTML(event.audience || 'Open audience')}</span></div><p>${escapeHTML(event.objectives || 'Check your dashboard for updates after registering.')}</p>${registeredIds.has(event.id) ? '<button class="quiet" disabled>Already registered</button>' : `<button class="primary" data-register-event="${event.id}">Register for event</button>`}</article>`).join('') || '<div class="empty">No upcoming events are published yet. Please check again soon.</div>';
}
function renderTicketQR(canvas, ticketCode) {
  if (!window.qrcode) { canvas.hidden = true; return; }
  const code = window.qrcode(0, 'M');
  code.addData(ticketCode); code.make();
  const count = code.getModuleCount(), scale = 4, margin = 4;
  canvas.width = canvas.height = (count + margin * 2) * scale;
  const context = canvas.getContext('2d');
  context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = '#000';
  for (let row = 0; row < count; row++) for (let column = 0; column < count; column++) {
    if (code.isDark(row, column)) context.fillRect((column + margin) * scale, (row + margin) * scale, scale, scale);
  }
}
function renderRegistrations() {
  $('registrationList').innerHTML = myRegistrations.map((registration, index) => `<article class="registration-card"><div><p class="eyebrow">YOUR TICKET</p><h3>${escapeHTML(registration.event.name)}</h3>${eventStarted(registration.event) ? '<span class="status started">Event started</span><p>The organizer has started this event. Check the updates below.</p>' : ''}${registration.event.organization_name ? `<p>Organized by ${escapeHTML(registration.event.organization_name)}</p>` : ''}<p>📅 ${escapeHTML(dateText(registration.event.date))}</p><p>📍 ${escapeHTML(registration.event.venue || 'Venue details coming soon')}</p><p><span class="status">${escapeHTML(registration.status)}</span>${registration.checked_in_at ? ` · Checked in ${escapeHTML(EventPilotClient.timestampText(registration.checked_in_at))}` : ''}</p><p>Registered ${escapeHTML(EventPilotClient.timestampText(registration.created_at))}</p>${registration.email_status ? `<p class="ticket-email-status">Registration confirmation · ${escapeHTML(notificationText(registration))}</p>` : ''}${registration.announcements && registration.announcements.length ? `<div class="announcements"><b>Latest announcements</b>${registration.announcements.map(announcement => `<p><strong>${escapeHTML(announcement.title)}:</strong> ${escapeHTML(announcement.message)}</p>`).join('')}</div>` : '<div class="announcements"><b>Event updates</b><p>No announcements yet.</p></div>'}</div><div class="ticket"><canvas id="qr-${index}" aria-label="Ticket QR code"></canvas><div><label class="ticket-code">Ticket code<input class="ticket-code-input" value="${escapeHTML(registration.ticket_code)}" readonly aria-label="Ticket code"></label><button class="quiet" data-copy-ticket="${escapeHTML(registration.ticket_code)}">Copy code</button><button class="quiet" data-email-ticket="${registration.id}">Email ticket</button></div></div></article>`).join('') || '<div class="empty">You have not registered for an event yet. Browse the events above to get started.</div>';
  myRegistrations.forEach((registration, index) => {
    try { renderTicketQR($(`qr-${index}`), registration.ticket_code); }
    catch { $(`qr-${index}`).hidden = true; message('portalMessage', 'QR generation is unavailable. Your ticket code can still be used for check-in.'); }
  });
}
async function loadPortalData() {
  const version = portalVersion;
  const [registrations, events] = await Promise.all([
    request('/api/attendees/registrations', 'GET', undefined, true), request(`/api/public/events${demoMode ? '?include_demo=true' : ''}`)
  ]);
  if (version !== portalVersion || !token) return;
  myRegistrations = registrations; publicEvents = events;
  renderEvents(); renderRegistrations();
}
async function enterPortal(attendee) {
  currentAttendee = attendee; ++portalVersion;
  $('attendeeName').textContent = attendee.name; showPortal();
  message('portalMessage', 'Loading your events…');
  try { await loadPortalData(); if (token) message('portalMessage', ''); }
  catch (error) { if (token) message('portalMessage', error.message, true); }
}
async function authenticate(path, form) {
  if (authBusy) return;
  authBusy = true;
  const button = form.querySelector('button'); button.disabled = true;
  try {
    message('authMessage', 'Working…');
    const result = await request(path, 'POST', Object.fromEntries(new FormData(form)));
    token = result.token; EventPilotClient.writeSession('eventpilot_attendee_token', token);
    form.reset(); await enterPortal(result.attendee);
  } catch (error) { message('authMessage', error.message, true); }
  finally { authBusy = false; button.disabled = false; }
}
$('loginForm').addEventListener('submit', event => { event.preventDefault(); authenticate('/api/attendees/login', event.currentTarget); });
$('registerForm').addEventListener('submit', event => { event.preventDefault(); authenticate('/api/attendees/register', event.currentTarget); });
document.querySelectorAll('[data-auth-tab]').forEach(button => button.addEventListener('click', () => { showAuth(button.dataset.authTab); message('authMessage', ''); }));
$('logout').addEventListener('click', async () => {
  try { await request('/api/attendees/logout', 'POST', {}, true); } catch {}
  clearSession(); showAuth(); message('authMessage', 'You have signed out.');
});
$('reload').addEventListener('click', async () => {
  $('reload').disabled = true;
  try {
    if (!currentAttendee) await enterPortal(await request('/api/attendees/me', 'GET', undefined, true));
    else { await loadPortalData(); if (token) message('portalMessage', 'Updated.'); }
  } catch (error) { if (token) message('portalMessage', error.message, true); }
  finally { $('reload').disabled = false; }
});
document.addEventListener('click', async event => {
  const register = event.target.closest('[data-register-event]'), copy = event.target.closest('[data-copy-ticket]'), emailTicket = event.target.closest('[data-email-ticket]');
  if (register) {
    const version = portalVersion; register.disabled = true;
    try {
      const result = await request('/api/attendees/registrations', 'POST', { event_id: Number(register.dataset.registerEvent) }, true);
      if (version !== portalVersion || !token) return;
      await loadPortalData(); if (version === portalVersion && token) message('portalMessage', `Registration confirmed. Your ticket is ready below. ${notificationText(result)}`);
    } catch (error) { if (token) message('portalMessage', error.message, true); register.disabled = false; }
  }
  if (emailTicket) {
    const version = portalVersion; emailTicket.disabled = true;
    try { const result = await request(`/api/attendees/registrations/${emailTicket.dataset.emailTicket}/email`, 'POST', {}, true); if (version === portalVersion && token) message('portalMessage', notificationText(result)); }
    catch (error) { if (version === portalVersion && token) message('portalMessage', error.message, true); }
    finally { emailTicket.disabled = false; }
  }
  if (copy) {
    try {
      if (!window.isSecureContext || !navigator.clipboard) throw Error('Manual copy required');
      await navigator.clipboard.writeText(copy.dataset.copyTicket); message('portalMessage', 'Ticket code copied.');
    } catch {
      const input = copy.closest('.ticket').querySelector('input'); input.focus(); input.select();
      message('portalMessage', 'Ticket code selected. Use your device’s copy action to copy it.');
    }
  }
});
(async () => {
  if (demoMode || focusedEventId) { $('eventContext').hidden = false; $('eventContext').innerHTML = `${demoMode ? '<strong>Demo mode</strong><p>Sample events use fictional data.</p>' : '<strong>Event registration</strong><p>Sign in or create an account to register for the event shared by your organizer.</p>'}<a href="attendee.html">Browse all active events</a>`; }
  if (!token) { showAuth(); if (window.eventpilotBoot) window.eventpilotBoot.ready(); return; }
  try { await enterPortal(await request('/api/attendees/me', 'GET', undefined, true)); }
  catch (error) {
    if (token) { showPortal(); message('portalMessage', `${error.message} Use Refresh to retry.`, true); }
  }
  if (window.eventpilotBoot) window.eventpilotBoot.ready();
})();
