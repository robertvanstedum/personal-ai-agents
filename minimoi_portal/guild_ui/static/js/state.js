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
// says so, and sends no write at all (api.js) until Robert chooses a mode.
export const live = { off: false, offSince: null, known: true };

export function onChange(fn) { listeners.add(fn); }
export function changed() { for (const fn of listeners) fn(); }

const RECORD_KEY = () => `${namespace}.record_mode`;
const TAB_KEY = () => `${namespace}.tab_id`;
const ALIVE_KEY = () => `${namespace}.tab_alive`;
// Which tabs of this browser are off the record right now: tab ids and times
// only, no content. A tab opened fresh (a middle-click from an off-the-record
// tab starts with empty sessionStorage) reads this to know it cannot assume
// "on the record". An off tab refreshes its entry every minute and removes it
// when its page goes away (pagehide), so a closed tab stops counting at once,
// and a crashed one after 10 minutes. If the list cannot be read, or is
// damaged, a fresh tab's mode is unknown: never "on the record" by default.
const OFF_TABS_KEY = () => `${namespace}.off_tabs`;
const OFF_TABS_MAX_AGE_MS = 10 * 60 * 1000;
const OFF_TABS_HEARTBEAT_MS = 60 * 1000;
let pageTabId = null;

const newTabId = () => `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;

// This tab's id. A duplicated tab (or one opened by window.open) starts with a
// copy of the original's sessionStorage, id included, while the original is
// still open; the original's page marks itself alive in sessionStorage and
// unmarks on pagehide, so a page that finds the mark set is a copy and takes a
// new id. Moving between pages of one tab unmarks first, so the id stays.
function claimTabId() {
  try {
    const store = window.sessionStorage;
    let id = store.getItem(TAB_KEY());
    if (!id || store.getItem(ALIVE_KEY()) === '1') {
      id = newTabId();
      store.setItem(TAB_KEY(), id);
    }
    store.setItem(ALIVE_KEY(), '1');
    return id;
  } catch (e) { return null; }
}

// The off tabs, or null when the list cannot be read or is damaged (unknown).
function offTabs() {
  let v;
  try { v = JSON.parse(window.localStorage.getItem(OFF_TABS_KEY()) || '{}'); } catch (e) { return null; }
  if (!isObj(v)) return null;
  const now = Date.now();
  const out = {};
  for (const [id, at] of Object.entries(v)) {
    const t = typeof at === 'string' ? Date.parse(at) : NaN;
    if (Number.isNaN(t)) return null;
    if (now - t < OFF_TABS_MAX_AGE_MS) out[id] = at;
  }
  return out;
}

function markOffTab(off) {
  if (!pageTabId) return false;
  try {
    const tabs = offTabs() || {};          // a damaged list is replaced by a clean one
    if (off) tabs[pageTabId] = new Date().toISOString(); else delete tabs[pageTabId];
    window.localStorage.setItem(OFF_TABS_KEY(), JSON.stringify(tabs));
    return true;
  } catch (e) { return false; }            // other tabs then cannot see this one
}

function saveRecordMode() {
  try {
    window.sessionStorage.setItem(RECORD_KEY(), JSON.stringify({ off: live.off, since: live.offSince }));
    return true;
  } catch (e) { return false; }   // the mode then lasts only for this page
}

function loadRecordMode() {
  pageTabId = claimTabId();
  let raw;
  try { raw = window.sessionStorage.getItem(RECORD_KEY()); } catch (e) { live.known = false; return; }
  if (raw == null) {
    // A fresh tab. If another tab of this browser is off the record, this one
    // may have been opened from it; if the list cannot be read, it cannot
    // tell. Either way: unknown until Robert chooses.
    live.off = false;
    live.offSince = null;
    const tabs = offTabs();
    if (tabs === null || Object.keys(tabs).some((id) => id !== pageTabId)) { live.known = false; return; }
    live.known = saveRecordMode();
    return;
  }
  try {
    const v = JSON.parse(raw);
    if (v && typeof v.off === 'boolean') {
      live.off = v.off;
      live.offSince = v.off && typeof v.since === 'string' ? v.since : null;
      live.known = true;
      if (live.off) markOffTab(true);      // this page is here again (or a copy with its own id)
      return;
    }
  } catch (e) { /* unreadable: unknown */ }
  live.known = false;
}

function watchTab() {
  window.addEventListener('pagehide', () => {
    try { window.sessionStorage.setItem(ALIVE_KEY(), '0'); } catch (e) { /* nothing to unmark */ }
    if (live.off) markOffTab(false);
  });
  window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;          // back from the back-forward cache
    try { window.sessionStorage.setItem(ALIVE_KEY(), '1'); } catch (e) { /* ignore */ }
    if (live.off) markOffTab(true);
  });
  window.setInterval(() => { if (live.off) markOffTab(true); }, OFF_TABS_HEARTBEAT_MS);
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
  watchTab();
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
