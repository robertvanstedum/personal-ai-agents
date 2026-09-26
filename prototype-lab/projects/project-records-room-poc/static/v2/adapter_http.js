// A4 — not wired in U. preview.html must never load this module, and nothing in U imports it.
//
// Live implementation of contract.js over the Records HTTP API (route names follow plan §5 and are
// finalized in A1/A2/A4). Its one hard rule: any payload that carries a "simulated" key is refused,
// so fixture data can never be shown as live.
import { SCHEMA_IDS, assertAdapter } from './contract.js';

export class SimulatedPayloadError extends Error {
  constructor(path) {
    super(`Refusing a payload with "simulated" at ${path}: live views never show fixture data.`);
    this.name = 'SimulatedPayloadError';
  }
}

/** Throws SimulatedPayloadError if `payload` contains a "simulated" key anywhere; otherwise returns it. */
export function rejectSimulated(payload, path = '$') {
  if (payload && typeof payload === 'object') {
    if (Object.prototype.hasOwnProperty.call(payload, 'simulated')) throw new SimulatedPayloadError(path);
    for (const [key, value] of Object.entries(payload)) rejectSimulated(value, `${path}.${key}`);
  }
  return payload;
}

export function createHttpAdapter({ base = '', fetchImpl = globalThis.fetch.bind(globalThis) } = {}) {
  async function request(method, path, body, headers = {}) {
    if (body !== undefined) rejectSimulated(body, '$request');
    const response = await fetchImpl(base + path, {
      method, credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = rejectSimulated(await response.json().catch(() => null));
    if (response.status === 503 && payload?.code === 'store_unavailable') return { status: 'unavailable', ...payload };
    if (response.status === 404) return { status: 'not_found', code: 'not_found', ...payload };
    if (!response.ok) throw new Error(payload?.error || `Request failed (HTTP ${response.status})`);
    return payload;
  }
  const get = (path) => request('GET', path);
  const id = encodeURIComponent;
  const unwired = (name) => async () => { throw new Error(`${name}: A4 wires this route; it is not available in U.`); };

  return assertAdapter({
    getOverview: unwired('getOverview'),
    getAttempt: (attemptId) => get(`/api/v1/work/attempts/${id(attemptId)}`),
    listRecords: () => get('/api/v1/records?recent=50'),
    getRecord: (recordId, revision) => get(`/api/v1/records/${id(recordId)}${revision ? `?revision=${id(revision)}` : ''}`),
    getInterpretation: unwired('getInterpretation'),
    getSession: (sessionId) => get(`/api/v2/sessions/${id(sessionId)}`),
    saveRecord: (draft) => request('POST', '/api/v1/records', { destination: 'workspace:robert', ...draft },
      { 'Idempotency-Key': `save-${crypto.randomUUID()}` }),
    act: unwired('act'),
    schemaIds: SCHEMA_IDS,
  });
}
