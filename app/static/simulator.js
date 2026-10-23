// Market simulator on the results page (templates/surveys/_simulator.html, app/simulator.py): every change of the
// scenario, adding and removing a product, "Produkt optimieren" and the saved scenarios fetch the fold of the question
// alone (sim_part) and put it in place; the page does not load again and nothing else is computed. The address keeps
// the scenario as it stands (replaceState), so loading the page again shows the same. Without kept results the
// server computes this question alone; only a run of a live vote answers 409, then the whole page loads.
(function () {
  "use strict";
  var seq = {};  // per question: only the answer to the last request is put in place

  function params(form, by) {
    var p = new URLSearchParams();
    new FormData(form).forEach(function (v, k) { p.append(k, v); });
    if (by && by.name) p.append(by.name, by.value);
    p.delete("sim_part");
    return p;
  }

  function current(fold) {
    var form = fold.querySelector(".sim-form");
    return form ? params(form, null) : new URLSearchParams({ sim: fold.dataset.simItem });
  }

  function tidy(fold) {
    fold.querySelectorAll("[data-sim-compute]").forEach(function (b) {
      if (!b.classList.contains("visually-hidden")) b.hidden = true;  // the fields compute on change
    });
  }

  function status(fold, text, undo) {
    var box = fold.querySelector(".sim-status");
    if (!box) return;
    box.textContent = text + " ";
    if (undo) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "button button-secondary button-small";
      b.textContent = de_en("Rückgängig", "Undo");
      b.simUndo = undo;
      box.appendChild(b);
    }
  }

  function load(fold, p, after) {
    var id = fold.dataset.simItem, url = fold.dataset.simUrl;
    var n = seq[id] = (seq[id] || 0) + 1;
    p = new URLSearchParams(p);
    p.delete("sim_part");
    var page = url + "?" + p.toString();
    p.set("sim_part", id);
    var active = document.activeElement, focus = null;
    if (active && fold.contains(active) && active.name) focus = { name: active.name, value: active.value, tag: active.tagName };
    fold.setAttribute("aria-busy", "true");
    fold.classList.add("sim-busy");
    return fetch(url + "?" + p.toString(), { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (r.status === 409) { location.href = page + "#sim-" + id; return null; }
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(function (html) {
        if (html === null || n !== seq[id]) return;
        var box = document.createElement("div");
        box.innerHTML = html.trim();
        var fresh = box.querySelector(".sim-fold");
        if (!fresh) throw new Error("no fold");
        fold.replaceWith(fresh);
        tidy(fresh);
        // the address: the scenario as it now stands (not the step that led there, "+ Produkt" would add again)
        try { history.replaceState(history.state, "", url + "?" + current(fresh).toString() + "#sim-" + id); } catch (e) { /* only the address */ }
        if (focus) {
          var same = Array.prototype.find.call(fresh.querySelectorAll("[name]"), function (el) {
            return el.name === focus.name && el.tagName === focus.tag && (el.tagName === "SELECT" || el.type === "number" || el.value === focus.value);
          });
          (same || fresh.querySelector(".sim-title")).focus();
        }
        if (after) after(fresh);
      })
      .catch(function () {
        if (n !== seq[id]) return;
        fold.removeAttribute("aria-busy");
        fold.classList.remove("sim-busy");
        status(fold, de_en("Das hat nicht geklappt. Bitte die Seite neu laden.", "That did not work. Please reload the page."));
      });
  }

  document.addEventListener("submit", function (e) {
    var form = e.target, fold = form.closest && form.closest(".sim-fold");
    if (!fold || e.defaultPrevented) return;
    e.preventDefault();
    if (form.classList.contains("sim-form")) {
      var by = e.submitter, drop = by && by.dataset.simDrop;
      var before = drop ? params(form, null) : null;
      load(fold, params(form, by), drop ? function (fresh) { status(fresh, drop + de_en(" entfernt.", " removed."), before); } : null);
      return;
    }
    // saving, deleting and taking back the saved scenarios: in the background, then the fold anew
    var body = new FormData(form);
    if (e.submitter && e.submitter.name) body.append(e.submitter.name, e.submitter.value);
    fetch(form.action, { method: "POST", body: body, credentials: "same-origin",
                         headers: { "X-Requested-With": "fetch", Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) {
        var p = current(fold);
        p.delete("sim_load");
        if (j.load !== null && j.load !== undefined) p.set("sim_load", j.load);
        load(fold, p, j.message ? function (fresh) { status(fresh, j.message); } : null);
      })
      .catch(function () { status(fold, de_en("Das hat nicht geklappt. Bitte die Seite neu laden.", "That did not work. Please reload the page.")); });
  });

  document.addEventListener("change", function (e) {
    var el = e.target, form = el.closest && el.closest(".sim-form");
    if (!form) return;
    if (el.type === "number" && !el.checkValidity()) { el.reportValidity(); return; }
    // "Produkt optimieren" not yet on: ticking and choosing the product compute nothing, only the address keeps them
    if (el.closest(".sim-opt") && !form.querySelector('[name="sim_opt"]')) {
      var fold = form.closest(".sim-fold");
      try { history.replaceState(history.state, "", fold.dataset.simUrl + "?" + params(form, null).toString() + "#sim-" + fold.dataset.simItem); } catch (err) { /* only the address */ }
      return;
    }
    // ACBC with a summed price: another level, the list price of the product (the server fills an empty field)
    var level = /^sim_(\d+)_\d+$/.exec(el.name || "");
    if (level && form.closest("[data-sim-summed]")) {
      var price = form.querySelector('[name="sim_' + level[1] + '_price"]');
      if (price) price.value = "";
    }
    load(form.closest(".sim-fold"), params(form, null));
  });

  document.addEventListener("click", function (e) {
    var fold = e.target.closest && e.target.closest(".sim-fold");
    if (!fold) return;
    var undo = e.target.closest("button");
    if (undo && undo.simUndo) { load(fold, undo.simUndo); return; }
    var a = e.target.closest(".sim-scenarios a");
    if (a && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0) {
      e.preventDefault();
      load(fold, new URLSearchParams(a.search));
    }
  });

  // an HB estimate came, ended or changed in place (static/hb.js): the simulator of the question anew, or for the
  // first time (ACBC has one only with HB; Ralf, 2026-10-06: none after "Nutzenwerte schätzen" until the page loaded)
  document.addEventListener("askurious:hb-updated", function (e) {
    var id = String(e.detail && e.detail.item), slot = document.querySelector('[data-sim-slot="' + id + '"]');
    if (!slot) return;
    var fold = slot.querySelector(".sim-fold"), p;
    if (fold) p = current(fold);  // the scenario on the page stays
    else {
      p = new URLSearchParams(location.search);
      Array.from(p.keys()).forEach(function (k) { if (k.indexOf("sim") === 0 || k === "go" || k === "hb_part") p.delete(k); });
    }
    p.delete("sim_part");
    p.set("sim_part", id);
    // numbered with the scenario's own requests (load): whichever was asked for last wins (F23)
    var n = seq[id] = (seq[id] || 0) + 1;
    fetch(slot.dataset.simUrl + "?" + p.toString(), { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html || n !== seq[id]) return;  // no simulator now (an estimation still running): what the slot shows stays
        var box = document.createElement("div");
        box.innerHTML = html.trim();
        var fresh = box.querySelector(".sim-fold");
        if (!fresh) return;
        var now = slot.querySelector(".sim-fold");  // the fold on the page now (a load may have replaced the old one)
        if (now) { fresh.open = now.open; now.replaceWith(fresh); } else slot.replaceChildren(fresh);
        tidy(fresh);
      })
      .catch(function () { /* the page shows it after loading again */ });
  });

  document.querySelectorAll(".sim-fold").forEach(tidy);
})();
