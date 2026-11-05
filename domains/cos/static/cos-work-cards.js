// Work cards waiting for Robert (B4, Blue Lantern 5 Nov 2026): the work MC delegated, shown in CoS Actions where he
// works with CoS. Read from the portal (GET <cos>/work-cards); a decision is one click on one card exactly as shown
// (POST <cos>/work-cards/approve|decline with the card's operation id and digest). The page never builds or edits a
// card, never approves on load, and claims nothing the server did not answer.
const PANEL = document.getElementById('tab-flight');
const BOX = PANEL && PANEL.querySelector('[data-wc]');
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
  const token = document.querySelector('meta[name="minimoi-csrf-token"]')?.content;
  return { 'Content-Type': 'application/json', Accept: 'application/json', ...(token ? { 'X-CSRF-Token': token } : {}) };
}

function say(text, warn) {
  const line = BOX.querySelector('[data-wc-say]');
  line.textContent = text || '';
  line.hidden = !text;
  line.classList.toggle('warn', !!warn);
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
    busy = true; render.last && render.last.forEach((b) => { b.disabled = true; });
    say(decision === 'approve' ? 'Approving this card…' : 'Declining this card…');
    let answer = null;
    try {
      const r = await fetch(`${API}/${decision}`, { method: 'POST', credentials: 'same-origin', headers: headers(),
        body: JSON.stringify({ operation_id: a.operation_id, card_digest: a.card_digest }) });
      answer = await r.json().catch(() => null);
      if (!r.ok) answer = { failed: true, message: answer?.message || "That decision wasn't recorded. Reload to check." };
    } catch (e) { answer = { failed: true, message: "The portal couldn't be reached. Nothing is claimed; try again." }; }
    busy = false;
    say(answer.failed ? answer.message : answer.note, answer.failed);
    await load(false);
  };
  return h('article', { class: 'wc-card', 'data-wc-op': a.operation_id },
    h('h3', {}, `${CLASS[c.class] || c.class || 'Work'} card for ${a.task_id}`),
    h('ul', { class: 'wc-workers' }, Object.entries(c.workers || {}).map(([role, w]) => worker(role, w))),
    h('p', {}, `Repository ${c.repository || 'unknown'} at ${ref}. Up to ${c.attempts ?? '?'} attempt(s), ${c.step_deadline_s ?? '?'} s per step.`),
    steps.length ? h('details', { open: true }, h('summary', {}, 'The words each worker gets'), h('ul', {}, steps)) : null,
    a.approval ? h('p', { class: 'wc-note' }, 'Approved; waiting for the authority to admit it.') : null,
    h('p', { class: 'wc-actions' },
      h('button', { type: 'button', class: 'fl-btn', 'data-wc-approve': a.operation_id, disabled: !!a.approval, onclick: decide('approve') }, 'Approve'),
      ' ',
      h('button', { type: 'button', class: 'fl-btn', 'data-wc-decline': a.operation_id, disabled: !!a.approval, onclick: decide('decline') }, 'Decline')),
    h('p', { class: 'wc-digest' }, h('small', {}, `Card ${a.card_digest}`)));
}

function render(list, problem) {
  const cards = BOX.querySelector('[data-wc-list]');
  cards.replaceChildren();
  if (problem) { cards.append(h('p', {}, problem)); render.last = []; return; }
  if (!list.length) cards.append(h('p', {}, 'No work cards are waiting for you.'));
  for (const a of list) cards.append(card(a));
  render.last = [...cards.querySelectorAll('button')];
}

async function load(clear = true) {
  if (clear) say('');
  try {
    const r = await fetch(API, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
    const body = await r.json().catch(() => null);
    if (r.ok && body && Array.isArray(body.admissions)) render(body.admissions);
    else render([], body?.message || "Work cards couldn't be read just now. Nothing is lost; try Refresh.");
  } catch (e) { render([], "Work cards couldn't be read just now. Nothing is lost; try Refresh."); }
}

if (BOX) {
  BOX.querySelector('[data-wc-refresh]').addEventListener('click', () => load());
  load();
}
