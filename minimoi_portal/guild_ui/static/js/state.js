// Browser state for the real floor. Only view settings are stored here (bench
// order and folds, per device; decision 5). Everything that matters lives on
// the server. "Off the record" is a view state of this tab (review B1c #3):
// a bare on/off flag in sessionStorage, so it survives moving between pages
// of the floor. It is never sent as data and holds no content.
const memory = new Map();
let namespace = 'guild';
let storageOk = true;
const pendingNotices = [];
const listeners = new Set();

// known is false when this tab's record mode could not be read: the page then
// makes no automatic write (Continue) until Robert chooses a mode.
export const live = { off: false, offSince: null, known: true };

export function onChange(fn) { listeners.add(fn); }
export function changed() { for (const fn of listeners) fn(); }

const RECORD_KEY = () => `${namespace}.record_mode`;
const TAB_KEY = () => `${namespace}.tab_id`;
// Which tabs of this browser are off the record right now: tab ids and times
// only, no content. A tab opened fresh (a middle-click from an off-the-record
// tab starts with empty sessionStorage) reads this to know it cannot assume
// "on the record". Entries older than 12 hours are ignored.
const OFF_TABS_KEY = () => `${namespace}.off_tabs`;
const OFF_TABS_MAX_AGE_MS = 12 * 60 * 60 * 1000;

function tabId() {
  try {
    let id = window.sessionStorage.getItem(TAB_KEY());
    if (!id) {
      id = `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
      window.sessionStorage.setItem(TAB_KEY(), id);
    }
    return id;
  } catch (e) { return null; }
}

function offTabs() {
  try {
    const v = JSON.parse(window.localStorage.getItem(OFF_TABS_KEY()) || '{}');
    if (!isObj(v)) return {};
    const now = Date.now();
    return Object.fromEntries(Object.entries(v).filter(([, at]) =>
      typeof at === 'string' && now - Date.parse(at) < OFF_TABS_MAX_AGE_MS));
  } catch (e) { return {}; }
}

function markOffTab(off) {
  const id = tabId();
  if (!id) return;
  try {
    const tabs = offTabs();
    if (off) tabs[id] = new Date().toISOString(); else delete tabs[id];
    window.localStorage.setItem(OFF_TABS_KEY(), JSON.stringify(tabs));
  } catch (e) { /* other tabs then cannot see this one */ }
}

function saveRecordMode() {
  try {
    window.sessionStorage.setItem(RECORD_KEY(), JSON.stringify({ off: live.off, since: live.offSince }));
    return true;
  } catch (e) { return false; }   // the mode then lasts only for this page
}

function loadRecordMode() {
  let raw;
  try { raw = window.sessionStorage.getItem(RECORD_KEY()); } catch (e) { live.known = false; return; }
  if (raw == null) {
    // A fresh tab. If another tab of this browser is off the record, this one
    // may have been opened from it: unknown until Robert chooses.
    live.off = false;
    live.offSince = null;
    const others = Object.keys(offTabs()).filter((id) => id !== tabId());
    if (others.length) { live.known = false; return; }
    live.known = saveRecordMode();
    return;
  }
  try {
    const v = JSON.parse(raw);
    if (v && typeof v.off === 'boolean') {
      live.off = v.off;
      live.offSince = v.off && typeof v.since === 'string' ? v.since : null;
      live.known = true;
      return;
    }
  } catch (e) { /* unreadable: unknown */ }
  live.known = false;
}

export function setOff(on) {
  live.off = !!on;
  live.offSince = on ? new Date().toISOString() : null;
  live.known = true;
  saveRecordMode();
  markOffTab(live.off);
  changed();
}

export function configure(ns) {
  namespace = ns || 'guild';
  loadRecordMode();
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
