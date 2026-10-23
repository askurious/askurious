// The preview bar on a phone (UI/UX audit 2026-10-16, 23): it slides away while the page is scrolled down and comes
// back on the way up, at the top and while its tools or the list of blocks are open. Loaded with the bar
// (participant/_preview_bar.html); the rest of the bar's script is in participant.js.
(function () {
  var bar = document.querySelector(".p-preview-bar");
  if (!bar || !window.matchMedia) return;
  var phone = window.matchMedia("(max-width: 560px)"), last = window.scrollY, ticking = false;
  function update() {
    ticking = false;
    var y = window.scrollY, jump = bar.querySelector(".p-jump");
    var keep = !phone.matches || y < 60 || bar.classList.contains("is-more") || (jump && jump.open) ||
               bar.contains(document.activeElement);
    if (keep || y < last - 4) bar.classList.remove("is-tucked");
    else if (y > last + 4) bar.classList.add("is-tucked");
    last = y;
  }
  window.addEventListener("scroll", function () {
    if (!ticking) { ticking = true; window.requestAnimationFrame(update); }
  }, { passive: true });
  bar.addEventListener("focusin", function () { bar.classList.remove("is-tucked"); });
})();
