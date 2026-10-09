// Master Craftsman jobs, in the conversation that asked for them (overnight build, step 3).
//
// A job is work that runs in the background. It shows as one card in the thread: a live "Job running · 42s · Stop"
// line, what the run is doing, and, when it ends, the honest end in words (done, failed, stopped, outcome unknown, not
// started) with the list of tool calls it made and the files it wrote. The result itself is an ordinary Master
// Craftsman note that appears in the thread. Everything is read from the server: the card is rebuilt on every poll, so a
// reload, another tab or a restarted portal shows the same job. The page never decides a state; it only displays one.
// Nothing here runs unless the server says jobs are on (page.jobs.enabled).

import { el, announce, toast } from './dom.js';
import { apiGet, apiStop } from './api.js';

const POLL_MS = 3000;
const LABELS = { queued: 'Job waiting', dispatching: 'Job starting', running: 'Job running', completed: 'Job done', failed: 'Job failed',
  stopped: 'Job stopped', unknown: 'Job outcome unknown', not_started: 'Job not started' };

// One plain line per tool call. A call with no target (checking on a background command) still says what it was.
const TOOL_WORDS = { exec: 'ran a command', process: 'checked on a background command', read: 'read', write: 'wrote', edit: 'edited', ls: 'listed', dir_list: 'listed',
  grep: 'searched', find: 'searched', web_search: 'searched the web', web_fetch: 'fetched a web page' };
export function toolWords(t) {
  const what = TOOL_WORDS[t.name] || t.name || 'did something';
  return t.target ? `${what}: ${t.target}` : what;
}

