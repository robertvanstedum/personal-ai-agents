// Work cards waiting for Robert (B4, Blue Lantern 5 Nov 2026): the work MC delegated, shown in CoS Actions where he
// works with CoS. Read from the portal (GET <cos>/work-cards); a decision is one click on one card exactly as shown
// (POST <cos>/work-cards/approve|decline with the card's operation id and digest). The page never builds or edits a
// card, never approves on load, and claims nothing the server did not answer.
const PANEL = document.getElementById('tab-flight');
const BOX = PANEL && PANEL.querySelector('[data-wc]');
const CHAT = document.getElementById('chat-log');
const CHAT_BOX = CHAT ? h('section', { class: 'wc wc-chat', 'data-wc-chat': '', 'aria-label': 'Work awaiting your decision' },
  h('h2', {}, 'Work awaiting your decision'),
  h('button', { type: 'button', class: 'fl-btn', 'data-wc-refresh': '' }, 'Refresh'),
  h('p', { 'data-wc-say': '', role: 'status', hidden: true }),
  h('div', { 'data-wc-list': '' })) : null;
const BOXES = [BOX, CHAT_BOX].filter(Boolean);
let readVersion = 0;
let chatSignature = '';
const JUMP = CHAT ? h('button', { type: 'button', class: 'wc-jump', hidden: true }, 'Work card updated ↓') : null;
if (JUMP) {
  CHAT.after(JUMP);
  JUMP.addEventListener('click', () => { CHAT.scrollTop = CHAT.scrollHeight; JUMP.hidden = true; });
}
const BASE = location.pathname.replace(/\/ui(\/.*)?$/, '').replace(/\/$/, '');
const API = BASE + '/work-cards';
const ROLE = { builder: 'Does the work', reviewer: 'Reviews it' };
const CLASS = { build_only: 'Build', review: 'Review' };
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
    const line = box.querySelector('[data-wc-say]');
    const scoped = box === CHAT_BOX && admission?.conversation_id && admission.conversation_id !== window.cosHistory?.conversationId ? '' : text;
    line.textContent = scoped || '';
    line.hidden = !scoped;
    line.classList.toggle('warn', !!warn);
  }
}

// Who does it, by which route, for how much. "fakework" is the simulated worker: no model runs and nothing is spent.
function worker(role, w) {
  const simulated = w.route === 'fakework';
  const cost = simulated ? 'Simulated work: no model runs, nothing is spent'
    : `Up to ${w.amount ?? '?'} (cap $${w.cost_cap_usd ?? '?'})`;
  return h('li', {}, h('strong', {}, `${ROLE[role] || role}: `), `${w.actor || 'unknown'} via ${w.route || 'unknown'}. ${cost}.`);
}

function card(a) {
  const c = a.card || {};
  const steps = Object.entries(c.briefs || {}).map(([kind, text]) => h('li', {}, h('strong', {}, `${kind}: `), String(text)));
  const ref = c.ref ? String(c.ref).slice(0, 12) : 'unknown';
  const decide = (decision) => async () => {
    if (busy) return;
    busy = true; ++readVersion; render.last && render.last.forEach((b) => { b.disabled = true; });
    say(decision === 'approve' ? 'Approving this card…' : 'Declining this card…', false, a);
    let answer = null;
    try {
      const r = await fetch(`${API}/${decision}`, { method: 'POST', credentials: 'same-origin', headers: headers(),
        body: JSON.stringify({ operation_id: a.operation_id, card_digest: a.card_digest }) });
      answer = await r.json().catch(() => null);
      if (!r.ok) answer = { failed: true, message: answer?.message || "That decision wasn't recorded. Reload to check." };
    } catch (e) { answer = { failed: true, message: "The portal couldn't be reached. Nothing is claimed; try again." }; }
    if (!answer || (!answer.failed && (answer.operation_id !== a.operation_id ||
        !['approved', 'declined'].includes(answer.state)))) {
      answer = { failed: true, message: 'The decision is unconfirmed. Refresh to check before trying again.' };
    }
    say(answer.failed ? answer.message : answer.note, answer.failed, a);
    // Keep both views locked until a fresh authoritative read replaces the old card.
    while (!(await load(false, true))) { /* Selection changed: reconcile before unlocking. */ }
    busy = false;
    render.last?.forEach(b => { b.disabled = b.dataset.decided === 'true'; });
  };
  return h('article', { class: 'wc-card', 'data-wc-op': a.operation_id },
    h('h3', {}, `${CLASS[c.class] || c.class || 'Work'} card for ${a.task_id}`),
    h('ul', { class: 'wc-workers' }, Object.entries(c.workers || {}).map(([role, w]) => worker(role, w))),
    h('p', {}, `Repository ${c.repository || 'unknown'} at ${ref}. Up to ${c.attempts ?? '?'} attempt(s), ${c.step_deadline_s ?? '?'} s per step.`),
    steps.length ? h('details', { open: true }, h('summary', {}, 'The words each worker gets'), h('ul', {}, steps)) : null,
    a.approval ? h('p', { class: 'wc-note' }, 'Approved; waiting for the authority to admit it.') : null,
    h('p', { class: 'wc-actions' },
      h('button', { type: 'button', class: 'fl-btn', 'data-wc-approve': a.operation_id, disabled: !!a.approval || busy, 'data-decided': String(!!a.approval), onclick: decide('approve') }, 'Approve'),
      ' ',
      h('button', { type: 'button', class: 'fl-btn', 'data-wc-decline': a.operation_id, disabled: !!a.approval || busy, 'data-decided': String(!!a.approval), onclick: decide('decline') }, 'Decline')),
    h('p', { class: 'wc-digest' }, h('small', {}, `Card ${a.card_digest}`)));
}

function render(list, problem) {
  render.last = [];
  for (const box of BOXES) {
    const inChat = box === CHAT_BOX;
    const visible = inChat ? list.filter(a => !a.conversation_id || a.conversation_id === window.cosHistory?.conversationId) : list;
    const follow = inChat && CHAT.scrollHeight - CHAT.clientHeight - CHAT.scrollTop < 48;
    const nextSignature = JSON.stringify([visible, problem]);
    const cards = box.querySelector('[data-wc-list]');
    cards.replaceChildren();
    if (problem) cards.append(h('p', {}, problem));
    else {
      if (!visible.length && !inChat) cards.append(h('p', {}, 'No work cards are waiting for you.'));
      for (const a of visible) cards.append(card(a));
    }
    if (inChat) {
      // Canonical history redraws the stream. Reattach this view, never save it as a chat turn.
      box.hidden = !problem && !visible.length && box.querySelector('[data-wc-say]').hidden;
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
    if (r.ok && body && Array.isArray(body.admissions)) render(body.admissions);
    else render([], body?.message || "Work cards couldn't be read just now. Nothing is lost; try Refresh.");
  } catch (e) {
    if (version !== readVersion) return false;
    render([], "Work cards couldn't be read just now. Nothing is lost; try Refresh.");
  }
  return true;
}

if (BOXES.length) {
  for (const box of BOXES) box.querySelector('[data-wc-refresh]').addEventListener('click', () => load());
  document.addEventListener('cos:conversation', () => { ++readVersion; CHAT_BOX?.remove(); if (JUMP) JUMP.hidden = true; chatSignature = ''; say(''); });
  document.addEventListener('cos:conversation-selected', () => load());
  document.addEventListener('cos:turns-changed', () => load(false));
  document.addEventListener('cos:turn-completed', () => load(false));
  load();
}
