import { apiGet } from './api.js';
import { load, save } from './state.js';
export async function initRoomsArt(page) {
  const select = document.querySelector('[data-room-art-select]');
  const image = document.querySelector('[data-room-art-image]');
  if (!image) return;
  const status = document.querySelector('[data-room-art-status]');
  const say = text => { if (status) status.textContent = text; };
  const preferred = load('rooms.picture', v => typeof v === 'string' ? v : null, () => 'theme', 'Room picture');
  const theme = '/static/guild/guild-workshop-team.webp';
  const assets = new Map();
  function show(id) {
    image.hidden = id === 'none';
    const asset = assets.get(id);
    image.src = asset ? asset.full_url : theme;
    image.alt = asset ? 'Your selected room photo' : 'Workshop team artwork';
  }
  image.addEventListener('error', () => { image.src = theme; say('Selected photo unavailable; showing theme artwork.'); }, { once: true });
  if (select) select.addEventListener('change', () => { save('rooms.picture', select.value); show(select.value); });
  if (!select && ['theme', 'none'].includes(preferred)) { show(preferred); return; }
  const result = await apiGet('/media?state=active&kind=photo');
  if (result.ok && Array.isArray(result.body.assets)) {
    for (const a of result.body.assets) {
      if (typeof a.id !== 'string' || typeof a.full_url !== 'string' || !a.full_url.startsWith(page.base + '/media/')) continue;
      assets.set(a.id, a);
      const option = document.createElement('option'); option.value = a.id; option.textContent = a.title || a.name || a.filename || 'Photo ' + a.id; if (select) select.append(option);
    }
    say(assets.size ? 'Owned photos only. Static picture; no rotation during a meeting.' : 'No owned photos yet. Theme artwork is available.');
  } else say('Media library unavailable. Theme artwork is available.');
  const chosen = preferred === 'none' || assets.has(preferred) ? preferred : 'theme';
  if (select) select.value = chosen;
  show(chosen);
}
