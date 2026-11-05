// The conversation's model route, under its Options menu. The server owns the catalogue: the page shows what it is
// given and sends back only a route id and the revision it saw. A change applies from the next turn.
import { apiGet, apiPost } from './api.js';
import { newKey } from './actions.js';

const optionText = (r) => `${r.tier_letter} · ${r.label} · ${r.billing_label}${r.available ? '' : ' · not available'}`;

function lastTurnText(last) {
  if (!last) return '';
  const sent = `${last.model}`;
  if (!last.served) return `Last turn: sent to ${sent}; the runtime's record was not read.`;
  if (last.served_matches === false) return `Last turn: selected ${sent}, but the runtime's record shows ${last.served.provider}/${last.served.model}.`;
  return `Last turn: served by ${last.served.provider}/${last.served.model}.`;
}

export function initRoutePicker(root, conversationId) {
  if (!root) return;
  const control = root.querySelector('[data-mc-route-control]');
  const select = root.querySelector('[data-mc-route]');
  const status = root.querySelector('[data-mc-route-status]');
  if (!control || !select || !status) return;
  let revision = 0;
  let selected = null;
  let loadedFor = null;

  function show(view) {
    revision = view.revision;
    selected = view.selected_route_id;
    select.replaceChildren(...view.routes.map((r) => {
      const option = document.createElement('option');
      option.value = r.route_id;
      option.textContent = optionText(r);
      option.disabled = !r.available;
      option.title = r.available ? r.tier_use : (r.reason || 'Not available');
      return option;
    }));
    select.value = selected;
    status.textContent = lastTurnText(view.last_turn);
    control.hidden = !view.selectable;
  }

  async function load() {
    const id = conversationId();
    if (!id) { control.hidden = true; return; }
    const r = await apiGet(`/conversations/${encodeURIComponent(id)}/route`);
    if (id !== conversationId()) return;           // the page moved on while this was read
    if (!r.ok || !r.body) { control.hidden = true; return; }
    loadedFor = id;
    show(r.body);
  }

  select.addEventListener('change', async () => {
    const id = conversationId();
    const wanted = select.value;
    select.disabled = true;
    try {
      const r = await apiPost(`/conversations/${encodeURIComponent(id)}/route`,
        { route_id: wanted, revision, idempotency_key: newKey() });
      if (r.ok && r.body && r.body.result === 'route_selected') {
        show(r.body);
        status.textContent = `Next turn uses ${r.body.selected.label} · ${r.body.selected.billing_label}.`;
      } else {
        select.value = selected;
        status.textContent = (r.body && r.body.message) || 'The model route could not be changed.';
        if (r.body && r.body.error === 'route_changed') await load();
      }
    } finally {
      select.disabled = false;
    }
  });

  root.addEventListener('toggle', () => { if (root.open) load(); });
  load();
  return { load, loadedFor: () => loadedFor };
}
