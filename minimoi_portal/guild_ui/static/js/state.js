// Browser state for the real floor. Only view settings are stored here (bench
// order and folds, per device; decision 5). Everything that matters lives on
// the server. "Off the record" is memory only and never stored.
const memory = new Map();
let namespace = 'guild';
let storageOk = true;
const pendingNotices = [];
const listeners = new Set();

export const live = { off: false, offSince: null };

export function onChange(fn) { listeners.add(fn); }
export function changed() { for (const fn of listeners) fn(); }

export function setOff(on) {
  live.off = !!on;
  live.offSince = on ? new Date().toISOString() : null;
  changed();
}

export function configure(ns) {
  namespace = ns || 'guild';
  try {
    const probe = `${namespace}.__probe`;
    window.localStorage.setItem(probe, '1');
    window.localStorage.removeItem(probe);
    // Keys from older layouts under this namespace are ignored and removed.
    const old = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (k && k.startsWith(`${namespace}.`) && k.endsWith('.v1')) old.push(k);
    }
    for (const k of old) window.localStorage.removeItem(k);
  } catch (e) {
    storageOk = false;
    pendingNotices.push('Browser storage is unavailable; your bench arrangement lasts only until you leave this page.');
  }
}

export const fullKey = (name) => `${namespace}.${name}`;

function rawGet(k) {
  if (!storageOk) return memory.has(k) ? memory.get(k) : null;
  try { return window.localStorage.getItem(k); } catch (e) { storageOk = false; return memory.get(k) ?? null; }
}

function rawSet(k, v) {
  if (!storageOk) { memory.set(k, v); return; }
  try { window.localStorage.setItem(k, v); } catch (e) { storageOk = false; memory.set(k, v); }
}

// validate(value) returns the (possibly repaired) value, or null when unusable.
export function load(name, validate, makeDefault, label) {
  const raw = rawGet(fullKey(name));
  if (raw == null) return makeDefault();
  try {
    const repaired = validate(JSON.parse(raw));
    if (repaired) return repaired;
  } catch (e) { /* fall through to defaults */ }
  pendingNotices.push(`Saved ${label} was unreadable; defaults loaded.`);
  return makeDefault();
}

export function save(name, value) {
  rawSet(fullKey(name), JSON.stringify(value));
}

export function takeNotices() {
  return pendingNotices.splice(0, pendingNotices.length);
}

export const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
export const isStrArr = (v) => Array.isArray(v) && v.every((s) => typeof s === 'string');
