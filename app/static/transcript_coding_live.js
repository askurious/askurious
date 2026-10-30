// Interviews kodieren: the pages of an analysis bring themselves up to date (app/transcript_coding_live.py). Every few
// seconds (data-tc-every), while the window is visible, the page asks whether what it shows is still current; the
// answer without a change is empty. With a change, the coding view takes its new data (window.tcCodeRefresh,
// transcript_coding_code.js); the other pages fetch themselves and swap the parts marked data-tc-live in place, no
// reload, no banner. Nothing is swapped while a passage is marked, a list, popover or dialog is open, something is
// dragged, a field has the focus or holds what is not saved yet: it waits and catches up afterwards.
(function () {
  var marker = document.querySelector("[data-tc-poll]");
  if (!marker || !window.fetch) return;
  var url = marker.getAttribute("data-tc-poll"), every = Math.max(2, Number(marker.getAttribute("data-tc-every")) || 5) * 1000;
  var version = marker.getAttribute("data-tc-version") || "", asking = false, drawing = false;
  var FIELDS = "input, textarea, select, [contenteditable=true]";

  function busy() {
    var a = document.activeElement;
    if (a && a !== document.body && a.matches && a.matches(FIELDS) && a.type !== "checkbox" && a.type !== "radio") return true;
    var sel = window.getSelection && window.getSelection();
    if (sel && !sel.isCollapsed && String(sel).trim()) return true;  // a passage is being marked
    if (document.querySelector("dialog[open], .tc-pop:not([hidden])")) return true;
    if (document.body.classList.contains("tc-dragging") || document.querySelector(".is-dragged")) return true;
    if (document.querySelector("[data-autosave-bar] .is-saving, form.is-saving")) return true;
    // a field of the parts to be swapped holds a value not saved yet
    return Array.prototype.some.call(document.querySelectorAll("[data-tc-live] input, [data-tc-live] textarea"), function (el) {
      if (el.type === "hidden" || el.type === "checkbox" || el.type === "radio" || el.type === "search") return false;
      return el.value !== el.defaultValue && !(el.form && el.form.hasAttribute("data-autosave-form"));
    });
  }

  // what a part shows open or folded stays so after it was drawn anew
  function openState(root) {
    var out = { details: {}, folded: [] };
    root.querySelectorAll("details").forEach(function (d) { out.details[key(d)] = d.open; });
    root.querySelectorAll('[data-tc-fold][aria-expanded="false"]').forEach(function (b) { out.folded.push(b.getAttribute("data-tc-fold")); });
    return out;
  }
  function key(d) {
    var home = d.closest("[id]"), summary = d.querySelector("summary");
    return (home ? home.id : "") + "|" + (summary ? summary.textContent.trim() : "");
  }
  function restore(root, state) {
    root.querySelectorAll("details").forEach(function (d) { var k = key(d); if (k in state.details) d.open = state.details[k]; });
    state.folded.forEach(function (id) {
      var b = root.querySelector('[data-tc-fold="' + id + '"]');
      if (!b) return;
      b.setAttribute("aria-expanded", "false");
      b.classList.add("is-folded");
      document.querySelectorAll('[data-tc-anc~="' + id + '"]').forEach(function (el) { el.hidden = true; });
    });
  }

  function redrawPage() {
    drawing = true;
    return fetch(location.href, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html) return false;
        if (busy()) return false;  // something started meanwhile: once more later
        var fresh = new DOMParser().parseFromString(html, "text/html");
        document.querySelectorAll("[data-tc-live]").forEach(function (el) {
          var now = fresh.querySelector('[data-tc-live="' + el.getAttribute("data-tc-live") + '"]');
          if (!now || el.innerHTML === now.innerHTML) return;
          var state = openState(el);
          el.innerHTML = now.innerHTML;
          restore(el, state);
          if (window.autosaveAdopt) window.autosaveAdopt(el);  // the fields of the part save by themselves again
        });
        ["tc-siblings", "tc-drop-data"].forEach(function (id) {  // what the dialogs and dragging read
          var mine = document.getElementById(id), theirs = fresh.getElementById(id);
          if (mine && theirs) mine.textContent = theirs.textContent;
        });
        document.dispatchEvent(new CustomEvent("tc:redrawn"));
        return true;
      })
      .catch(function () { return false; })
      .then(function (done) { drawing = false; return done; });
  }

  function ask() {
    if (document.hidden || asking || drawing) return;
    if (busy()) return;  // the next round catches up
    asking = true;
    fetch(url + (url.indexOf("?") < 0 ? "?" : "&") + "v=" + encodeURIComponent(version),
          { credentials: "same-origin", headers: { "X-Requested-With": "fetch" }, cache: "no-store" })
      .then(function (r) { return r.status === 200 ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.v || d.v === version) return;
        if (d.data && window.tcCodeRefresh) {  // the coding view: its data at once
          if (window.tcCodeRefresh(d.data)) version = d.v;
          return;
        }
        return redrawPage().then(function (done) { if (done) version = d.v; });
      })
      .catch(function () { /* the next round asks again */ })
      .then(function () { asking = false; });
  }

  setInterval(ask, every);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) ask(); });
})();
