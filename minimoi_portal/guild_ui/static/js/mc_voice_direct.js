// The Guild's thread for MC voice on the direct path (Spec 164 v0.3 §6–§7; UI_CONTRACT_chunk_B.md). mc_voice.js loads this file
// only when the capabilities answer carries direct_voice (admitted) or direct_records (installed, not admitted: Spec 165 §11.1
// S1, S3, where this thread alone is painted, so an earlier session's proposal can still be decided); without either the file
// is never asked for, the page does not list it, and the server does not serve it. The session itself (bridge, capture, End,
// summary) is the shared browser session module, core/realtime_voice/static/direct-voice-session.js (Spec 165 §4.3), which
// mc_voice.js loads beside it; with direct_records only its jsonPost is used.
//
// No imports: mc_voice.js hands in the document, the thread, the post (the session module's jsonPost) and the reveal, so the
// node tests drive this file with a small fake DOM.

export const LIVE_ELSEWHERE = 'Voice session in progress; what is said appears when it ends.';
export const RECOVERING = 'Voice session ended unexpectedly; recovering what was said.';
export const CONFIRM_SCOPE = 'Confirming confirms the summary only. Anything it mentions still needs its own request.';
export const ASK_SUMMARY = 'Ask for a summary';
export const EDIT_MAX = 1860;
// A session some of whose words are known not to be kept (review B4): said where it started, in every view, after a reload
// too, with the count when the server has one (Spec 165 §5.2). A session that ended without End (dropped) may not have all
// of it, and says so (review R2 N6).
export const INCOMPLETE_LINE = 'Some of what was said in this voice session was not kept. What was kept is in the conversation.';
// The thread's posts after the session are bounded (review L N7), each above the server's own: a decision is one store write;
// an ask for a summary may join an attempt the server bounds at 35 s. With no answer, the words say what may still be true.
const DECISION_WAIT_MS = 15000;
const ASK_WAIT_MS = 45000;
export const DECISION_NO_ANSWER = 'The server did not answer. The decision may have been kept; reload to see it.';
export const ASK_NO_ANSWER = 'The server did not answer. The summary may still appear here.';
export const DROPPED_LINE = 'This voice session ended without closing; some of what was said may not have been kept.';
export function incompleteLine(n) {
  return Number.isInteger(n) && n > 0
    ? `Some of what was said in this voice session was not kept (${n} turn${n === 1 ? '' : 's'}). What was kept is in the conversation.`
    : INCOMPLETE_LINE;
}

