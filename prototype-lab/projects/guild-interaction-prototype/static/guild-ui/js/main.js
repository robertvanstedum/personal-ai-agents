// Bootstrap: read the page's JSON config, restore state, bind page modules.
import { $, $$, el, notice } from './dom.js';
import { configure, takeNotices, clearKeys, load, save, isObj } from './state.js';
import { initWorld, world, onChange, applyShowWhen, nowLabel, changed } from './world.js';
import { receiptsFor, receiptLabel } from './proposals.js';
import { returnMonday } from './scenario.js';
import { initConversation, openPanel } from './conversation.js';
import { initBench } from './bench.js';
import { initOperate } from './operate.js';
import { initQueue } from './queue.js';
import { initFloor } from './floor.js';
import { clearEvidenceCookie } from './capture.js';

const page = JSON.parse(document.getElementById('guild-page').textContent);
configure(page.storage_ns);
initWorld(page);

function refuse(text) {
  const r = $('[data-mc-refusal]');
  if (r) { r.hidden = false; r.textContent = text; }
}

function renderWorkItems() {
  for (const ul of $$('[data-receipts-for]')) {
    const list = receiptsFor(ul.dataset.receiptsFor);
    ul.replaceChildren();
    if (!list.length) { ul.append(el('li', { class: 'small' }, 'No receipts yet.')); continue; }
    for (const r of list) ul.append(el('li', { 'data-receipt': r.id }, `${receiptLabel(r)} · ${r.when}`));
  }
  const card = $('[data-decision-card]');
  if (card) {
    const d = world.overlay.decision;
    const mon = world.clock.day === 'mon';
    card.hidden = !d;
    card.id = 'decision';
    const none = $('[data-decision-none]');
    none.hidden = !!d || !mon;
    none.textContent = world.scenario.monday.no_decision || '';
    if (d) {
      $('[data-dc-when]', card).textContent = d.when;
      $('[data-dc-receipt]', card).textContent = d.receipt;
      $('[data-dc-session]', card).textContent = d.session;
      $('[data-dc-text]', card).textContent = d.text;
      $('[data-dc-owners]', card).textContent = d.owners;
      const steps = $('[data-dc-steps]', card);
      steps.replaceChildren();
      for (const s of (mon ? world.scenario.monday.steps : world.scenario.monday.sat_steps) || []) {
        const li = el('li', { 'data-step': s.state });
        li.append(el('span', { class: 'tag' }, s.state), document.createTextNode(` ${s.text}`));
        steps.append(li);
      }
      const p = world.conv.participants;
      const joined = ['Robert', 'MC', ...(p.claude.state === 'joined' ? ['Claude'] : [])];
      let line = `Participants: ${joined.join(', ')}`;
      if (p.codex.state === 'invited') line += ' · Codex never joined';
      else if (p.codex.state === 'none') line += ' · Codex was not invited';
      $('[data-dc-participants]', card).textContent = line;
      $('[data-dc-note]', card).textContent = mon ? world.scenario.monday.note : '';
    }
  }
  for (const t of $$('[data-decision-text]')) t.textContent = world.overlay.decision ? world.overlay.decision.text : '';
  const people = $('[data-s023-people]');
  if (people) {
    const p = world.conv.participants;
    const parts = ['Robert', 'MC'];
    if (p.claude.state === 'joined') parts.push('Claude');
    let s = parts.join(', ');
    const invitedOnly = ['claude', 'codex'].filter((k) => p[k].state === 'invited').map((k) => (k === 'claude' ? 'Claude' : 'Codex'));
    if (invitedOnly.length) s += ` · invited, not joined: ${invitedOnly.join(', ')}`;
    people.textContent = s;
  }
  for (const c of $$('[data-clock-label]')) c.textContent = nowLabel();
  const needsCount = $('[data-needs-count]');
  if (needsCount) needsCount.textContent = String($$('[data-needs-row]').filter((r) => !r.hidden).length);
}

function initCommon() {
  // continue where you were
  const okPath = (p) => typeof p === 'string' && p.startsWith(`${page.base}/guild`);
  const nav = load('nav.v1', (v) => (isObj(v) && okPath(v.href) && (v.detail_href == null || okPath(v.detail_href)) ? v : null),
    () => ({ href: page.urls.floor, label: 'Shop floor' }), 'last area');
  const cont = $('[data-continue]');
  if (cont) { cont.href = nav.href; $('[data-continue-label]').textContent = nav.label; }
  if (page.page !== 'landing') {
    const label = page.page === 'floor' ? 'Shop floor' : page.page === 'bench' ? 'Build · workbench'
      : page.page === 'queue' ? 'Build Queue' : page.page === 'item' ? `Build Queue · #${page.item_id}` : page.area;
    const href = window.location.pathname + window.location.search.replace(/[?&]clock=mon/, '');
    const next = { href, label, detail_href: nav.detail_href, detail_label: nav.detail_label };
    if (page.page !== 'floor') { next.detail_href = href; next.detail_label = label; }
    save('nav.v1', next);
  }
  if (page.page === 'floor') initFloor(nav);
  // phone: open the page content
  const openBtn = $('[data-phone-open]');
  if (openBtn) openBtn.addEventListener('click', () => {
    const on = document.body.dataset.pageOpen !== 'true';
    document.body.dataset.pageOpen = String(on);
    openBtn.setAttribute('aria-expanded', String(on));
    openBtn.textContent = on ? `Hide ${page.area} ▴` : `Open ${page.area} ▸`;
  });
  // prototype controls
  const reset = $('[data-proto-reset]');
  if (reset) {
    const confirmBox = $('[data-proto-reset-confirm]');
    reset.addEventListener('click', () => { confirmBox.hidden = false; reset.hidden = true; $('[data-proto-reset-yes]').focus(); });
    $('[data-proto-reset-no]').addEventListener('click', () => { confirmBox.hidden = true; reset.hidden = false; reset.focus(); });
    $('[data-proto-reset-yes]').addEventListener('click', async () => {
      let keys = page.storage_keys;
      try {
        const res = await fetch(page.urls.reset, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        if (res.ok) keys = (await res.json()).storage_keys || keys;
      } catch (e) { /* clear locally anyway */ }
      clearKeys(keys);
      clearEvidenceCookie(page.capture);
      window.location.reload();
    });
  }
  const monday = $('[data-return-monday]');
  if (monday) {
    monday.addEventListener('click', () => { returnMonday(); });
    onChange(() => { if (world.clock.day === 'mon') { monday.dataset.locked = 'true'; monday.disabled = true; monday.textContent = 'Monday · simulated clock'; } });
  }
  if (page.prototype && ['bench', 'floor'].includes(page.page) && new URLSearchParams(window.location.search).get('clock') === 'mon') returnMonday();
  if (page.page === 'item') {
    const discuss = $('[data-discuss]');
    if (discuss) discuss.addEventListener('click', openPanel);
  }
}

initConversation();
if ($('[data-bench]')) initBench(page);
if (page.page === 'operate') initOperate(page, openPanel, refuse);
if (page.page === 'queue' || page.page === 'item') initQueue(refuse);
initCommon();
onChange(() => { applyShowWhen(); renderWorkItems(); });
applyShowWhen();
renderWorkItems();
changed();
for (const n of takeNotices()) notice(n);
document.body.dataset.ready = 'true';
