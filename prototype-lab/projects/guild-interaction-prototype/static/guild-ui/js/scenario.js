// Scripted-turn engine. Every MC/Claude line produced here is labelled
// "Simulated reply"; nothing calls a model. Turns live in config/scenario.json.
import { world, addMessage, persist, changed, nowLabel, tick, flags, newId } from './world.js';
import { propose, proposeFile, proposeRefill, proposeVendor, confirm, cancel, pending, OFF_REASON } from './proposals.js';
import { stripPayment, stripCard, looksLikeReceipt, looksLikeWarning, parseReceipt, parseWarning } from './capture.js';

const CONSEQUENTIAL = new Set(['confirm', 'edit', 'cancel', 'file', 'invite', 'propose']);
let ui = { openEdit: () => {}, refuse: () => {} };
export function bindUi(hooks) { ui = { ...ui, ...hooks }; }

function mc(text, extra = {}) {
  return addMessage({ kind: 'turn', who: 'mc', text, simulated: true, ...extra });
}

function findTurn(text) {
  const t = text.toLowerCase();
  for (const turn of world.scenario.turns || []) {
    if (turn.match.some((re) => new RegExp(re, 'i').test(t))) return turn;
  }
  return null;
}

export function goOffRecord() {
  if (world.off) return;
  world.off = { since: nowLabel(), messages: [] };
  changed();
}

export function goOnRecord() {
  if (!world.off) return;
  world.off = null;
  addMessage({ kind: 'system', text: `Back on the record · ${nowLabel()} · the off-record segment was not recorded` });
  persist(); changed();
}

export function handle(rawText, via = 'text') {
  const raw = String(rawText || '').trim();
  // Payment-method details never reach the thread or storage (rev 3.1).
  const text = looksLikeReceipt(raw) ? stripPayment(raw) : stripCard(raw);
  if (!text || !world.page.prototype) return;
  const turn = findTurn(text);
  if (world.off) {
    if (turn && turn.do === 'on_record') { goOnRecord(); return; }
    addMessage({ kind: 'turn', who: 'robert', via, text });
    if (turn && CONSEQUENTIAL.has(turn.do)) {
      addMessage({ kind: 'turn', who: 'mc', text: OFF_REASON(), simulated: true });
      ui.refuse(OFF_REASON());
    }
    changed();
    return;
  }
  if (turn && turn.do === 'off_record') { goOffRecord(); return; }
  if (turn && turn.do === 'on_record') { mc('We are already on the record.'); persist(); changed(); return; }
  tick();
  addMessage({ kind: 'turn', who: 'robert', via, text });
  run(turn || { do: 'reply', text: '' });
  persist(); changed();
}

function run(turn) {
  switch (turn.do) {
    case 'confirm': case 'cancel': case 'edit': {
      const p = pending();
      if (!p) { mc('Nothing is waiting for confirmation.'); return; }
      if (turn.do === 'confirm') confirm(p.id);
      else if (turn.do === 'cancel') cancel(p.id);
      else ui.openEdit(p.id);
      return;
    }
    case 'file': {
      const pasted = pastedCandidate();
      if (pasted) return filePasted(pasted);
      const atts = world.conv.attachments.filter((a) => a.state === 'discussion');
      const att = atts[atts.length - 1];
      if (!att) { mc(turn.none_text); return; }
      const already = world.conv.proposals.find((p) => p.attachment === att.id && p.status === 'proposed');
      if (already) { mc('A filing proposal for this attachment is already waiting — confirm, edit or cancel it.'); return; }
      proposeFile(att);
      return;
    }
    case 'recall': return recall(turn);
    case 'usage': return usage();
    case 'invite': return invite(turn);
    case 'status': return status();
    case 'propose': {
      if (world.overlay.decision && turn.proposal === 'decision') { mc(turn.done_text); return; }
      const res = propose(turn.proposal);
      if (res.existing && res.existing.status === 'proposed') mc('That proposal is already waiting for you.');
      return;
    }
    case 'reply': default: {
      mc(turn.text || '');
      if (turn.flag) world.conv.flags[turn.flag] = true;
      if (turn.then_propose && !world.overlay.needs_posted) propose(turn.then_propose);
    }
  }
}

// The most recent unfiled item before "file this": a pasted receipt or vendor
// warning, or an attachment (whichever came last).
function pastedCandidate() {
  const msgs = world.conv.messages;
  for (let i = msgs.length - 2; i >= 0; i -= 1) {
    const m = msgs[i];
    if (m.kind === 'attachment') return null;          // the attachment path handles it
    if (m.kind !== 'turn' || m.who !== 'robert' || m.filed) continue;
    if (looksLikeReceipt(m.text) || looksLikeWarning(m.text)) return m;
  }
  return null;
}

