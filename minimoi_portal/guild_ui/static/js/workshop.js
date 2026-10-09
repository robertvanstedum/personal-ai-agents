// The focused Workshop (4a): a read-only status refresh every minute from
// /workshop (files and the queue on the server; never a model call). A failed
// refresh marks the screen unknown, never "nothing running": the old reading's
// age, headroom and recovery advice are marked as from the last good read.
import { $, $$, el, localTime } from './dom.js';
import { apiGet } from './api.js';

const REFRESH_MS = 60000;
const FAILED_RECOVERY = 'The refresh failed: reload the page. If it keeps failing, on the Mac run workshop.py observe, then sync.';
let page;
let lastGood = null;   // when the last good host reading was taken (ISO)
const AHEAD_MS = 2 * 60000;   // a time further ahead than this: the clocks disagree (record.CLOCK_AHEAD)

// An age the same way the server words it, from now (so it keeps moving
// while refreshes fail). A time dated ahead is never "just now".
function ageText(iso) {
  const t = iso ? new Date(iso).getTime() : NaN;
  if (Number.isNaN(t)) return '';
  const ms = Date.now() - t;
  if (ms < -AHEAD_MS) return 'unknown (clock ahead)';
  const mins = Math.max(Math.floor(ms / 60000), 0);
  return mins < 1 ? 'just now' : mins < 90 ? `${mins} min ago` : `${Math.floor(mins / 60)} h ago`;
}

function stamp(fresh) {
  return `${fresh.label ? `${fresh.label} ` : ''}${fresh.date ? `${fresh.date} ` : ''}${localTime(fresh.at)}`;
}

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
    const at0 = (o.fresh && o.fresh.at) || '';
    const chip = $(`[data-ws-op="${o.id}"]`);
    if (chip) {
      chip.dataset.state = o.state;
      chip.dataset.at = at0;           // this figure's own last good time (never the host's for spend)
      chip.dataset.date = (o.fresh && o.fresh.date) || '';
      const text = $('[data-ws-op-text]', chip);
      if (text) text.textContent = o.text;
      let at = $('.sx-at', chip);
      if (o.fresh && o.fresh.at) {
        if (!at) { at = document.createElement('span'); at.className = 'sx-at'; chip.append(at); }
        at.textContent = ` · ${stamp(o.fresh)}`;
      } else if (at) at.remove();
    }
    const row = $(`[data-ws-op-row="${o.id}"]`);
    if (row) {
      row.dataset.state = o.state;
      row.dataset.at = at0;
      row.dataset.date = (o.fresh && o.fresh.date) || '';
      const value = $('[data-ws-op-value]', row);
      if (value) value.textContent = (o.value || (o.state === 'not_measured' ? 'not measured' : 'unknown')) + (o.state === 'stale' ? ' (stale)' : '');
      const fresh = $('[data-ws-op-fresh]', row);
      if (fresh) fresh.textContent = o.fresh && o.fresh.at ? `${stamp(o.fresh)}${o.fresh.age ? ` · ${o.fresh.age}` : ''}` : '—';
    }
  }
}

// A failed refresh: every figure that had a value keeps it, marked stale,
// with its own last good time and an age that keeps counting (F3).
function goodRead(node) {
  return `${node.dataset.date ? `${node.dataset.date} ` : ''}${localTime(node.dataset.at)}`;
}

function opsFailed() {
  for (const chip of $$('[data-ws-op]')) {
    if (chip.dataset.state !== 'measured' && chip.dataset.state !== 'stale') continue;
    chip.dataset.state = 'stale';
    const at = $('.sx-at', chip);
    const own = chip.dataset.at;
    if (at && own) at.textContent = ` · last good read ${goodRead(chip)}`;
    const text = $('[data-ws-op-text]', chip);
    if (text && !text.textContent.endsWith(' · stale')) text.textContent += ' · stale';
  }
  for (const row of $$('[data-ws-op-row]')) {
    if (row.dataset.state !== 'measured' && row.dataset.state !== 'stale') continue;
    row.dataset.state = 'stale';
    const value = $('[data-ws-op-value]', row);
    if (value && !value.textContent.endsWith(' (stale)')) value.textContent += ' (stale)';
    const fresh = $('[data-ws-op-fresh]', row);
    const own = row.dataset.at;
    if (fresh) fresh.textContent = own ? `last good read ${goodRead(row)} · ${ageText(own)}` : 'unknown';
  }
}

