// Bootstrap: read the page's JSON block, then bind the page's modules.
import { $, notice, localizeTimes } from './dom.js';
import { configure, takeNotices } from './state.js';
import { configureApi } from './api.js';
import { initConversation } from './conversation.js';
import { initFloorState } from './floor.js';
import { initQueue } from './queue.js';
import { initBuildLog } from './build_log.js';
import { initBoard } from './board.js';
import { initMedia } from './media.js';
import { initRooms } from './rooms.js';
import { initRoomsArt } from './rooms_art.js';
import { initOperate } from './operate.js';
import { initPostits } from './postits.js';
import { continueFromItem } from './continue.js';
import { initZones } from './zones.js';
import { initFloorLayout } from './floorlayout.js';
import { initConversations } from './conversations.js';
import { initWorkshop } from './workshop.js';
import { initWorkshopTopics } from './workshop_topic.js';
import './nav.js';
import { initSelection } from './selection.js';
import { initLinkedWork } from './linked_work.js';
import { initAttachments } from './attachments.js';
import { initPrototypeLab } from './prototype_lab.js';

const page = JSON.parse(document.getElementById('guild-page').textContent);
configure(page.storage_ns);
configureApi(page);
initConversation(page);
initFloorState(page);
initFloorLayout(page);
initConversations(page);
initSelection(page);
if (page.page === 'floor') { initLinkedWork(page); initAttachments(page); }
initPrototypeLab(page);
initWorkshop(page);
initWorkshopTopics(page);
// The Workbench script is a reserve asset, excluded from the release image: it is only ever requested when its
// page is on screen (never in Guild 1.1, where that address redirects), so a missing file cannot break this module.
if ($('[data-bench]')) import('./bench.js').then((m) => m.initBench(page));
if (page.page === 'queue' || page.page === 'item') initQueue(page);
if (page.page === 'build_log') initBuildLog(page);
if (page.page === 'board') initBoard(page);
if (page.page === 'media') initMedia(page);
if (page.page === 'rooms') { initRooms(page); initRoomsArt(page); }
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

document.querySelectorAll("[data-document-print]").forEach(button => button.addEventListener("click", () => window.print()));

import './wiki.js';

import './library.js';
