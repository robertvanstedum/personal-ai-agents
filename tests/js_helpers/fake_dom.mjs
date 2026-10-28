// A very small DOM for the MC voice node tests: elements with attributes, children, text, events, and querySelector for compound
// attribute selectors only ([a], [a="v"], chained). Enough for mc_voice_direct.js's thread code; not a browser.
class Node {
  constructor(tag) { this.tagName = String(tag).toUpperCase(); this.attrs = new Map(); this.children = []; this.parent = null;
    this.ownText = ''; this.hidden = false; this.disabled = false; this.value = ''; this.listeners = {}; }
  setAttribute(k, v) { this.attrs.set(k, String(v)); if (k === 'hidden') this.hidden = true; }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  get className() { return this.getAttribute('class') || ''; }
  set className(v) { this.setAttribute('class', v); }
  get textContent() { return this.ownText + this.children.map((c) => (typeof c === 'string' ? c : c.textContent)).join(''); }
  set textContent(v) { this.ownText = String(v ?? ''); for (const c of this.children) if (typeof c !== 'string') c.parent = null; this.children = []; }
  append(...nodes) { for (const n of nodes) this.insertBefore(n, null); }
  insertBefore(node, ref) {
    if (typeof node !== 'string') { node.remove(); node.parent = this; }
    const at = ref ? this.children.indexOf(ref) : -1;
    if (at < 0) this.children.push(node); else this.children.splice(at, 0, node);
    return node;
  }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; } }
  get elements() { return this.children.filter((c) => typeof c !== 'string'); }
  *walk() { for (const c of this.elements) { yield c; yield* c.walk(); } }
  matches(selector) {
    const parts = selector.match(/\[[^\]]+\]/g);
    if (!parts || parts.join('') !== selector.trim()) throw new Error(`fake DOM: unsupported selector ${selector}`);
    return parts.every((p) => {
      const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(p);
      if (!m) throw new Error(`fake DOM: unsupported selector ${selector}`);
      return this.attrs.has(m[1]) && (m[2] === undefined || this.attrs.get(m[1]) === m[2]);
    });
  }
  querySelector(selector) { for (const n of this.walk()) if (n.matches(selector)) return n; return null; }
  querySelectorAll(selector) { return Array.from(this.walk()).filter((n) => n.matches(selector)); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) { for (const fn of this.listeners[type] || []) fn({ target: this, preventDefault() {}, ...event }); }
  click() { if (!this.disabled) this.dispatch('click'); }
  focus() {}
}

export function fakeDocument() {
  return { createElement: (tag) => new Node(tag), createTextNode: (t) => String(t), body: new Node('body') };
}
