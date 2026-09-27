// The floor store's zones (post-its, Continue, the notes line) follow the
// floor's own freshness (freshness.js, review F1): when GET /floor answers,
// they are drawn from it; while the floor is stale, unknown or signed out,
// each keeps its last value under the same mark the lights use, so an old
// value is never shown as current.
import { $$, el } from './dom.js';
import { zoneMarkText } from './freshness.js';
import { renderContinue } from './continue.js';
import { renderRail } from './postits.js';

let drawn = null;   // the /floor answer the zones were last drawn from

function draw(state) {
  if (state.continue) renderContinue(state.continue);
  if (state.postits) renderRail(state.postits);
  if (state.notes) {
    for (const n of $$('[data-notes-line]')) { n.textContent = state.notes.text; n.dataset.notesState = state.notes.state; }
  }
  for (const n of $$('[data-mc-header]')) if (state.mc_header) n.textContent = state.mc_header;
}

// Called by floor.js on every render with the floor's mode and "since" text.
export function applyFloorZones(state, mode, since) {
  // Redraw only from a new answer: a post-it change the page made itself
  // is already on screen from its own server answer.
  if (state && state !== drawn) { drawn = state; draw(state); }
  for (const n of $$('[data-zone-fresh]')) n.remove();
  const text = zoneMarkText(mode, since);
  for (const n of $$('[data-continue], [data-postits], [data-notes-line]')) {
    if (!text) { delete n.dataset.stale; continue; }
    n.dataset.stale = mode;
    if (n.matches('[data-notes-line]')) continue;
    n.prepend(el('span', { class: 'stale-mark', 'data-zone-fresh': true }, `${text}. `));
  }
}