export function initJobs({ page, thread, noteLine, follow }) {
  if (!page || !page.jobs || !page.jobs.enabled || !page.conversation || !thread) return { accepted() {}, refresh() {} };
  const cid = page.conversation.id;
  const base = `/conversations/${encodeURIComponent(cid)}/jobs`;
  const cards = new Map();            // job id → { li, view, shownAt, perf }
  let timer = null;
  let ticker = null;

  const timeOf = (li) => {
    const t = li.querySelector('[data-iso]');
    const ms = t ? Date.parse(t.getAttribute('data-iso')) : NaN;
    return Number.isNaN(ms) ? null : ms;
  };

  function place(li, createdAtSeconds) {
    // In time order among the notes already in the thread; a job newer than everything goes last.
    const at = createdAtSeconds * 1000;
    const next = Array.from(thread.children).find((c) => c !== li && c.dataset.job === undefined && (timeOf(c) || 0) > at && timeOf(c) !== null);
    if (next) thread.insertBefore(li, next); else thread.append(li);
  }

  function build(view) {
    const li = el('li', { class: 'msg msg-note msg-job', 'data-job': view.id, 'data-kind': 'job' });
    const meta = el('p', { class: 'msg-meta' });
    meta.append(el('span', { class: 'mc-mark', 'aria-hidden': 'true' }, 'MC'), ' ', el('span', { class: 'msg-label' }, 'Job'), ' · ',
      el('span', { 'data-job-title': '' }));
    const line = el('p', { class: 'job-line', role: 'status' });
    line.append(el('span', { 'data-job-state': '' }), ' ', el('span', { 'data-job-elapsed': '', class: 'job-elapsed' }), ' ',
      el('button', { type: 'button', class: 'btn-mini', 'data-job-stop': '', title: 'Stop this job' }, 'Stop'));
    li.append(meta, line, el('p', { class: 'msg-text small', 'data-job-message': '' }), el('ul', { class: 'small job-files', 'data-job-files': '' }));
    const audit = el('details', { class: 'job-audit small', 'data-job-audit': '' });
    audit.append(el('summary', { 'data-job-audit-summary': '' }), el('ul', { 'data-job-tools': '' }), el('ul', { 'data-job-writes': '' }),
      el('p', { class: 'small', 'data-job-audit-note': '' }));
    li.append(audit, el('p', { class: 'small', 'data-job-result': '', hidden: '' }));
    li.querySelector('[data-job-stop]').addEventListener('click', () => stop(view.id, li));
    return li;
  }

  function paint(li, v) {
    li.dataset.state = v.state;
    li.querySelector('[data-job-title]').textContent = v.title;
    li.querySelector('[data-job-state]').textContent = LABELS[v.state] || 'Job';
    li.querySelector('[data-job-message]').textContent = v.message;
    const stopBtn = li.querySelector('[data-job-stop]');
    stopBtn.hidden = !v.can_stop;
    stopBtn.disabled = !!v.stop_requested;
    stopBtn.textContent = v.stop_requested ? 'Stop requested' : 'Stop';
    li.querySelector('[data-job-elapsed]').hidden = !v.active && v.state !== 'completed';
    const files = li.querySelector('[data-job-files]');
    files.replaceChildren(...(v.files || []).map((f) => el('li', {}, `${f.name} · given to the job as a private copy`)));
    files.hidden = !(v.files || []).length;
    const audit = li.querySelector('[data-job-audit]');
    const tools = v.tools || [];
    const writes = v.writes || [];
    audit.hidden = !tools.length && !writes.length;
    li.querySelector('[data-job-audit-summary]').textContent =
      `What it did · ${tools.length}${v.tools_truncated ? '+' : ''} tool call${tools.length === 1 ? '' : 's'}${writes.length ? ` · ${writes.length} file${writes.length === 1 ? '' : 's'} written` : ''}`;
    li.querySelector('[data-job-tools]').replaceChildren(...tools.filter((t) => t && (t.name || t.target)).map((t) =>
      el('li', t.flag ? { 'data-flag': t.flag, class: 'job-flagged' } : {}, `${toolWords(t)}${t.ok ? '' : ' · failed'}${t.flag ? ' · ⚠ outside its allowed area' : ''}`)));
    li.dataset.flagged = v.flagged ? 'true' : 'false';
    li.querySelector('[data-job-writes]').replaceChildren(...writes.map((w) =>
      el('li', {}, `${w.path} · ${w.verified && w.exists ? `checked: ${w.bytes} bytes, ${String(w.sha256).slice(0, 12)}…` : 'not confirmed by the relay'}`)));
    li.querySelector('[data-job-audit-note]').textContent = v.audit_note || '';
    const result = li.querySelector('[data-job-result]');
    result.replaceChildren();
    if (v.result_full) {
      const a = el('a', { href: `${page.urls.api}${base}/${encodeURIComponent(v.id)}/result`, download: '', 'data-job-open-result': '' }, 'Open full result');
      result.append(a, ` (${Number(v.result_chars).toLocaleString()} characters)`);
    }
    result.hidden = !v.result_full;
  }

  function elapsedNow(entry) {
    const v = entry.view;
    return v.active ? v.elapsed_s + Math.floor((performance.now() - entry.perf) / 1000) : v.elapsed_s;
  }

  function tick() {
    for (const entry of cards.values()) {
      const e = entry.li.querySelector('[data-job-elapsed]');
      if (e) e.textContent = `${elapsedNow(entry)}s`;
    }
  }

  async function addResultNote(v) {
    if (!v.result_note_id) return;
    const r = await apiGet(`${base}/${encodeURIComponent(v.id)}/note`);
    if (!r.ok || !r.body || !r.body.note) return;
    if (thread.querySelector(`[data-note="${r.body.note.id}"]`)) return;
    const card = cards.get(v.id);
    const li = noteLine(r.body.note);
    if (card) card.li.after(li); else thread.append(li);
    follow();
  }

  function upsert(v, { fresh = false } = {}) {
    let entry = cards.get(v.id);
    const before = entry ? entry.view.state : null;
    if (!entry) {
      const existing = thread.querySelector(`[data-job="${v.id}"]`);
      entry = { li: existing || build(v), view: v, perf: performance.now() };
      cards.set(v.id, entry);
      if (!existing) place(entry.li, v.created_at);
    }
    entry.view = v;
    entry.perf = performance.now();
    paint(entry.li, v);
    tick();
    if (before && before !== v.state) {
      announce(`${LABELS[v.state] || 'Job'}: ${v.title}`);
      if (v.state === 'completed') addResultNote(v);
    } else if (!before && v.state === 'completed' && !fresh) {
      // a job that finished while this page was closed: its result note is already in the server-rendered thread
    }
    return entry;
  }

  async function refresh() {
    const r = await apiGet(base);
    if (r.ok && r.body && Array.isArray(r.body.jobs)) {
      for (const v of r.body.jobs.slice().reverse()) upsert(v);
      follow();
    }
    schedule();
  }

  function schedule() {
    window.clearTimeout(timer);
    timer = null;
    const active = Array.from(cards.values()).some((c) => c.view.active);
    if (active) timer = window.setTimeout(refresh, POLL_MS);
    if (active && !ticker) ticker = window.setInterval(tick, 1000);
    if (!active && ticker) { window.clearInterval(ticker); ticker = null; }
  }

  async function stop(id, li) {
    const btn = li.querySelector('[data-job-stop]');
    btn.disabled = true;
    const r = await apiStop(`${base}/${encodeURIComponent(id)}/stop`);
    if (r.ok && r.body && r.body.job) {
      upsert(r.body.job);
      toast(r.body.message || 'Stop requested');
    } else {
      btn.disabled = false;
      toast((r.body && r.body.message) || 'Could not stop it. Nothing was changed.');
    }
    schedule();
  }

  // The server answered a spoken command ("run this as a job", "stop") or a start with a job.
  function accepted(data) {
    if (!data || !data.job) return;
    upsert(data.job, { fresh: true });
    announce(data.message || '');
    follow();
    schedule();
  }

  refresh();
  return { accepted, refresh };
}
