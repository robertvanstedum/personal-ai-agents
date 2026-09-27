// The prototype's shared state: conversation, overlay (receipts, decision, pending
// queue writes) and the simulated clock. Off-the-record content is memory-only.
import { load, save, isObj } from './state.js';

const listeners = new Set();
export const world = {
  page: null, scenario: null,
  conv: null, overlay: null, clock: null,
  off: null,          // { since, messages: [] } while off the record — never persisted
  liveFiles: new Map(), // attachment id -> File (this page only; never persisted)
};

const MODES = ['pill', 'floating', 'docked'];

function defaultConv() {
  return { v: 1, mode: 'pill', pos: { preset: 'right' }, messages: [], unread: 0,
    participants: { claude: { state: 'none' }, codex: { state: 'none' } },
    attachments: [], proposals: [], flags: { asked: false } };
}
function validConv(c) {
  if (!isObj(c) || c.v !== 1 || !Array.isArray(c.messages) || !Array.isArray(c.attachments) ||
      !Array.isArray(c.proposals) || !isObj(c.participants) || !isObj(c.flags)) return null;
  if (!c.messages.every((m) => isObj(m) && typeof m.kind === 'string')) return null;
  if (!c.proposals.every((p) => isObj(p) && typeof p.id === 'string' && Array.isArray(p.fields))) return null;
  if (!c.attachments.every((a) => isObj(a) && typeof a.id === 'string' && typeof a.name === 'string')) return null;
  for (const who of ['claude', 'codex']) if (!isObj(c.participants[who])) return null;
  if (!MODES.includes(c.mode)) c.mode = 'pill';
  if (!isObj(c.pos)) c.pos = { preset: 'right' };
  if (typeof c.unread !== 'number') c.unread = 0;
  return c;
}
function defaultOverlay(first) {
  return { v: 1, next_receipt: first, receipts: [], decision: null, needs_posted: false, queue: {}, queue_history: [],
    vendor: [], refills: [] };
}
function validOverlay(o) {
  if (!isObj(o) || o.v !== 1 || typeof o.next_receipt !== 'number' || !Array.isArray(o.receipts) ||
      !isObj(o.queue) || !Array.isArray(o.queue_history)) return null;
  if (o.decision !== null && !isObj(o.decision)) return null;
  if (!Array.isArray(o.vendor)) o.vendor = [];
  if (!Array.isArray(o.refills)) o.refills = [];
  if (!o.receipts.every((r) => isObj(r) && typeof r.id === 'string')) return null;
  return o;
}
function defaultClock(s) { return { v: 1, day: 'sat', minute: s.clock.sat.start_minute }; }
function validClock(c) {
  return isObj(c) && c.v === 1 && ['sat', 'mon'].includes(c.day) && Number.isInteger(c.minute) ? c : null;
}

export function initWorld(page) {
  world.page = page;
  world.scenario = page.scenario || { clock: { sat: { label: 'Sat', start_minute: 552 }, mon: { label: 'Mon', start_minute: 480 } },
    receipts: { first: 4486, reserved: [] }, participants: [], turns: [], proposals: {}, bench: {}, monday: {} };
  world.conv = load('conversation.v1', validConv, defaultConv, 'conversation');
  world.overlay = load('overlay.v1', validOverlay, () => defaultOverlay(world.scenario.receipts.first), 'receipts and pending writes');
  world.clock = load('clock.v1', validClock, () => defaultClock(world.scenario), 'clock');
}

export function persist() {
  save('conversation.v1', world.conv);
  save('overlay.v1', world.overlay);
  save('clock.v1', world.clock);
}

export function onChange(fn) { listeners.add(fn); }
export function changed() { for (const fn of listeners) fn(); }

export function nowLabel() {
  const c = world.clock;
  const label = world.scenario.clock[c.day].label;
  const h = Math.floor(c.minute / 60);
  const m = String(c.minute % 60).padStart(2, '0');
  return `${label} ${h}:${m}`;
}
export function tick() { world.clock.minute += 1; }
// Minutes from Sat 00:00 of the scenario week (the usage fixture's time base).
export function nowMinutes() { return (world.clock.day === 'mon' ? 2880 : 0) + world.clock.minute; }

export function context() {
  return { area: document.body.dataset.area || 'Guild', item: document.body.dataset.contextItem || 'nothing selected' };
}

export function flags() {
  const c = world.conv;
  return {
    clock: world.clock.day,
    mon: world.clock.day === 'mon',
    decision: !!world.overlay.decision,
    needs_posted: !!world.overlay.needs_posted,
    invited: c.participants.claude.state !== 'none' || c.participants.codex.state !== 'none',
    claude_joined: c.participants.claude.state === 'joined',
    pending: c.proposals.some((p) => p.status === 'proposed'),
    unfiled: c.attachments.some((a) => a.state === 'discussion'),
    asked: !!c.flags.asked,
    off: !!world.off,
  };
}

// show-when: every key must equal the current flag value.
export function predicate(cond) {
  const f = flags();
  return Object.entries(cond || {}).every(([k, v]) => f[k] === v);
}

export function applyShowWhen(root = document) {
  for (const node of root.querySelectorAll('[data-show-when]')) {
    let cond = null;
    try { cond = JSON.parse(node.getAttribute('data-show-when')); } catch (e) { cond = null; }
    node.hidden = cond ? !predicate(cond) : false;
  }
}

export function nextReceiptId() {
  const reserved = new Set(world.scenario.receipts.reserved || []);
  let n = world.overlay.next_receipt;
  while (reserved.has(`r-${n}`)) n += 1;
  world.overlay.next_receipt = n + 1;
  return `r-${n}`;
}

let seq = 0;
export function newId(prefix) {
  seq += 1;
  return `${prefix}-${Date.now().toString(36)}-${seq}`;
}

// Recorded (persisted) vs off-the-record (memory-only) messages.
export function addMessage(msg) {
  const full = { id: newId('m'), when: nowLabel(), context: context(), ...msg };
  if (world.off) {
    full.offrecord = true;
    world.off.messages.push(full);
    return full;
  }
  world.conv.messages.push(full);
  const phone = window.matchMedia('(max-width: 640px)').matches;
  if (['mc', 'claude'].includes(full.who) && world.conv.mode === 'pill' && !phone && !world.inPage) world.conv.unread += 1;
  return full;
}
