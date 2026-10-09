// Files in a conversation (Guild 1.1). Two kinds, kept apart on purpose:
//  * documents (text, code, Markdown, CSV, JSON, HTML, PDF with text, Word .docx): the server reads them into text and
//    keeps only that text. They go with the message they are in the tray for, and Master Craftsman reads them as text.
//  * images (JPEG, PNG, WebP, GIF): kept in the Media library as pictures. Master Craftsman can't see them yet, and the
//    page says so before sending and after.
// Anything else is refused here, before an upload, with the reason. Nothing is attached or sent while Private.
import { $, el, announce, toast } from './dom.js';
import { live, onChange } from './state.js';
import { apiUpload, apiPost } from './api.js';
import { newKey } from './actions.js';

const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
const MB = (n) => `${Math.round(n / (1024 * 1024))} MB`;
const extOf = (name) => { const i = (name || '').lastIndexOf('.'); return i < 0 ? '' : name.slice(i).toLowerCase(); };

// The tray's ready files, for the composer's send: take() hands them to the message being sent and empties the tray;
// restore() puts them back if the message was not kept, so a failed send loses nothing.
const tray = { take: () => [], restore: () => {} };
export const takeTray = () => tray.take();
export const restoreTray = (files) => tray.restore(files);
// True while a file is still being read or uploaded: the composer waits for it (or for it to be dismissed) rather than
// sending the message without it and carrying the file into some later, unrelated message.
export const trayBusy = () => document.querySelectorAll('[data-mc-tray] .att[data-state="working"]').length > 0;

