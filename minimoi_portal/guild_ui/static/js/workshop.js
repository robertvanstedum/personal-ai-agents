// The focused Workshop (4a): a read-only status refresh every minute from
// /workshop (files and the queue on the server; never a model call). A failed
// refresh marks the screen unknown, never "nothing running".
import { $, localTime } from './dom.js';
import { apiGet } from './api.js';

const REFRESH_MS = 60000;
let page;

function show(v) {
  const adm = v.admission || {};
  const box = $('[data-ws-admission]');
  if (box) box.dataset.verdict = adm.verdict || 'unknown';
  const verdict = $('[data-ws-verdict]');
  if (verdict) verdict.textContent = `Host ${adm.verdict || 'unknown'}`;
  const reason = $('[data-ws-reason]');
  if (reason) reason.textContent = (adm.reasons && adm.reasons[0]) || '';
  const age = $('[data-ws-age]');
  if (age) age.textContent = adm.age ? `· read ${adm.age}` : '';
  const runs = $('[data-ws-runs]');
  if (runs && v.runs) {
    runs.dataset.known = String(Boolean(v.runs.known));
    const list = (v.runs.runs || []).map((r) => `${r.kind} (up ${r.elapsed})`).join(', ');
    runs.textContent = list ? `${v.runs.text}: ${list}` : v.runs.text;
  }
  const next = $('[data-ws-next] strong');
  if (next) next.textContent = v.next_actor || 'unknown';
  const stamp = $('[data-ws-refreshed]');
  if (stamp) stamp.textContent = `as of ${localTime(v.observed_at)}`;
}

function failed() {
  const box = $('[data-ws-admission]');
  if (box) box.dataset.verdict = 'unknown';
  const verdict = $('[data-ws-verdict]');
  if (verdict) verdict.textContent = 'Host unknown';
  const reason = $('[data-ws-reason]');
  if (reason) reason.textContent = 'the refresh failed; the values below may be old';
  const runs = $('[data-ws-runs]');
  if (runs) { runs.dataset.known = 'false'; runs.textContent = 'Running agents unknown (refresh failed)'; }
}

async function refresh() {
  const item = page.workshop_item ? `?item=${encodeURIComponent(page.workshop_item)}` : '';
  const r = await apiGet(`/workshop${item}`);
  if (r.ok && r.body && r.body.admission) show(r.body); else failed();
}

export function initWorkshop(p) {
  page = p;
  if (page.page !== 'workshop') return;
  const more = $('[data-ws-more]');
  if (more && window.matchMedia('(max-width: 640px)').matches) more.open = false;   // phone: Now, Needs you, Budget, chat first
  window.setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, REFRESH_MS);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
}

export { refresh as refreshWorkshop };
