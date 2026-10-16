// Requests MC put to Claude Code or Codex, in the conversation that asked for them (MC front door, slice 1).
//
// The server keeps what the agents report in a file ledger and turns each receipt ("picked up"), question and report into ONE ordinary note in this
// conversation (agent-authored, labelled, not a verification). The page only asks the server to keep the new ones and shows them. While a request
// is still open it asks every few seconds; when none is open it stops, so an idle page makes no calls. A request that is not picked up stays open
// and nothing is claimed about it. Nothing here runs unless the server says agent requests are on (page.agent_requests.enabled).

import { announce, el } from './dom.js';
import { apiPost } from './api.js';
import { live, onChange } from './state.js';

const POLL_MS = 5000;
const TIMEOUT_MS = 20000;
const RETRY_MS = [3000, 6000, 12000, 30000, 60000];     // after a failed look: bounded backoff, then it rests until the page is shown again, a reply arrives or it is reloaded

export function initAgentRequests({ page, thread, noteLine, follow }) {
  const ar = page && page.agent_requests;
  if (!ar || !ar.enabled || !page.conversation || !thread) return { kick() {} };
  const path = `/conversations/${encodeURIComponent(page.conversation.id)}/agent-requests/sync`;
  let timer = null;
  let busy = false;
  let failures = 0;
  let status = null;
  const say = (text) => {                                      // a quiet local line, never saved and never a claim about an agent
    if (!text) { if (status) status.hidden = true; return; }
    if (!status) { status = el('p', { class: 'mc-blocker', 'data-ar-status': '', role: 'status' }); thread.after(status); }
    status.textContent = text;
    status.hidden = false;
  };

  const at = (li) => { const t = li.querySelector('[data-iso]'); const ms = t ? Date.parse(t.getAttribute('data-iso')) : NaN; return Number.isNaN(ms) ? null : ms; };
  function place(li, createdAt) {
    const ms = Date.parse(createdAt);
    const next = Number.isNaN(ms) ? null : Array.from(thread.children).find((c) => c !== li && at(c) !== null && at(c) > ms);
    if (next) thread.insertBefore(li, next); else thread.append(li);
  }

  // One guarded look: keep the receipts and reports that arrived while nobody was looking (the tab was closed, the host was asleep). It runs once when the page opens even when
  // nothing is outstanding (a request can have finished while the page was closed), polls only while something is still open, and retries a failed look on a bounded backoff.
  // A recoverable failure is one that can pass by itself: the network, a server restart or error, an answer that could not be read, a throttle. A refusal that will
  // not change by asking again (signed out, not allowed, Private, not knowing the mode) is not retried; Private and unknown mode resume when the mode changes.
  const usable = (b) => !!b && Array.isArray(b.notes) && Number.isFinite(b.outstanding) && b.outstanding >= 0
    && (b.unkept === undefined || (Number.isInteger(b.unkept) && b.unkept >= 0))
    && b.notes.every((n) => n && typeof n === 'object' && (Number.isInteger(n.id) || (typeof n.id === 'string' && n.id)) && typeof n.text === 'string' && typeof n.created_at === 'string');
  const recoverable = (r) => r.status === 0 || r.status >= 500 || r.status === 408 || r.status === 429 || (r.body && r.body.error === 'unreadable');
  async function sync() {
    if (busy) return;
    busy = true;
    window.clearTimeout(timer);
    timer = null;
    let outstanding = 0;
    let retry = false;
    const abort = new AbortController();                      // a look that never settles is cut off (the fetch itself), so one request is in flight at a time
    const cutoff = window.setTimeout(() => abort.abort(), TIMEOUT_MS);
    try {
      const r = await apiPost(path, {}, abort.signal);
      if (r.offline) {
        // Private, or this tab does not know its mode: nothing is sent. It looks again when the mode changes (below).
      } else if (r.ok && usable(r.body)) {
        outstanding = r.body.outstanding;
        let added = false;
        for (const note of r.body.notes) {
          if (thread.querySelector(`[data-note="${note.id}"]`)) continue;
          place(noteLine(note), note.created_at);
          announce(`${note.author_label}: ${String(note.text).split('\n')[0].slice(0, 120)}`);
          added = true;
        }
        if (added) follow();
        if (r.body.unkept > 0) {
          retry = true;
          say('An agent update is kept in the request ledger but could not yet be added here. Retrying.');
        } else {
          failures = 0;                                        // only a fully applied look starts the count again
          say('');
        }
      } else if (r.ok || recoverable(r)) {
        retry = true;                                          // a 200 that is not the expected answer (not JSON, wrong shape) is a failed look, not "nothing outstanding"
      }
    } catch (e) {
      retry = true;                                           // an unexpected payload or a rendering error: the lock below is still released
    } finally {
      window.clearTimeout(cutoff);
      busy = false;
    }
    if (retry) {
      if (failures < RETRY_MS.length) timer = window.setTimeout(sync, RETRY_MS[failures++]);
      else say('Could not refresh agent replies. Reconnect or reload to try again.');
    } else if (outstanding > 0 && !document.hidden) {
      timer = window.setTimeout(sync, POLL_MS);
    }
  }

  window.addEventListener('online', () => { if (!timer && !busy) { failures = 0; sync(); } });     // connectivity came back after the retries ran out
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !timer && !busy) { failures = 0; sync(); } });
  onChange(() => { if (!live.off && !busy) { failures = 0; sync(); } });     // back on the record: look now
  sync();                                                                      // always once at load, whatever the page believed was outstanding
  return { kick() { failures = 0; window.setTimeout(sync, 1200); } };         // a reply just arrived: it may have put a request to an agent
}