// The job cards, rebuilt from the refresh (F2): the same wording as the
// server's first paint, text only.
function jobCard(c, stale) {
  const state = c.flag ? (c.flag === 'stale' ? 'stale' : 'unknown') : c.state;
  const card = el('article', { class: 'ws-job', 'data-ws-job': c.item, 'data-state': state });
  const head = el('div', { class: 'ws-jh' });
  head.append(el('span', { class: 'ws-pill', 'data-ws-pill': true }, c.pill || c.state));
  if (stale) head.append(el('span', { class: 'ws-pill', 'data-state': 'stale', 'data-ws-stale-pill': true }, 'stale record'));
  head.append(el('span', { class: 'ws-sp' }));
  if (c.queue_id) {
    const a = el('a', { class: 'ws-ref', href: `${page.urls.build_log}?item=${encodeURIComponent(c.queue_id)}` }, `#${c.queue_id}`);
    head.append(a);
  } else head.append(el('span', { class: 'ws-ref' }, c.item));
  card.append(head, el('h3', { class: 'ws-job-title' }, c.title || c.item));
  const lc = c.last_contact || {};
  const contact = lc.at ? `${localTime(lc.at)}${lc.age ? ` (${ageText(lc.at) || lc.age})` : ''}` : 'unknown';
  card.append(el('p', { class: 'ws-who' },
    `${c.flag ? `last reported: ${c.state} · ` : ''}${c.actor || 'unknown'}${c.stage ? ` · ${c.stage}` : ''} · last contact ${contact}, reported${c.next_actor ? ` · next: ${c.next_actor}` : ''}`));
  if (c.note) card.append(el('p', { class: 'unknown-line', 'data-ws-job-note': true }, c.note));
  const ev = el('ol', { class: 'ws-ev', 'data-ws-evidence': true, 'aria-label': `Evidence for ${c.item}` });
  for (const e of c.evidence || []) ev.append(el('li', {}, `${localTime(e.at)} ${e.actor || ''} · ${e.kind || ''} · ${e.text || ''}`));
  card.append(ev, el('p', { class: 'ws-jf' }, 'Evidence is what the worker reported, with its time; nothing here is inferred.'));
  const actions = el('div', { class: 'ws-ja' });
  card.append(actions);
  return card;
}

function showJobs(j) {
  const section = $('[data-ws-jobs]');
  const body = $('[data-ws-jobs-body]');
  if (!section || !body || !j) return;
  section.dataset.known = String(Boolean(j.known));
  section.dataset.stale = String(Boolean(j.stale));
  const nodes = [];
  if (!j.known) nodes.push(el('p', { class: 'unknown-line', 'data-ws-jobs-unknown': true }, j.text));
  else if (j.stale) nodes.push(el('p', { class: 'unknown-line', 'data-ws-jobs-stale': true }, j.stale_text));
  if (j.cards && j.cards.length) {
    const grid = el('div', { class: 'ws-cards' });
    grid.append(...j.cards.map((c) => jobCard(c, j.stale)));
    nodes.push(grid);
    if (j.more) nodes.push(el('p', { class: 'small', 'data-ws-jobs-more': true }, `+${j.more} more in the workshop record (needs you first; older and completed jobs are not shown).`));
  } else if (j.known) nodes.push(el('p', { class: 'small', 'data-ws-jobs-empty': true }, j.text));
  body.replaceChildren(...nodes);
}

// A failed refresh: the cards stay as last read, visibly marked as possibly out of date.
function jobsFailed(when) {
  const section = $('[data-ws-jobs]');
  const body = $('[data-ws-jobs-body]');
  if (!section || !body || section.dataset.known !== 'true') return;
  section.dataset.stale = 'true';
  const text = `These job states may be out of date (the refresh failed; last good read ${when}).`;
  let line = $('[data-ws-jobs-stale]', body);
  if (!line) { line = el('p', { class: 'unknown-line', 'data-ws-jobs-stale': true }); body.prepend(line); }
  line.textContent = text;
  for (const head of $$('.ws-job .ws-jh', body)) {
    if (!$('[data-ws-stale-pill]', head)) {
      const pill = $('[data-ws-pill]', head);
      const stale = el('span', { class: 'ws-pill', 'data-state': 'stale', 'data-ws-stale-pill': true }, 'stale record');
      if (pill) pill.after(stale); else head.prepend(stale);
    }
  }
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
  showJobs(v.jobs);
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
  opsFailed();
  jobsFailed(when);
}

async function refresh() {
  const item = page.workshop_item ? `?item=${encodeURIComponent(page.workshop_item)}` : '';
  const r = await apiGet(`/workshop${item}`);
  if (r.ok && r.body && r.body.admission) show(r.body); else failed();
}

export function initWorkshop(p) {
  page = p;
  if (page.page !== 'operate_build_host') return;
  const age = $('[data-ws-age]');
  lastGood = (age && age.dataset.observed) || null;
  const more = $('[data-ws-more]');
  if (more && window.matchMedia('(max-width: 640px)').matches) more.open = false;   // phone: Now, Needs you, Budget, chat first
  window.setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, REFRESH_MS);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
}

export { refresh as refreshWorkshop };
