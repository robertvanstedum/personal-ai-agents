"""FF-02, offline: "Archive this chat" archives in place, lands on the next chat (or a new one) and offers Undo.

conversations.js runs in node with a tiny fake DOM and a recording API; no server, no model."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/conversations.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
class Node {
  constructor(tag, attrs = {}, text) {
    this.tag = tag; this.attrs = {...attrs}; this.children = []; this.parent = null; this.handlers = {}; this.hidden = false;
    this.dataset = {}; for (const [k, v] of Object.entries(attrs)) if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    this._text = text == null ? '' : String(text);
  }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  append(...xs) { for (const x of xs) { x.parent = this; this.children.push(x); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); this.parent = null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, fn) { (this.handlers[t] ||= []).push(fn); }
  fire(t) { return Promise.all((this.handlers[t] || []).map(fn => fn({target: this}))); }
  all() { return this.children.flatMap(c => [c, ...c.all()]); }
  querySelector(s) { const k = s.slice(1, -1); return this.all().find(n => k in n.attrs) || null; }
  closest(s) { const k = s.replace(/[\[\]]/g, ''); let n = this; while (n && !(k in n.attrs) && n.tag !== k) n = n.parent; return n; }
}
const body = new Node('body'), list = new Node('ol', {'data-conv-list': ''}); body.append(list);
for (const [id, title] of ROWS) {
  const row = new Node('li', {'data-conv': id}); row.append(new Node('span', {'data-conv-title': ''}, title)); list.append(row);
}
const details = new Node('details'); details.open = true; const fbArchive = new Node('button', {'data-fb-archive': ''}); details.append(fbArchive); body.append(details);
const all = () => body.all();
const sel = s => {
  if (s === '[data-conv-list] [data-conv]') return list.children.filter(n => 'data-conv' in n.attrs);
  const k = s.slice(1, -1); return all().filter(n => k in n.attrs);
};
const storage = new Map(STORAGE), posts = [], went = [], timers = [];
const ctx = vm.createContext({
  console, JSON, Date, URL, encodeURIComponent,
  $: s => sel(s)[0] || null, $$: sel, el: (t, a, x) => new Node(t, a, x), notice: m => posts.push(['notice', m]),
  apiGet: async () => ({ok: true, body: {conversations: []}}), recordMode: () => 'on_record', newKey: () => 'k',
  apiPost: async (path, b) => { posts.push([path]); return FAIL.includes(path) ? {ok: false, body: {message: 'no'}} : (path === '/conversations' ? {ok: true, body: {conversation: {id: 'c-new'}}} : {ok: true, body: {}}); },
  window: { location: { href: 'https://x/guild-next/guild/build', assign: u => went.push(u), reload: () => went.push('reload') },
            sessionStorage: { getItem: k => storage.has(k) ? storage.get(k) : null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
            setTimeout: (fn, ms) => { timers.push(ms); return timers.length; }, clearTimeout() {} },
  document: { body, addEventListener() {}, createTextNode: t => new Node('#text', {}, t) },
});
const src = fs.readFileSync(process.argv[1], 'utf8').replace(/^import [\s\S]*?;$/mg, '').replace(/^export /mg, '');
vm.runInContext(src + '\n;globalThis.__api = {initConversations, archiveChat};', ctx);
const page = {urls: {floor: '/guild-next/guild/build'}, conversation: CURRENT ? {id: CURRENT, title: 'Open chat'} : null};
(async () => {
  ctx.__api.initConversations(page);
  const toast = () => { const t = all().find(n => 'data-undo-toast' in n.attrs); return t ? {text: t.textContent, role: t.attrs.role, undo: t.querySelector('[data-undo-archive]').dataset.undoArchive} : null; };
  const out = {};
  out.toastOnLoad = toast();
  await (async () => { STEP })();
  await new Promise(r => setImmediate(r));
  Object.assign(out, {posts, went, rows: list.children.map(n => n.dataset.conv), toast: toast(), stored: storage.get('guild-archive-undo') ? JSON.parse(storage.get('guild-archive-undo')).cid : null, menuOpen: details.open, timers});
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


def run(step, rows=(("a", "Alpha"), ("b", "Bravo"), ("c", "Charlie")), current="b", storage=(), fail=()):
    script = (HARNESS.replace("ROWS", json.dumps([list(r) for r in rows])).replace("CURRENT", json.dumps(current))
              .replace("STORAGE", json.dumps([list(s) for s in storage])).replace("FAIL", json.dumps(list(fail)))
              .replace("STEP", step))
    p = subprocess.run(["node", "-e", script, str(JS)], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_archive_this_chat_from_the_menu_lands_on_the_next_chat_and_carries_undo():
    out = run("await fbArchive.fire('click');")
    assert out["posts"] == [["/conversations/b/archive"]]
    assert out["went"] == ["/guild-next/guild/build?c=c"]           # the next chat in the list
    assert out["rows"] == ["a", "c"] and out["menuOpen"] is False
    assert out["stored"] == "b"                                     # Undo rides to the next page


def test_archiving_the_last_chat_in_the_list_lands_on_the_one_above():
    out = run("await fbArchive.fire('click');", current="c")
    assert out["went"] == ["/guild-next/guild/build?c=b"]


def test_archiving_the_only_chat_opens_a_new_one():
    out = run("await fbArchive.fire('click');", rows=(("b", "Bravo"),))
    assert out["posts"] == [["/conversations/b/archive"], ["/conversations"]]
    assert out["went"] == ["/guild-next/guild/build?c=c-new"]


def test_a_refused_archive_changes_nothing():
    out = run("await fbArchive.fire('click');", fail=("/conversations/b/archive",))
    assert out["went"] == [] and out["rows"] == ["a", "b", "c"] and out["stored"] is None and out["toast"] is None


def test_archiving_another_chat_stays_here_and_shows_undo():
    out = run("await ctx.__api.archiveChat('a');")
    assert out["went"] == [] and out["rows"] == ["b", "c"]
    assert out["toast"] == {"text": "Archived · Undo", "role": "status", "undo": "a"}
    assert out["timers"] and out["timers"][-1] >= 5000               # shown for a few seconds


def test_undo_after_landing_restores_and_reopens_the_archived_chat():
    # The stored entry carries the time it was written; it is built inside node so it is fresh.
    out = run("""storage.set('guild-archive-undo', JSON.stringify({cid:'b', title:'Bravo', at: Date.now()}));
ctx.__api.initConversations(page);
const btn = all().find(n => 'data-undo-archive' in n.attrs); await btn.fire('click');""", current="c")
    assert out["posts"] == [["/conversations/b/restore"]]
    assert out["went"] == ["/guild-next/guild/build?c=b"]
    assert out["stored"] is None                                    # read once


def test_the_undo_offer_shows_on_the_next_page_once_and_expires():
    fresh = run("", current="c", storage=(("guild-archive-undo", json.dumps({"cid": "b", "title": "Bravo", "at": 9e15})),))
    assert fresh["toastOnLoad"] is None                              # a clock in the future is not trusted as fresh
    old = run("", current="c", storage=(("guild-archive-undo", json.dumps({"cid": "b", "title": "Bravo", "at": 1})),))
    assert old["toastOnLoad"] is None and old["stored"] is None
