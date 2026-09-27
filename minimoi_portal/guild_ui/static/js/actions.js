// Owner actions, each one request to the server: the verified queue Save and
// clearing a Check. The server issues every receipt; this page never makes one.
import { apiPost, recordMode } from './api.js';

const KIND = {
  saved: 'ok', checked: 'ok',
  conflict: 'warn', busy: 'warn', not_found: 'warn', invalid: 'warn', idempotency_mismatch: 'warn',
  not_listening: 'warn', csrf: 'warn', not_signed_in: 'warn', not_allowed: 'warn',
  uncertain: 'bad', unavailable: 'bad', refused: 'bad', failed: 'bad', network: 'bad', unreadable: 'bad',
};

export const kindOf = (code) => KIND[code] || 'bad';

function newKey() {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID().replace(/-/g, '');
  return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

export async function saveStatus(itemId, to, note, digest) {
  const r = await apiPost(`/queue/items/${itemId}/status`, {
    to, note: note || null, expect_item_digest: digest, idempotency_key: newKey(), record_mode: recordMode(),
  });
  const body = r.body || {};
  const code = body.result || body.error || 'failed';
  return { ...body, code, kind: kindOf(code), httpStatus: r.status };
}

export async function markChecked(opId) {
  const r = await apiPost(`/queue/journal/${opId}/checked`, { record_mode: recordMode() });
  const body = r.body || {};
  const code = body.result || body.error || 'failed';
  return { ...body, code, kind: kindOf(code), httpStatus: r.status };
}
