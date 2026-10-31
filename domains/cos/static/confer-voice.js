// Chief of Staff's voice on Confer, on the direct path (Spec 165 v0.3 §6.6): the page's side of the shared contract.
//
// cos_ui.html loads this file only when her capabilities read (…/api/realtime-voice/direct/api/realtime-voice/capabilities)
// says the direct path is installed: direct_voice (she is admitted: one Voice button, the live bar, the provider choice)
// or direct_records (sessions that need his attention, also after admission is turned off, Spec 165 §11.1 S3). Without
// either, the file is never asked for and Confer is as it was.
//
// The session itself (the bridge to her typed Confer turn, capture, End, the summary) is the shared session module,
// core/realtime_voice/static/direct-voice-session.js (Spec 165 §4.3), which the page hands in as `session`; this file adds
// nothing to its rules. It draws what the module reports: while live, only that voice is on, the time so far, End and
// Check with CoS (and Sum up and Keep talking when offered), never what is said, except the words Check sends (review
// B N8). End reveals the session's kept records from her store (§6.5, the session detail), in Confer's own message style;
// sessions needing attention are listed below the composer with their line, their summary card and Download transcript.
//
// No imports: the page hands in the document, its nodes, the session module, the fetch, the headers and the timers, so the
// node tests drive this file with a small fake DOM (tests/confer_voice_direct.mjs).

export const CONFIRM_SCOPE = 'Confirming confirms the summary only. Anything it mentions still needs its own request.';
export const NOT_ACTED = 'Not acted on: say it again if you still want it';
// A turn whose reply the End cut off (Gate 1 n3's closed; Gate 2 Q N12): he ended it, so it is not "not acted on".
export const CUT_OFF = 'Cut off at End';
// Why a turn of his was not acted on, as End kept it (Gate 1 n2, n3): said beside NOT_ACTED, so the reveal says which.
export const NOT_ACTED_WHY = {
  overtaken: 'overtaken by what you said next',
  cut_short: 'its reply was cut short',
  never_started: 'no reply came for it',
  closed: 'the voice ended before it was answered',
};
export const RECOVERING = 'Voice session ended unexpectedly; recovering what was said.';
export const DROPPED = 'This voice session ended without closing; some of what was said may not have been kept.';
export const KEPT = 'What was said is kept.';
export const DAMAGED = 'Voice records damaged. Nothing was revealed.';
export const ASK_SUMMARY = 'Ask for a summary';
export const DOWNLOAD = 'Download transcript';
export const ATTENTION_TITLE = 'Voice sessions to look at';
export const NO_VOICE_CHOSEN = 'no voice chosen yet';
export const START_FAILED = 'Voice unavailable: the voice page could not load. Typing still works.';
// A check still running when the voice ended (Gate 2 E S2; Spec 165 §6.5's pending consults): the reveal says so, and the
// session is listed under ATTENTION_TITLE while it runs and once its result has landed, until he dismisses it.
export const STILL_RUNNING = 'still running';
export const PENDING_LINE = 'A check was still running when the voice ended. Its result is kept when it lands; reload to see it under Voice sessions to look at.';
export function lateLabel(view) {             // §5.2's words when a summary was made; otherwise what is true
  return ['proposed', 'confirmed', 'edited', 'declined', 'unavailable'].includes(view?.summary)
    ? 'arrived after the summary' : 'arrived after the voice ended';
}
const LATE_MARK = '[Arrived after the summary] ';
export function incompleteLine(n) {
  return Number.isInteger(n) && n > 0
    ? `Some of what was said in this voice session was not kept (${n} turn${n === 1 ? '' : 's'}). What was kept is shown.`
    : 'Some of what was said in this voice session was not kept. What was kept is shown.';
}
// What each reason a session needs his attention (the core's session view, §6.5) says in the list.
const REASONS = {
  damaged: () => 'Voice records damaged.',
  recovering: () => RECOVERING,
  dropped: () => DROPPED,
  incomplete: (v) => incompleteLine(v.missing),
  undecided: () => 'Its summary waits for your decision.',
  summary_unavailable: () => 'Its summary is unavailable.',
  unknown_consult: (v) => {
    const n = Number(v.consults?.unknown) || 1;
    return `${n === 1 ? 'A check\'s result is' : `${n} checks' results are`} not known: check in writing before asking again.`;
  },
  pending_consult: (v) => {
    const n = Number(v.consults?.pending) || 1;
    return `${n === 1 ? 'A check was' : `${n} checks were`} still running when the voice ended; ${n === 1 ? 'its result is' : 'their results are'} kept when ${n === 1 ? 'it lands' : 'they land'}.`;
  },
  late_result: () => 'A check finished after the voice ended: open to see its result.',
};
const PHASES = { connecting: 'Connecting…', listening: 'Listening', working: 'Working on it…', speaking: 'Speaking…' };
const PROVIDER_KEY = 'minimoi.cos.voice.provider';     // her own key: MC's choice (minimoi.voice.provider) is not hers
const READ_WAIT_MS = 10000;                             // a records read; past it nothing is shown, and nothing is claimed
const DECISION_WAIT_MS = 15000;                         // a decision is one store write
const ASK_WAIT_MS = 45000;                              // an ask may join an attempt the server bounds itself
const STATUS_CLEAR_MS = 5000;                           // a passing line clears after about five seconds

