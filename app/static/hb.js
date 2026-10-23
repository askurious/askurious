// Hierarchical Bayes on the results page (templates/surveys/_maxdiff_hb.html, _conjoint_hb.html, _hb_longer.html):
// "Individuelle Nutzen schätzen (Hierarchical Bayes)" and "Neu schätzen mit doppelter Länge" are sent in the background, then the HB block of
// the question alone is fetched (hb_part) and put in place, with a bar where it is while the server works. A running
// estimation is followed the same way: its bar fills, and when it ends the block is fetched anew. The page does not
// load again (Ralf, 2026-10-05: all buttons of the results page except the data basis and the grouping).
(function () {
  "use strict";
  var seq = {};  // per question: only the answer to the last request is put in place

  function status(part, text) {
    var box = part.querySelector(".hb-status");
    if (!box) {
      box = document.createElement("p");
      box.className = "muted small hb-status";
      box.setAttribute("role", "status");
      var head = part.querySelector(".res-group-head");
      (head || part.firstChild).after(box);
    }
    box.textContent = text || "";
    box.hidden = !text;
  }

  function busy(part, on) {
    part.classList.toggle("hb-busy", on);
    if (on) part.setAttribute("aria-busy", "true"); else part.removeAttribute("aria-busy");
    part.querySelectorAll("[data-hb-form] button").forEach(function (b) { b.disabled = on; });
    var bar = part.querySelector(".lc-loading");
    if (on && !bar) {
      bar = document.createElement("div");
      bar.className = "lc-loading";
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-label", de_en("HB-Schätzung wird aktualisiert", "Updating HB estimation"));
      bar.appendChild(document.createElement("span"));
      var head = part.querySelector(".res-group-head");
      (head || part.firstChild).after(bar);
    }
    if (!on && bar) bar.remove();
  }

  function load(part, message) {
    var id = part.dataset.hbItem, url = part.dataset.hbUrl;
    var n = seq[id] = (seq[id] || 0) + 1;
    var p = new URLSearchParams(location.search);
    p.delete("go");
    p.set("hb_part", id);
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
        var fresh = box.querySelector(".hb-part");
        if (!fresh) throw new Error("no part");
        part.replaceWith(fresh);
        // the fit and the convergence of the same estimation in the fold "Modellgüte" (UI/UX audit 2026-10-07, S7)
        var quality = box.querySelector(".hb-quality"), old = document.querySelector('.hb-quality[data-hb-quality="' + id + '"]');
        if (quality && old) old.replaceWith(quality);
        // with a current HB result on top, the aggregate logit goes into its fold below, as the page draws it
        if (fresh.hasAttribute("data-hb-current")) {
          var main = document.querySelector('.cm-main[data-cm-item="' + id + '"]');
          var fold = document.querySelector('.cm-fold[data-cm-item="' + id + '"] .cm-fold-body');
          if (main && fold && !fold.contains(main)) {
            var head = main.querySelector(".cm-main-head");
            if (head) head.remove();
            fold.prepend(main);
          }
        }
        if (message) status(fresh, message);
        // the market simulator rests on the HB estimate: static/simulator.js fetches it anew (or for the first time)
        document.dispatchEvent(new CustomEvent("askurious:hb-updated", { detail: { item: id } }));
        follow(fresh);
      })
      .catch(function () {
        if (n !== seq[id]) return;
        busy(part, false);
        status(part, de_en("Das hat nicht geklappt. Bitte die Seite neu laden.", "That did not work. Please reload the page."));
      });
  }

  // a running estimation: the bar follows its marker, at the end the block comes anew
  function follow(part) {
    var box = part.querySelector("[data-hb-running]");
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
        // no answer (signed out, server busy): a few more tries, then the block shows what is going on
        .catch(function () { if (++failed >= 5) load(part); else setTimeout(poll, 3000); });
    })();
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("[data-hb-form]")) return;
    var part = form.closest(".hb-part");
    if (!part) return;  // outside the results page: the form is sent as usual
    e.preventDefault();
    busy(part, true);
    status(part, "");
    fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin",
                         headers: { "X-Requested-With": "fetch", Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { load(part, d.ok && d.state !== "started" ? d.message : (d.ok ? "" : d.message)); })
      .catch(function () { busy(part, false); status(part, de_en("Das hat nicht geklappt. Bitte die Seite neu laden.", "That did not work. Please reload the page.")); });
  });

  document.querySelectorAll(".hb-part").forEach(follow);
})();
