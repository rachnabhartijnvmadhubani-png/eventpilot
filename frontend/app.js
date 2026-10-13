const API = window.EventPilotClient.apiBase;
const $ = id => document.getElementById(id), esc = x => String(x == null ? '' : x).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let events = [], eid = null, d = null, chart = null, page = 'auth', intelligence = null, refreshVersion = 0;
let editingId = null, operationsVersion = 0, emailDeliveryVersion = 0, recommendationActions = [];
let access = null, accessRevision = 0, authBusy = false;
const organizationTokenName = 'eventpilot_organization_token';
async function req(path, method = 'GET', body) {
  const revision = accessRevision;
  try {
    const result = await EventPilotClient.request(path, { method, body, headers: EventPilotClient.readSession(organizationTokenName) ? { Authorization: `Bearer ${EventPilotClient.readSession(organizationTokenName)}` } : {}, timeoutMs: path === '/api/ai' ? 90000 : 20000 });
    if (revision !== accessRevision) throw Object.assign(Error('Access changed.'), { staleAccess: true });
    return result;
  } catch (error) {
    if (revision !== accessRevision) error.staleAccess = true;
    else if (error.status === 401) { lockOrganizer('Your session expired. Sign in again.'); error.staleAccess = true; }
    throw error;
  }
}
function clearOrganizerView() {
  access = null; ++accessRevision; ++operationsVersion;
  editingId = null; recommendationActions = [];
  if (window.clearEventPilotChat) window.clearEventPilotChat();
  clearSnapshot(); eid = null; events = []; ++refreshVersion;
  $('events').innerHTML = '<option>Choose an event</option>';
  $('eventCards').textContent = ''; $('demoCards').textContent = '';
  $('organizationName').textContent = ''; $('organizerEmail').textContent = '';
  $('profileOrganization').textContent = ''; $('profileEmail').textContent = ''; $('organizationEmailStatus').textContent = '';
  $('changeOrganizationPassword').reset(); $('create').reset(); $('passwordMessage').textContent = '';
  $('error').textContent = ''; $('actionStatus').textContent = '';
  $('includeArchived').checked = false;
  go('auth');
}
function showAuth(which = 'login') {
  go('auth');
  $('organizationLogin').hidden = which !== 'login'; $('organizationSignup').hidden = which !== 'signup';
  document.querySelectorAll('[data-auth-tab]').forEach(button => { button.classList.toggle('active', button.dataset.authTab === which); button.setAttribute('aria-pressed', String(button.dataset.authTab === which)); });
}
function authMessage(text, isError = false) { $('authMessage').textContent = text || ''; $('authMessage').classList.toggle('error', isError); }
function lockOrganizer(message = 'Sign in to your organization.') {
  EventPilotClient.writeSession(organizationTokenName, '');
  clearOrganizerView(); stopQrScanner(); showAuth(); authMessage(message);
}
async function action(fn) { try { $('error').textContent = ''; $('actionStatus').textContent = ''; await fn() } catch (error) { if (error.staleAccess) return; $('error').textContent = String(error.message || error); } }
function selectedSession() { return `eventpilot_selected_event_account_${access && access.organization ? access.organization.id : 'none'}_${access && access.organizer ? access.organizer.id : 'none'}`; }
async function unlockOrganizer() {
  const resolved = await req('/api/organizations/me');
  if (!resolved.organization || !resolved.organizer) throw Error('Could not load your organization account. Please sign in again.');
  access = resolved;
  $('organizationName').textContent = resolved.organization.name; $('organizerEmail').textContent = resolved.organizer.email;
  $('profileOrganization').textContent = resolved.organization.name; $('profileEmail').textContent = resolved.organizer.email;
  await load(); go('events'); authMessage('');
  if (window.eventpilotBoot) window.eventpilotBoot.ready();
}
async function authenticateOrganization(path, form) {
  if (authBusy) return;
  authBusy = true;
  const body = Object.fromEntries(new FormData(form));
  EventPilotClient.writeSession(organizationTokenName, ''); clearOrganizerView(); showAuth(path.endsWith('/signup') ? 'signup' : 'login');
  const revision = ++accessRevision;
  document.querySelectorAll('#organizerAuth button').forEach(button => button.disabled = true);
  authMessage(path.endsWith('/signup') ? 'Creating your organization…' : 'Signing in…');
  try {
    const result = await EventPilotClient.request(path, { method: 'POST', body });
    if (revision !== accessRevision) return;
    if (!result.token) throw Error('Sign in could not finish. Please try again.');
    EventPilotClient.writeSession(organizationTokenName, result.token);
    form.reset(); await unlockOrganizer();
  } catch (error) {
    if (error.staleAccess || revision !== accessRevision) return;
    authMessage(error.status === 401 ? 'Email or password is incorrect. Please try again.' : error.message, true);
  } finally {
    authBusy = false; document.querySelectorAll('#organizerAuth button').forEach(button => button.disabled = false);
    if (window.eventpilotBoot) window.eventpilotBoot.ready();
  }
}
function need() { if (!eid) throw Error('Open or create an event first.'); return eid; }
function isDemo(event) { return !!event.is_demo || /\[DEMO\]/i.test(event.objectives || ''); }
function today() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const value = type => parts.find(part => part.type === type).value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}
function dateText(value) { return new Date(`${value}T12:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }); }
function eventHasStarted(event) { return event && event.lifecycle_status === 'Started'; }
function emailStatusText(result) {
  const mail = result && result.email_status;
  if (!mail) return 'Email status is not available yet.';
  const counts = [['sent', 'sent'], ['queued', 'queued'], ['failed', 'failed']].filter(([field]) => Number(mail[field]) > 0).map(([field, label]) => `${Number(mail[field])} ${label}`);
  if (counts.length) {
    let explanation = String(mail.message || '').replace(/^\d+\s+email(?:\(s\)|s)?\s+(?:queued|sent)(?:;\s*\d+\s+failed)?\.\s*/i, '').trim();
    if (!explanation && Number(mail.queued) > 0) explanation = 'Delivery is pending.';
    if (!explanation && Number(mail.failed) > 0) explanation = 'You can retry failed emails.';
    return `Email: ${counts.join(', ')}.${explanation ? ` ${explanation}` : ''}`;
  }
  return mail.message || ({ queued: 'Email queued. Delivery is pending.', sent: 'Email sent.', failed: 'Email could not be sent. The action is saved.', none: 'No email notifications needed.' })[mail.status] || 'Email status is not available yet.';
}
function eventCard(event) {
  return `<article class="panel event-card${event.archived ? ' archived' : ''}"><div class="card-labels"><span>${esc(event.type)} · ${esc(event.format)}</span>${isDemo(event) ? '<span class="demo-badge">Demo</span>' : ''}${event.archived ? '<span class="muted-badge">Archived</span>' : ''}${eventHasStarted(event) ? '<span class="started-badge">Started</span>' : ''}</div><h2>${esc(event.name)}</h2><p>${esc(dateText(event.date))}${event.organization_name ? ` · ${esc(event.organization_name)}` : ''}</p><p>${esc(event.venue || (event.format === 'Online' ? 'Online event' : 'Venue to be confirmed'))}</p><div class="card-footer"><span>Target: ${Number(event.target_regs).toLocaleString('en-IN')}</span><button type="button" data-open-event="${event.id}">Open event</button>${event.archived ? `<button type="button" data-restore-event="${event.id}" class="secondary">Restore</button>` : ''}</div></article>`;
}
function renderLibrary() {
  const realEvents = events.filter(event => !isDemo(event)), demoEvents = events.filter(event => isDemo(event));
  $('newEvent').disabled = !access; $('includeArchived').disabled = !access; $('demoControls').hidden = !access;
  $('libraryHint').textContent = !access ? 'Sign in to see your organization’s events.' : realEvents.length ? 'Open an event to see its overview, plan and people.' : 'No events yet. Create your first event to generate its plan.';
  $('eventCards').innerHTML = !access ? '' : realEvents.map(eventCard).join('') || '<article class="panel empty-state"><h2>Your first event starts here</h2><p>Add the event brief. The plan is generated for you.</p><button type="button" data-create-event>Create event</button></article>';
  $('demoCards').innerHTML = access ? demoEvents.map(eventCard).join('') || '<p>No demo events loaded yet.</p>' : '';
}
function eventOverview() {
  const event = d.event;
  $('eventOverview').hidden = false;
  $('eventOverview').innerHTML = `<div class="event-heading"><div><p class="event-kind">${esc(event.type)} · ${esc(event.format)}${event.organization_name ? ` · ${esc(event.organization_name)}` : ''}</p></div>${isDemo(event) ? '<span class="demo-badge">Fictional demo</span>' : ''}${event.archived ? '<span class="muted-badge">Archived</span>' : ''}${eventHasStarted(event) ? '<span class="started-badge">Started</span>' : ''}</div><dl class="event-details">${[['Date', dateText(event.date)], ['Venue', event.venue || (event.format === 'Online' ? 'Online event' : 'To be confirmed')], ['Audience', event.audience || 'To be confirmed'], ['Registration target', Number(event.target_regs).toLocaleString('en-IN')]].map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>${isDemo(event) ? '<p class="demo-note">This event uses invented sample data for demonstration.</p>' : ''}`;
}
async function load(selectId = null) {
  const loaded = await req(`/api/events${$('includeArchived').checked ? '?include_archived=true' : ''}`);
  events = loaded;
  const desired = selectId || eid || Number(EventPilotClient.readSession(selectedSession()));
  eid = events.some(event => event.id === desired) ? desired : ((events.find(event => !event.archived && !isDemo(event)) || events.find(event => !isDemo(event))) || {}).id || null;
  $('events').innerHTML = events.map(event => `<option value="${event.id}">${isDemo(event) ? 'Demo · ' : ''}${esc(event.name)}${event.archived ? ' (archived)' : ''}</option>`).join('') || '<option value="">No events</option>';
  $('events').value = eid || ''; renderLibrary();
  if (eid && ['dashboard', 'tasks', 'registrations', 'ai'].includes(page)) { clearSnapshot(); await refresh(true); }
  else { d = null; intelligence = null; render(); }
}
function clearSnapshot() {
  d = null; intelligence = null; recommendationActions = []; ++operationsVersion; ++emailDeliveryVersion;
  if (chart) { chart.destroy(); chart = null; }
  for (const id of ['stats', 'recs', 'forecast', 'funnel', 'riskHeatmap', 'impactCompare', 'timelineRows', 'taskRows', 'partnerRows', 'logisticRows', 'contentRows', 'escalationRows', 'attendeeRows', 'decisionRows', 'recommendationActionRows']) $(id).textContent = '';
  $('eventOverview').hidden = true; $('dataNotice').hidden = true; $('planSummary').hidden = true; $('opsMessage').textContent = ''; $('announcementStatus').textContent = ''; $('emailDeliveryRows').textContent = ''; $('emailDeliverySummary').textContent = ''; $('retryFailedEmails').disabled = true;
  $('checkinForm').reset(); $('announcementForm').reset(); $('partnerForm').reset(); fillMetrics(null);
  for (const form of [$('metrics'), $('announcementForm'), $('checkinForm'), $('partnerForm')]) form.querySelectorAll('button,input,textarea').forEach(element => element.disabled = true); $('qrImage').disabled = true;
  $('startEvent').disabled = true; $('eventStarted').hidden = true;
  $('editEvent').disabled = true; $('archiveEvent').disabled = true; $('openCopilot').disabled = true;
}
async function openEvent(id) {
  if (!events.some(event => event.id === Number(id))) throw Error('This event is unavailable. Refresh the event list.');
  await stopQrScanner(); if (window.clearEventPilotChat) window.clearEventPilotChat(); eid = Number(id); clearSnapshot();
  EventPilotClient.writeSession(selectedSession(), String(eid)); $('events').value = String(eid); go('dashboard');
  await refresh(true);
}
async function refresh(reset = false) {
  const version = ++refreshVersion;
  if (!eid) { d = null; intelligence = null; render(); return; }
  const event = events.find(event => event.id === eid);
  if (reset) {
    const now = today(); $('date').min = event.start_date; $('date').max = event.date > now ? event.date : now;
    $('date').value = isDemo(event) && now < event.start_date ? event.start_date : now;
    if ($('date').value < event.start_date) $('date').value = event.start_date;
    $('metrics').elements.date.value = today();
  }
  $('snapshotStatus').textContent = 'Loading event…';
  let snapshot;
  try { snapshot = await req(`/api/events/${need()}/dashboard?sim_date=${encodeURIComponent($('date').value)}`); }
  catch (error) { if (version === refreshVersion) { $('snapshotStatus').textContent = 'Could not load event. Use Refresh to try again.'; $('recs').textContent = 'Event details are unavailable.'; } throw error; }
  if (version !== refreshVersion) return;
  d = snapshot; intelligence = null; events = events.map(event => event.id === snapshot.event.id ? { ...event, ...snapshot.event } : event);
  const manual = d.metrics.find(metric => metric.date === $('metrics').elements.date.value) || (reset ? d.metrics[d.metrics.length - 1] : null);
  if (reset && manual) $('metrics').elements.date.value = manual.date;
  fillMetrics(manual);
  render();
  if (window.loadIntelligence) await window.loadIntelligence();
}
function fillMetrics(manual) { for (const field of ['reach', 'clicks', 'registrations', 'confirmations', 'attendance']) $('metrics').elements[field].value = manual ? Number(manual[field]) || 0 : 0; }
function go(p) {
  if (!access && p !== 'auth') p = 'auth';
  document.body.classList.toggle('auth-mode', p === 'auth');
  if (page === 'registrations' && p !== page) stopQrScanner();
  page = p; $('archivePrompt').hidden = true; $('startEventPrompt').hidden = true;
  document.querySelectorAll('main > section').forEach(section => section.hidden = section.id !== (p === 'events' ? 'eventLibrary' : p === 'auth' ? 'organizerAuth' : p));
  const workspace = !!eid && ['dashboard', 'tasks', 'registrations', 'ai'].includes(p);
  $('workspaceBar').hidden = !workspace; $('events').hidden = !workspace;
  $('newEvent').hidden = p === 'setup' || p === 'auth';
  document.querySelectorAll('#nav button').forEach(button => button.classList.toggle('active', button.dataset.page === p || button.dataset.page === 'events' && ['dashboard', 'tasks', 'registrations', 'setup', 'ai'].includes(p)));
  document.querySelectorAll('[data-workspace]').forEach(button => { button.classList.toggle('active', button.dataset.workspace === p); button.setAttribute('aria-pressed', String(button.dataset.workspace === p)); });
  const selected = events.find(event => event.id === eid);
  $('title').textContent = workspace && selected ? selected.name : ({ events: 'Events', setup: editingId ? 'Edit event' : 'Create event', account: 'Organization account', auth: 'Sign in' })[p] || 'Events';
  $('headerContext').textContent = workspace ? ({ dashboard: 'OVERVIEW', tasks: 'EVENT PLAN', registrations: 'PEOPLE', ai: 'OPTIONAL COPILOT' })[p] : 'ORGANIZER WORKSPACE';
  render();
}
function render() {
  renderLibrary();
  if (!d) {
    $('eventOverview').hidden = true;
    if (page === 'dashboard') { $('stats').innerHTML = ''; $('recs').textContent = 'Loading event plan…'; $('forecast').textContent = ''; $('dataNotice').hidden = true; $('planSummary').hidden = true; }
    return;
  }
  const archived = !!d.event.archived;
  $('startEvent').hidden = archived || eventHasStarted(d.event); $('startEvent').disabled = archived || eventHasStarted(d.event); $('eventStarted').hidden = !eventHasStarted(d.event);
  $('editEvent').disabled = archived; $('archiveEvent').disabled = false; $('archiveEvent').hidden = archived; $('openCopilot').disabled = false;
  for (const form of [$('metrics'), $('announcementForm'), $('checkinForm')]) form.querySelectorAll('button,input,textarea').forEach(element => element.disabled = archived);
  $('qrImage').disabled = archived; $('startQrScanner').disabled = archived || !window.isSecureContext;
  if (page === 'dashboard') { eventOverview(); draw(); renderDecisions(); loadRecommendationHistory().catch(error => { if (!error.staleAccess) $('recommendationActionRows').textContent = `Could not load history: ${error.message}`; }); }
  if (page === 'tasks') { tasks(); partners(); list('contentRows', d.content, 'content'); }
  if (page === 'registrations') { $('eventPortalLink').href = `attendee.html?event=${d.event.id}${isDemo(d.event) ? '&demo=1' : ''}`; }
  if (page === 'registrations' && $('emailDelivery').open) loadEmailDelivery().catch(error => { if (!error.staleAccess) $('emailDeliverySummary').textContent = error.message; });
  if (page === 'registrations') loadRegistrationOps().catch(error => { if (!error.staleAccess) $('error').textContent = error.message; });
  if (window.renderIntelligence && page === 'dashboard') window.renderIntelligence();
}
function renderDecisions() { $('decisionRows').innerHTML = d.decisions.map(item => `<div class="line"><div><b>${esc(item.action)}</b><p>${esc(item.actor)}</p></div><span>${item.before_value != null && item.after_value != null ? `${Number(item.before_value).toFixed(1)}% → ${Number(item.after_value).toFixed(1)}%` : 'Task completed · forecast needs more history'}</span></div>`).join('') || '<p>No decisions recorded yet.</p>'; }
function draw() {
  const f = d.forecast;
  $('snapshotStatus').textContent = `${d.sim_date === today() ? 'Live view' : 'Simulated date'} · ${dateText(d.sim_date)}`;
  const state = d.data_state || {}; $('dataNotice').hidden = !state.message;
  $('dataNotice').innerHTML = `${esc(state.message || '')}${state.status === 'no_activity' && !d.event.archived ? ' <button type="button" data-open-metrics class="secondary">Add numbers</button>' : ''}${d.event.archived ? '<p>This event is archived. Restore it from All events to make changes.</p>' : ''}`;
  if (d.event.archived) $('dataNotice').hidden = false;
  $('planSummary').hidden = !d.plan_summary || !d.plan_summary.message;
  $('planSummary').textContent = d.plan_summary ? d.plan_summary.message || '' : '';
  $('stats').innerHTML = [['Registrations', d.actual, `Target ${d.event.target_regs} · expected ${d.expected}`], ['Registration gap', `${d.gap_pct}%`, state.status === 'no_activity' ? 'Waiting for activity' : d.bottleneck === 'None' ? 'No bottleneck detected' : `${d.bottleneck} bottleneck`], ['Days left', Math.max(0, d.days_left), d.days_left < 0 ? 'Event date has passed' : dateText(d.event.date)], ['Readiness', `${d.readiness}%`, `Risk score ${d.risk}/100`]].map(item => `<div class="stat"><small>${esc(item[0])}</small><b>${esc(item[1])}</b><small>${esc(item[2])}</small></div>`).join('');
  $('forecast').innerHTML = f.available === false ? '<div class="empty-state"><h3>Forecast needs activity data</h3><p>Record activity on at least two dates to estimate the trend. Portal registrations are included automatically.</p></div>' : `<div class="stat"><small>Projected registrations</small><b>${f.median}</b><small>Estimated 80% range: ${f.low}–${f.high}</small></div><div class="stat"><small>Chance of reaching target</small><b>${f.probability}%</b><small>1,000 simulated outcomes</small></div><p>Estimates based on recorded activity and task assumptions.</p>`;
  $('forecast').innerHTML += `<div class="track"><span style="width:${Math.max(0, Math.min(100, d.readiness))}%"></span></div><p>${d.tasks.filter(task => task.status === 'Done').length} of ${d.tasks.length} tasks completed</p>`;
  const max = Math.max(1, ...Object.values(d.funnel));
  $('funnel').innerHTML = Object.entries(d.funnel).map(([name, value]) => `<div class="line"><b>${esc(name)}</b><span>${value}${d.bottleneck === name ? ' · bottleneck' : ''}</span></div><div class="track"><span style="width:${Math.max(0, value / max * 100)}%"></span></div>`).join('');
  $('recs').innerHTML = renderRecommendations(d.recommendations);
  const trajectory = d.registration_trajectory || d.metrics, start = new Date(d.event.start_date), total = Math.max(1, (new Date(d.event.date) - start) / 86400000), sig = value => 1 / (1 + Math.exp(-10 * (value - .55)));
  const expected = trajectory.map(item => Math.round(d.event.target_regs * (sig(Math.max(0, Math.min(1, (new Date(item.date) - start) / 86400000 / total))) - sig(0)) / (sig(1) - sig(0))));
  if (chart) chart.destroy();
  if (window.Chart) chart = new Chart($('chart'), { type: 'line', data: { labels: trajectory.map(item => item.date), datasets: [{ label: 'Recorded registrations', data: trajectory.map(item => item.registrations), borderColor: '#356c55', tension: .3 }, { label: 'Benchmark', data: expected, borderColor: '#a77932', borderDash: [6, 4], tension: .3 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: '#526158' } } }, scales: { x: { ticks: { color: '#69736d', maxTicksLimit: 7 } }, y: { beginAtZero: true, ticks: { color: '#69736d' } } } } });
}
function renderRecommendations(items) { return items.map(item => `<div class="rec"><b>${esc(item.title)}</b><p>${esc(item.role)} · Estimated +${item.impact} registrations · Effort ${item.effort}</p><details><summary>Why this?</summary><p>${esc(item.why)}</p></details>${d.event.archived ? '' : `<div class="actions"><button type="button" data-done="${item.id}">Complete task</button></div>`}</div>`).join('') || '<p>No recovery actions needed right now. Your generated tasks are in Plan.</p>'; }
function tasks() {
  const role = $('role').value, rows = d.tasks.filter(task => role === 'All' || task.role === role);
  $('taskRows').innerHTML = rows.map(task => `<tr><td>${esc(task.title)}</td><td>${esc(task.role)}</td><td>${esc(task.due_date)}</td><td>${esc(task.priority)}</td><td>${esc(task.status)}</td><td>${task.status === 'Done' ? 'Done' : d.event.archived ? '—' : `<button type="button" data-done="${task.id}">Complete</button>`}</td></tr>`).join('') || '<tr><td colspan="6">No tasks for this role.</td></tr>';
}
function list(id, items, kind) {
  $(id).innerHTML = items.map(item => {
    const done = d.event.archived, state = item.stage || item.status;
    const controls = done ? '' : kind === 'partners' ? `${state === 'Contacted' ? `<button type="button" data-update="partners:${item.id}:Confirmed">Confirm</button>` : ''}${state === 'Confirmed' ? `<button type="button" data-update="partners:${item.id}:Activated">Activate</button>` : ''}` : kind === 'logistics' && state !== 'Done' ? `<button type="button" data-update="logistics:${item.id}:Done">Resolve</button>` : kind === 'content' && state !== 'Approved' ? `<button type="button" data-update="content:${item.id}:Approved">Approve</button>` : kind === 'escalations' && state === 'Open' ? `<button type="button" data-update="escalations:${item.id}:Approved">Approve</button><button type="button" data-update="escalations:${item.id}:Rejected">Reject</button>` : '';
    return `<div class="line"><div><b>${esc(item.name || item.item || item.topic || `Task ${item.task_id}`)}</b><p>${esc([item.channel, item.date, item.level].filter(Boolean).join(' · '))}</p></div><div class="actions"><span>${esc(state)}</span>${controls}</div></div>`;
  }).join('') || `<p>${kind === 'partners' ? 'No partners added. Add an actual organization below.' : 'No records yet.'}</p>`;
}
function partners() { list('partnerRows', d.partners, 'partners'); list('logisticRows', d.logistics, 'logistics'); list('escalationRows', d.escalations, 'escalations'); $('partnerForm').querySelectorAll('input,button').forEach(element => element.disabled = !!d.event.archived); }
function prepareEventEditor(event = null) {
  if (!access) throw Error('Sign in to your organization first.');
  editingId = event ? event.id : null; $('create').reset();
  const fields = $('create').elements;
  const hasActivity = !!event && !!d && d.event.id === event.id && (eventHasStarted(d.event) || d.metrics.length > 0 || d.tasks.some(task => task.status === 'Done') || Number(d.registration_sources && d.registration_sources.portal) > 0 || recommendationActions.length > 0);
  fields.type.disabled = hasActivity; fields.format.disabled = hasActivity; $('briefTypeHint').hidden = !hasActivity;
  if (event) for (const field of ['name', 'type', 'date', 'format', 'audience', 'target_regs', 'venue', 'objectives']) fields[field].value = event[field] || '';
  else { const future = new Date(); future.setDate(future.getDate() + 30); fields.date.value = `${future.getFullYear()}-${String(future.getMonth()+1).padStart(2,'0')}-${String(future.getDate()).padStart(2,'0')}`; fields.target_regs.value = ''; }
  fields.date.min = event && event.date < today() ? event.date : today();
  $('editorTitle').textContent = event ? 'Edit event brief' : 'Create event';
  $('editorHelp').textContent = event ? 'Update the brief. Changing the date shifts deadlines; completed tasks, attendees and history are kept.' : 'Start with the event brief. EventPilot generates the tasks, content calendar and operations checklist.';
  $('saveEvent').textContent = event ? 'Save changes' : 'Create event & generate plan';
  updateVenueRequirement(); go('setup');
}
function updateVenueRequirement() { $('create').elements.venue.required = $('create').elements.format.value !== 'Online'; }
async function loadRecommendationHistory() {
  if (!eid) return; const selected = eid, revision = refreshVersion;
  let rows;
  try { rows = await req(`/api/events/${selected}/recommendation-actions`); } catch (error) { if (eid !== selected || revision !== refreshVersion) return; throw error; }
  if (eid !== selected || revision !== refreshVersion) return;
  recommendationActions = rows;
  $('recommendationActionRows').innerHTML = rows.map(item => `<div class="line"><div><b>${esc(item.recommendation)}</b><p>${esc(item.notes || '')}</p></div><span>${esc(item.status)}</span></div>`).join('') || '<p>No recommendation actions recorded yet.</p>';
}
async function loadEmailDelivery() {
  const selected = need(), version = ++emailDeliveryVersion;
  $('emailDeliverySummary').textContent = 'Loading email delivery…';
  let result;
  try { result = await req(`/api/events/${selected}/emails`); }
  catch (error) { if (eid !== selected || version !== emailDeliveryVersion) return; throw error; }
  if (eid !== selected || version !== emailDeliveryVersion || page !== 'registrations') return;
  $('emailDeliverySummary').textContent = emailStatusText(result);
  $('retryFailedEmails').disabled = !!(d && d.event.archived) || !result.email_status || Number(result.email_status.failed) === 0;
  $('emailDeliveryRows').innerHTML = result.emails.map(mail => `<tr><td>${esc(mail.subject)}</td><td>${esc(mail.recipient)}</td><td>${esc(mail.status === 'Pending' ? 'Queued' : mail.status)}</td><td>${esc(EventPilotClient.timestampText(mail.delivered_at || mail.last_attempt_at || mail.created_at))}</td><td>${esc(mail.last_error || (mail.attempts ? `${Number(mail.attempts)} attempt${Number(mail.attempts) === 1 ? '' : 's'}` : 'Waiting for delivery'))}</td></tr>`).join('') || '<tr><td colspan="5">No email notifications recorded for this event yet.</td></tr>';
}
async function loadRegistrationOps() {
  if (!eid) return;
  const selected = need(), version = ++operationsVersion;
  $('attendeeRows').innerHTML = '<tr><td colspan="6">Loading attendee registrations…</td></tr>';
  $('recommendationActionRows').textContent = 'Loading recommendation history…';
  let rows, actions;
  try { [rows, actions] = await Promise.all([
    req(`/api/events/${selected}/attendees`), req(`/api/events/${selected}/recommendation-actions`)
  ]); } catch (error) {
    if (eid === selected && version === operationsVersion) {
      $('attendeeRows').innerHTML = '<tr><td colspan="6">Could not load attendee registrations. Try again.</td></tr>';
      $('recommendationActionRows').textContent = 'Could not load recommendation history.';
    }
    if (eid !== selected || version !== operationsVersion) return;
    throw error;
  }
  if (eid !== selected || version !== operationsVersion) return;
  recommendationActions = actions;
  $('attendeeRows').innerHTML = rows.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.email)}</td><td>${esc(r.status)}</td><td>${esc(EventPilotClient.timestampText(r.created_at))}</td><td>${r.checked_in_at ? esc(EventPilotClient.timestampText(r.checked_in_at)) : 'Not yet'}</td><td><button type="button" class="secondary" data-email-registration="${r.registration_id}">Email ticket</button></td></tr>`).join('') || '<tr><td colspan="6">No attendee registrations yet.</td></tr>';
  $('recommendationActionRows').innerHTML = actions.map(a => `<div class="line"><div><b>${esc(a.recommendation)}</b><p>${esc(a.notes || '')}</p></div><span>${esc(a.status)}</span></div>`).join('') || '<p>No recommendation actions recorded yet.</p>';
}
$('emailDelivery').ontoggle = () => { if ($('emailDelivery').open && page === 'registrations' && eid) loadEmailDelivery().catch(error => { if (!error.staleAccess) $('emailDeliverySummary').textContent = error.message; }); };
$('refreshEmailDelivery').onclick = () => action(() => loadEmailDelivery());
$('retryFailedEmails').onclick = () => action(async () => {
  const selected = need(), button = $('retryFailedEmails'); button.disabled = true;
  try { const result = await req(`/api/events/${selected}/emails/retry`, 'POST', {}); if (eid !== selected) return; $('actionStatus').textContent = result.message || emailStatusText(result); await loadEmailDelivery(); }
  catch (error) { if (eid === selected) button.disabled = false; throw error; }
});
$('announcementForm').onsubmit = event => {
  event.preventDefault();
  const form = event.currentTarget, button = form.querySelector('button');
  action(async () => {
    button.disabled = true; $('announcementStatus').textContent = 'Publishing update…';
    try {
      const selected = need(); const result = await req(`/api/events/${selected}/announcements`, 'POST', Object.fromEntries(new FormData(form)));
      if (eid !== selected) return;
      $('announcementStatus').textContent = `Update published to attendee dashboards. ${emailStatusText(result)}`;
      form.reset();
      if ($('emailDelivery').open) loadEmailDelivery().catch(error => { if (!error.staleAccess && eid === selected) $('emailDeliverySummary').textContent = error.message; });
    } catch (error) { $('announcementStatus').textContent = ''; throw error; } finally { button.disabled = false; }
  });
};
let qrScanner = null, qrActive = false, qrSubmitting = false;
async function stopQrScanner() {
  if (qrScanner && qrActive) { try { await qrScanner.stop(); } catch {} }
  qrActive = false;
  $('qrScanner').hidden = true;
  $('startQrScanner').textContent = 'Scan QR with camera';
}
async function checkIn(ticketCode) {
  const selected = need();
  const result = await req('/api/check-in', 'POST', { ticket_code: ticketCode.trim(), event_id: selected });
  if (eid !== selected) return;
  $('opsMessage').textContent = `Ticket checked in. ${emailStatusText(result)}`;
  $('checkinForm').reset();
  await refresh();
}
$('checkinForm').onsubmit = event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  action(async () => {
    button.disabled = true;
    try { await checkIn($('ticketCodeInput').value); }
    finally { button.disabled = false; }
  });
};
if (!window.isSecureContext) {
  $('startQrScanner').disabled = true;
  $('scanHelp').textContent = 'Camera scanning requires HTTPS or localhost. On this HTTP site, paste the ticket code or choose a QR image.';
}
$('startQrScanner').onclick = () => action(async () => {
  need();
  if (qrActive) { await stopQrScanner(); return; }
  if (!window.isSecureContext || !window.Html5Qrcode) throw Error('Camera scanning is unavailable. Paste the ticket code or choose a QR image.');
  try {
    $('qrScanner').hidden = false;
    if (!qrScanner) qrScanner = new Html5Qrcode('qrScanner');
    await qrScanner.start({ facingMode: 'environment' }, { fps: 10, qrbox: { width: 230, height: 230 } }, async decodedText => {
      if (qrSubmitting) return;
      qrSubmitting = true;
      await stopQrScanner();
      $('ticketCodeInput').value = decodedText;
      await action(() => checkIn(decodedText));
      qrSubmitting = false;
    }, () => {});
    qrActive = true;
    if (page !== 'registrations') { await stopQrScanner(); return; }
    $('startQrScanner').textContent = 'Stop camera';
    $('opsMessage').textContent = 'Camera active. Point it at an attendee ticket QR code.';
  } catch (error) {
    await stopQrScanner();
    throw Error('Camera could not start. Allow camera access, or paste the ticket code or choose a QR image.');
  }
});
$('qrImage').onchange = event => {
  const file = event.currentTarget.files[0];
  if (!file) return;
  action(async () => {
    need();
    if (!window.Html5Qrcode) throw Error('QR image reading is unavailable. Paste the ticket code instead.');
    await stopQrScanner();
    if (!qrScanner) qrScanner = new Html5Qrcode('qrScanner');
    $('qrScanner').hidden = false;
    try {
      const code = await qrScanner.scanFile(file, true);
      $('ticketCodeInput').value = code;
      await checkIn(code);
    } catch (error) {
      if (error.status) throw error;
      throw Error('Could not read or check in this QR image. Check the ticket code or paste it manually.');
    } finally { $('qrScanner').hidden = true; $('qrImage').value = ''; }
  });
};
$('nav').innerHTML = '<button type="button" data-page="events" class="active">Events</button><button type="button" data-page="account">Account</button>';
document.querySelectorAll('#nav button').forEach(button => button.onclick = () => go(button.dataset.page));
document.querySelectorAll('[data-workspace]').forEach(button => button.onclick = () => go(button.dataset.workspace));
document.querySelectorAll('[data-auth-tab]').forEach(button => button.onclick = () => { showAuth(button.dataset.authTab); authMessage(''); });
$('organizationLogin').onsubmit = event => { event.preventDefault(); authenticateOrganization('/api/organizations/login', event.currentTarget); };
$('organizationSignup').onsubmit = event => { event.preventDefault(); authenticateOrganization('/api/organizations/signup', event.currentTarget); };
$('organizerLogout').onclick = async () => {
  const token = EventPilotClient.readSession(organizationTokenName);
  lockOrganizer('You have signed out.'); $('organizationLogin').reset(); $('organizationSignup').reset();
  if (token) { try { await EventPilotClient.request('/api/organizations/logout', { method: 'POST', body: {}, headers: { Authorization: `Bearer ${token}` } }); } catch {} }
};
$('emailOrganization').onclick = () => action(async () => {
  const button = $('emailOrganization'); button.disabled = true; $('organizationEmailStatus').textContent = 'Sending email…';
  try { const result = await req('/api/organizations/email', 'POST', {}); $('organizationEmailStatus').textContent = emailStatusText(result); }
  catch (error) { $('organizationEmailStatus').textContent = ''; throw error; }
  finally { button.disabled = false; }
});
$('changeOrganizationPassword').onsubmit = event => {
  event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button');
  action(async () => {
    button.disabled = true; $('passwordMessage').textContent = '';
    try { await req('/api/organizations/change-password', 'POST', Object.fromEntries(new FormData(form))); form.reset(); $('passwordMessage').textContent = 'Password updated. Other signed-in sessions have been signed out.'; }
    finally { button.disabled = false; }
  });
};
$('newEvent').onclick = () => action(() => prepareEventEditor());
$('editEvent').onclick = () => action(() => prepareEventEditor(d.event));
$('cancelEvent').onclick = () => { if (editingId) go('dashboard'); else go('events'); editingId = null; };
$('backToEvents').onclick = () => go('events'); $('openCopilot').onclick = () => go('ai');
$('events').onchange = () => action(() => openEvent(Number($('events').value)));
$('includeArchived').onchange = () => action(() => load());
$('refresh').onclick = () => action(() => refresh());
$('date').onchange = () => action(() => refresh());
$('resetDate').onclick = () => action(async () => { $('date').value = today() < d.event.start_date ? d.event.start_date : today(); await refresh(); });
$('role').onchange = render;
$('demo').onclick = () => action(async () => { $('demo').disabled = true; try { const event = await req('/api/demo', 'POST'); await load(event.id); await openEvent(event.id); $('simulationControls').open = true; } finally { $('demo').disabled = false; } });
$('escalate').onclick = () => action(async () => { $('escalate').disabled = true; try { await req(`/api/events/${need()}/escalate?sim_date=${encodeURIComponent($('date').value)}`, 'POST'); await refresh(); $('actionStatus').textContent = 'Overdue escalations checked. Review them in Plan.'; } finally { $('escalate').disabled = false; } });
$('startEvent').onclick = () => { $('startEventPrompt').hidden = false; $('startEventPrompt').scrollIntoView({ block: 'nearest' }); };
$('cancelStartEvent').onclick = () => $('startEventPrompt').hidden = true;
$('confirmStartEvent').onclick = () => action(async () => {
  const selected = need(), button = $('confirmStartEvent'); button.disabled = true;
  try { const result = await req(`/api/events/${selected}/start`, 'POST', {}); if (eid !== selected) return; $('startEventPrompt').hidden = true; if (d && d.event.id === selected) { d.event.lifecycle_status = 'Started'; d.event.started_at = result.started_at; } $('startEvent').hidden = true; $('eventStarted').hidden = false; $('actionStatus').textContent = `Event started. ${emailStatusText(result)}`; await refresh(); }
  finally { button.disabled = false; }
});
$('archiveEvent').onclick = () => { $('archivePrompt').hidden = false; $('archivePrompt').scrollIntoView({block:'nearest'}); };
$('cancelArchive').onclick = () => $('archivePrompt').hidden = true;
$('confirmArchive').onclick = () => action(async () => { $('confirmArchive').disabled = true; try { await req(`/api/events/${need()}/archive`, 'PATCH', { archived: true }); eid = null; d = null; go('events'); await load(); $('actionStatus').textContent = 'Event archived. Turn on Show archived to restore it.'; } finally { $('confirmArchive').disabled = false; } });
$('create').elements.format.onchange = updateVenueRequirement;
$('create').onsubmit = event => { event.preventDefault(); const form = event.currentTarget; action(async () => {
  $('saveEvent').disabled = true;
  try { const body = Object.fromEntries(new FormData(form)); body.target_regs = Number(body.target_regs);
    const updated = await req(editingId ? `/api/events/${editingId}` : '/api/events', editingId ? 'PATCH' : 'POST', body); const wasEdit = !!editingId; editingId = null; await load(updated.id); await openEvent(updated.id); $('actionStatus').textContent = wasEdit ? 'Event updated.' : 'Event created. Open Plan to see the generated tasks.';
  } finally { $('saveEvent').disabled = false; }
}); };
$('metrics').elements.date.onchange = () => fillMetrics(d && d.metrics.find(metric => metric.date === $('metrics').elements.date.value));
$('metrics').onsubmit = event => { event.preventDefault(); const button = event.currentTarget.querySelector('button'); action(async () => { button.disabled = true; try { const body = Object.fromEntries(new FormData(event.target)); for (const key of ['reach', 'clicks', 'registrations', 'confirmations', 'attendance']) body[key] = Number(body[key]); await req(`/api/events/${need()}/metrics`, 'POST', body); await refresh(); $('actionStatus').textContent = 'Numbers saved. Forecast and recommendations updated.'; } finally { button.disabled = false; } }); };
$('partnerForm').onsubmit = event => { event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button'); action(async () => { button.disabled = true; try { await req(`/api/events/${need()}/partners`, 'POST', { name: form.elements.name.value.trim(), stage: 'Contacted' }); form.reset(); await refresh(); $('actionStatus').textContent = 'Partner added.'; } finally { button.disabled = false; } }); };
document.addEventListener('click', event => {
  const button = event.target.closest('[data-done],[data-update],[data-open-event],[data-create-event],[data-restore-event],[data-open-metrics],[data-email-registration]');
  if (!button) return;
  action(async () => {
    if (button.hasAttribute('data-open-metrics')) { $('metricsDetails').open = true; $('metricsDetails').scrollIntoView({block:'start',behavior:'smooth'}); $('metrics').elements.reach.focus(); return; }
    if (button.hasAttribute('data-create-event')) { prepareEventEditor(); return; }
    if (button.dataset.openEvent) { await openEvent(Number(button.dataset.openEvent)); return; }
    button.disabled = true;
    try {
      if (button.dataset.emailRegistration) { const selected = need(); $('opsMessage').textContent = 'Sending ticket email…'; let result; try { result = await req(`/api/events/${selected}/attendees/${button.dataset.emailRegistration}/email`, 'POST', {}); } catch (error) { if (eid === selected) $('opsMessage').textContent = ''; throw error; } if (eid === selected) { $('opsMessage').textContent = emailStatusText(result); if ($('emailDelivery').open) await loadEmailDelivery(); } return; }
      if (button.dataset.restoreEvent) { await req(`/api/events/${button.dataset.restoreEvent}/archive`, 'PATCH', { archived: false }); await load(); $('actionStatus').textContent = 'Event restored.'; return; }
      if (button.dataset.done) { const task = d.tasks.find(task => task.id === Number(button.dataset.done)); const result = await req(`/api/tasks/${button.dataset.done}/done`, 'POST', { actor: $('role').value === 'All' ? task.role : $('role').value, sim_date: $('date').value }); $('actionStatus').textContent = `Task completed. Readiness and forecast recalculated. ${emailStatusText(result)}`; }
      else { const [kind, id, status] = button.dataset.update.split(':'); await req(`/api/${kind}/${id}`, 'PATCH', { status }); $('actionStatus').textContent = 'Plan updated.'; }
      await refresh();
    } finally { button.disabled = false; }
  });
});
$('metrics').elements.date.value = today();
EventPilotClient.writeSession('eventpilot_organizer_key', '');
clearOrganizerView(); showAuth();
if (EventPilotClient.readSession(organizationTokenName)) {
  authBusy = true; authMessage('Loading your account…'); document.querySelectorAll('#organizerAuth button').forEach(button => button.disabled = true);
  unlockOrganizer().catch(error => { if (!error.staleAccess) { showAuth(); authMessage(`${error.message} Please try signing in again.`, true); } }).finally(() => {
    authBusy = false; document.querySelectorAll('#organizerAuth button').forEach(button => button.disabled = false);
    if (window.eventpilotBoot) window.eventpilotBoot.ready();
  });
} else if (window.eventpilotBoot) window.eventpilotBoot.ready();



/* Groq AI chat with safe Markdown rendering */
// Drop-in chat controller for existing EventPilot HTML frontend.
// Requires marked and DOMPurify script tags in index.html.
const chatHistory = [];
let chatConversationVersion = 0;
const chatForm = document.getElementById('ask');
const chatPrompt = document.getElementById('prompt');
const chatSend = document.getElementById('sendButton');
const chatMessages = document.getElementById('chatMessages');
function renderMarkdown(raw) {
  if (!window.marked || !window.DOMPurify) return document.createTextNode(raw);
  const html = marked.parse(raw, { gfm: true, breaks: true });
  const template = document.createElement('template');
  template.innerHTML = DOMPurify.sanitize(html);
  return template.content;
}
function addMessage(role, content) {
  const item = document.createElement('article');
  item.className = `chat-message ${role}`;
  const avatar = document.createElement('div');
  avatar.className = 'chat-avatar';
  avatar.textContent = role === 'assistant' ? '✦' : 'You';
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble markdown-body';
  bubble.append(renderMarkdown(content));
  item.append(avatar, bubble);
  chatMessages.append(item);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return item;
}
function clearChat() {
  ++chatConversationVersion; chatSend.disabled = false;
  chatHistory.length = 0;
  chatMessages.innerHTML = '';
  const welcome = document.createElement('div');
  welcome.className = 'chat-welcome';
  welcome.innerHTML = '<div class="welcome-icon">✦</div><h2>How can I help your event?</h2><p>Ask about outreach, registration conversion, operations or sponsorships.</p>';
  chatMessages.append(welcome);
}
async function sendChat(event) {
  event.preventDefault();
  const prompt = chatPrompt.value.trim();
  if (!access || !d) { $('error').textContent = 'Open an event before asking the copilot.'; return; }
  const chatRevision = accessRevision, selectedEvent = eid, conversation = chatConversationVersion;
  if (!prompt || chatSend.disabled) return;
  const welcome = chatMessages.querySelector('.chat-welcome');
  if (welcome) welcome.remove();
  addMessage('user', prompt);
  chatHistory.push({ role: 'user', content: prompt });
  chatPrompt.value = '';
  chatSend.disabled = true;
  const pending = addMessage('assistant', 'Thinking…');
  try {
    const eventContext = typeof d !== 'undefined' && d ? `Event: ${d.event.name}; target registrations: ${d.event.target_regs}; actual: ${d.actual}; expected: ${d.expected}; gap: ${d.gap_pct}%; days remaining: ${d.days_left}; bottleneck: ${d.bottleneck}; risk: ${d.risk}%; forecast: ${d.forecast.available === false ? 'unavailable; record activity on two dates before predicting' : `median ${d.forecast.median}; target probability ${d.forecast.probability}%`}; pending partners: ${d.partners.filter(p => p.stage === 'Contacted').length}.` : 'No event selected.';
    const recent = chatHistory.slice(-8).map(m => `${m.role}: ${m.content}`).join('\n');
    const result = await req('/api/ai', 'POST', { event_id: selectedEvent, prompt: `Use this event context when relevant: ${eventContext}\n\nConversation:\n${recent}\n\nRespond to the latest user message. Format using Markdown headings, lists, and tables when useful.` });
    if (chatRevision !== accessRevision || selectedEvent !== eid || conversation !== chatConversationVersion) return;
    const bubble = pending.querySelector('.chat-bubble');
    bubble.textContent = '';
    bubble.append(renderMarkdown(result.text || 'No response received.'));
    chatHistory.push({ role: 'assistant', content: result.text || '' });
    if (result.response_id) {
      const tools = document.createElement('div'); tools.className = 'chat-email-actions';
      const emailButton = document.createElement('button'); emailButton.type = 'button'; emailButton.className = 'secondary'; emailButton.textContent = 'Email this advice';
      emailButton.dataset.emailAdvice = String(result.response_id); emailButton.dataset.adviceEvent = String(selectedEvent);
      const status = document.createElement('small'); status.setAttribute('role', 'status');
      tools.append(emailButton, status); bubble.append(tools);
    } else {
      const hint = document.createElement('small'); hint.className = 'email-status'; hint.textContent = 'Ask again to save this advice before emailing it.'; bubble.append(hint);
    }
  } catch (err) {
    if (chatRevision !== accessRevision || selectedEvent !== eid || conversation !== chatConversationVersion || err.staleAccess) return;
    pending.querySelector('.chat-bubble').textContent = `Request failed: ${err.message}`;
  } finally {
    if (conversation !== chatConversationVersion) return;
    chatSend.disabled = false;
    chatMessages.scrollTop = chatMessages.scrollHeight;
    if (chatRevision === accessRevision && selectedEvent === eid && page === 'ai') chatPrompt.focus();
  }
}
chatForm.addEventListener('submit', sendChat);
chatPrompt.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); chatForm.requestSubmit(); } });
document.getElementById('newChat').addEventListener('click', clearChat);
document.addEventListener('click', event => {
  const button = event.target.closest('[data-email-advice]'); if (!button) return;
  const selected = Number(button.dataset.adviceEvent), conversation = chatConversationVersion, status = button.parentElement.querySelector('small');
  if (selected !== eid || !access) return;
  button.disabled = true; status.textContent = 'Sending email…';
  req(`/api/ai/responses/${button.dataset.emailAdvice}/email`, 'POST', {}).then(result => { if (selected === eid && conversation === chatConversationVersion) status.textContent = emailStatusText(result); }).catch(error => { if (!error.staleAccess && selected === eid && conversation === chatConversationVersion) status.textContent = error.message; }).finally(() => button.disabled = false);
});
document.querySelectorAll('[data-copilot-prompt]').forEach(button => button.onclick = () => { chatPrompt.value = button.dataset.copilotPrompt; chatForm.requestSubmit(); });
window.clearEventPilotChat = () => { clearChat(); chatPrompt.value = ''; chatSend.disabled = false; };
clearChat();
