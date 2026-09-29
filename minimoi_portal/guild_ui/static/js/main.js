// Bootstrap: read the page's JSON block, then bind the page's modules.
import { $, notice, localizeTimes } from './dom.js';
import { configure, takeNotices } from './state.js';
import { configureApi } from './api.js';
import { initConversation } from './conversation.js';
import { initFloorState } from './floor.js';
import { initBench } from './bench.js';
import { initQueue } from './queue.js';
import { initOperate } from './operate.js';
import { initPostits } from './postits.js';
import { continueFromItem } from './continue.js';
import { initZones } from './zones.js';
import { initFloorLayout } from './floorlayout.js';

const page = JSON.parse(document.getElementById('guild-page').textContent);
configure(page.storage_ns);
configureApi(page);
initConversation(page);
initFloorState(page);
initFloorLayout(page);
if ($('[data-bench]')) initBench(page);
if (page.page === 'queue' || page.page === 'item') initQueue(page);
if (page.page === 'operate') initOperate();
initZones(page.floor);
initPostits(page);
continueFromItem(page);

const openBtn = $('[data-phone-open]');
if (openBtn && document.body.dataset.pageOpen === 'true') {
  openBtn.setAttribute('aria-expanded', 'true');
  openBtn.textContent = `Hide ${page.area} ▴`;
}
if (openBtn) openBtn.addEventListener('click', () => {
  const on = document.body.dataset.pageOpen !== 'true';
  document.body.dataset.pageOpen = String(on);
  openBtn.setAttribute('aria-expanded', String(on));
  openBtn.textContent = on ? `Hide ${page.area} ▴` : `Open ${page.area} ▸`;
});

localizeTimes();
for (const n of takeNotices()) notice(n);
document.body.dataset.ready = 'true';
