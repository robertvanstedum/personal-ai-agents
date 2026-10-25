// Read back platform-authored cards during a turn. No action is replayed here.
// Each sweep starts at the newest page again: sequence ids can commit out of
// order, so advancing a permanent high-water cursor could lose a late commit.
export function platformNotes({ after, fetchPage, current, insert, kinds = ['platform'] }) {
  const seen = new Set();
  let inFlight = null;
  async function sweep() {
    let before;
    const notes = [];
    for (let pages = 0; pages < 5; pages += 1) {
      if (!current()) return false;
      const r = await fetchPage(before);
      if (!current() || !r?.ok || !Array.isArray(r.body?.notes)) return false;
      const rows = r.body.notes;
      if (rows.some((n) => !Number.isSafeInteger(n.id) || n.id <= 0)) return false;
      notes.push(...rows.filter((n) => n.id > after && kinds.includes(n.author_kind)));
      const oldest = rows.length ? Math.min(...rows.map((n) => n.id)) : 0;
      const complete = !r.body.more || oldest <= after;
      // A malformed/repeating page must not loop. Fetches and work are bounded.
      if (!complete && (!oldest || (before !== undefined && oldest >= before))) return false;
      if (complete) {
        for (const n of notes.sort((a, b) => a.id - b.id)) {
          if (!current()) return false;
          if (!seen.has(n.id)) { insert(n); seen.add(n.id); }
        }
        return true;
      }
      before = oldest;
    }
    return false; // no partial sweep presented as a complete read-back
  }
  return {
    refresh() {
      if (!inFlight) inFlight = sweep().catch(() => false).finally(() => { inFlight = null; });
      return inFlight;
    },
  };
}

export const confirmedStop = (ev) => ev.status === 'stopped' && ev.failure_class === 'stopped'
  && ev.stop_acknowledged === true && ev.tool_admission !== 'unconfirmed';

// One read at a time, a fresh read after completion, then bounded retries.
// A longer outage sleeps until wake() (online/visible), without replaying work.
export function noteWatch({ refresh, schedule = setTimeout, cancel = clearTimeout, onDispose = () => {} }) {
  let disposed = false, timer, finished = false, ending = false, ticking = null, retries = 0;
  const dispose = () => {
    if (disposed) return;
    disposed = true; cancel(timer); onDispose();
  };
  const tick = () => {
    if (disposed || ending) return Promise.resolve();
    if (!ticking) ticking = (async () => {
      cancel(timer);
      const ok = await refresh();
      if (disposed || ending) return;
      if (finished && ok) { dispose(); return; }
      if (finished && ++retries >= 3) return;
      timer = schedule(tick, finished ? 2000 : 1000);
    })().finally(() => { ticking = null; });
    return ticking;
  };
  return {
    wake() { retries = 0; return tick(); },
    dispose,
    async finish() {
      ending = true; cancel(timer);
      // A read started before completion is not the final read-back.
      if (ticking) await ticking;
      ending = false; finished = true;
      if (!disposed) await tick();
    },
  };
}
