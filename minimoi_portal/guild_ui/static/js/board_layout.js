// The Board's free placement (11 Oct, BOARD_SIMPLE_NOTEPAD_PROPOSAL): pure geometry, no DOM. The board is 1200 logical units wide and grows downward;
// every note and photo has the same footprint; notes never overlap. The page scales the board to the window, so a saved position means the same place at
// every size. Notes without a saved position are laid out in list order into the first free cell, the same way in every window, and saved only when the
// person first moves something (then the whole arrangement is saved in one batch, so nothing jumps).
export const W = 1200;
export const NOTE_W = 180;
export const NOTE_H = 170;
export const STEP = 20;
export const MAX_Y = 20000;
const CELL_W = NOTE_W + 20;
const CELL_H = NOTE_H + 20;
const MAX_X = W - NOTE_W;

export const MIN_W = 140, MIN_H = 130, MAX_W = 480, MAX_H = 450;
export const widthOf = p => p.w ?? NOTE_W;
export const heightOf = p => p.h ?? NOTE_H;
export const overlaps = (a, b) => a.x < b.x + widthOf(b) && b.x < a.x + widthOf(a) && a.y < b.y + heightOf(b) && b.y < a.y + heightOf(a);
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const snap = (v) => Math.round(v / STEP) * STEP;
export const inside = (p) => p.x >= 0 && p.x + widthOf(p) <= W && p.y >= 0 && p.y <= MAX_Y;
export const isFree = (p, others) => inside(p) && !others.some((o) => overlaps(p, o));

// The free position nearest to `want` (on the 20-unit grid), searching outward ring by ring; null when none is found within the board.
export function nearestFree(want, others) {
  const shape = want.w != null ? { w: want.w, h: want.h } : {};
  const start = { ...shape, x: clamp(snap(want.x), 0, W - widthOf(want)), y: clamp(snap(want.y), 0, MAX_Y) };
  if (isFree(start, others)) return start;
  const maxRing = Math.ceil(Math.max(W, MAX_Y) / STEP);
  for (let r = 1; r <= maxRing; r += 1) {
    let best = null;
    let bestD = Infinity;
    for (let dx = -r; dx <= r; dx += 1) {
      for (const dy of (Math.abs(dx) === r ? range(-r, r) : [-r, r])) {
        const p = { ...shape, x: start.x + dx * STEP, y: start.y + dy * STEP };
        if (!isFree(p, others)) continue;
        const d = (p.x - want.x) ** 2 + (p.y - want.y) ** 2;
        if (d < bestD) { best = p; bestD = d; }
      }
    }
    if (best) return best;
  }
  return null;
}

function range(a, b) { const out = []; for (let i = a; i <= b; i += 1) out.push(i); return out; }

// notes: active notes in list order, each {id, pos_x, pos_y}. Returns Map(id -> {x, y, placed}). A saved position is kept as it is; the others fill the first
// free cell (left to right, top to bottom) that does not touch a saved note or an earlier one.
export function layoutAll(notes) {
  const out = new Map();
  const taken = [];
  for (const n of notes) {
    if (Number.isInteger(n.pos_x) && Number.isInteger(n.pos_y)) { const shape = n.size_w != null ? { w: n.size_w, h: n.size_h } : {}; const pos = { ...shape, x: n.pos_x, y: n.pos_y, placed: true };
      if (isFree(pos, taken)) { out.set(n.id, pos); taken.push(pos); } }
  }
  for (const n of notes) {
    if (out.has(n.id)) continue;
    const shape = n.size_w != null ? { w: n.size_w, h: n.size_h } : {};
    let spot = null;
    for (let y = 10; y <= MAX_Y && !spot; y += CELL_H) {
      for (let x = 10; x + widthOf(shape) <= W && !spot; x += CELL_W) if (isFree({ ...shape, x, y }, taken)) spot = { ...shape, x, y };
    }
    spot = spot || { ...shape, x: 0, y: MAX_Y };
    out.set(n.id, { ...spot, placed: false });
    taken.push(spot);
  }
  return out;
}

// The height the board needs: the lowest note plus a margin, never less than a screenful.
export const boardHeight = (map, min = 600) => Math.max(min, ...[...map.values()].map((p) => p.y + heightOf(p) + 40));

// A keyboard step: the neighbour position one step (or five) away in a direction, or null when blocked or off the board.
export function stepFrom(pos, key, others, big = false) {
  const d = (big ? 5 : 1) * STEP;
  const next = { ...(pos.w != null ? {w: pos.w, h: pos.h} : {}), x: pos.x + (key === 'ArrowRight' ? d : key === 'ArrowLeft' ? -d : 0), y: pos.y + (key === 'ArrowDown' ? d : key === 'ArrowUp' ? -d : 0) };
  return isFree(next, others) ? next : null;
}

// The notes whose positions must be saved with a move: on the first move every note shown is saved at where it is drawn (the snapshot), after that only the moved one.
export function movesFor(movedId, to, map, anyPlacedBefore) {
  const moves = [];
  for (const [id, p] of map) if (id !== movedId && id !== 0 && (!anyPlacedBefore || !p.placed)) moves.push({ id, x: p.x, y: p.y });
  moves.push({ id: movedId, x: to.x, y: to.y, ...(to.w != null ? {w: to.w, h: to.h} : {}) });
  return moves;
}
