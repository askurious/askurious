// Interactions between attributes on the results page (templates/surveys/_conjoint_ww.html, app/conjoint_interactions.py):
// a running search fills its bar and loads the page again when it ends; the table shows 10, 20 or 50 pairs per page.
(function () {
  "use strict";

  document.querySelectorAll("[data-ww-running]").forEach(function (box) {
    var bar = box.querySelector("[data-bar]"), meter = box.querySelector("[role=progressbar]");
    var text = box.querySelector("[data-status]"), failed = 0;
    (function poll() {
      fetch(box.dataset.url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          failed = 0;
          if (d.state !== "running") { location.reload(); return; }
          var pct = Math.round(d.progress * 100);
          bar.style.width = pct + "%"; meter.setAttribute("aria-valuenow", pct);
          if (text) text.textContent = pct + de_en(" %", "%");
          setTimeout(poll, 2000);
        })
        .catch(function () { if (++failed >= 5) location.reload(); else setTimeout(poll, 4000); });
    })();
  });

  document.querySelectorAll("[data-ww-table]").forEach(function (table) {
    var part = table.closest(".ww-part"), pager = part && part.querySelector("[data-ww-pager]");
    var rows = Array.prototype.slice.call(table.querySelectorAll("[data-ww-row]"));
    if (!pager || rows.length <= 10) return;
    var size = pager.querySelector("[data-ww-size]"), label = pager.querySelector("[data-ww-page]");
    var prev = pager.querySelector("[data-ww-prev]"), next = pager.querySelector("[data-ww-next]"), page = 0;
    function show() {
      var n = parseInt(size.value, 10) || 10, pages = Math.max(1, Math.ceil(rows.length / n));
      page = Math.min(page, pages - 1);
      rows.forEach(function (r, i) { r.hidden = i < page * n || i >= (page + 1) * n; });
      label.textContent = de_en("Seite {p} von {n}", "Page {p} of {n}").replace("{p}", page + 1).replace("{n}", pages);
      prev.disabled = page === 0; next.disabled = page >= pages - 1;
    }
    size.addEventListener("change", function () { page = 0; show(); });
    prev.addEventListener("click", function () { page -= 1; show(); });
    next.addEventListener("click", function () { page += 1; show(); });
    pager.hidden = false;
    show();
  });
})();
