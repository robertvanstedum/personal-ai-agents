// Initial task proposals in CoS chat, using the canonical owner API.
const BOX = null;
const CHAT = document.getElementById('chat-log');
const CHAT_BOX = CHAT ? h('section', { class: 'wc wc-chat', 'data-tp-chat': '', 'aria-label': 'Proposed work awaiting your decision' },
  h('h2', { 'data-tp-heading': '' }, 'Proposed work awaiting your decision'),
  h('button', { type: 'button', class: 'fl-btn', 'data-tp-refresh': '' }, 'Refresh'),
  h('p', { 'data-tp-say': '', role: 'status', hidden: true }),
  h('div', { 'data-tp-list': '' })) : null;
const BOXES = [BOX, CHAT_BOX].filter(Boolean);
let readVersion = 0;
let chatSignature = '';
const JUMP = CHAT ? h('button', { type: 'button', class: 'wc-jump', hidden: true }, 'Proposal updated ↓') : null;
if (JUMP) {
  CHAT.after(JUMP);
  JUMP.addEventListener('click', () => { CHAT.scrollTop = CHAT.scrollHeight; JUMP.hidden = true; });
}
const BASE = location.pathname.replace(/\/ui(\/.*)?$/, '').replace(/\/$/, '');
const API = BASE + '/task-proposals';
let busy = false;

function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
  return n;
}

function headers() {
  return { 'Content-Type': 'application/json', Accept: 'application/json', ...cosWriteHeaders() };
}

function say(text, warn, admission) {
  for (const box of BOXES) {
    const line = box.querySelector('[data-tp-say]');
    const scoped = box === CHAT_BOX && admission?.conversation_id && admission.conversation_id !== window.cosHistory?.conversationId ? '' : text;
    line.textContent = scoped || '';
    line.hidden = !scoped;
    line.classList.toggle('warn', !!warn);
  }
}

// Render recorded terms as text; never derive authority from model prose.
function card(a) {
  const p = a.proposed || {};
  const t = p.task;
  const article = h('article', { class: 'wc-card', 'data-tp-id': a.interpretation_id },
    h('h3', {}, t?.outcome || p.outcome || 'Proposed work'));
  if (!t || !a.interpretation_id || !a.proposal_digest) {
    article.append(h('p', {}, 'This proposal is incomplete. It cannot be confirmed here.'));
    return article;
  }
  // Plain words on the card; what is confirmed stays exactly what the proposal holds (Robert, 7 Nov).
  const who = (id) => ({ mc: 'Master Craftsman', cos: 'Chief of Staff', principal: 'You', owner: 'You', robert: 'You' })[String(id).toLowerCase()] || id;
  const fields = [
    ...(p.outcome && p.outcome !== t.outcome ? [['Request', p.outcome]] : []),
    ['Lead', who(t.lead)], ['Next step with', who(t.holder)], ['Done when', t.done_when],
    ['Return window', t.return_window == null ? 'Not set' : JSON.stringify(t.return_window)],
    ['Scope', p.scope], ['Goes to', (p.recipients || []).map(who).join(', ')],
    ['Continuing work', p.continuing ? 'Yes' : 'No']];
  for (const [label, value] of fields) if (value != null && value !== '')
    article.append(h('p', {}, h('strong', {}, label + ': '), String(value)));
  const sources = h('details', {}, h('summary', {}, 'Source words and references'));
  const seen = new Set([t.outcome, p.outcome].filter(Boolean));
  for (const source of a.sources || []) {
    sources.append(h('p', {}, !source.content ? 'Source text is not included in this recovery view.' :
      seen.has(source.content) ? 'Same wording as shown above.' : source.content));
    if (source.content) seen.add(source.content);
    if (source.source_event_id) sources.append(h('small', {}, source.source_event_id));
  }
  if (a.sources?.length) article.append(sources);
  if (a.finishing) article.append(h('p', {}, 'Your confirmation was recorded; click to finish the remaining steps.'));
  const actions = h('p', { class: 'wc-actions' });
  for (const decision of a.finishing ? ['confirm'] : ['confirm', 'decline']) {
    actions.append(h('button', { type: 'button', class: 'fl-btn', ['data-tp-' + decision]: a.interpretation_id,
      disabled: busy, onclick: async () => {
        if (busy) return;
        busy = true; ++readVersion; render.last?.forEach(b => { b.disabled = true; });
        say(decision === 'confirm' ? 'Confirming this proposal…' : 'Declining this proposal…', false, a);
        let answer;
        try {
          const r = await fetch(`${API}/${decision}`, { method: 'POST', credentials: 'same-origin', headers: headers(),
            body: JSON.stringify({ interpretation_id: a.interpretation_id, proposal_digest: a.proposal_digest }) });
          answer = await r.json().catch(() => null);
          if (!r.ok) answer = { failed: true, message: answer?.message || 'The decision is unconfirmed. Refresh to check.' };
        } catch (_) { answer = { failed: true, message: 'The portal could not be reached. Refresh to check before trying again.' }; }
        const expected = decision === 'confirm' ? 'confirmed' : 'declined';
        if (!answer || (!answer.failed && (answer.interpretation_id !== a.interpretation_id ||
            answer.state !== expected || (decision === 'confirm' && !answer.task_id))))
          answer = { failed: true, message: 'The decision is unconfirmed. Refresh to check before trying again.' };
        say(answer.failed ? answer.message : decision === 'confirm' ? 'Confirmed; the task exists. Work has not been confirmed as started.' : 'Proposal declined.', answer.failed, a);
        while (!(await load(false, true))) { /* Reconcile selection changes before unlocking. */ }
        busy = false;
        render.last?.forEach(b => { b.disabled = false; });
      } }, decision === 'confirm' ? a.finishing ? 'Finish confirmation' : 'Confirm this work' : 'Decline'));
  }
  article.append(actions);
  return article;
}

