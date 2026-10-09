import { apiPost, recordMode } from './api.js';
import { newKey } from './actions.js';
const dirtyForms = new Set();
window.addEventListener('beforeunload', e => { if (dirtyForms.size) { e.preventDefault(); e.returnValue = ''; } });
for (const form of document.querySelectorAll('[data-library-form]')) {
  form.addEventListener('input', () => dirtyForms.add(form));
  let key = newKey(), lastPayload = null, pending = false;
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (pending) return;
    const payload = {url: form.elements.url.value.trim(), title: form.elements.title.value,
      tags: form.elements.tags.value.split(',').map(t => t.trim()).filter(Boolean),
      note: form.elements.note.value, revision: Number(form.dataset.revision), record_mode: recordMode()};
    const serial = JSON.stringify(payload);
    if (serial !== lastPayload) { key = newKey(); lastPayload = serial; }
    const button = form.querySelector('button[type="submit"]');
    const result = form.querySelector('[data-library-result]');
    pending = true;
    const fields = [...form.querySelectorAll('input,textarea,button')];
    fields.forEach(f => { f.disabled = true; });
    const r = await apiPost('/library/links', {...payload, idempotency_key:key});
    pending = false;
    fields.forEach(f => { f.disabled = false; });
    result.textContent = r.body?.message || 'Could not confirm the save. Your draft is still here.';
    const saved = r.ok && r.body?.result === 'saved' && Number.isInteger(r.body.revision) && r.body.revision > 0 && typeof r.body.url === 'string' && /^https?:\/\//.test(r.body.url);
    if (saved) {
      dirtyForms.delete(form);
      form.dataset.revision = String(r.body.revision);
      form.elements.url.value = r.body.url;
      form.elements.url.readOnly = true;
      button.textContent = 'Save changes';
      // Do not reload: other reference cards may contain unsaved drafts.
      const refresh = document.createElement('a');
      refresh.href = location.href;
      refresh.textContent = 'Refresh library';
      result.append(' · ', refresh);
    }
  });
}
const web = document.querySelector('[data-library-web]');
if (web) web.addEventListener('click', () => {
  const input = document.getElementById('library-query');
  const query = input.value.trim();
  if (!query) { input.focus(); return; }
  window.open('https://duckduckgo.com/?q=' + encodeURIComponent(query), '_blank', 'noopener,noreferrer');
});
