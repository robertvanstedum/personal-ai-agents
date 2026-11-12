/* Shared presentation controller. Agent APIs and conversation state stay with their owners. */
(() => {
  'use strict';
  window.MiniMoiChatSidebar = { mount(c) {
    if (!c.panel) return {open() {}, closeDrawer() {}};
    const body = document.body, panel = c.panel, media = matchMedia('(min-width: 900px)');
    const all = s => s ? [...document.querySelectorAll(s)] : [];
    const read = () => { try { return localStorage.getItem(c.key) === '1'; } catch (_) { return false; } };
    let collapsed = read(), drawerOpen = false, returnTo = null;
    body.classList.add('mm-chat'); panel.classList.add('mm-chat-sidebar');
    panel.id ||= 'mm-chat-list';
    let rail = c.rail;
    const button = (label, glyph, action) => {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = glyph;
      b.title = label; b.setAttribute('aria-label', label); b.addEventListener('click', action); return b;
    };
    if (!rail) {
      rail = document.createElement('nav'); rail.setAttribute('aria-label', 'Chat navigation');
      const opener = button('Open chat list', '☰', () => open(false)); opener.dataset.sidebarToggle = ''; rail.append(opener);
      const create = panel.querySelector(c.newButton);
      if (create) {
        const proxy = button(c.newLabel, '+', () => create.click()); rail.append(proxy);
        const sync = () => { proxy.disabled = create.disabled; }; sync();
        new MutationObserver(sync).observe(create, {attributes:true, attributeFilter:['disabled']});
      }
      panel.before(rail);
    }
    rail.classList.add('mm-chat-rail');
    const scrim = c.scrim || document.createElement('div');
    if (!c.scrim) {scrim.className = 'mm-chat-scrim'; panel.after(scrim);}
    scrim.addEventListener('click', () => closeDrawer(true));
    const drawer = () => c.drawer ? c.drawer() : !media.matches;
    const save = () => {try {localStorage.setItem(c.key, collapsed ? '1' : '0');} catch (_) {}};
    const isVisible = n => n && n.getClientRects().length > 0;
    const originalRole = panel.getAttribute('role');
    const focusables = () => [...panel.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(isVisible);
    function render() {
      const floating = drawer(), visible = floating ? drawerOpen : !collapsed;
      body.dataset.chatSidebar = floating ? (drawerOpen ? 'drawer' : 'rail') : (collapsed ? 'rail' : 'expanded');
      panel.inert = !visible;
      if (floating && drawerOpen) {panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true');}
      else {if (originalRole) panel.setAttribute('role', originalRole); else panel.removeAttribute('role'); panel.removeAttribute('aria-modal');}
      scrim.hidden = !(floating && drawerOpen);
      for (const b of [...all(c.open), ...all(c.search), ...rail.querySelectorAll('[data-sidebar-toggle]')].filter(b => !b.disabled)) {
        if (b.getAttribute('aria-label') === c.newLabel) continue;
        b.setAttribute('aria-controls', panel.id); b.setAttribute('aria-expanded', String(visible));
      }
      c.render?.({collapsed, drawerOpen:floating && drawerOpen});
    }
    function open(search = false) {
      returnTo = document.activeElement;
      if (drawer()) drawerOpen = true; else {collapsed = false; save();}
      render(); c.onOpen?.();
      if (search) c.filter?.focus();
      else if (drawer()) (panel.querySelector('button,input,a') || panel).focus();
    }
    function closeDrawer(restore = false) {
      if (!drawerOpen) return;
      drawerOpen = false; render();
      if (restore) (isVisible(returnTo) ? returnTo : [...all(c.open), ...rail.querySelectorAll('button')].find(isVisible))?.focus();
    }
    for (const b of all(c.open)) b.addEventListener('click', () => open(false));
    for (const b of all(c.search)) b.addEventListener('click', () => open(true));
    for (const b of all(c.fold)) b.addEventListener('click', () => {
      if (drawer()) closeDrawer(true);
      else {collapsed = true; save(); render(); rail.querySelector('button')?.focus();}
    });
    const filter = () => {
      if (c.onFilter) c.onFilter();
      else if (c.rows && c.filter) {
        const q = c.filter.value.trim().toLocaleLowerCase();
        panel.querySelectorAll(c.rows).forEach(row => {row.hidden = !((row.querySelector(c.title) || row).textContent.toLocaleLowerCase().includes(q));});
      }
    };
    c.filter?.addEventListener('input', filter);
    if (c.rows) new MutationObserver(filter).observe(panel, {childList:true, subtree:true});
    document.addEventListener('keydown', e => {
      if (!drawerOpen) return;
      if (e.key === 'Escape') {e.preventDefault(); closeDrawer(true);}
      if (e.key === 'Tab') {
        const items = focusables(), first = items[0], last = items[items.length - 1];
        if (!items.length) {e.preventDefault(); return;}
        if (e.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {e.preventDefault(); last.focus();}
        else if (!e.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) {e.preventDefault(); first.focus();}
      }
    });
    document.addEventListener('focusin', e => {
      if (drawerOpen && drawer() && !panel.contains(e.target)) focusables()[0]?.focus();
    });
    media.addEventListener('change', () => {drawerOpen = false; render();});
    if (c.drawer) new MutationObserver(() => {drawerOpen = false; render();}).observe(body, {attributes:true, attributeFilter:['class']});
    render();
    return {open, closeDrawer};
  }};
})();
