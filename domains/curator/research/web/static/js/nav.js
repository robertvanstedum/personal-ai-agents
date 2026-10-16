// ─── Research nav: ··· dropdown toggle ───────────────────────────────────────
// CSS lives in nav.css (loaded in <head>). This file handles click behavior.

document.addEventListener('DOMContentLoaded', function () {
  var btn = document.querySelector('.nav-more-btn');
  var dd  = document.querySelector('.nav-more-dropdown');
  if (!btn || !dd) return;

  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    dd.classList.toggle('nav-open');
  });

  document.addEventListener('click', function () {
    dd.classList.remove('nav-open');
  });
});
