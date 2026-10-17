(() => {
  const root = document.getElementById('minimoi-companion');
  if (!root || root.dataset.ready) return;
  root.dataset.ready = 'true';
  const launch = root.querySelector('.companion-launch');
  const dialog = root.querySelector('dialog');
  const frame = root.querySelector('iframe');
  const loading = root.querySelector('.companion-loading');
  const failure = root.querySelector('.companion-fallback');
  let timer;
  function fit() {
    const v = window.visualViewport;
    dialog.style.setProperty('--companion-height', `${v?.height || innerHeight}px`);
    dialog.style.setProperty('--companion-top', `${v?.offsetTop || 0}px`);
  }
  function fail() { loading.hidden = true; frame.hidden = true; failure.hidden = false; }
  function load() {
    loading.hidden = false; failure.hidden = true;
    frame.src = frame.dataset.src;
    clearTimeout(timer); timer = setTimeout(fail, 20000);
  }
  frame.addEventListener('load', () => {
    clearTimeout(timer);
    try {
      const doc = frame.contentDocument;
      if (!doc?.querySelector('[data-mc-embed]')) { fail(); return; }
      loading.hidden = true; failure.hidden = true; frame.hidden = false;
      // Keep full-chat navigation attached to the selected conversation.
      const update = () => {
        const data = JSON.parse(doc.getElementById('guild-page').textContent);
        const cid = new URL(frame.contentWindow.location.href).searchParams.get('c') || data.conversation?.id;
        const url = new URL('/guild-next/guild/build', location.origin);
        if (cid) url.searchParams.set('c', cid);
        root.querySelector('.companion-full').href = url.pathname + url.search;
      };
      update();
      doc.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); close(); } });
    } catch (_) { fail(); }
  });
  function close() {
    // Keep the document/drafts, but stop microphone consumers before hiding it.
    try { frame.contentDocument?.dispatchEvent(new frame.contentWindow.CustomEvent('guild:mic-claim', {detail:{who:'companion-closed'}})); } catch (_) {}
    dialog.close(); launch.hidden = false; launch.setAttribute('aria-expanded', 'false'); launch.focus();
  }
  launch.addEventListener('click', async () => {
    const pending = [];
    const openError = root.querySelector('.companion-open-error');
    openError.hidden = true; launch.disabled = true;
    let deadline;
    document.dispatchEvent(new CustomEvent('minimoi:companion-opening', {detail:{waitUntil: p => pending.push(Promise.resolve(p))}}));
    try { await Promise.race([Promise.all(pending), new Promise((_, reject) => {deadline=setTimeout(() => reject(new Error('voice termination pending')), 8000);})]); } catch (_) {
      openError.textContent = 'The current voice session has not confirmed it ended. Finish it before opening MC; your conversation remains saved.';
      openError.hidden = false;
      return;
    } finally { clearTimeout(deadline); launch.disabled = false; }
    fit(); dialog.showModal(); launch.hidden = true; launch.setAttribute('aria-expanded', 'true');
    if (!frame.hasAttribute('src')) load();
    root.querySelector('.companion-close').focus();
  });
  root.querySelector('.companion-close').addEventListener('click', close);
  root.querySelector('.companion-retry').addEventListener('click', load);
  dialog.addEventListener('cancel', e => { e.preventDefault(); close(); });
  window.visualViewport?.addEventListener('resize', fit);
  window.visualViewport?.addEventListener('scroll', fit);
  window.addEventListener('resize', fit);
})();
