// Operate: one selected tile and its detail. Rules only; no actions here yet.
import { $$ } from './dom.js';

export function initOperate() {
  const tiles = $$('[data-tile]');
  function select(id) {
    for (const t of tiles) t.setAttribute('aria-pressed', String(t.dataset.tile === id));
    for (const d of $$('[data-drill-for]')) d.hidden = d.dataset.drillFor !== id;
    const url = new URL(window.location.href);
    url.searchParams.set('tile', id);
    window.history.replaceState(null, '', url);
  }
  for (const t of tiles) t.addEventListener('click', () => select(t.dataset.tile));
}
