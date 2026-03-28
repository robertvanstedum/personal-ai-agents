// ─── Research nav: ··· dropdown toggle ───────────────────────────────────────
// Shared by all Research pages. Injects its own CSS so no page needs to
// define .nav-more-dropdown styles. Handles one dropdown per page.

(function () {
  // Inject CSS — hidden by default, visible with .nav-open class
  var style = document.createElement('style');
  style.textContent = [
    '.nav-more-dropdown { display: none !important; }',
    '.nav-more-dropdown.nav-open { display: block !important; }'
  ].join('\n');
  document.head.appendChild(style);

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
}());
