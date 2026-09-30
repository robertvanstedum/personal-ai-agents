// The one way this front end talks to the server: the JSON API at <mount>/api/v1.
// Writes carry the CSRF token and the record mode; errors come back as JSON.
import { live } from './state.js';
import { notice } from './dom.js';

let page = null;
let signedOutShown = false;

export function configureApi(p) { page = p; }
export const recordMode = () => (live.off ? 'off_record' : 'on_record');
export const RECORD_UNKNOWN_TEXT = 'Not sent: this tab does not know whether you are on the record. Choose Confirm on the record, or Off the record.';

function signedOut() {
  document.body.dataset.signedOut = 'true';
  if (!signedOutShown) {
    signedOutShown = true;
    notice('Signed out — sign in again to see current values. Nothing was changed.');
  }
}

async function call(method, path, body, etag) {
  // Off the record, no write leaves the page (review B1c #3); the server
  // would refuse it too (409), but nothing is even sent.
  if (method !== 'GET' && live.off) {
    return { ok: false, status: 409, offline: true,
      body: { error: 'not_listening', result: 'not_listening', message: page.off_record_text } };
  }
  // While this tab does not know whether it is on the record, nothing is sent
  // either: notes, post-its and Save wait until Robert chooses a mode.
  if (method !== 'GET' && !live.known) {
    return { ok: false, status: 409, offline: true,
      body: { error: 'record_unknown', result: 'record_unknown', message: RECORD_UNKNOWN_TEXT } };
  }
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    headers['X-CSRF-Token'] = page.csrf_token;
    headers['X-Record-Mode'] = recordMode();
  }
  if (etag) headers['If-None-Match'] = etag;
  let res;
  try {
    res = await fetch(`${page.urls.api}${path}`, {
      method, headers, credentials: 'same-origin', cache: 'no-store',
      body: body == null ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    return { ok: false, status: 0, body: { error: 'network', message: 'The server could not be reached. Nothing was changed.' } };
  }
  if (res.status === 304) return { ok: true, status: 304, notModified: true, body: null, etag };
  let data = null;
  try { data = await res.json(); } catch (e) { data = { error: 'unreadable', message: 'The server answer could not be read.' }; }
  if (res.status === 401) signedOut();
  return { ok: res.ok, status: res.status, body: data || {}, etag: res.headers.get('ETag') };
}

// A streamed write (streaming spec v0.2 §3): the same guard headers as any
// write, but the raw Response comes back for the caller to read as NDJSON.
// Off the record nothing is sent. A network failure throws to the caller.
export async function apiStream(path, body, signal) {
  if (live.off) return { refused: true, status: 409, message: page.off_record_text };
  return fetch(`${page.urls.api}${path}`, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store', signal,
    headers: { Accept: 'application/x-ndjson, application/json', 'Content-Type': 'application/json',
      'X-CSRF-Token': page.csrf_token, 'X-Record-Mode': recordMode() },
    body: JSON.stringify(body || {}),
  });
}

// Stop (streaming spec v0.3 N1): allowed off the record, so it never goes
// through call()'s off-the-record hold; the server skips its record-mode check.
export async function apiStop(path) {
  try {
    const res = await fetch(`${page.urls.api}${path}`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': page.csrf_token },
      body: '{}',
    });
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    return { ok: res.ok, status: res.status, body: data };
  } catch (e) {
    return { ok: false, status: 0, body: { error: 'network', message: 'The server could not be reached.' } };
  }
}

export const apiGet = (path, etag) => call('GET', path, null, etag);
export const apiPost = (path, body) => call('POST', path, body || {});
export const apiPut = (path, body) => call('PUT', path, body || {});
