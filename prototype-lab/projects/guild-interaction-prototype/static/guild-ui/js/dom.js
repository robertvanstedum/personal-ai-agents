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
