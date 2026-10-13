/* Cache intelligence for the selected snapshot so navigation keeps its rankings. */
let intelligenceError = '';
window.renderIntelligence = function () {
  if (!d) return;
  const info = intelligence;
  if (info) {
    $('riskHeatmap').innerHTML = info.heatmap.map(x => `<div class="risk-card ${esc(x.level.toLowerCase())}"><div class="risk-head"><strong>${esc(x.category)}</strong><span>${esc(x.level)}</span></div><b>${esc(x.score)}/100</b><p>${esc(x.why)}</p></div>`).join('');
    $('timelineRows').innerHTML = info.timeline.map(t => `<div class="timeline-row ${t.overdue?'overdue':''}"><div><b>${esc(t.title)}</b><p>${esc(t.role)} · ${esc(t.priority)}</p></div><div>${esc(t.due_date)} · ${t.overdue?'Overdue':esc(t.status)}</div></div>`).join('') || '<p>No tasks yet.</p>';
    if (page === 'dashboard') $('recs').innerHTML = renderRecommendations(info.ranked_actions);
  } else {
    $('riskHeatmap').textContent = intelligenceError || 'Loading event health…';
    $('timelineRows').textContent = intelligenceError || 'Loading event timeline…';
  }
    const last = d.decisions[d.decisions.length - 1];
    $('impactCompare').innerHTML = last && last.before_value != null && last.after_value != null && d.forecast.available !== false ? `<div class="impact-stat"><small>Before action</small><b>${Number(last.before_value).toFixed(1)}%</b><span>Forecast probability</span></div><div class="impact-stat"><small>After action</small><b>${Number(last.after_value).toFixed(1)}%</b><span>Forecast probability</span></div><div class="impact-stat"><small>Change</small><b>${(last.after_value-last.before_value)>=0?'+':''}${(last.after_value-last.before_value).toFixed(1)} pp</b><span>${esc(last.action)}</span></div>` : last ? '<p>Forecast comparison needs activity on at least two dates.</p>' : '<p>Complete a recommended action to see its simulated forecast impact.</p>';
};
window.loadIntelligence = async function () {
  if (!eid || !d) return;
  const snapshot = d;
  intelligence = null;
  intelligenceError = '';
  window.renderIntelligence();
  try {
    const info = await req(`/api/events/${snapshot.event.id}/intelligence?sim_date=${encodeURIComponent(snapshot.sim_date)}`);
    if (d !== snapshot) return;
    intelligence = info;
  } catch (err) {
    if (d !== snapshot) return;
    intelligenceError = `Could not load intelligence: ${err.message}`;
  }
  window.renderIntelligence();
};
