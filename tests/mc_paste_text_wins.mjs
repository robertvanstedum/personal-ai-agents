// Pasting into MC chat (#407, Robert's prod test 26 Oct): text copied from Word arrived as an image MC cannot read, and
// the text was dropped, because Word puts a picture of the selection on the clipboard beside its text. Runs the real
// paste handler from attachments.js with stand-ins: text wins whenever there is text; a picture alone is still taken.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/attachments.js', import.meta.url), 'utf8');
const head = "composer.addEventListener('paste', (e) => {";
const start = source.indexOf(head);
const end = source.indexOf('\n  });', start);
assert.ok(start > 0 && end > start, 'paste handler present in attachments.js');
const handler = source.slice(start + head.length - '(e) => {'.length, end + '\n  }'.length);

function paste({ text = '', images = 0, off = false, long = null } = {}) {
  const taken = [];
  let prevented = false;
  const ctx = {
    live: { off }, page: { note_max: 4000, attach: { doc_chars: 100000 } }, docCap: 10,
    take: (files) => taken.push(...files), announce: () => {},
    longPasteFile: (t) => (long && t.length > long ? { file: { name: 'pasted.txt', text: t }, note: 'as a file' } : null),
  };
  const fn = vm.runInNewContext(`(${handler})`, ctx);
  fn({
    preventDefault() { prevented = true; },
    clipboardData: {
      getData: (kind) => (kind === 'text/plain' ? text : ''),
      files: Array.from({ length: images }, (_, i) => ({ name: `image${i}.png`, type: 'image/png' })),
    },
  });
  return { taken: taken.map((f) => f.name), prevented };
}
let passed = 0;

// Word: picture and text together -> the text is pasted normally (the browser inserts it), no image is taken
assert.deepEqual(paste({ text: 'Robert van Stedum\nChicago, IL', images: 1 }), { taken: [], prevented: false });
passed++;
// Word, long: picture and long text -> the text becomes the .txt attachment, not the picture
assert.deepEqual(paste({ text: 'x'.repeat(5000), images: 1, long: 4000 }), { taken: ['pasted.txt'], prevented: true });
passed++;
// a screenshot: a picture and no text -> the picture is taken
assert.deepEqual(paste({ images: 1 }), { taken: ['image0.png'], prevented: true });
passed++;
// a picture with only whitespace for text still counts as a picture
assert.deepEqual(paste({ text: '  \n', images: 1 }), { taken: ['image0.png'], prevented: true });
passed++;
// in Private, a lone picture is left alone, as before
assert.deepEqual(paste({ images: 1, off: true }), { taken: [], prevented: false });
passed++;

console.log(`${passed} paste scenarios passed`);
