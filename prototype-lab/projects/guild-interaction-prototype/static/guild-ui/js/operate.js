// Operate: one selected tile; drill-down for Capabilities; next steps open proposals.
import { $, $$ } from './dom.js';
import { propose } from './proposals.js';
import { world } from './world.js';

export function initOperate(page, openPanel, refuse) {
  const tiles = $$('[data-tile]');
  function select(id) {
    for (const t of tiles) {
      const on = t.dataset.tile === id;
      t.setAttribute('aria-pressed', String(on));
      const sel = t.querySelector('.tile-sel');
      if (sel && !on) sel.remove();
      if (on && !sel) { const s = document.createElement('span'); s.className = 'tile-sel'; s.textContent = ' · selected'; t.querySelector('.tile-label').append(s); }
    }
    const drills = $$('[data-drill-for]');
    const has = drills.some((d) => d.dataset.drillFor === id);
    for (const d of drills) d.hidden = d.dataset.drillFor !== id;
    const none = $('[data-drill-none]');
    none.hidden = has;
    const label = tiles.find((t) => t.dataset.tile === id)?.dataset.tileLabel || id;
    $('[data-drill-none-label]').textContent = label;
    document.body.dataset.contextItem = id === 'capabilities' ? page.item : label;
    document.dispatchEvent(new CustomEvent('guild:context'));
  }
  for (const t of tiles) t.addEventListener('click', () => select(t.dataset.tile));
  for (const b of $$('[data-propose]')) {
    b.addEventListener('click', () => {
      if (!world.page.prototype) return;
      openPanel();
      const res = propose(b.dataset.propose);
      if (res.refused) refuse(res.refused);
    });
  }
  select(page.layout && tiles.find((t) => t.getAttribute('aria-pressed') === 'true')?.dataset.tile || 'capabilities');
}
