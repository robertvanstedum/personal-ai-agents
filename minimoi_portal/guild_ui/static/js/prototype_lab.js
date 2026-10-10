// Prototype Lab: the Cards / List toggle (remembered in this browser), an
// optional filter and search (shown only when there are enough prototypes to
// need them), and copy buttons for commands and paths. Reads nothing from the
// server and starts nothing: the page is drawn from the catalog.
import { $, $$, announce } from './dom.js';

const KEY = 'guild.proto.view';
const remembered = () => { try { const v = window.localStorage.getItem(KEY); return v === 'list' || v === 'cards' ? v : 'cards'; } catch (e) { return 'cards'; } };
const remember = (v) => { try { window.localStorage.setItem(KEY, v); } catch (e) { /* the choice just isn't remembered */ } };

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall back below */ }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.className = 'visually-hidden';
  document.body.append(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  area.remove();
  return ok;
}

export function initPrototypeLab(page) {
  if (page.page !== 'experiment') return;
  for (const b of $$('[data-copy]')) {
    b.addEventListener('click', async () => {
      const ok = await copyText(b.dataset.copy);
      const was = b.dataset.label || b.textContent;
      b.dataset.label = was;
      b.textContent = ok ? 'copied' : 'select it';
      announce(ok ? 'Copied' : 'Could not copy; select the text instead');
      window.setTimeout(() => { b.textContent = was; }, 1500);
    });
  }
  const views = $('[data-proto-views]');
  if (!views) return;
  let kind = '';
  let query = '';
  const apply = () => {
    for (const n of $$('[data-proto]', views)) {
      const hit = (!kind || n.dataset.kind === kind) && (!query || (n.dataset.text || '').includes(query));
      n.hidden = !hit;
    }
  };
  const setView = (v, persist) => {
    views.dataset.view = v;
    for (const b of $$('[data-proto-view]')) b.setAttribute('aria-pressed', String(b.dataset.protoView === v));
    if (persist) remember(v);
  };
  for (const b of $$('[data-proto-view]')) b.addEventListener('click', () => setView(b.dataset.protoView, true));
  for (const b of $$('[data-proto-filter]')) {
    b.addEventListener('click', () => {
      kind = b.dataset.protoFilter;
      for (const o of $$('[data-proto-filter]')) o.setAttribute('aria-pressed', String(o === b));
      apply();
    });
  }
  const search = $('[data-proto-search]');
  if (search) search.addEventListener('input', () => { query = search.value.trim().toLowerCase(); apply(); });
  setView(remembered(), false);
}
