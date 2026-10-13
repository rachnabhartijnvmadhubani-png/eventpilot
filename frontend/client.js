/* Shared same-origin API client; private browser storage is optional. */
(function () {
  const memory = {};
  const host = window.location.hostname;
  const apiBase = ['localhost', '127.0.0.1'].includes(host) && window.location.port !== '8000'
    ? `${window.location.protocol}//${host}:8000` : '';

  function readSession(name) {
    if (Object.prototype.hasOwnProperty.call(memory, name)) return memory[name];
    try { return sessionStorage.getItem(name) || memory[name] || ''; }
    catch { return memory[name] || ''; }
  }
  function timestampText(value) {
    if (!value) return '—';
    // Older database records contain UTC timestamps without an explicit offset.
    const text = String(value);
    const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text) ? text : `${text}Z`);
    if (Number.isNaN(date.getTime())) return '—';
    return `${new Intl.DateTimeFormat('en-IN', {
      timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: true
    }).format(date)} IST`;
  }
  function writeSession(name, value) {
    memory[name] = value || '';
    try {
      if (value) sessionStorage.setItem(name, value);
      else sessionStorage.removeItem(name);
      return true;
    } catch { return false; }
  }
  async function request(path, options = {}) {
    const headers = { Accept: 'application/json', ...(options.headers || {}) };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), options.timeoutMs || 20000) : null;
    try {
      const response = await fetch(apiBase + path, {
        method: options.method || 'GET', headers,
        ...(controller ? { signal: controller.signal } : {}),
        ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) })
      });
      const raw = await response.text();
      let data;
      try { data = raw ? JSON.parse(raw) : {}; }
      catch { throw Error(`EventPilot returned an unexpected response (HTTP ${response.status}). Reload the page or try again later.`); }
      if (!response.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.map(issue => `${(issue.loc || []).filter(part => part !== 'body').join('.')}: ${issue.msg}`).join('; ')
          : data.detail;
        const error = Error(typeof detail === 'string' ? detail : `Request failed (HTTP ${response.status}).`);
        error.status = response.status;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw Error('The request timed out. Check your connection and try again.');
      if (error instanceof TypeError) throw Error('Could not reach EventPilot. Check your connection and try again.');
      throw error;
    } finally { if (timer) clearTimeout(timer); }
  }
  window.EventPilotClient = { apiBase, readSession, writeSession, timestampText, request };
}());
