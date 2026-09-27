// Tiny DOM helpers. Text is always set with textContent; never innerHTML with data.
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function clone(templateId) {
  const tpl = document.getElementById(templateId);
  return tpl.content.firstElementChild.cloneNode(true);
}

export function slot(node, name) {
  return node.querySelector(`[data-slot="${name}"]`);
}

export function setSlot(node, name, text) {
  const target = slot(node, name);
  if (target) target.textContent = text == null ? '' : String(text);
  return target;
}

export function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    node.setAttribute(k, v === true ? '' : String(v));
  }
  if (text != null) node.textContent = String(text);
  return node;
}

export function announce(text) {
  const live = document.querySelector('[data-mc-announcer]');
  if (live) { live.textContent = ''; window.setTimeout(() => { live.textContent = text; }, 30); }
}

export function notice(text) {
  const box = document.querySelector('[data-notice]');
  if (!box) return;
  box.textContent = box.hidden ? text : `${box.textContent} ${text}`;
  box.hidden = false;
}

// Local wall-clock time for an ISO timestamp (the server sends UTC).
export function localTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function localizeTimes(root = document) {
  for (const node of root.querySelectorAll('[data-iso]')) node.textContent = localTime(node.dataset.iso);
}

// The light's shape, built as SVG nodes (colour is never the only signal).
const SVG = 'http://www.w3.org/2000/svg';
export function shapeSvg(shape) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('class', 'lshape');
  svg.setAttribute('data-shape', shape);
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const add = (tag, attrs, text) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text) n.textContent = text;
    svg.append(n);
  };
  if (shape === 'circle') add('circle', { cx: 8, cy: 8, r: 6.5 });
  else if (shape === 'triangle') add('polygon', { points: '8,1.2 15.2,14.6 0.8,14.6' });
  else if (shape === 'square') add('rect', { x: 1.8, y: 1.8, width: 12.4, height: 12.4 });
  else { add('circle', { cx: 8, cy: 8, r: 6.2, class: 'lshape-ring' }); add('text', { x: 8, y: 11.6, 'text-anchor': 'middle', class: 'lshape-q' }, '?'); }
  return svg;
}
