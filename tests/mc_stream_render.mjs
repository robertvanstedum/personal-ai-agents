// Streamed MC replies (#402, 26 Oct prod test): a render event carries the whole answer so far, and replacing it all
// twice a second rebuilt every settled paragraph and table and snapped an opened Details shut. Runs the real
// morphChildren from conversation.js on stand-in nodes: settled blocks stay the same objects, only the first changed
// block onward is replaced, and a Details fold is kept with its open state while its body is brought up to date.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/conversation.js', import.meta.url), 'utf8');
const start = source.indexOf('function sameNode(a, b) {');
const end = source.indexOf('function applyRendered(');
assert.ok(start > 0 && end > start, 'sameNode and morphChildren present in conversation.js');
const ctx = {};
vm.runInNewContext(`${source.slice(start, end)}\nthis.morphChildren = morphChildren;`, ctx);
const { morphChildren } = ctx;

// Stand-in nodes: an element's outerHTML is derived from its tag and children, as the browser's is.
function node(props) {
  const n = { parent: null, childNodes: [], ...props };
  n.remove = () => { const s = n.parent.childNodes; s.splice(s.indexOf(n), 1); n.parent = null; };
  n.append = (c) => { if (c.parent) c.remove(); c.parent = n; n.childNodes.push(c); };
  return n;
}
const text = (t) => node({ nodeType: 3, nodeName: '#text', textContent: t });
function el(tag, ...kids) {
  const n = node({ nodeType: 1, nodeName: tag.toUpperCase() });
  Object.defineProperty(n, 'outerHTML', { get: () => `<${tag}>${n.childNodes.map((c) => (c.nodeType === 1 ? c.outerHTML : c.textContent)).join('')}</${tag}>` });
  for (const k of kids) n.append(typeof k === 'string' ? text(k) : k);
  return n;
}
const render = (...blocks) => blocks;               // what the template parse yields: a list of top-level nodes
let passed = 0;

{ // the first render fills an empty message
  const box = el('div');
  morphChildren(box, render(el('p', 'Ja'), text('\n')));
  assert.equal(box.outerHTML, '<div><p>Ja</p>\n</div>');
  passed++;
}

{ // a growing last paragraph: the settled heading and table are untouched, only the last block changes
  const box = el('div');
  morphChildren(box, render(el('h2', 'Scenarios'), el('table', el('tr', el('td', 'a'))), el('p', 'Ask me')));
  const [h2, table] = box.childNodes;
  morphChildren(box, render(el('h2', 'Scenarios'), el('table', el('tr', el('td', 'a'))), el('p', 'Ask me in plain language')));
  assert.equal(box.childNodes[0], h2, 'heading kept');
  assert.equal(box.childNodes[1], table, 'table kept');
  assert.equal(box.childNodes[2].outerHTML, '<p>Ask me in plain language</p>');
  passed++;
}

{ // a block that changed in the middle is replaced with everything after it, never left stale
  const box = el('div');
  morphChildren(box, render(el('p', 'one'), el('ul', el('li', 'a')), el('p', 'three')));
  const first = box.childNodes[0];
  morphChildren(box, render(el('p', 'one'), el('ul', el('li', 'a'), el('li', 'b')), el('p', 'three'), el('p', 'four')));
  assert.equal(box.childNodes[0], first);
  assert.equal(box.outerHTML, '<div><p>one</p><ul><li>a</li><li>b</li></ul><p>three</p><p>four</p></div>');
  passed++;
}

{ // an opened Details stays the same element (so it stays open) while its body grows
  const box = el('div');
  morphChildren(box, render(el('p', 'Answer'), el('details', el('summary', 'Details'), el('p', 'Evidence'))));
  const details = box.childNodes[1];
  details.open = true;                               // Robert opens it while MC is still writing
  morphChildren(box, render(el('p', 'Answer'), el('details', el('summary', 'Details'), el('p', 'Evidence'), el('ul', el('li', 'read')))));
  assert.equal(box.childNodes[1], details, 'Details kept');
  assert.equal(details.open, true, 'still open');
  assert.equal(details.outerHTML, '<details><summary>Details</summary><p>Evidence</p><ul><li>read</li></ul></details>');
  passed++;
}

{ // fewer blocks than shown (the server re-split a block): the extras go
  const box = el('div');
  morphChildren(box, render(el('p', 'a'), el('p', 'b')));
  morphChildren(box, render(el('p', 'a b')));
  assert.equal(box.outerHTML, '<div><p>a b</p></div>');
  passed++;
}

console.log(`${passed} mc stream render fixtures passed`);