// The capabilities answer's URLs are her server's own paths; through the portal the page lives under /app/cos. Each is
// rebased on the page's own place (`base`: "" directly, "/app/cos" through the portal), as Confer's downloads are.
export function rebase(direct, base) {
  const out = { ...direct };
  const prefix = String(base || '').replace(/\/+$/, '');
  for (const [k, v] of Object.entries(direct || {})) {
    if (k.endsWith('_url') && typeof v === 'string' && v.startsWith('/') && !v.startsWith('//')
        && !(prefix && (v === prefix || v.startsWith(prefix + '/')))) out[k] = prefix + v;
  }
  return out;
}

function maker(doc) {
  return (tag, attrs = {}, text) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  };
}
const clock = (iso) => {
  const t = new Date(iso || '');
  if (!Number.isFinite(t.getTime())) return '';
  const two = (n) => String(n).padStart(2, '0');
  return `${t.getFullYear()}-${two(t.getMonth() + 1)}-${two(t.getDate())} ${two(t.getHours())}:${two(t.getMinutes())}`;
};
const at = (template, sid) => String(template || '').split('{session_id}').join(encodeURIComponent(sid || ''));

// The reveal and the needs-attention list (§6.5, §6.6): a session's kept records in Confer's message style, its capture
// said honestly (kept, incomplete with its count, unconfirmed: never complete when End never arrived), his turns not acted
// on marked with why, the summary card (Confirm, Edit, Decline; Ask for a summary when it was unavailable) and Download
// transcript. Records are shown as kept: nothing is merged or deduplicated (live probe LV6: on Grok an overtaken turn's
// words can repeat at the start of the next).
export function conferSessions(d) {
  const { doc, direct } = d;
  const make = maker(doc);
  const short = direct.person?.short || 'CoS';
  const shown = new Set();                               // sessions revealed in this page's conversation: not listed again
  const get = (url) => d.get(url, { timeoutMs: READ_WAIT_MS });
  const post = (url, body, ms) => d.post(url, body, { timeoutMs: ms });

  async function detail(sid) {
    if (!direct.session_url || !sid) return null;
    const r = await get(`${at(direct.session_url, sid)}?conversation_id=owner`);
    return r.status === 200 && r.body && r.body.ok ? r.body : null;
  }
  function captureLine(view) {
    if (view.damaged) return DAMAGED;
    if (view.state === 'recovering') return RECOVERING;
    if (view.capture === 'unconfirmed' || view.state === 'dropped') return DROPPED;
    if (view.capture === 'incomplete') return incompleteLine(view.missing);
    return view.complete_read === false ? 'Not every record could be read. What was read is shown.' : KEPT;
  }
  function row(rec, notActed, view = {}) {
    const role = rec.role;
    const owner = typeof rec.item_id === 'string' && rec.item_id.startsWith('k') ? rec.item_id.slice(1) : null;
    const pending = role === 'consult' && owner && Array.isArray(view.pending) && view.pending.includes(owner);
    const late = role === 'receipt' && owner && Array.isArray(view.late) && view.late.includes(owner);
    const kind = role === 'user' ? 'msg-user' : role === 'assistant' ? 'msg-cos' : 'msg-voice-note';
    const box = make('div', { class: `msg msg-voice ${kind}`, ...(rec.item_id ? { 'data-voice-item': rec.item_id } : {}) });
    const label = make('div', { class: 'msg-label' });
    const name = role === 'user' ? 'You · spoken' : role === 'assistant' ? `${short} · spoken`
      : role === 'consult' ? `Asked ${short}` : role === 'receipt' ? 'Result' : (rec.speaker || 'Voice');
    label.append(name + (rec.interrupted ? ' · interrupted' : rec.partial ? ' · partial' : '')
      + (pending ? ` · ${STILL_RUNNING}` : '') + (late ? ` · ${lateLabel(view)}` : ''));
    if (role === 'user' && rec.item_id && Object.hasOwn(notActed, rec.item_id) && notActed[rec.item_id] === 'closed') {
      label.append(' · ', make('span', { 'data-voice-cut-off': '' }, CUT_OFF));
    } else if (role === 'user' && rec.item_id && Object.hasOwn(notActed, rec.item_id)) {
      label.append(' · ', make('span', { 'data-voice-not-acted': '' }, NOT_ACTED));
      const why = NOT_ACTED_WHY[notActed[rec.item_id]];
      if (why) label.append(' · ', make('span', { 'data-voice-not-acted-why': '' }, why));
    }
    const text = late && String(rec.text || '').startsWith(LATE_MARK) ? String(rec.text).slice(LATE_MARK.length) : rec.text;
    box.append(label, make('div', { class: 'msg-text' }, text));
    return box;
  }
  function download(view) {
    if (!direct.session_transcript_url || view.damaged || view.state === 'live') return null;
    const partial = view.capture !== 'complete' || view.complete_read === false;
    const link = make('a', { class: 'voice-download', 'data-voice-download': '', download: '',
      href: `${at(direct.session_transcript_url, view.session_id)}?conversation_id=owner` }, DOWNLOAD + (partial ? ' (partial)' : ''));
    return link;
  }

  // The summary card on a waiting proposal (Confirm, Edit, Decline), from any device, however much later; the first
  // decision stands. Its words say what the server did, never more.
  function card(view, refreshAfter) {
    const box = make('div', { class: 'voice-card', 'data-voice-summary-card': '', role: 'group', 'aria-label': 'Voice summary proposal' });
    const text = String(view.proposal || '');
    const cut = text.indexOf('\n\n');
    const head = cut >= 0 ? text.slice(0, cut) : '';
    const body = cut >= 0 ? text.slice(cut + 2) : text;
    box.append(make('div', { class: 'msg-label' }, `${short} · summary proposal`));
    if (head) box.append(make('div', { class: 'voice-card-head' }, head));
    box.append(make('div', { class: 'msg-text' }, body));
    const actions = make('div', { class: 'voice-actions' });
    const buttons = ['Confirm', 'Edit', 'Decline'].map((label) => make('button', { type: 'button', class: 'voice-act', [`data-voice-summary-${label.toLowerCase()}`]: '' }, label));
    actions.append(...buttons);
    const scope = make('div', { class: 'voice-card-scope' }, CONFIRM_SCOPE);
    const result = make('div', { class: 'voice-card-result', role: 'status', 'data-voice-summary-result': '' });
    result.hidden = true;
    box.append(actions, scope, result);
    const [confirm, edit, decline] = buttons;
    const busy = (on) => { for (const b of buttons) b.disabled = on; };
    const tell = (words) => { result.textContent = words; result.hidden = !words; };
    async function decide(decision, words) {
      busy(true); tell('');
      const r = await post(at(direct.summary_decision_url, view.session_id),
        { conversation_id: 'owner', decision, ...(decision === 'edit' ? { text: words } : {}) }, DECISION_WAIT_MS);
      const b = r.body || {};
      if (r.status === 200 && b.ok) {
        actions.remove(); box.querySelector('[data-voice-summary-editor]')?.remove();
        tell({ confirmed: 'Confirmed. It confirms the summary only; it authorizes nothing it mentions.',
          edited: 'Your wording replaces the proposal. It confirms the summary only; it authorizes nothing it mentions.',
          declined: 'Declined. The proposal is not your record of the session.' }[b.state] || 'Kept.');
        await refreshAfter?.();
        return true;
      }
      if (r.status === 409 && b.error === 'already_decided') {
        actions.remove();
        tell(`Already decided${b.state ? ` (${b.state})` : ''}. Nothing was changed.`);
        await refreshAfter?.();
        return false;
      }
      busy(false);
      tell(b.message || (r.status === 0 ? 'The server did not answer. The decision may have been kept; reload to see it.'
        : 'That was not kept. Nothing was changed.'));
      return false;
    }
    confirm.addEventListener('click', () => decide('confirm'));
    decline.addEventListener('click', () => decide('decline'));
    edit.addEventListener('click', () => {
      if (box.querySelector('[data-voice-summary-editor]')) return;
      const form = make('div', { class: 'voice-edit', 'data-voice-summary-editor': '' });
      const area = make('textarea', { rows: '5', maxlength: '1860', 'aria-label': 'Your wording of the summary' });
      area.value = body;
      const save = make('button', { type: 'button', class: 'voice-act', 'data-voice-summary-save': '' }, 'Save');
      const back = make('button', { type: 'button', class: 'voice-act', 'data-voice-summary-cancel': '' }, 'Cancel');
      const row2 = make('div', { class: 'voice-actions' });
      row2.append(save, back);
      form.append(area, row2);
      box.insertBefore(form, scope);
      actions.hidden = true;
      save.addEventListener('click', async () => {
        const words = String(area.value || '').trim();
        if (!words) { tell('Write the summary, or Cancel.'); return; }
        if (words.length > 1860) { tell('At most 1860 characters.'); return; }
        save.disabled = true;
        if (!(await decide('edit', words))) { save.disabled = false; actions.hidden = Boolean(box.querySelector('[data-voice-summary-editor]')); }
      });
      back.addEventListener('click', () => { form.remove(); actions.hidden = false; tell(''); });
      area.focus?.();
    });
    return box;
  }
  // "Summary unavailable" may be asked again later (the route allows it after End): one quiet button. A proposal that
  // comes back replaces the session's block with its card.
  function ask(view, again) {
    const box = make('div', { class: 'voice-actions', 'data-voice-summary-ask': '' });
    const button = make('button', { type: 'button', class: 'voice-act', 'data-voice-summary-retry': '' }, ASK_SUMMARY);
    const result = make('span', { class: 'voice-card-result', role: 'status', 'data-voice-summary-result': '' });
    result.hidden = true;
    box.append(button, result);
    const tell = (words) => { result.textContent = words; result.hidden = !words; };
    button.addEventListener('click', async () => {
      button.disabled = true; tell('Asking…');
      const r = await post(at(direct.summary_url, view.session_id), { conversation_id: 'owner' }, ASK_WAIT_MS);
      const b = r.body || {};
      if (r.status === 200 && b.ok) { await again(); return; }
      button.disabled = false;
      tell(r.status === 200 && b.state === 'unavailable' ? 'Still unavailable. You can ask again later.'
        : r.status === 200 && b.state === 'pending' ? 'The summary is still being written; it will appear here.'
        : b.message || (r.status === 0 ? 'The server did not answer. The summary may still appear here.'
          : 'The summary could not be asked for. Nothing was changed.'));
    });
    return box;
  }
  // `listed`: the block is opened in the needs-attention list. A decision there keeps its row and its result line in view
  // (re-reading the list now would take the row, and the words saying what was kept, away at once); the list is read
  // again at the next load or reveal. In the conversation, a decision re-reads the list at once.
  function summary(view, again, listed) {
    if (view.summary === 'proposed' && view.proposal) return card(view, listed ? null : () => refresh());
    if (view.summary === 'unavailable' && ['ended', 'dropped'].includes(view.state)) {
      const box = make('div', { class: 'voice-summary-state' });
      box.append(make('div', { class: 'voice-line' }, 'Summary unavailable.'), ask(view, again));
      return box;
    }
    const decided = { confirmed: 'Summary confirmed.', edited: 'Summary kept in your wording.', declined: 'Summary declined.' }[view.summary];
    return decided ? make('div', { class: 'voice-line' }, decided) : null;
  }

  // One session's block: what the reveal adds to the conversation, and what Open shows in the list.
  function render(view, { listed = false } = {}) {
    const block = make('div', { class: 'voice-session', 'data-voice-session': view.session_id });
    const when = [clock(view.started_at), view.ended_at ? clock(view.ended_at).slice(11) : ''].filter(Boolean).join('–');
    block.append(make('div', { class: 'msg-label' }, `Voice with ${short}${when ? ` · ${when}` : ''}`));
    block.append(make('div', { class: 'voice-line', 'data-voice-capture': view.capture || '' }, captureLine(view)));
    const notActed = view.not_acted && typeof view.not_acted === 'object' ? view.not_acted : {};
    const records = make('div', { class: 'voice-records' });
    for (const rec of Array.isArray(view.records) ? view.records : []) records.append(row(rec, notActed, view));
    block.append(records);
    if (Array.isArray(view.pending) && view.pending.length) block.append(make('div', { class: 'voice-line', 'data-voice-pending': '' }, PENDING_LINE));
    const again = async () => {
      const fresh = await detail(view.session_id);
      if (!fresh) return;
      const next = render(fresh, { listed });
      if (typeof block.replaceWith === 'function') block.replaceWith(next); else swap(block, next);
    };
    const s = summary(view, again, listed);
    if (s) block.append(s);
    const link = download(view);
    if (link) { const p = make('div', { class: 'voice-actions' }); p.append(link); block.append(p); }
    return block;
  }
  function swap(old, fresh) {
    const parent = old.parent || old.parentNode;
    if (!parent) return;
    parent.insertBefore(fresh, old);
    old.remove();
  }

  // The reveal at this page's own End: the session's records, from her store, at the end of the conversation.
  async function reveal(sid) {
    const view = await detail(sid);
    if (!view || view.state === 'live') return false;
    shown.add(sid);
    d.chat.append(render(view));
    if (typeof d.chat.scrollHeight === 'number') d.chat.scrollTop = d.chat.scrollHeight;
    await refresh();
    return true;
  }

  // The needs-attention list (§6.5): each session's lines, Open (its block, here), Dismiss. Read at load when the
  // capabilities say there is something, after each reveal and after each decision; never on a timer.
  const list = d.list;
  async function refresh() {
    if (!list || !direct.sessions_url) return;
    const r = await get(direct.sessions_url);
    if (r.status !== 200 || !Array.isArray(r.body?.sessions)) return;
    paint(r.body.sessions.filter((v) => v && typeof v.session_id === 'string' && !shown.has(v.session_id)));
  }
  function paint(sessions) {
    list.textContent = '';
    list.hidden = !sessions.length;
    if (!sessions.length) return;
    list.append(make('div', { class: 'voice-sessions-title' }, ATTENTION_TITLE));
    for (const view of sessions) {
      const item = make('div', { class: 'voice-attention', 'data-voice-attention': view.session_id });
      const head = make('div', { class: 'voice-attention-head' });
      head.append(make('span', { class: 'voice-attention-when' }, clock(view.started_at) || 'Voice session'));
      for (const why of Array.isArray(view.reasons) ? view.reasons : []) {
        const say = REASONS[why];
        if (say) head.append(make('span', { class: 'voice-line', 'data-voice-reason': why }, say(view)));
      }
      const actions = make('div', { class: 'voice-actions' });
      const open = make('button', { type: 'button', class: 'voice-act', 'data-voice-open': '', 'aria-expanded': 'false' }, 'Open');
      const dismiss = make('button', { type: 'button', class: 'voice-act', 'data-voice-dismiss': '' }, 'Dismiss');
      const result = make('span', { class: 'voice-card-result', role: 'status', 'data-voice-attention-result': '' });
      result.hidden = true;
      if (view.damaged) open.disabled = true;
      actions.append(open, dismiss, result);
      item.append(head, actions);
      open.addEventListener('click', async () => {
        const opened = item.querySelector('[data-voice-session]');
        if (opened) { opened.remove(); open.setAttribute('aria-expanded', 'false'); open.textContent = 'Open'; return; }
        open.disabled = true;
        const fresh = await detail(view.session_id);
        open.disabled = false;
        if (!fresh || fresh.state === 'live') { result.textContent = 'Its records could not be read now.'; result.hidden = false; return; }
        item.append(render(fresh, { listed: true })); open.setAttribute('aria-expanded', 'true'); open.textContent = 'Close';
      });
      dismiss.addEventListener('click', async () => {
        dismiss.disabled = true;
        const r = await post(at(direct.session_dismiss_url, view.session_id), { conversation_id: 'owner' }, DECISION_WAIT_MS);
        if (r.status === 200 && r.body?.ok) { item.remove(); if (!list.querySelector('[data-voice-attention]')) { list.textContent = ''; list.hidden = true; } return; }
        dismiss.disabled = false;
        result.textContent = r.body?.message || (r.status === 0 ? 'The server did not answer. Reload to see whether it was kept.'
          : 'That was not kept. Nothing was changed.');
        result.hidden = false;
      });
      list.append(item);
    }
  }
  return { reveal, refresh, render, detail };
}

