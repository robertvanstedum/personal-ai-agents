/* The Model choice in CoS's ⋯ menu: this conversation only, from its next turn. Hidden where routes are off.
 * Every answer is checked against the conversation still showing: a slow answer for another chat changes nothing here. */
(() => {
  const control = document.getElementById('cos-route-control'), select = document.getElementById('cos-route');
  const note = document.getElementById('cos-route-note');
  if (!control || !select) return;
  const base = location.pathname.replace(/\/ui(?:\/[^/]*)?\/?$/, '') + '/ui/history/conversations/';
  let cid = null, revision = 0, generation = 0;
  const url = id => base + encodeURIComponent(id) + '/route';
  const label = r => `${r.tier_letter} · ${r.label} · ${r.billing_label}`;
  function receipt(v) {
    if (!v.last_turn) return '';
    const served = v.last_turn.served;
    return served ? `Last turn: served by ${served.provider}/${served.model}` : 'Last turn: no evidence of who served it';
  }
  function show(v) {
    revision = v.revision;
    select.replaceChildren(...v.routes.map(r => {
      const o = document.createElement('option');
      o.value = r.route_id; o.textContent = r.available ? label(r) : `${label(r)} (not available)`; o.disabled = !r.available; o.title = r.reason || '';
      return o;
    }));
    select.value = v.selected_route_id;
    note.textContent = receipt(v);
    control.hidden = !v.selectable;
  }
  async function load(id, keepNote) {
    const mine = ++generation;
    cid = id;
    try {
      const r = await fetch(url(id), {credentials: 'same-origin', cache: 'no-store'});
      if (mine !== generation) return;
      if (!r.ok) { control.hidden = true; return; }
      const v = await r.json();
      if (mine !== generation) return;
      show(v);
      if (keepNote) note.textContent = keepNote;
    } catch (_) { if (mine === generation) control.hidden = true; }
  }
  select.addEventListener('change', async () => {
    const id = cid, wanted = select.value, mine = ++generation;
    select.disabled = true;
    let message = 'That model could not be chosen. Nothing changed.', shown = null;
    try {
      const r = await fetch(url(id), {method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', ...cosWriteHeaders()}, body: JSON.stringify({route_id: wanted, revision})});
      const d = await r.json();
      if (r.ok) { shown = d; message = 'Applies from your next message.'; } else if (d.message) message = d.message;
    } catch (_) { /* the words above */ }
    if (mine !== generation) return;          // the page moved on: this answer belongs to another moment
    select.disabled = false;
    if (shown) { show(shown); note.textContent = message; } else await load(id, message);
  });
  document.addEventListener('cos:conversation', e => { select.disabled = false; load(e.detail.id); });
  document.addEventListener('cos:turns-changed', e => { if (e.detail.id === cid && !select.disabled) load(cid); });
})();
