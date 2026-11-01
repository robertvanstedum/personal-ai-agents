/* Move the real controls, retaining their handlers and state across breakpoints. */
const cos = document.body.classList.contains('founders-cos');

function keepInPlace(nodes, target, phone, after) {
  // Each control leaves a marker where it lives on a wide screen and returns there when the phone width ends.
  const slots = nodes.map(node => {
    const slot = document.createComment('desktop control');
    node.before(slot);
    return {node, slot};
  });
  const move = () => {
    for (const {node, slot} of slots) {
      if (phone.matches) target.append(node);
      else slot.after(node);
    }
    after?.();
  };
  move(); phone.addEventListener('change', move);
}

if (cos) {
  // Chief of Staff (Review 06): below 640 px the app row is the one bar. The tabs and Guildhall go to the top of ☰'s
  // list; Files and ⋯ join the bar.
  const phone = matchMedia('(max-width: 639px)');
  const places = document.querySelector('[data-mobile-menu]');
  const bar = document.querySelector('[data-phone-tools]');
  const nav = document.querySelector('.header-nav > .tab-nav');
  const guildhall = document.querySelector('.header-nav > a[data-portal-link]');
  const tools = document.querySelector('[data-conv-tools]');
  const more = document.querySelector('[data-conv-more]');
  if (places && nav) keepInPlace([nav, guildhall].filter(Boolean), places, phone);
  if (bar && tools) keepInPlace([tools], bar, phone, () => { if (more) more.open = false; });
} else {
  // Craft and Guildhall (Review 06): below 640 px the page's own header is the one bar; the mini-moi strip and the
  // workshop row ride at the top of ☰'s list. Between 640 and 700 px, and on every other Guild page, the More menu below.
  const places = document.querySelector('[data-phone-places]');
  const onePhone = matchMedia('(max-width: 639px)');
  if (places) {
    const strip = document.querySelector('body > #portal-nav-bar');
    const row = document.querySelector('body > .guild-nav');
    keepInPlace([strip, row].filter(Boolean), places, onePhone);
  }
  const phone = matchMedia(places ? '(min-width: 640px) and (max-width: 700px)' : '(max-width: 700px)');
  const menu = document.querySelector('[data-mobile-menu]');
  const more = menu?.closest('details');
  if (menu && more) {
    const controls = [...document.querySelectorAll('.workshop-primary > a:not([data-section="chat"])')];
    keepInPlace(controls, menu, phone, () => { more.open = false; });
    menu.addEventListener('click', event => {
      if (event.target.closest('button,a')) {
        more.open = false;
        requestAnimationFrame(() => { if (menu.contains(document.activeElement)) more.querySelector('summary').focus(); });
      }
    });
    document.addEventListener('click', event => {
      if (more.open && !more.contains(event.target)) more.open = false;
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && more.open) {
        more.open = false; more.querySelector('summary').focus();
      }
    });
    document.querySelector('[data-mobile-files]')?.addEventListener('click', () => {
      const files = document.querySelector('[data-floor-context]');
      if (files) { files.open = true; files.scrollIntoView({block:'nearest'}); }
    });
  }
}
