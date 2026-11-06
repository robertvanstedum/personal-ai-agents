/* The Model choice in CoS's ⋯ menu: this conversation only, from its next turn. Hidden where routes are off. */
(() => {
  const control = document.getElementById('cos-route-control'), select = document.getElementById('cos-route');
  const note = document.getElementById('cos-route-note');
  if (!control || !select) return;
  const base = location.pathname.replace(/\/ui(?:\/[^/]*)?\/?$/, '') + '/ui/history/conversations/';
  let cid = null, revision = 0;
  const label = r => `${r.tier_letter} · ${r.label} · ${r.billing_label}`;
  function show(v) {
    revision = v.revision;
    select.replaceChildren(...v.routes.map(r => {
      const o = document.createElement('option');
      o.value = r.route_id; o.textContent = r.available ? label(r) : `${label(r)} (not available)`; o.disabled = !r.available; o.title = r.reason || '';
      return o;
    }));
    select.value = v.selected_route_id;
    const served = v.last_turn && v.last_turn.served;
    note.textContent = v.last_turn ? (served ? `Last turn: served by ${served.provider}/${served.model}` : 'Last turn: no evidence of who served it') : '';
    control.hidden = !v.selectable;
  }
  async function load(id) {
    cid = id;
    try {
      const r = await fetch(base + encodeURIComponent(id) + '/route', {credentials: 'same-origin'});
      if (!r.ok) { control.hidden = true; return; }
      if (cid === id) show(await r.json());
    } catch (_) { control.hidden = true; }
  }
  select.addEventListener('change', async () => {
    const id = cid, wanted = select.value;
    select.disabled = true;
    try {
      const r = await fetch(base + encodeURIComponent(id) + '/route', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({route_id: wanted, revision})});
      const d = await r.json();
      if (r.ok) { show(d); note.textContent = 'Applies from your next message.'; }
      else { note.textContent = d.message || 'That model could not be chosen. Nothing changed.'; await load(id); }
    } catch (_) { note.textContent = 'That model could not be chosen. Nothing changed.'; await load(id); }
    finally { select.disabled = false; }
  });
  document.addEventListener('cos:conversation', e => load(e.detail.id));
})();
