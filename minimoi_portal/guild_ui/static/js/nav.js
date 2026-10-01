// The Guild bar's More ▾ menu (Guild 1.1 slice 1): a plain <details>, so it
// works without script; this only closes it on a click outside, on Escape
// (focus back on More) and when another menu opens. Also writes today's date
// on the Guild home, in the browser's own time zone. No request, no model.
const more = document.querySelector('[data-subnav-more]');
if (more) {
  const summary = more.querySelector('summary');
  document.addEventListener('click', (e) => {
    if (more.open && !more.contains(e.target)) more.open = false;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && more.open) {
      more.open = false;
      if (summary) summary.focus();
    }
  });
}
const today = document.querySelector('[data-today]');
if (today) {
  today.textContent = new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}
