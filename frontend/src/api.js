// Reviewer-Name und Token liegen nur in sessionStorage (verschwinden mit dem Tab).
const get = (k) => { try { return sessionStorage.getItem(k) || ''; } catch { return ''; } };
export const reviewer = {
  get name() { return get('reviewer'); }, get token() { return get('token'); },
  set(name, token) { try { sessionStorage.setItem('reviewer', name); sessionStorage.setItem('token', token); } catch { /* egal */ } },
  clear() { try { sessionStorage.clear(); } catch { /* egal */ } }
};

async function request(path, { method = 'GET', body, auth = false } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth) { headers['X-Reviewer'] = reviewer.name; headers['X-Reviewer-Token'] = reviewer.token; }
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let message = `Fehler ${res.status}`;
    try { const b = await res.json(); if (typeof b.detail === 'string') message = b.detail; else if (b.detail) message = 'Ungültige Eingabe.'; } catch { /* keine JSON-Antwort */ }
    throw new Error(message);
  }
  return res.json();
}

export const api = {
  config: () => request('/api/config'),
  metrics: () => request('/api/metrics'),
  tickets: () => request('/api/tickets'),
  ticket: (id) => request(`/api/tickets/${id}`),
  createTicket: (data) => request('/api/tickets', { method: 'POST', body: data }),
  approvals: () => request('/api/approvals', { auth: true }),
  decide: (id, approve, comment) => request(`/api/actions/${id}/decision`, { method: 'POST', auth: true, body: { approve, comment } }),
  undo: (id) => request(`/api/actions/${id}/undo`, { method: 'POST', auth: true }),
  review: (id, verdict, comment) => request(`/api/tickets/${id}/review`, { method: 'POST', auth: true, body: { verdict, comment } }),
  audit: (ticketId) => request(`/api/audit${ticketId ? `?ticket_id=${ticketId}` : ''}`, { auth: true }),
  verifyAudit: () => request('/api/audit/verify', { auth: true }),
  simulation: () => request('/api/simulation'),
  resetSimulation: () => request('/api/simulation/reset', { method: 'POST', auth: true })
};