// The live session on Confer's one Voice button (D8 (a)): no mode select, no "Hearing: …". `els`: the page's Voice button,
// status and line (the quiet lines under the composer), the provider slot in its meta row, the live bar's place, the
// conversation and the needs-attention list. `beforeStart`: Dictate yields the microphone first, as today.
export function conferVoice(d) {
  const { doc, els, direct, session: S } = d;
  const make = maker(doc);
  const person = direct.person || { name: 'Chief of Staff', short: 'CoS' };
  const short = person.short || 'CoS';
  const later = d.setTimeout || ((fn, ms) => setTimeout(fn, ms));
  const cancel = d.clearTimeout || ((t) => clearTimeout(t));
  const records = conferSessions({ doc, direct, chat: els.chat, list: els.sessions, get: d.get, post: d.post });
  let s = null, starting = false, live = false, ending = false, released = Promise.resolve(), release = () => {};
  let clearing = null, withdrawn = false;           // withdrawn: stopped while Dictate was still yielding, before a session
  // The status line under the composer is shared with Dictate (Gate 2 Q S4): voice clears only what it wrote itself, so
  // its late End never wipes Dictate's line after the handover.
  let wrote = null;
  const status = (text) => { els.status.textContent = text; wrote = text; };
  const unstatus = () => { if (wrote !== null && els.status.textContent === wrote) els.status.textContent = ''; wrote = null; };

  // ── the provider choice, in the meta row: remembered under her own key ──
  const unvoiced = direct.unvoiced && typeof direct.unvoiced === 'object' ? direct.unvoiced : {};
  const select = make('select', { class: 'voice-provider-choice', 'data-voice-provider': '', 'aria-label': 'Voice provider' });
  const offered = (Array.isArray(direct.providers) ? direct.providers : []).filter((p) => p && typeof p.provider === 'string');
  for (const p of offered) {
    const name = p.label || p.provider;
    const refused = typeof unvoiced[p.provider] === 'string' ? unvoiced[p.provider] : null;
    const option = make('option', { value: p.provider, ...(refused ? { title: refused } : {}) },
      refused ? `${name} · ${NO_VOICE_CHOSEN}` : name);      // v3: both providers confirm by voice; none is "Check only"
    option.disabled = Boolean(refused);
    select.append(option);
  }
  const usable = offered.filter((p) => typeof unvoiced[p.provider] !== 'string').map((p) => p.provider);
  let saved = null;
  try { saved = d.storage?.getItem(PROVIDER_KEY) ?? null; } catch (e) { /* the choice is simply not remembered */ }
  select.value = [saved, direct.default_provider].find((p) => usable.includes(p)) || usable[0] || offered[0]?.provider || '';
  select.addEventListener('change', () => { try { d.storage?.setItem(PROVIDER_KEY, select.value); } catch (e) { /* not remembered */ } });

  // ── the live bar: voice on, elapsed, Check with CoS, End; Sum up and Keep talking when offered; one notice line ──
  const bar = els.live;
  bar.textContent = '';
  const on = make('span', { class: 'voice-on' });
  on.append(make('span', { class: 'voice-dot', 'aria-hidden': 'true' }), 'Voice on');
  const elapsed = make('span', { class: 'voice-elapsed', 'data-voice-elapsed': '', 'aria-hidden': 'true' }, '0s');
  const check = make('button', { type: 'button', class: 'voice-act', 'data-voice-check': '', title: `Ask ${person.name} about your last words` }, `Check with ${short}`);
  const end = make('button', { type: 'button', class: 'voice-act', 'data-voice-end': '' }, 'End');
  const propose = make('span', { class: 'voice-propose', 'data-voice-propose': '' });
  const sumUp = make('button', { type: 'button', class: 'voice-act', 'data-voice-sumup': '' }, 'Sum up');
  const keep = make('button', { type: 'button', class: 'voice-act', 'data-voice-keep': '' }, 'Keep talking');
  propose.append('Shall I sum up? ', sumUp, ' ', keep);
  propose.hidden = true;
  const checked = make('span', { class: 'voice-checked', 'data-voice-checked': '', 'aria-live': 'polite' });
  checked.hidden = true;
  const notice = make('span', { class: 'voice-notice', 'data-voice-notice': '' });
  notice.hidden = true;
  bar.append(on, elapsed, check, end, propose, checked, notice);
  bar.hidden = true;

  function say(text, { error = false, transient = false } = {}) {
    if (clearing) { cancel(clearing); clearing = null; }
    els.line.textContent = text || '';
    els.line.classList?.toggle('voice-error', Boolean(error && text));
    if (transient && text) clearing = later(() => { clearing = null; if (els.line.textContent === text) els.line.textContent = ''; }, STATUS_CLEAR_MS);
  }
  function paint() {
    const active = starting || live || Boolean(s);
    const b = els.button;
    b.textContent = active ? '■' : '🎤';
    b.title = active ? 'End voice conversation' : 'Start voice conversation';
    b.setAttribute('aria-label', b.title);
    b.setAttribute('aria-pressed', String(active));
    b.classList?.toggle('recording', active);
    b.disabled = ending;
    select.disabled = active || ending || !usable.length;
  }
  const ui = {
    say: (text, o) => say(text, o),
    phase: (state) => { status(PHASES[state] || ''); },
    live: (yes) => {
      live = Boolean(yes);
      bar.hidden = !live;
      if (!live) { propose.hidden = true; checked.hidden = true; checked.textContent = ''; }
      paint();
    },
    elapsed: (text) => { elapsed.textContent = text; },
    notice: (text) => { notice.textContent = text || ''; notice.hidden = !text; },
    propose: (yes) => { propose.hidden = !yes; },
    // Check shows the words it sends (review B N8): as it sends them, then with the answer, until the next Check or End.
    checking: (yes, words) => {
      check.disabled = Boolean(yes);
      if (typeof words === 'string' && words) {
        checked.textContent = `${yes ? 'Checking' : 'Checked'} “${words}”${yes ? '…' : ''}`;
        checked.hidden = false;
      }
    },
    // The microphone and the transport are released once the session reaches ended (the controller tore them down first).
    lifecycle: (state) => { if (state === 'ended') { live = false; release(); } },
    // The session ended by itself or by End: the button is ready again; its words say what is true.
    ended: (words, { error = false } = {}) => {
      s = null; live = false; ending = false; starting = false;
      bar.hidden = true; propose.hidden = true; checked.hidden = true; checked.textContent = '';
      unstatus();
      release();
      paint();
      if (words) say(words, { error, transient: !error });
    },
  };

  async function start() {
    if (s || starting || ending) return;
    const provider = select.value;
    say('');
    if (!provider || !usable.includes(provider)) {
      say(unvoiced[provider] || 'Voice unavailable here right now. Typing still works.', { error: true });
      return;
    }
    starting = true; withdrawn = false; paint();
    try { await d.beforeStart?.(); } catch (e) { /* Dictate stopping never blocks voice */ }
    if (withdrawn) { starting = false; withdrawn = false; paint(); return; }      // stopped meanwhile: nothing starts
    released = new Promise((resolve) => { release = resolve; });
    const mine = S.directVoice({
      page: { conversation: { id: 'owner' } }, direct, ui, headers: d.headers, fetch: d.fetch,
      reveal: () => records.reveal(mine.sessionId()), loadController: d.loadController, warn: d.warn,
      setTimeout: later, clearTimeout: cancel, setInterval: d.setInterval, clearInterval: d.clearInterval,
    });
    s = mine; paint();
    try {
      await mine.start(provider);
    } catch (e) {
      if (s === mine) { s = null; release(); say(START_FAILED, { error: true }); }
    } finally { starting = false; paint(); }
  }
  // Ends the session for any reason (End, the Voice button, Dictate taking the microphone, the companion panel, the page
  // leaving). Resolves once the microphone and transport are released (the session reached ended), or once End is sent
  // and the reveal shown, whichever comes first; a session still connecting is ended once it connects (the session
  // module's own bound, 15 s), so a handover waits for that too.
  function stop(reason = 'user_ended') {
    const mine = s;
    if (!mine) {
      if (starting) withdrawn = true;                      // not yet a session: it never starts
      return released;
    }
    if (reason !== 'pagehide') { ending = true; status('Ending…'); paint(); }
    return Promise.race([released, mine.end(reason)]);
  }

  check.addEventListener('click', () => s?.check());
  end.addEventListener('click', () => { if (s?.proposing()) s.sumUp(); else stop('user_ended'); });
  sumUp.addEventListener('click', () => s?.sumUp());
  keep.addEventListener('click', () => s?.keepTalking());

  // Show this path: the provider choice in the meta row, the button painted, the needs-attention list read once.
  function show() {
    els.provider.textContent = '';
    els.provider.append(select);
    paint();
  }
  return {
    toggle: () => (s || starting ? stop('user_ended') : start()),
    start, stop,
    // The page is going away: the session module sends its one keepalive End in this same tick, and the session is over
    // here (a page restored from the back-forward cache shows Voice ready, not a session that has already ended).
    pagehide: () => {
      const mine = s;
      if (!mine) return;
      mine.end('pagehide');
      if (s === mine) { s = null; live = false; ending = false; starting = false; bar.hidden = true; unstatus(); release(); paint(); }
    },
    busy: () => Boolean(s || starting || ending),
    show, paint, records,
    provider: () => select.value,
  };
}
