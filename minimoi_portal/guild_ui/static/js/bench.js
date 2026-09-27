// Workbench arrangement: Robert arranges (order, fold, focus). A view setting,
// kept per device in this browser (decision 5); validated against the layout.
import { $, $$, announce, notice } from './dom.js';
import { load, save, isObj, isStrArr } from './state.js';

const KEY = 'bench.v2';
let cfg, st, ids;
let merged = false;

function defaults() {
  return { layout_version: cfg.version, order: [...cfg.bench.default_order],
    folded: [...cfg.bench.default_folded], focus: cfg.bench.default_focus };
}

function validate(v) {
  if (!isObj(v) || typeof v.layout_version !== 'number' || !isStrArr(v.order) || !isStrArr(v.folded) ||
      !(v.focus === null || typeof v.focus === 'string')) return null;
  const order = v.order.filter((id) => ids.includes(id));
  for (const id of cfg.bench.default_order) {
    if (!order.includes(id)) order.splice(Math.min(cfg.bench.default_order.indexOf(id), order.length), 0, id);
  }
  if (v.layout_version !== cfg.version || order.length !== v.order.length) merged = true;
  return { layout_version: cfg.version, order: [...new Set(order)],
    folded: v.folded.filter((id) => ids.includes(id)),
    focus: ids.includes(v.focus) ? v.focus : null };
}

function persist() { save(KEY, st); }
function panels() { return $$('[data-bench] > [data-panel]'); }
function panelEl(id) { return $(`[data-bench] > [data-panel="${id}"]`); }
function titleOf(id) { return cfg.bench.panels.find((p) => p.id === id)?.title || id; }

function applyBench() {
  const bench = $('[data-bench]');
  const order = st.focus ? [st.focus, ...st.order.filter((x) => x !== st.focus)] : st.order;
  for (const id of order) { const p = panelEl(id); if (p) bench.append(p); }
  const foldedNames = [];
  for (const p of panels()) {
    const id = p.dataset.panel;
    const folded = st.folded.includes(id);
    const focused = st.focus === id;
    const rows = $$('[data-row]', $('[data-panel-body]', p)).filter((r) => !r.hidden);
    // A panel you can add to is never "empty": folding it away would hide its
    // Add box, and there would be no way to add the first post-it here.
    const empty = rows.length === 0 && !$('.zone-next', p) && !$('[data-postit-add]', p);
    p.dataset.folded = String(folded);
    p.dataset.focused = String(focused);
    p.dataset.empty = String(empty && !folded);
    $('[data-empty-reason]', p).hidden = !(empty && !folded);
    $('[data-focus-label]', p).hidden = !focused;
    const fold = $('[data-act="fold"]', p);
    fold.setAttribute('aria-expanded', String(!folded));
    fold.textContent = folded ? 'Unfold' : 'Fold';
    fold.setAttribute('aria-label', `${folded ? 'Unfold' : 'Fold'} ${titleOf(id)}`);
    const focus = $('[data-act="focus"]', p);
    focus.setAttribute('aria-pressed', String(focused));
    focus.textContent = focused ? '☆ In focus' : '☆ Focus';
    const idx = st.order.indexOf(id);
    $('[data-act="up"]', p).disabled = idx <= 0;
    $('[data-act="down"]', p).disabled = idx === st.order.length - 1;
    $('[data-panel-state]', p).textContent = folded ? 'Folded by you' : (empty ? 'nothing to show' : '');
    if (folded) foldedNames.push(titleOf(id));
  }
  $('[data-folded-summary]').textContent = foldedNames.length ? `Folded by you: ${foldedNames.join(', ')}` : 'nothing folded';
}

function move(id, delta) {
  const i = st.order.indexOf(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= st.order.length) return;
  st.order.splice(i, 1);
  st.order.splice(j, 0, id);
  persist(); applyBench();
  announce(`${titleOf(id)} moved ${delta < 0 ? 'up' : 'down'}`);
}

export function initBench(page) {
  cfg = page.layout;
  ids = cfg.bench.panels.map((p) => p.id);
  st = load(KEY, validate, defaults, 'arrangement');
  if (merged) { notice('Layout changed; your arrangement was merged with the default.'); persist(); }
  const bench = $('[data-bench]');
  bench.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const id = b.closest('[data-panel]').dataset.panel;
    const act = b.dataset.act;
    if (act === 'up' || act === 'down') {
      move(id, act === 'up' ? -1 : 1);
      const again = $(`[data-panel="${id}"] [data-act="${act}"]`);
      (again && !again.disabled ? again : $(`[data-panel="${id}"] [data-act="fold"]`)).focus();
      return;
    }
    if (act === 'fold') st.folded = st.folded.includes(id) ? st.folded.filter((x) => x !== id) : [...st.folded, id];
    else if (act === 'focus') st.focus = st.focus === id ? null : id;
    persist(); applyBench();
    $(`[data-panel="${id}"] [data-act="${act}"]`).focus();
  });
  let dragging = null;
  bench.addEventListener('dragstart', (e) => {
    const h = e.target.closest('[data-drag-handle]');
    if (!h) return;
    dragging = h.closest('[data-panel]').dataset.panel;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragging);
  });
  bench.addEventListener('dragover', (e) => {
    const p = e.target.closest('[data-panel]');
    if (!dragging || !p) return;
    e.preventDefault();
    for (const x of panels()) x.dataset.dragover = String(x === p && x.dataset.panel !== dragging);
  });
  bench.addEventListener('drop', (e) => {
    const p = e.target.closest('[data-panel]');
    e.preventDefault();
    const src = dragging || e.dataTransfer.getData('text/plain');
    dragging = null;
    for (const x of panels()) x.dataset.dragover = 'false';
    if (!p || !src || p.dataset.panel === src || !st.order.includes(src)) return;
    st.order = st.order.filter((x) => x !== src);
    st.order.splice(st.order.indexOf(p.dataset.panel), 0, src);
    persist(); applyBench();
    announce(`${titleOf(src)} moved`);
  });
  bench.addEventListener('dragend', () => { dragging = null; for (const x of panels()) x.dataset.dragover = 'false'; });
  $('[data-bench-reset]').addEventListener('click', () => {
    st = defaults(); persist(); applyBench();
    announce('Arrangement reset to the default');
  });
  applyBench();
}