export function initAttachments(page) {
  const button = $('[data-mc-attach]');
  const input = $('[data-mc-file]');
  const trayEl = $('[data-mc-tray]');
  if (!button || !input || !trayEl || !page.conversation) return;
  const imageCap = (page.attach && page.attach.max_bytes) || 8 * 1024 * 1024;
  const docCap = (page.attach && page.attach.doc_max_bytes) || 5 * 1024 * 1024;
  const docExt = new Set((page.attach && page.attach.doc_ext) || []);
  const cid = page.conversation.id;
  const list = $('[data-conv-files]');
  const emptyNote = $('[data-conv-files-empty]');
  const pendingList = $('[data-conv-pending]');
  const ready = new Map();          // file id -> { id, name, kind } for tray rows that are ready to go with the next message
  let n = 0;

  function fileUrl(id, variant) { return page.urls.media_file.replace('__ID__', id).replace('__V__', variant); }

  // The server's answer is the truth: after every attach, remove or retry, the rail's lists are rebuilt from the
  // conversation it sends, so a stale row (another tab, a re-attach) can never stay on screen with a button that would
  // act on the wrong thing.
  function reconcile(conv) {
    if (!conv) return;
    if (list) list.replaceChildren();
    if (pendingList) pendingList.replaceChildren();
    for (const f of conv.attachments || []) listed(f.asset_id, f.name);
    for (const d of conv.documents || []) listedDoc(d);
    for (const p of conv.release_pending || []) showPending(p.asset_id, p.name);
    if (pendingList) pendingList.hidden = !pendingList.children.length;
    if (emptyNote && list) emptyNote.hidden = !!list.children.length;
  }

  function listed(asset, name) {
    if (!list || list.querySelector(`[data-file="${asset}"]`)) return;
    const a = el('a', { href: fileUrl(asset, 'full'), target: '_blank', rel: 'noopener' });
    a.append(el('img', { src: fileUrl(asset, 'thumb'), alt: '', width: '36', height: '36' }), el('span', { class: 'fc-file-name' }, name));
    const li = el('li', { class: 'fc-file', 'data-file': asset });
    li.append(a, el('button', { type: 'button', class: 'fc-file-remove', 'data-file-remove': asset, 'aria-label': `Remove ${name} from this conversation` }, 'Remove'));
    list.append(li);
    if (emptyNote) emptyNote.hidden = true;
  }

  function listedDoc(d) {
    if (!list || list.querySelector(`[data-doc="${d.id}"]`)) return;
    const meta = d.truncated ? ` · only ${Number(d.chars).toLocaleString()} of ${Number(d.chars_total).toLocaleString()} characters kept` : '';
    const li = el('li', { class: 'fc-file fc-doc', 'data-doc': d.id });
    const label = el('span', { class: 'fc-file-name' }, d.name);
    label.append(el('span', { class: 'fc-doc-meta small' }, meta));
    li.append(el('span', { class: 'fc-doc-icon', 'aria-hidden': 'true' }, '📄'), label);
    const acts = el('span', { class: 'fc-file-acts' });
    if (d.has_original) acts.append(el('a', { class: 'fc-file-download', 'data-doc-download': '', download: '', href: `${page.urls.api}/conversations/${encodeURIComponent(cid)}/documents/${encodeURIComponent(d.id)}/original`, 'aria-label': `Download the original of ${d.name}` }, 'Download'));
    acts.append(el('button', { type: 'button', class: 'fc-file-remove', 'data-doc-remove': d.id, 'aria-label': `Remove ${d.name} from this conversation` }, 'Remove'));
    li.append(acts);
    list.append(li);
    if (emptyNote) emptyNote.hidden = true;
  }

  function row(file, kind) {
    const li = el('div', { class: 'att', 'data-att': String(++n), 'data-state': 'working' });
    const name = el('span', { class: 'att-name' }, file.name || 'file');
    const status = el('span', { class: 'att-status' }, kind === 'document' ? 'Reading…' : 'Attaching…');
    const close = el('button', { type: 'button', class: 'att-x', 'aria-label': `Dismiss ${file.name || 'file'}` }, '×');
    const r = { li, status, id: null, dismissed: false };
    // Dismissing takes the file out of the NEXT message for good: a late answer from the server must not put it back.
    // (A document the server already read stays kept with the conversation; only its place in the tray is gone.)
    close.addEventListener('click', () => { r.dismissed = true; if (r.id) ready.delete(r.id); li.remove(); trayEl.hidden = !trayEl.children.length; });
    li.append(el('span', { class: 'att-clip', 'aria-hidden': 'true' }, kind === 'document' ? '📄' : '📎'), name, close, status);
    trayEl.hidden = false;
    trayEl.append(li);
    return r;
  }

  const end = (r, state, text) => {
    if (r.dismissed) return;
    r.li.dataset.state = state; r.status.textContent = text; r.status.title = text;
    announce(`${r.li.querySelector('.att-name').textContent}: ${text}`);
  };
  const ok = (r, id, name, kind, text) => {
    if (r.dismissed) return;                       // dismissed while it was being read: never selected for a message
    r.id = id; r.li.dataset.fileId = id; ready.set(id, { id, name, kind }); end(r, 'ok', text);
  };

  async function attachImage(file, r) {
    if (file.size > imageCap) { end(r, 'failed', `Not attached: larger than ${MB(imageCap)}.`); return; }
    const up = await apiUpload('/media', file, newKey());
    if (!up.ok || !up.body || !up.body.asset) { end(r, 'failed', `Not attached: ${(up.body && up.body.message) || 'the upload failed.'}`); retryable(r, file); return; }
    const done = await apiPost(`/conversations/${encodeURIComponent(cid)}/attachments`,
      { asset_id: up.body.asset.id, name: file.name || 'image', idempotency_key: newKey() });
    if (!done.ok) { end(r, 'failed', `Not attached: ${(done.body && done.body.message) || 'it could not be kept with this conversation.'}`); retryable(r, file); return; }
    reconcile(done.body && done.body.conversation);
    ok(r, up.body.asset.id, file.name || 'image', 'image', 'Kept as a picture · Master Craftsman can’t see images yet, it will be told it is attached');
  }

  async function attachDocument(file, r) {
    if (file.size > docCap) { end(r, 'failed', `Not attached: larger than ${MB(docCap)}.`); return; }
    const up = await apiUpload(`/conversations/${encodeURIComponent(cid)}/documents`, file, newKey());
    if (!up.ok || !up.body || !up.body.document) { end(r, 'failed', `Not attached: ${(up.body && up.body.message) || 'it could not be read.'}`); if (up.status === 0 || up.status >= 500) retryable(r, file); return; }
    reconcile(up.body.conversation);
    const d = up.body.document;
    // A file that was read in full is just a chip with its name; only a cut (a long file, some pages) says anything.
    ok(r, d.id, d.name || file.name, 'document', d.truncated ? (up.body.message || 'Only part of it was read') : '');
  }

  async function attach(file) {
    const isImage = IMAGE_TYPES.includes(file.type);
    const isDoc = docExt.has(extOf(file.name));
    const r = row(file, isDoc && !isImage ? 'document' : 'image');
    if (live.off) { end(r, 'failed', 'Not attached: you are Private. Nothing is sent or saved.'); return; }
    if (isImage) { await attachImage(file, r); return; }
    if (isDoc) { await attachDocument(file, r); return; }
    end(r, 'failed', `Not attached: ${extOf(file.name) || 'this kind of'} files can’t be read yet. Text, code, Markdown, CSV, JSON, HTML, PDF (with text) and Word (.docx) files can; images are kept as pictures.`);
  }

  function retryable(r, file) {
    const again = el('button', { type: 'button', class: 'att-retry' }, 'Retry');
    again.addEventListener('click', () => { r.li.remove(); trayEl.hidden = !trayEl.children.length; attach(file); });
    r.li.append(again);
  }

  function take(files) {
    const picked = Array.from(files || []);
    for (const f of picked.slice(0, 10)) attach(f);
    if (picked.length > 10) announce('Only the first 10 files were taken.');
  }

  // A message takes the ready files with it; a message that was not kept gives them back.
  tray.take = () => {
    const files = Array.from(ready.values());
    for (const li of Array.from(trayEl.querySelectorAll('[data-state="ok"]'))) li.remove();
    ready.clear();
    trayEl.hidden = !trayEl.children.length;
    return files;
  };
  tray.restore = (files) => {
    for (const f of files || []) {
      if (ready.has(f.id)) continue;
      const r = row({ name: f.name }, f.kind);
      ok(r, f.id, f.name, f.kind, f.kind === 'image' ? 'Kept as a picture · Master Craftsman can’t see images yet' : '');
    }
  };

  // Remove: this conversation lets go of the file. An image stays in the Media library; if its library hold could not
  // be released, it moves to a "not released yet" line with a Retry, and the page never says it was released. A
  // document's saved text is deleted.
  function showPending(asset, name) {
    if (!pendingList || pendingList.querySelector(`[data-pending="${asset}"]`)) return;
    const li = el('li', { class: 'fc-file is-pending', 'data-pending': asset });
    li.append(el('span', { class: 'fc-file-name' }, name || 'image'),
      el('span', { class: 'fc-pending-note small' }, 'Removed here. Its hold in the library is not released yet.'),
      el('button', { type: 'button', class: 'fc-file-remove', 'data-file-retry': asset, 'aria-label': `Retry releasing ${name || 'image'}` }, 'Retry'));
    pendingList.append(li);
    pendingList.hidden = false;
  }
  const section = $('[data-conv-files-sec]');
  if (section) section.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-file-remove], [data-file-retry], [data-doc-remove]');
    if (!b) return;
    const isDoc = b.hasAttribute('data-doc-remove');
    const retry = b.hasAttribute('data-file-retry');
    const id = b.dataset.docRemove || b.dataset.fileRemove || b.dataset.fileRetry;
    b.disabled = true;
    // Remove takes a kept file out. Retry only finishes releasing a removal that did not complete: it is a different
    // request and cannot remove anything, so a stale Retry (another tab, or the file attached again) is harmless.
    const r = isDoc
      ? await apiPost(`/conversations/${encodeURIComponent(cid)}/documents/remove`, { document_id: id, idempotency_key: newKey() })
      : await apiPost(`/conversations/${encodeURIComponent(cid)}/attachments/${retry ? 'retry' : 'remove'}`, { asset_id: id, idempotency_key: newKey() });
    if (!r.ok) {
      b.disabled = false;
      toast((r.body && r.body.message) || 'Could not change it. Nothing was changed.');
      return;
    }
    ready.delete(id);
    for (const li of Array.from(trayEl.querySelectorAll(`[data-file-id="${id}"]`))) li.remove();
    trayEl.hidden = !trayEl.children.length;
    reconcile(r.body.conversation);
    toast(r.body.message || (r.body.released ? 'Removed from this conversation. The image is still in your library.'
      : 'Removed here, but its hold in the library was not released yet. Retry.'));
  });

  button.addEventListener('click', () => { if (!button.disabled) input.click(); });
  input.addEventListener('change', () => { take(input.files); input.value = ''; });

  // Paste an image into the composer.
  const composer = $('[data-mc-input]');
  if (composer) composer.addEventListener('paste', (e) => {
    const files = Array.from((e.clipboardData && e.clipboardData.files) || []).filter((f) => f.type.startsWith('image/'));
    if (!files.length || live.off) return;
    e.preventDefault();
    take(files);
  });

  // Drop files on the chat.
  let depth = 0;
  const zone = $('[data-mc-panel]');
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  document.addEventListener('dragenter', (e) => { if (!hasFiles(e) || live.off) return; depth += 1; document.body.dataset.dropping = 'true'; });
  document.addEventListener('dragleave', (e) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) delete document.body.dataset.dropping; });
  document.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    delete document.body.dataset.dropping;
    if (!live.off && zone) take(e.dataTransfer.files);
  });

  // Private: nothing can be attached while it is on.
  const sync = () => {
    button.disabled = live.off;
    button.title = live.off ? 'Attaching is off while you are Private' : 'Attach a file: choose one, drop it here, or paste an image';
  };
  onChange(sync);
  sync();
}
