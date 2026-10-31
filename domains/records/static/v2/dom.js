// Small DOM and formatting helpers. Everything is built with createElement/textContent: no innerHTML,
// no inline styles (the page runs under a strict Content-Security-Policy).

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, String(value));
  }
  appendAll(el, children);
  return el;
}

export function appendAll(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** A status chip. Text is mandatory: status is never conveyed by colour alone. */
export function chip(text, tone = 'neutral', extra = {}) {
  if (!String(text || '').trim()) throw new Error('A status chip needs visible text.');
  return h('span', { class: `chip chip--${tone}`, ...extra }, text);
}

export function simTag() {
  return h('span', { class: 'sim-tag' }, 'Simulated');
}

/** Marks a card/row/list item as data from the adapter; every such item carries a Simulated tag. */
export function item(tag, props, ...children) {
  const el = h(tag, { ...props, 'data-item': '' }, ...children);
  if (!el.querySelector('.sim-tag')) (tag === 'tr' && el.cells.length ? el.cells[0] : el).append(simTag());
  return el;
}

const TIME = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const TIME_S = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
const DATE = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric' });

/** "10:42 CT", or "Unknown" when there is no timestamp. */
export function time(iso) {
  return iso ? `${TIME.format(new Date(iso))} CT` : 'Unknown';
}
export function timeSeconds(iso) {
  return iso ? `${TIME_S.format(new Date(iso))} CT` : 'Unknown';
}
export function dateTime(iso) {
  return iso ? `${DATE.format(new Date(iso))}, ${TIME.format(new Date(iso))} CT` : 'Unknown';
}
export function timeEl(iso, formatter = time) {
  return iso ? h('time', { datetime: iso }, formatter(iso)) : h('span', { class: 'unknown-text' }, 'Unknown');
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return 'Unknown';
  const m = Math.floor(seconds / 60);
  if (m < 1) return `${seconds} s`;
  const hours = Math.floor(m / 60);
  return hours ? `${hours} h ${m % 60} min` : `${m} min`;
}

export function shortHash(hex) {
  return hex ? `${hex.slice(0, 12)}…` : 'Unknown';
}

export function citationText(c) {
  return `${c.source_id}@${c.revision}#${c.ordinal}`;
}

export function citationHref(c) {
  return `#continue/${encodeURIComponent(c.source_id)}/r${c.revision}/s${c.ordinal}`;
}

export function citeLink(c) {
  return h('a', { class: 'cite', href: citationHref(c), title: `Open ${citationText(c)} in the reader` }, citationText(c));
}

export function citeList(citations) {
  return h('span', { class: 'cite-list' }, 'Sources: ', ...citations.flatMap((c, i) => (i ? [' ', citeLink(c)] : [citeLink(c)])));
}

export function principalLabels(...lists) {
  const map = new Map();
  for (const list of lists) for (const p of list || []) map.set(p.id, p);
  return (id) => (id ? map.get(id)?.label || id : 'Nobody');
}

export const EXECUTION = {
  queued: ['Queued', 'neutral'], claimed: ['Claimed', 'info'], running: ['Running', 'info'], waiting: ['Waiting', 'info'],
  outcome_uncertain: ['Outcome uncertain', 'warn'], finished: ['Finished', 'neutral'], failed: ['Failed', 'bad'],
  cancelled: ['Cancelled', 'neutral'],
};
export const DELIVERY = {
  local_only: ['Saved locally only', 'warn'], central_ack_pending: ['Central acknowledgment pending', 'warn'],
  centrally_recorded: ['Centrally recorded', 'ok'],
};
export const FRESHNESS = {
  current: ['Current', 'ok'], stale: ['Stale', 'warn'], unavailable: ['Unavailable', 'bad'], unknown: ['Unknown', 'unknown'],
};
export const AGENT_STATUS = {
  running: ['Running', 'info'], waiting: ['Waiting', 'info'], configured_not_running: ['Configured, not running', 'neutral'],
  stale: ['Stale', 'warn'], unknown: ['Unknown', 'unknown'],
};
export const WAIT_REASON = {
  tool: 'tool', input: 'input', review: 'review', host: 'host', quota: 'quota', sign_in: 'sign-in', schedule: 'schedule',
};
export const FIDELITY = {
  original: ['Original', 'ok'], excerpt: ['Excerpt', 'warn'], extraction: ['Extraction', 'info'],
  reconstruction: ['Reconstruction · derived', 'warn'],
};
export const COVERAGE = {
  complete: ['Complete coverage', 'ok'], partial: ['Partial coverage', 'warn'], unknown: ['Coverage unknown', 'unknown'],
};
export const HEALTH = {
  available: ['Available', 'ok'], degraded: ['Degraded', 'warn'], stale: ['Stale', 'warn'], unavailable: ['Unavailable', 'bad'],
  unknown: ['Unknown', 'unknown'],
};
export const REQUEST_STATE = {
  requested: ['Requested', 'warn'], picked_up: ['Picked up', 'info'], submitted: ['Submitted', 'info'],
  answered: ['Answered', 'ok'], acknowledged: ['Acknowledged', 'neutral'], cancelled: ['Cancelled', 'neutral'],
};

export function labelled(map, key) {
  const [text, tone] = map[key] || ['Unknown', 'unknown'];
  return chip(text, tone, { 'data-status': key || 'unknown' });
}

export function executionChip(execution, waitReason) {
  const [text, tone] = EXECUTION[execution] || ['Unknown', 'unknown'];
  const suffix = execution === 'waiting' && waitReason ? ` · ${WAIT_REASON[waitReason] || waitReason}` : '';
  return chip(text + suffix, tone, { 'data-status': execution || 'unknown' });
}

export function reducedMotion() {
  return globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}