// The thread side of §6 and §7: one quiet line where a recovering or elsewhere-live session started, and the summary card
// (Confirm, Edit, Decline) on a waiting proposal, from any device, however much later.
export function voiceThread(d) {
  const { doc, direct } = d;
  const proposals = new Map();                       // session -> the proposal note's text, for Edit
  const make = (tag, attrs = {}, text) => {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  };
  const whenOf = (row) => Date.parse(row.querySelector?.('[data-iso]')?.getAttribute('data-iso') || row.getAttribute('data-at') || '');

  function place(li, startedAt) {
    const thread = d.thread();
    if (!thread) return;
    const at = Date.parse(startedAt || '');
    const next = Number.isFinite(at) ? Array.from(thread.querySelectorAll('[data-note]')).find((row) => !row.hidden && whenOf(row) > at) : null;
    thread.insertBefore(li, next || thread.querySelector('[data-live-turn]') || null);
  }
  function line(sid, text, startedAt) {
    const thread = d.thread();
    let li = thread?.querySelector(`[data-voice-line="${sid}"]`);
    if (!text) { li?.remove(); return; }
    if (!li) { li = make('li', { class: 'msg msg-voice-line', 'data-voice-line': sid }); place(li, startedAt); }
    li.textContent = text;
  }

  function card(sid) {
    const row = d.thread()?.querySelector(`[data-voice-kind="summary_proposal"][data-voice-session="${sid}"]`);
    if (!row || row.querySelector('[data-voice-summary-card]')) return;
    const box = make('div', { class: 'mc-voice-summary', 'data-voice-summary-card': '', role: 'group', 'aria-label': 'Voice summary proposal' });
    const actions = make('p', { class: 'mc-voice-summary-actions' });
    const buttons = ['Confirm', 'Edit', 'Decline'].map((label) => make('button', { type: 'button', class: 'btn-mini', [`data-voice-summary-${label.toLowerCase()}`]: '' }, label));
    actions.append(...buttons);
    const scope = make('p', { class: 'small mc-voice-summary-note' }, CONFIRM_SCOPE);
    const result = make('p', { class: 'small', role: 'status', 'data-voice-summary-result': '' });
    result.hidden = true;
    box.append(actions, scope, result);
    row.append(box);
    const [confirm, edit, decline] = buttons;
    const busy = (on) => { for (const b of buttons) b.disabled = on; };
    const tell = (text) => { result.textContent = text; result.hidden = !text; };

    async function decide(decision, text) {
      busy(true); tell('');
      const r = await d.post(direct.summary_decision_url.split('{session_id}').join(encodeURIComponent(sid)),
        { conversation_id: d.conversationId(), decision, ...(decision === 'edit' ? { text } : {}) }, { timeoutMs: DECISION_WAIT_MS });
      const body = r.body || {};
      if (r.status === 200 && body.ok) {
        actions.remove();
        const editor = box.querySelector('[data-voice-summary-editor]');
        editor?.remove();
        tell({ confirmed: 'Confirmed. It confirms the summary only; it authorizes nothing it mentions.',
          edited: 'Your wording replaces the proposal. It confirms the summary only; it authorizes nothing it mentions.',
          declined: 'Declined. The proposal is not your record of the session.' }[body.state] || 'Kept.');
        await d.reveal?.();
        return true;
      }
      if (r.status === 409 && body.error === 'already_decided') {
        actions.remove();
        tell(`Already decided${body.state ? ` (${body.state})` : ''}. Nothing was changed.`);
        await d.reveal?.();
        return false;
      }
      busy(false);
      tell(body.message || (r.status === 0 ? DECISION_NO_ANSWER : 'That was not kept. Nothing was changed.'));
      return false;
    }

    confirm.addEventListener('click', () => decide('confirm'));
    decline.addEventListener('click', () => decide('decline'));
    edit.addEventListener('click', () => {
      if (box.querySelector('[data-voice-summary-editor]')) return;
      const form = make('div', { class: 'mc-voice-summary-edit', 'data-voice-summary-editor': '' });
      const area = make('textarea', { class: 'mc-input', rows: '5', maxlength: String(EDIT_MAX), 'aria-label': 'Your wording of the summary' });
      const written = proposals.get(sid) || '';
      area.value = written.includes('\n\n') ? written.slice(written.indexOf('\n\n') + 2) : written;
      const save = make('button', { type: 'button', class: 'btn-mini', 'data-voice-summary-save': '' }, 'Save');
      const back = make('button', { type: 'button', class: 'btn-mini', 'data-voice-summary-cancel': '' }, 'Cancel');
      const row2 = make('p', { class: 'mc-voice-summary-actions' });
      row2.append(save, back);
      form.append(area, row2);
      box.insertBefore(form, scope);
      actions.hidden = true;
      save.addEventListener('click', async () => {
        const words = String(area.value || '').trim();
        if (!words) { tell('Write the summary, or Cancel.'); return; }
        if (words.length > EDIT_MAX) { tell(`At most ${EDIT_MAX} characters.`); return; }
        save.disabled = true;
        if (!(await decide('edit', words))) { save.disabled = false; actions.hidden = Boolean(box.querySelector('[data-voice-summary-editor]')); }
      });
      back.addEventListener('click', () => { form.remove(); actions.hidden = false; tell(''); });
      area.focus?.();
    });
  }

  // §7: "Summary unavailable" may be asked again later, from any device (the route allows it after End): one quiet button
  // on the session's summary-unavailable note (UI review #8). A proposal that comes back is shown as its card.
  function ask(sid) {
    const row = d.thread()?.querySelector(`[data-voice-kind="summary_unavailable"][data-voice-session="${sid}"]`);
    if (!row || row.querySelector('[data-voice-summary-ask]')) return;
    const box = make('p', { class: 'mc-voice-summary-actions', 'data-voice-summary-ask': '' });
    const button = make('button', { type: 'button', class: 'btn-mini', 'data-voice-summary-retry': '' }, ASK_SUMMARY);
    const result = make('span', { class: 'small', role: 'status', 'data-voice-summary-result': '' });
    result.hidden = true;
    box.append(button, result);
    row.append(box);
    const tell = (text) => { result.textContent = text; result.hidden = !text; };
    button.addEventListener('click', async () => {
      button.disabled = true; tell('Asking…');
      const r = await d.post(String(direct.summary_url).split('{session_id}').join(encodeURIComponent(sid)), { conversation_id: d.conversationId() },
        { timeoutMs: ASK_WAIT_MS });
      const body = r.body || {};
      if (r.status === 200 && body.ok) { box.remove(); await d.reveal?.(); return; }
      button.disabled = false;
      // Still being written (an attempt already running, which this ask joined): no second one starts (review CX V2-5).
      tell(r.status === 200 && body.state === 'unavailable' ? 'Still unavailable. You can ask again later.'
        : r.status === 200 && body.state === 'pending' ? 'The summary is still being written; it will appear here.'
        : body.message || (r.status === 0 ? ASK_NO_ANSWER : 'The summary could not be asked for. Nothing was changed.'));
    });
  }
  function unask(sid) {
    d.thread()?.querySelector(`[data-voice-kind="summary_unavailable"][data-voice-session="${sid}"]`)?.querySelector('[data-voice-summary-ask]')?.remove();
  }

  return {
    // Paint the sessions the notes answer named: `notes` (optional) supplies the proposal's words for Edit.
    paint(sessions, notes) {
      for (const n of Array.isArray(notes) ? notes : []) {
        if (n && n.voice && n.voice.kind === 'summary_proposal' && typeof n.text === 'string') proposals.set(n.voice.session_id, n.text);
      }
      for (const s of Array.isArray(sessions) ? sessions : []) {
        if (!s || typeof s.session_id !== 'string') continue;
        const text = s.state === 'live' ? (s.session_id === d.mine() ? null : LIVE_ELSEWHERE)
          : ['recovering', 'unknown'].includes(s.state) ? (s.line || RECOVERING)
          : s.capture === 'incomplete' ? incompleteLine(s.missing) : s.capture === 'unconfirmed' ? DROPPED_LINE : null;   // §5.2, G4
        line(s.session_id, text, s.started_at);
        if (s.summary === 'proposed') card(s.session_id);
        if (s.summary === 'unavailable' && ['ended', 'dropped'].includes(s.state)) ask(s.session_id); else unask(s.session_id);
      }
    },
  };
}
