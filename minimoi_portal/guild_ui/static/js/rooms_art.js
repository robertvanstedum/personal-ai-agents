import { apiGet } from './api.js';
import { load, save } from './state.js';
export async function initRoomsArt(page) {
  const select = document.querySelector('[data-room-art-select]');
  if (!select) return;
  const image = document.querySelector('[data-room-art-image]');
  const status = document.querySelector('[data-room-art-status]');
  const theme = '/static/guild/guild-workshop-team.webp';
  const assets = new Map();
  function show(id) {
    image.hidden = id === 'none';
    const asset = assets.get(id);
    image.src = asset ? asset.full_url : theme;
    image.alt = asset ? 'Your selected room photo' : 'Workshop team artwork';
  }
  image.addEventListener('error', () => { image.src = theme; status.textContent = 'Selected photo unavailable; showing theme artwork.'; }, { once: true });
  select.addEventListener('change', () => { save('rooms.picture', select.value); show(select.value); });
  const result = await apiGet('/media?state=active&kind=photo');
  if (result.ok && Array.isArray(result.body.assets)) {
    for (const a of result.body.assets) {
      if (typeof a.id !== 'string' || typeof a.full_url !== 'string' || !a.full_url.startsWith(page.base + '/media/')) continue;
      assets.set(a.id, a);
      const option = document.createElement('option'); option.value = a.id; option.textContent = a.title || a.name || a.filename || 'Photo ' + a.id; select.append(option);
    }
    status.textContent = assets.size ? 'Owned photos only. Static picture; no rotation during a meeting.' : 'No owned photos yet. Theme artwork is available.';
  } else status.textContent = 'Media library unavailable. Theme artwork is available.';
  const preferred = load('rooms.picture', v => typeof v === 'string' ? v : null, () => 'theme', 'Room picture');
  select.value = preferred === 'none' || assets.has(preferred) ? preferred : 'theme';
  show(select.value);
}
