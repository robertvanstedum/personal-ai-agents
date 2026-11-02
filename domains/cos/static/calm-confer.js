/* Confer's picture choice uses its existing device-scoped server preference. No chat state is changed. */
(() => {
  const button = document.getElementById('picture-mode');
  const label = button.querySelector('[data-picture-label]');
  const status = document.getElementById('save-state');
  const base = location.pathname.replace(/\/ui(?:\/[^/]*)?\/?$/, '') + '/ui/';
  const device = window.cosHistory?.device;
  let loaded = false, busy = false, pictureError = '';
  function failure(message) { pictureError = message; status.textContent = message; }
  function recovered() {
    if (pictureError && status.textContent === pictureError) status.textContent = '';
    pictureError = '';
  }
  function paint(value) {
    // Legacy "faded" means visible in the new reading layout; a saved Hide choice remains hidden.
    const shown = value !== 'hidden';
    document.body.dataset.picture = shown ? 'shown' : 'hidden';
    button.setAttribute('aria-pressed', String(shown));
    button.setAttribute('aria-label', shown ? 'Hide picture' : 'Show picture');
    button.title = shown ? 'Hide picture' : 'Show picture';
    label.textContent = shown ? 'Hide picture' : 'Picture';
  }
  async function preference(value) {
    const response = await fetch(base + 'history/preferences' + (value ? '' : '?device=' + encodeURIComponent(device)), {
      method: value ? 'POST' : 'GET', cache: 'no-store',
      headers: {'Content-Type':'application/json', ...cosWriteHeaders()},
      ...(value ? {body:JSON.stringify({device,picture:value})} : {})
    });
    if (!response.ok) throw new Error('Picture preference unavailable');
    const data = await response.json();
    if (!['shown','hidden','faded'].includes(data.picture)) throw new Error('Invalid picture preference');
    return data.picture;
  }
  async function load() {
    busy = true; button.disabled = true;
    try { if (!device) throw new Error('History not initialized'); paint(await preference()); loaded = true; recovered(); }
    catch (_) { failure('Picture preference could not be loaded. Use Picture to try again.'); label.textContent = 'Picture'; button.setAttribute('aria-label','Retry picture preference'); }
    finally { busy = false; button.disabled = false; }
  }
  button.addEventListener('click', async () => {
    if (busy) return;
    if (!loaded) { await load(); return; }
    const value = document.body.dataset.picture === 'hidden' ? 'shown' : 'hidden';
    busy = true; button.disabled = true;
    try { paint(await preference(value)); recovered(); }
    catch (_) { failure('Picture preference was not saved. Please try again.'); }
    finally { busy = false; button.disabled = false; }
  });
  const dialog = document.getElementById('confer-picture-dialog');
  document.querySelectorAll('.confer-picture-open, [data-full-picture]').forEach(b => b.addEventListener('click', () => dialog.showModal()));
  document.getElementById('confer-picture-close').addEventListener('click', () => dialog.close());
  load();
})();
