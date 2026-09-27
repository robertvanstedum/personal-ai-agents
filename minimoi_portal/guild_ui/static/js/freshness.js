// How current the floor on screen is (review F1). Pure functions, no DOM, so
// they are tested directly (tests/guild/shop_floor/test_floor_freshness_js.py).
//
// live        the last poll answered (200 or 304): values as the server sent them
// stale       one poll failed (network error, 5xx, 503) or no good read for 90 s:
//             every light says "Stale", with its own shape, and the time since
//             the last good read; the colour is dropped, the old value is named
// unknown     two polls failed, or no good read for 3 minutes: grey "Unknown"
// signed_out  the server answered 401: grey "Signed out" at once
export const STALE_AFTER_MS = 90 * 1000;
export const UNKNOWN_AFTER_MS = 3 * 60 * 1000;
export const UNKNOWN_AFTER_MISSES = 2;

export function freshnessOf({ missed = 0, lastGoodMs, nowMs, signedOut = false }) {
  if (signedOut) return 'signed_out';
  const age = Math.max(0, nowMs - lastGoodMs);
  if (missed >= UNKNOWN_AFTER_MISSES || age >= UNKNOWN_AFTER_MS) return 'unknown';
  if (missed >= 1 || age >= STALE_AFTER_MS) return 'stale';
  return 'live';
}

export function agoText(lastGoodMs, nowMs) {
  const min = Math.floor(Math.max(0, nowMs - lastGoodMs) / 60000);
  return min < 1 ? 'under a minute ago' : `${min} min ago`;
}

// "12:03 (2 min ago)"; clock formats a millisecond time as local HH:MM.
export function sinceText(lastGoodMs, nowMs, clock) {
  return `${clock(lastGoodMs)} (${agoText(lastGoodMs, nowMs)})`;
}

// What one light shows in a given mode. Never the server's "live" mark unless live.
export function lightView(light, mode, since) {
  if (mode === 'live') {
    return { state: light.state, shape: light.shape, word: light.word, compactWord: light.word,
      reason: light.reason, src: light.source_mark, source: light.source, unknown: light.state === 'unknown' };
  }
  if (mode === 'stale') {
    return { state: 'stale', shape: 'stale', word: `Stale · was ${light.word}`, compactWord: 'Stale',
      reason: light.reason, src: `stale · last good read ${since}`, source: 'stale', unknown: true };
  }
  if (mode === 'signed_out') {
    return { state: 'unknown', shape: 'ring', word: 'Signed out', compactWord: 'Signed out',
      reason: 'sign in again to see current values', src: `signed out · last good read ${since}`,
      source: 'signed_out', unknown: true };
  }
  return { state: 'unknown', shape: 'ring', word: 'Unknown', compactWord: 'Unknown',
    reason: 'the floor could not be read', src: `unknown · no good read since ${since}`,
    source: 'unknown', unknown: true };
}

// The Needs-you line and counts in a given mode; liveLine is the live text.
export function needsView(needs, mode, since, liveLine) {
  const liveCount = needs && needs.total != null ? String(needs.total) : null;
  if (mode === 'live') return { line: liveLine, count: liveCount ?? '?', countWord: liveCount ?? 'unknown' };
  if (mode === 'stale') {
    return { line: `${liveLine} · stale, last good read ${since}`, count: liveCount ?? '?', countWord: liveCount ?? 'unknown' };
  }
  if (mode === 'signed_out') return { line: 'Needs you · unknown — signed out', count: '?', countWord: 'unknown' };
  return { line: `Needs you · unknown — no good read since ${since}`, count: '?', countWord: 'unknown' };
}

export function bannerText(mode, since, why) {
  if (mode === 'stale') {
    return `Stale · the floor could not be refreshed (${why || 'no answer'}). These are the values from the last good read, ${since}. Retrying every minute.`;
  }
  if (mode === 'unknown') {
    return `Unknown · no good read since ${since}${why ? ` (${why})` : ''}. Values are greyed until the server answers.`;
  }
  if (mode === 'signed_out') return `Signed out · values are greyed. Sign in again to see current values. Last good read ${since}.`;
  return '';
}

export function briefingText(mode, since, liveText) {
  if (mode === 'live') return liveText;
  if (mode === 'stale') return `Stale, last good read ${since} · ${liveText}`;
  if (mode === 'signed_out') return `Signed out · the floor is not being read (last good read ${since})`;
  return `Floor unknown · no good read since ${since}`;
}
