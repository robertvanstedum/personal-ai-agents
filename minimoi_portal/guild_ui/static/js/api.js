// The one way this front end talks to the server: the JSON API at <mount>/api/v1.
// Writes carry the CSRF token and the record mode; errors come back as JSON.
import { live } from './state.js';
import { notice } from './dom.js';

let page = null;
let signedOutShown = false;

export function configureApi(p) { page = p; }
export const recordMode = () => (live.off ? 'off_record' : 'on_record');

function signedOut() {
  document.body.dataset.signedOut = 'true';
  if (!signedOutShown) {
    signedOutShown = true;
    notice('Signed out — sign in again to see current values. Nothing was changed.');
  }
}

async function call(method, path, body, etag) {
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

export const apiGet = (path, etag) => call('GET', path, null, etag);
export const apiPost = (path, body) => call('POST', path, body || {});
