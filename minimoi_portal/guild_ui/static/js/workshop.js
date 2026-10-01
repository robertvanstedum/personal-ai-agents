// The focused Workshop (4a): a read-only status refresh every minute from
// /workshop (files and the queue on the server; never a model call). A failed
// refresh marks the screen unknown, never "nothing running": the old reading's
// age, headroom and recovery advice are marked as from the last good read.
import { $, localTime } from './dom.js';
import { apiGet } from './api.js';

const REFRESH_MS = 60000;
const FAILED_RECOVERY = 'The refresh failed: reload the page. If it keeps failing, on the Mac run workshop.py observe, then sync.';
let page;
let lastGood = null;   // when the last good host reading was taken (ISO)

function setText(sel, text) {
  const el = $(sel);
  if (el) el.textContent = text;
  return el;
}

function setRecovery(lines) {
  const list = $('[data-ws-recovery-list]');
  if (!list) return;
  list.replaceChildren(...lines.map((line) => {
    const li = document.createElement('li');
    li.textContent = line;
    return li;
  }));
}

// The ops strip (Guild 1.1 slice 5): each figure with its source and
// freshness, or "not measured"; text only, set with textContent.
function showOps(ops) {
  if (!Array.isArray(ops)) return;
  for (const o of ops) {
    const chip = $(`[data-ws-op="${o.id}"]`);
    if (chip) {
      chip.dataset.state = o.state;
      const text = $('[data-ws-op-text]', chip);
      if (text) text.textContent = o.text;
      let at = $('.sx-at', chip);
      if (o.fresh && o.fresh.at) {
        if (!at) { at = document.createElement('span'); at.className = 'sx-at'; chip.append(at); }
        at.textContent = ` · ${localTime(o.fresh.at)}`;
      } else if (at) at.remove();
    }
    const row = $(`[data-ws-op-row="${o.id}"]`);
    if (row) {
      row.dataset.state = o.state;
      const value = $('[data-ws-op-value]', row);
      if (value) value.textContent = (o.value || (o.state === 'not_measured' ? 'not measured' : 'unknown')) + (o.state === 'stale' ? ' (stale)' : '');
      const fresh = $('[data-ws-op-fresh]', row);
      if (fresh) fresh.textContent = o.fresh && o.fresh.at ? `${localTime(o.fresh.at)}${o.fresh.age ? ` · ${o.fresh.age}` : ''}` : '—';
    }
  }
}

function opsFailed(when) {
  for (const chip of document.querySelectorAll('[data-ws-op]')) {
    if (chip.dataset.state === 'measured') chip.dataset.state = 'stale';
    const at = $('.sx-at', chip);
    if (at && !at.textContent.includes('last good read')) at.textContent = ` · last good read ${when}`;
  }
  for (const row of document.querySelectorAll('[data-ws-op-row]')) {
    if (row.dataset.state === 'measured') row.dataset.state = 'stale';
  }
  const jobs = $('[data-ws-jobs]');
  if (jobs) jobs.dataset.stale = 'true';
}

function show(v) {
  const adm = v.admission || {};
  const box = $('[data-ws-admission]');
  if (box) { box.dataset.verdict = adm.verdict || 'unknown'; delete box.dataset.stale; }
  setText('[data-ws-verdict]', `Host ${adm.verdict || 'unknown'}`);
  setText('[data-ws-reason]', (adm.reasons && adm.reasons[0]) || '');
  setText('[data-ws-age]', adm.age ? `· read ${adm.age}` : '');
  if (adm.observed_at) lastGood = adm.observed_at;
  const runs = $('[data-ws-runs]');
  if (runs && v.runs) {
    runs.dataset.known = String(Boolean(v.runs.known));
    const list = (v.runs.runs || []).map((r) => `${r.label || r.kind} (up ${r.elapsed})`).join(', ');
    runs.textContent = list ? `${v.runs.text}: ${list}` : v.runs.text;
  }
  const bg = $('[data-ws-background]');
  if (bg && v.runs) {
    const names = (v.runs.background || []).map((r) => r.label || r.kind);
    bg.textContent = names.length ? `Also running, not counted as build sessions: ${names.join(', ')}.` : '';
  }
  const next = $('[data-ws-next] strong');
  if (next) next.textContent = v.next_actor || 'unknown';
  const head = setText('[data-ws-headroom]', v.headroom || 'Headroom unknown (no fresh host reading).');
  if (head) head.dataset.stale = v.headroom ? 'false' : 'true';
  if (Array.isArray(v.recovery)) setRecovery(v.recovery);
  setText('[data-ws-refreshed]', `as of ${localTime(v.observed_at)}`);
  showOps(v.ops);
}

function failed() {
  const box = $('[data-ws-admission]');
  if (box) { box.dataset.verdict = 'unknown'; box.dataset.stale = 'true'; }
  setText('[data-ws-verdict]', 'Host unknown');
  setText('[data-ws-reason]', 'the refresh failed; what follows is from the last good read');
  const when = lastGood ? localTime(lastGood) : 'unknown';
  setText('[data-ws-age]', `· last good read ${when}`);
  const runs = $('[data-ws-runs]');
  if (runs) { runs.dataset.known = 'false'; runs.textContent = 'Running agents unknown (refresh failed)'; }
  const head = $('[data-ws-headroom]');
  if (head && head.dataset.stale !== 'true') {
    head.dataset.stale = 'true';
    head.textContent = `${head.textContent} (last good read ${when}; may be out of date)`;
  }
  setRecovery([FAILED_RECOVERY]);
  setText('[data-ws-refreshed]', `as of ${when} (last good read; the refresh failed)`);
  opsFailed(when);
}

async function refresh() {
  const item = page.workshop_item ? `?item=${encodeURIComponent(page.workshop_item)}` : '';
  const r = await apiGet(`/workshop${item}`);
  if (r.ok && r.body && r.body.admission) show(r.body); else failed();
}

export function initWorkshop(p) {
  page = p;
  if (page.page !== 'workshop') return;
  const age = $('[data-ws-age]');
  lastGood = (age && age.dataset.observed) || null;
  const more = $('[data-ws-more]');
  if (more && window.matchMedia('(max-width: 640px)').matches) more.open = false;   // phone: Now, Needs you, Budget, chat first
  window.setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, REFRESH_MS);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
}

export { refresh as refreshWorkshop };
