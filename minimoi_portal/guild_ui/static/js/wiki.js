import { apiPost, recordMode } from './api.js';
import { newKey } from './actions.js';
const form = document.querySelector('[data-wiki-editor]');
if (form) {
  let dirty = false, key = newKey(), savedPayload = null;
  form.addEventListener('input', () => { dirty = true; });
  window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const result = form.querySelector('[data-wiki-result]');
    const button = form.querySelector('button[type=submit]');
    const payload = { page: form.dataset.page || null, revision: Number(form.dataset.revision),
      title: form.elements.title.value, body: form.elements.body.value, record_mode: recordMode() };
    const serialized = JSON.stringify(payload);
    if (savedPayload !== serialized) { key = newKey(); savedPayload = serialized; }
    button.disabled = true; form.elements.title.disabled = true; form.elements.body.disabled = true; result.textContent = 'Saving…';
    const response = await apiPost('/wiki/pages', { ...payload, idempotency_key: key });
    button.disabled = false; form.elements.title.disabled = false; form.elements.body.disabled = false;
    if (response.ok && response.body.page) {
      dirty = false;
      const url = new URL(location.href); url.search = ''; url.searchParams.set('page', response.body.page);
      location.assign(url.href);
    } else result.textContent = response.body?.message || 'Could not confirm the save. Your draft is still here.';
  });
}
