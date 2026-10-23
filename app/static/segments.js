// Segments on the results page (templates/surveys/_segments.html, app/segments.py): starting an estimation, choosing
// the shown solution, naming the segments, choosing the questions that describe them and taking a change back are
// sent in the background; then the segments of the question alone are fetched (lc_part) and put in place, with a bar
// where they are while the server works. The page does not load again (Ralf, 2026-10-05). A running estimation is
// followed the same way: its bar fills, and when it ends the segments are fetched anew.
(function () {
  "use strict";
  var seq = {};  // per question: only the answer to the last request is put in place

  function status(part, text) {
    var box = part.querySelector(".lc-status");
    if (box) box.textContent = text || "";
  }

  function busy(part, on) {
    part.classList.toggle("lc-busy", on);
    if (on) part.setAttribute("aria-busy", "true"); else part.removeAttribute("aria-busy");
    var bar = part.querySelector(".lc-loading");
    if (on && !bar) {
      bar = document.createElement("div");
      bar.className = "lc-loading";
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-label", de_en("Segmente werden aktualisiert", "Updating segments"));
      bar.appendChild(document.createElement("span"));
      var head = part.querySelector(".res-group-head");
      (head || part.firstChild).after(bar);
    }
    if (!on && bar) bar.remove();
  }

  function load(part, message) {
    var id = part.dataset.lcItem, url = part.dataset.lcUrl;
    var n = seq[id] = (seq[id] || 0) + 1;
    var p = new URLSearchParams(location.search);
    p.delete("go");
    p.set("lc_part", id);
    busy(part, true);
    return fetch(url + "?" + p.toString(), { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (r.status === 409) { location.reload(); return null; }
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(function (html) {
        if (html === null || n !== seq[id]) return;
        var box = document.createElement("div");
        box.innerHTML = html.trim();
        var fresh = box.querySelector(".lc-part");
        if (!fresh) throw new Error("no part");
        part.replaceWith(fresh);
        if (message) status(fresh, message);
        follow(fresh);
      })
      .catch(function () {
        if (n !== seq[id]) return;
        busy(part, false);
        status(part, de_en("Das hat nicht geklappt. Bitte die Seite neu laden.", "That did not work. Please reload the page."));
      });
  }

  // a running estimation: the bar follows its marker, at the end the segments come anew
  function follow(part) {
    var box = part.querySelector("[data-lc-running]");
    if (!box) return;
    var bar = box.querySelector("[data-bar]"), meter = box.querySelector("[role=progressbar]");
    var text = box.querySelector("[data-status]"), failed = 0;
    (function poll() {
      if (!document.body.contains(box)) return;
      fetch(box.dataset.url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          failed = 0;
          if (d.state !== "running" || (box.hasAttribute("data-queued") && !d.queued)) { load(part); return; }
          var pct = Math.round(d.progress * 100);
          bar.style.width = pct + "%"; meter.setAttribute("aria-valuenow", pct);
          if (text) text.textContent = pct + de_en(" %", "%");
          setTimeout(poll, 1500);
        })
        .catch(function () { if (++failed >= 5) load(part); else setTimeout(poll, 3000); });
    })();
  }

  document.addEventListener("submit", function (e) {
    var form = e.target, part = form.closest && form.closest(".lc-part");
    if (!part || e.defaultPrevented) return;
    e.preventDefault();
    var body = new FormData(form);
    if (e.submitter && e.submitter.name) body.append(e.submitter.name, e.submitter.value);
    busy(part, true);
    // to the start of the segments, where the bar runs (Ralf, 2026-10-05)
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var header = document.querySelector(".site-header");
    var top = window.scrollY + part.getBoundingClientRect().top - (header ? header.offsetHeight : 0) - 12;
    window.scrollTo({ top: Math.max(0, top), behavior: reduce ? "auto" : "smooth" });
    fetch(form.action, { method: "POST", body: body, credentials: "same-origin",
                         headers: { "X-Requested-With": "fetch", Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { load(part, j.message); })
      .catch(function () { busy(part, false); status(part, de_en("Das hat nicht geklappt. Bitte die Seite neu laden.", "That did not work. Please reload the page.")); });
  });

  document.querySelectorAll(".lc-part").forEach(follow);
})();
