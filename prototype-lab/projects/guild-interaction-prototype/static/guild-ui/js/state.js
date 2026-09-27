// Browser persistence. Every access is wrapped; if storage is unavailable, state
// lives in memory for this page only. Keys are namespaced per mount (prefix-aware).
const memory = new Map();
let namespace = 'guild';
let storageOk = true;
const pendingNotices = [];

export function configure(ns) {
  namespace = ns || 'guild';
  try {
    const probe = `${namespace}.__probe`;
    window.localStorage.setItem(probe, '1');
    window.localStorage.removeItem(probe);
  } catch (e) {
    storageOk = false;
    pendingNotices.push('Browser storage is unavailable; state lasts only until you leave this page.');
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

function rawDel(k) {
  memory.delete(k);
  try { window.localStorage.removeItem(k); } catch (e) { /* storage unavailable */ }
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

export function clearKeys(keys) {
  for (const k of keys) rawDel(k);
}

export function takeNotices() {
  return pendingNotices.splice(0, pendingNotices.length);
}

export const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
export const isStrArr = (v) => Array.isArray(v) && v.every((s) => typeof s === 'string');
