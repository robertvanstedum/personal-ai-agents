import assert from 'node:assert/strict';
import { longPasteFile } from '../minimoi_portal/guild_ui/static/js/paste_file.js';

const opts = { noteMax: 2000, docCap: 5 * 1024 * 1024, docChars: 30000, now: new Date('2026-10-25T14:05:09.123Z') };

// At or under the limit: the box takes it as before.
assert.equal(longPasteFile('x'.repeat(2000), opts), null);
assert.equal(longPasteFile('', opts), null);

// Over the limit: a named .txt file with the exact text, and a plain note.
{
  const text = 'line\n'.repeat(1000);                     // 5000 characters
  const r = longPasteFile(text, opts);
  assert.equal(r.file.name, 'pasted-text-20261025T140509Z.txt');
  assert.equal(r.file.type, 'text/plain');
  assert.equal(await r.file.text(), text);
  assert.equal(r.chars, 5000);
  assert.equal(r.kept, 5000);
  assert.match(r.note, /5,000-character paste was attached as a text file/);
}

// Counted in characters, not UTF-16 units: 2000 emoji is at the limit, 2001 is over.
assert.equal(longPasteFile('😀'.repeat(2000), opts), null);
assert.ok(longPasteFile('😀'.repeat(2001), opts));

// Longer than the server keeps: says so instead of pretending it is all read.
{
  const r = longPasteFile('a'.repeat(45000), opts);
  assert.equal(r.kept, 30000);
  assert.match(r.note, /Only the first 30,000 characters are kept/);
}

// Private is on (nothing can be attached), or larger than the upload limit: stays in the box.
assert.equal(longPasteFile('a'.repeat(5000), { ...opts, off: true }), null);
assert.equal(longPasteFile('a'.repeat(5000), { ...opts, docCap: 100 }), null);

console.log('guild_paste_file: ok');