function render(list, problem) {
  render.last = [];
  for (const box of BOXES) {
    const inChat = box === CHAT_BOX;
    // In a chat, only the proposals that chat made. One with no conversation waits in Actions, never under a new chat.
    const visible = inChat ? list.filter(a => a.conversation_id && a.conversation_id === window.cosHistory?.conversationId) : list;
    const follow = inChat && CHAT.scrollHeight - CHAT.clientHeight - CHAT.scrollTop < 48;
    const nextSignature = JSON.stringify([visible, problem]);
    const cards = box.querySelector('[data-tp-list]');
    cards.replaceChildren();
    if (problem) cards.append(h('p', {}, problem));
    else {
      if (!visible.length && !inChat) cards.append(h('p', {}, 'No proposals are waiting for you.'));
      for (const a of visible) cards.append(card(a));
    }
    if (inChat) {
      // Canonical history redraws the stream. Reattach this view, never save it as a chat turn.
      const line = box.querySelector('[data-tp-say]'), waiting = !!problem || visible.length > 0;
      box.hidden = !waiting && line.hidden;
      // Once nothing waits, only the outcome line stays; Refresh stays for an outcome it asks him to check.
      box.querySelector('[data-tp-heading]').hidden = !waiting;
      box.querySelector('[data-tp-refresh]').hidden = !waiting && !line.classList.contains('warn');
      CHAT.append(box); // Keep the decision view after newly appended canonical turns.
      if (nextSignature !== chatSignature && !box.hidden) {
        if (follow) { CHAT.scrollTop = CHAT.scrollHeight; JUMP.hidden = true; }
        else JUMP.hidden = false;
      }
      chatSignature = nextSignature;
    }
    render.last.push(...cards.querySelectorAll('button'));
  }
}

async function load(clear = true, afterDecision = false) {
  if (busy && !afterDecision) return;
  const version = ++readVersion;
  if (clear) say('');
  try {
    const r = await fetch(API, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
    const body = await r.json().catch(() => null);
    if (version !== readVersion) return false;
    if (r.ok && body && Array.isArray(body.proposals)) render(body.proposals);
    else render([], body?.message || "Proposals couldn't be read just now. Nothing is lost; try Refresh.");
  } catch (e) {
    if (version !== readVersion) return false;
    render([], "Proposals couldn't be read just now. Nothing is lost; try Refresh.");
  }
  return true;
}

if (BOXES.length) {
  for (const box of BOXES) box.querySelector('[data-tp-refresh]').addEventListener('click', () => load());
  document.addEventListener('cos:conversation', () => { ++readVersion; CHAT_BOX?.remove(); if (JUMP) JUMP.hidden = true; chatSignature = ''; say(''); });
  document.addEventListener('cos:conversation-selected', () => load());
  document.addEventListener('cos:turns-changed', () => load(false));
  document.addEventListener('cos:turn-completed', () => load(false));
  load();
}
