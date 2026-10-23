// The omega intervals on the results page (templates/surveys/_omega_macros.html): the page comes without them while
// their bootstrap is drawn (results.fill_omega, performance audit 2026-10-06, P1), with "[wird berechnet …]" in their
// place. Here they are asked for (omega_part) and put in place, in the same markup the page has when they are kept.
// The bootstrap runs as a job of its own (app/omega_job.py, 2026-10-07): while it runs the part answers 202 with its
// progress, shown in the placeholders, and the script asks again. No button: the server keeps them under the state of
// the data, the next visit has them at once.
(function () {
  "use strict";
  var slots = document.querySelectorAll("[data-omega-slot]");
  if (!slots.length) return;
  var p = new URLSearchParams(location.search);
  p.delete("go");
  p.set("omega_part", "1");
  var url = location.pathname + "?" + p.toString();
  var WAIT = 2000;

  function waiting(text) {
    document.querySelectorAll("[data-omega-slot].res-wait").forEach(function (slot) { slot.textContent = text; });
  }

  function failed() {
    waiting(de_en("[nicht berechnet: Seite neu laden]", "[not computed: reload the page]"));
  }

  function put(html) {
    var box = document.createElement("template");
    box.innerHTML = html;
    var found = {};
    box.content.querySelectorAll("template[data-omega-for]").forEach(function (t) { found[t.dataset.omegaFor] = t; });
    document.querySelectorAll("[data-omega-slot]").forEach(function (slot) {
      var t = found[slot.dataset.omegaSlot];
      if (t) slot.replaceWith(t.content.cloneNode(true));
      else if (slot.classList.contains("res-wait")) slot.textContent = "[–]";
    });
  }

  function ask() {
    fetch(url, { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (r.status === 202) {
          return r.json().then(function (s) {
            var share = Math.round((s.progress || 0) * 100);
            waiting(s.queued ? de_en("[wartet auf einen freien Platz …]", "[waiting for a free place …]")
                             : de_en("[wird berechnet … {p} %]", "[being computed … {p}%]").replace("{p}", share));
            setTimeout(ask, WAIT);
          });
        }
        if (!r.ok) throw new Error(r.status);
        return r.text().then(put);
      })
      .catch(failed);
  }

  ask();
})();
