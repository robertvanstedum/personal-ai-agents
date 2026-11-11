"""When Rooms are off, Start meeting is not a live action and the page does not promise a recording."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/rooms.js"

SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
class Element {
  constructor() { this.disabled = false; this.hidden = false; this.textContent = ''; this.children = []; }
  showModal() { this.opened = true; }
}
const nodes = new Map();
const $ = s => { if (!nodes.has(s)) nodes.set(s, new Element()); return nodes.get(s); };
const context = vm.createContext({
  $, $$: () => [], el: () => new Element(), announce() {}, assert, process,
  window: { location: { search: '' }, setInterval() {}, clearInterval() {}, clearTimeout() {} },
  document: { addEventListener() {}, getElementById() { return null; }, hidden: false },
  URLSearchParams, console,
});
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/^import .*;$/mg, '').replace(/^export /mg, '');
vm.runInContext(source + `
(async () => {
  cfg = { state: 'off', access: 'ok' };
  await loadAll();
  assert.equal($('[data-rm-start]').disabled, true);
  assert.equal($('[data-rm-new]').disabled, true);
  assert.equal($('[data-rm-new-proxy]').disabled, true);
  assert.equal($('[data-rm-off]').hidden, false);
  assert.equal($('[data-rm-recorded]').hidden, true);
  const dialog = $('[data-rm-new-dialog]');
  await openNew();
  assert.notEqual(dialog.opened, true);
})().catch(e => { console.error(e); process.exitCode = 1; });
`, context);
"""


def test_start_meeting_is_off_when_rooms_are_not_connected():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for executable UI checks")
    result = subprocess.run([node, "-e", SCRIPT, str(JS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
