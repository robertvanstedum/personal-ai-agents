// The conversation, real mode. Master Craftsman is off: nothing here produces
// a reply. The thread shows the opening briefing and platform lines (explain
// cards, Save results), labelled as platform rules and kept only on this page.
// Notes arrive with the next deliverable; until then Send stays disabled.
import { $, clone, slot, setSlot, el, announce, localTime } from './dom.js';
import { live, setOff, onChange } from './state.js';

let page, panel, thread, pill;
const inPage = () => document.body.dataset.page === 'floor';
let mode = 'pill';

function applyMode() {
  if (inPage()) {
    panel.dataset.mode = 'inpage';
    document.body.dataset.mcMode = 'inpage';
    return;
  }
  panel.dataset.mode = mode;
  document.body.dataset.mcMode = mode;
  pill.setAttribute('aria-expanded', String(mode !== 'pill'));
  const dock = $('[data-mc-dock]');
  dock.setAttribute('aria-pressed', String(mode === 'docked'));
  dock.textContent = mode === 'docked' ? 'Undock' : 'Dock';
}

function setMode(next) { mode = next; applyMode(); }

function follow() { thread.scrollTop = thread.scrollHeight; }

export function addPlatform(label, text) {
  const li = clone('tpl-platform');
  setSlot(li, 'label', label);
  setSlot(li, 'when', localTime(new Date().toISOString()));
  setSlot(li, 'text', text);
  thread.append(li);
  follow();
  announce(text);
  return li;
}

export function explain(light) {
  const li = clone('tpl-explain');
  setSlot(li, 'label', page.rules_label);
  setSlot(li, 'title', light.label);
  setSlot(li, 'word', light.word);
  setSlot(li, 'reason', light.reason);
  setSlot(li, 'source', light.source_mark);
  setSlot(li, 'observed', light.observed_at ? localTime(light.observed_at) : 'not observed');
  setSlot(li, 'evidence', light.evidence || '—');
  const ul = slot(li, 'detail');
  for (const line of light.detail || []) ul.append(el('li', {}, line));
  li.dataset.explain = light.id;
  thread.append(li);
  follow();
  announce(`${light.label}: ${light.word}, ${light.reason}`);
  if (!inPage() && mode === 'pill') setMode('floating');
}

export function setBriefing(briefing) {
  if (!briefing) return;
  const text = $('[data-briefing-text]');
  // The server writes the time in UTC; show it in local time like every other time here.
  if (text) text.textContent = briefing.text.replace(/^As of [^·]+·/, `As of ${localTime(briefing.observed_at)} ·`);
}

export function refuse(text) {
  const r = $('[data-mc-refusal]');
  r.hidden = false;
  r.textContent = text;
  announce(text);
}

function renderRecord() {
  const btn = $('[data-mc-record]');
  btn.setAttribute('aria-pressed', String(live.off));
  btn.textContent = live.off ? 'Back on the record' : 'Off the record';
  const ctx = $('[data-mc-context]');
  const area = document.body.dataset.area || 'Guild';
  const item = document.body.dataset.contextItem || '';
  ctx.textContent = `Context: ${area}${item ? ` · ${item}` : ''} · ${live.off ? `off the record since ${localTime(live.offSince)}` : 'on the record'}`;
  const r = $('[data-mc-refusal]');
  if (live.off) { r.hidden = false; r.textContent = page.off_record_text; } else { r.hidden = true; r.textContent = ''; }
  document.body.dataset.offRecord = String(live.off);
}

export function initConversation(p) {
  page = p;
  panel = $('[data-mc-panel]'); thread = $('[data-mc-thread]'); pill = $('[data-mc-pill]');
  pill.addEventListener('click', () => { setMode('floating'); const i = $('[data-mc-input]'); if (i) i.focus(); });
  $('[data-mc-min]').addEventListener('click', () => { setMode('pill'); pill.focus(); });
  $('[data-mc-dock]').addEventListener('click', () => setMode(mode === 'docked' ? 'floating' : 'docked'));
  panel.addEventListener('keydown', (e) => {
    if (!inPage() && e.key === 'Escape' && mode === 'floating') { setMode('pill'); pill.focus(); }
  });
  const form = $('[data-mc-composer]');
  const input = $('[data-mc-input]');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (live.off) { refuse(page.off_record_text); return; }
    refuse(page.floor.notes.text);
  });
  $('[data-mc-record]').addEventListener('click', () => {
    const wasOff = live.off;
    setOff(!live.off);
    if (wasOff) addPlatform('Guild platform', 'Back on the record · nothing from the off-the-record stretch was kept');
  });
  const typeBtn = $('[data-mc-type]');
  typeBtn.addEventListener('click', () => {
    document.body.dataset.typing = 'true';
    input.scrollIntoView({ block: 'nearest' });
    input.focus();
  });
  onChange(renderRecord);
  setBriefing(page.floor && page.floor.briefing);
  applyMode();
  renderRecord();
  follow();
}