function filePasted(m) {
  const cap = world.page.capture || { tools: [], balances: [] };
  if (looksLikeReceipt(m.text)) {
    const r = parseReceipt(m.text, cap.balances);
    if (r.amount == null) {
      mc('I can\'t read an amount in that receipt, so I won\'t guess. What was paid? For example “xAI top-up $25”, then say “file this”.');
      return;
    }
    proposeRefill(r, m.id);
    return;
  }
  proposeVendor(parseWarning(m.text, cap.tools), m.text, m.id);
}

function recall(turn) {
  const d = world.overlay.decision;
  if (!d) { mc(turn.none_text); return; }
  const steps = (world.clock.day === 'mon' ? world.scenario.monday.steps : world.scenario.monday.sat_steps) || [];
  const open = steps.filter((s) => s.state === 'open').map((s) => s.text).join('; ');
  mc(`Decision · ${d.when} · receipt ${d.receipt} · session ${d.session}: ${d.text}. Owners: ${d.owners}.${open ? ` Open: ${open}.` : ''}`,
    { link: { href: world.page.urls.bench + '#decision', text: 'See the decision card on the bench' } });
}

function usage() {
  const u = (world.page.usage || {})[world.clock.day];
  if (!u) { mc('Usage and limits are not available on this page.'); return; }
  mc(u.reply);
  if (!u.shift) return;
  const open = world.conv.proposals.find((p) => p.template === 'usage_shift' && p.status === 'proposed');
  if (open) { mc('A shift proposal is already waiting for you — confirm, edit or cancel it.'); return; }
  propose('usage_shift', { fields: u.shift.fields });
}

function status() {
  const q = world.page.queue_summary;
  if (q.badge.status !== 'ok') {
    mc(`The Build Queue is unknown right now (${q.badge.text}${q.badge.error ? ': ' + q.badge.error : ''}). Treat as unknown.`);
    return;
  }
  const sr = q.spec_ready.length ? q.spec_ready.join('; ') : 'none';
  const ib = q.in_build.length ? q.in_build.join('; ') : 'none';
  mc(`Build Queue (${q.badge.text}): Spec Ready — ${sr}. In Build — ${ib}.${q.note ? ` Not counted: ${q.note}.` : ''}`);
}

export function invite(turn) {
  turn = turn || (world.scenario.turns || []).find((t) => t.do === 'invite');
  if (world.off) { ui.refuse(OFF_REASON()); return; }
  const parts = world.conv.participants;
  if (parts.claude.state !== 'none' || parts.codex.state !== 'none') { mc(turn.again_text); return; }
  const at = nowLabel();
  for (const p of world.scenario.participants.filter((x) => x.invitable)) {
    parts[p.id] = { state: 'invited', invited_at: at };
  }
  mc(turn.text);
  persist(); changed();
  scheduleJoins();
}

const scheduled = new Set();
export function scheduleJoins() {
  const turn = (world.scenario.turns || []).find((t) => t.do === 'invite') || {};
  for (const p of world.scenario.participants.filter((x) => x.invitable && x.joins_after_ms != null)) {
    const st = world.conv.participants[p.id];
    if (st.state !== 'invited' || scheduled.has(p.id)) continue;
    scheduled.add(p.id);
    window.setTimeout(() => {
      const cur = world.conv.participants[p.id];
      if (cur.state !== 'invited') return;
      const wasOff = world.off; world.off = null; // a join follows an on-record invite; it is recorded
      cur.state = 'joined'; cur.joined_at = nowLabel();
      addMessage({ kind: 'system', text: `${p.label} joined · ${cur.joined_at} · simulated` });
      const line = (turn.join_text || {})[p.id];
      if (line) addMessage({ kind: 'turn', who: p.id, text: line, simulated: true });
      world.off = wasOff;
      persist(); changed();
    }, p.joins_after_ms);
  }
}

export function returnMonday() {
  if (world.off) { ui.refuse(OFF_REASON()); return false; }
  if (world.clock.day === 'mon') return false;
  world.clock.day = 'mon';
  world.clock.minute = world.scenario.clock.mon.start_minute;
  addMessage({ kind: 'system', text: `Simulated clock moved to ${nowLabel()}` });
  mc(world.scenario.monday.mc_message);
  persist(); changed();
  return true;
}

export function voiceLine() {
  if (world.off) return 'back on the record';
  const f = flags();
  for (const v of world.scenario.voice_script || []) {
    const w = v.when || '';
    const neg = w.startsWith('!');
    const key = neg ? w.slice(1) : w;
    if (!key) return v.say;
    if (neg ? !f[key] : f[key]) return v.say;
  }
  return 'What\'s in build?';
}

export { newId };
