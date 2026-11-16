(() => {
  const root = document.getElementById('minimoi-companion');
  if (!root || root.dataset.ready) return;
  root.dataset.ready = 'true';
  const launch = root.querySelector('.companion-launch');
  const reset = root.querySelector('.companion-reset');
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
    if (reset && launch.dataset.placed === 'true') reset.hidden = false;
  }
  // The button can be dragged on a phone or a desktop (#357). A short press still opens the chat.
  // The place is the same browser memory the Guild panel uses, and Reset puts the button back.
  const POS_KEY = 'guild.mc.pos';
  let edge = { x: 8, y: 8, b: 8, r: 8 };
  function readEdge() {
    const probe = document.createElement('div');
    probe.style.cssText = 'position:absolute;visibility:hidden;padding-top:env(safe-area-inset-top);padding-right:env(safe-area-inset-right);padding-bottom:env(safe-area-inset-bottom);padding-left:env(safe-area-inset-left)';
    document.body.append(probe);
    const s = getComputedStyle(probe);
    edge = {
      x: Math.max(8, parseFloat(s.paddingLeft) || 0), y: Math.max(8, parseFloat(s.paddingTop) || 0),
      b: Math.max(8, parseFloat(s.paddingBottom) || 0), r: Math.max(8, parseFloat(s.paddingRight) || 0),
    };
    probe.remove();
  }
  readEdge();
  const kept = () => { try { const v = JSON.parse(localStorage.getItem(POS_KEY) || 'null'); return v && Number.isFinite(v.x) && Number.isFinite(v.y) ? v : null; } catch (_) { return null; } };
  function clamp(x, y) {
    const r = launch.getBoundingClientRect();
    const m = edge;
    const view = window.visualViewport;
    const left = (view?.offsetLeft || 0) + m.x;
    const top = (view?.offsetTop || 0) + m.y;
    const right = (view?.offsetLeft || 0) + (view?.width || innerWidth) - r.width - m.r;
    const bottom = (view?.offsetTop || 0) + (view?.height || innerHeight) - r.height - m.b;
    return { x: Math.round(Math.min(Math.max(left, x), Math.max(left, right))), y: Math.round(Math.min(Math.max(top, y), Math.max(top, bottom))) };
  }
  function placeReset(p) {
    if (!reset) return;
    const r = launch.getBoundingClientRect();
    const m = edge;
    const beside = p.x + r.width + 8 + 72 <= (window.visualViewport?.width || innerWidth) - m.r ? p.x + r.width + 8 : Math.max(m.x, p.x - 76);
    reset.style.left = beside + 'px';
    reset.style.top = p.y + 'px';
    reset.hidden = false;
  }
  function place(x, y, keep) {
    const p = clamp(x, y);
    launch.style.left = p.x + 'px';
    launch.style.top = p.y + 'px';
    launch.style.right = 'auto';
    launch.style.bottom = 'auto';
    launch.dataset.placed = 'true';
    placeReset(p);
    if (keep) { try { localStorage.setItem(POS_KEY, JSON.stringify(p)); } catch (_) { /* the place is a convenience */ } }
  }
  function restore() {
    launch.style.left = launch.style.top = launch.style.right = launch.style.bottom = '';
    delete launch.dataset.placed;
    if (reset) reset.hidden = true;
    try { localStorage.removeItem(POS_KEY); } catch (_) { /* nothing was kept */ }
  }
  const saved = kept();
  if (saved) place(saved.x, saved.y, false);
  window.addEventListener('resize', () => { readEdge(); const now = kept(); if (now) place(now.x, now.y, false); });
  let drag = null, moved = false;
  launch.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const r = launch.getBoundingClientRect();
    drag = { id: e.pointerId, dx: e.clientX - r.left, dy: e.clientY - r.top, x: e.clientX, y: e.clientY };
    moved = false;
    launch.setPointerCapture(e.pointerId);
  });
  launch.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 8) return;
    moved = true;
    const p = clamp(e.clientX - drag.dx, e.clientY - drag.dy);
    launch.style.left = p.x + 'px'; launch.style.top = p.y + 'px'; launch.style.right = 'auto'; launch.style.bottom = 'auto';
  });
  const endDrag = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null;
    try { launch.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    if (!moved) return;
    const r = launch.getBoundingClientRect();
    place(r.left, r.top, true);
  };
  launch.addEventListener('pointerup', endDrag);
  launch.addEventListener('pointercancel', endDrag);
  if (reset) reset.addEventListener('click', restore);
  launch.addEventListener('click', async (e) => {
    if (moved) { moved = false; e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (reset) reset.hidden = true;
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
