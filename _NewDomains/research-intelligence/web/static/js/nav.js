// ─── Research nav: ··· dropdown toggle ───────────────────────────────────────
// Shared by all Research pages. Loaded once, handles one dropdown per page.

document.addEventListener('DOMContentLoaded', function () {
  var btn = document.querySelector('.nav-more-btn');
  var dd  = document.querySelector('.nav-more-dropdown');
  if (!btn || !dd) return;

  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
  });

  document.addEventListener('click', function () {
    dd.style.display = 'none';
  });
});
