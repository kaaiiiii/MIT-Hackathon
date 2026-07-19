const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: isForm
      ? options.headers
      : { 'content-type': 'application/json', ...options.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return body;
}

export const createIntakeSession = () =>
  api('/api/v1/intake/sessions', {
    method: 'POST',
    body: JSON.stringify({ vertical: 'moving', benchmark_refs: [] }),
  });

export const startVoiceIntake = (sessionId) =>
  api(`/api/v1/intake/sessions/${sessionId}/voice`, { method: 'POST' });

export const captureVoiceEvidence = (sessionId, payload) =>
  api(`/api/v1/intake/sessions/${sessionId}/voice`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const enrichIntake = (sessionId) =>
  api(`/api/v1/research/intake/sessions/${sessionId}/enrich`, { method: 'POST' });

export const confirmIntake = (sessionId) =>
  api(`/api/v1/intake/sessions/${sessionId}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ approved: true, confirmed_by: 'negotiator_web_user' }),
  });

export const getReportContext = (versionId) =>
  api(`/api/v1/research/specs/${encodeURIComponent(versionId)}/report-context`);

export const prepareReport = (versionId) =>
  api(`/api/v1/reports/${encodeURIComponent(versionId)}/prepare`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
