/* Move the real controls, retaining their handlers and state across breakpoints. */
const phone = matchMedia('(max-width: 700px)');
const cos = document.body.classList.contains('founders-cos');
const menu = document.querySelector('[data-mobile-menu]');
const more = menu?.closest('details');
if (menu && more) {
  const controls = [...document.querySelectorAll(cos
    ? '.tab-nav a, .tab-nav [data-tab]:not([data-tab="confer"]), .header-nav > button, .header-nav > a'
    : '.workshop-primary > a:not([data-section="chat"])')];
  const slots = controls.map(node => {
    const slot = document.createComment('desktop control');
    node.before(slot);
    return {node, slot};
  });
  const move = () => {
    more.open = false;
    for (const {node, slot} of slots) {
      if (phone.matches) menu.append(node);
      else slot.after(node);
    }
  };
  move(); phone.addEventListener('change', move);
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
