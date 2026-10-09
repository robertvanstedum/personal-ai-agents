// Linked work (Guild 1.1 Build refinement): the one spec or issue this
// conversation is explicitly tied to. Empty until Robert attaches one; nothing
// here reads browsing history or fetches a pasted link: the server checks its
// shape (a number, or a link into this repository on GitHub) and stores it.
import { $, announce } from './dom.js';
import { apiPost } from './api.js';
import { newKey } from './actions.js';

const KIND_TEXT = {
  item: 'Build Log item',
  github_file: 'GitHub file',
  github: 'GitHub issue or pull request · title not loaded',
};
const GITHUB_TEXT = { issue: 'GitHub issue · title not loaded', pull: 'GitHub pull request · title not loaded' };
const kindText = (w) => (w.kind === 'github' && GITHUB_TEXT[w.github_kind]) || KIND_TEXT[w.kind] || 'GitHub';

export function initLinkedWork(page) {
  const root = $('[data-linked-work]');
  if (!root || !page.conversation) return;
  const cid = page.conversation.id;
  const empty = $('[data-linked-empty]', root);
  const card = $('[data-linked-card]', root);
  const form = $('[data-linked-form]', root);
  const input = $('[data-linked-input]', root);
  const link = $('[data-linked-link]', root);
  const meta = $('[data-linked-meta]', root);
  const error = $('[data-linked-error]', root);
  const emptyText = $('.fc-empty', empty);
  let busy = false;

  const fail = (text) => { error.textContent = text; error.hidden = !text; };

  function show(work) {
    page.conversation = { ...page.conversation, work_item: work || null };
    empty.hidden = !!work;
    card.hidden = !work;
    if (emptyText) emptyText.hidden = false;
    if (!work) { input.value = ''; return; }
    link.textContent = work.label;
    // A Build Log item opens with a way back to THIS conversation (?c=); a GitHub link is left exactly as saved.
    link.href = work.kind === 'item' ? `${work.href}?c=${encodeURIComponent(cid)}` : work.href;
    if (work.kind === 'item') { link.removeAttribute('target'); link.removeAttribute('rel'); }
    else { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    meta.textContent = kindText(work);
  }

  async function write(path, body, done) {
    if (busy) return;
    busy = true;
    fail('');
    const r = await apiPost(`/conversations/${encodeURIComponent(cid)}/${path}`, { ...body, idempotency_key: newKey() });
    busy = false;
    if (r.ok && r.body && r.body.conversation) {
      show(r.body.conversation.work_item);
      announce(done(r.body.conversation.work_item));
      return;
    }
    fail((r.body && r.body.message) || 'Could not change the linked work. Nothing was changed.');
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const ref = input.value.trim();
    if (!ref) { fail('Enter a number or paste a GitHub link.'); input.focus(); return; }
    write('work-item', { ref }, (w) => `Linked to ${w.label}`);
  });
  $('[data-linked-remove]', root).addEventListener('click', () => write('work-item/clear', {}, () => 'Linked work removed'));
  $('[data-linked-replace]', root).addEventListener('click', () => {
    // The form comes back beside the current link; Escape or a successful attach closes it again.
    empty.hidden = false;
    if (emptyText) emptyText.hidden = true;
    input.value = '';
    input.focus();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && page.conversation.work_item) { empty.hidden = true; fail(''); $('[data-linked-replace]', root).focus(); }
  });
}
