/* Shared, quiet-until-needed service notices. No provider text is interpreted as HTML.
   Callers own a key and clear it on recovery; dismissing a notice does not retry an action. */
(() => {
  const entries = new Map();
  let region;
  function clear(key) {
    entries.get(key)?.remove();
    entries.delete(key);
    if (region) region.hidden = !entries.size;
  }
  function show(key, message, { title = 'Something needs attention' } = {}) {
    if (!message) return clear(key);
    if (!region) {
      region = document.createElement('aside');
      region.className = 'minimoi-notices';
      region.setAttribute('aria-label', 'Service notices');
      document.body.append(region);
    }
    const text = String(message);
    const prior = entries.get(key);
    if (prior?.dataset.message === text) return;
    clear(key);
    const box = document.createElement('section');
    box.className = 'minimoi-notice';
    box.dataset.message = text;
    const live = document.createElement('div');
    live.setAttribute('role', 'status');
    live.setAttribute('aria-live', 'polite');
    const heading = document.createElement('strong');
    heading.textContent = title;
    const body = document.createElement('p');
    // Keep allowance accounting out of the conversation. Callers supply public error text.
    body.textContent = /voice allowance.*(used up|not enough)|Today's voice allowance is used up/i.test(text)
      ? 'Not enough voice allowance for another session. It renews at midnight Chicago time. You can still type.'
      : text.slice(0, 350);
    live.append(heading, body);
    const close = document.createElement('button');
    close.type = 'button'; close.textContent = '×';
    close.title = 'Dismiss notice'; close.setAttribute('aria-label', 'Dismiss notice');
    close.addEventListener('click', () => clear(key));
    box.append(live, close); entries.set(key, box); region.append(box); region.hidden = false;
  }
  window.MiniMoiNotices = { show, clear };
})();
