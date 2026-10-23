// The language of these pages (app/lang.py): each text in German and English side by side, the one of <html lang>
// is shown: de_en("Gespeichert", "Saved"). tests/test_researcher_i18n.py keeps German text out of the scripts
// unless it has its English half.
// the text in the page language, with the name of the domain the page came from (data-brand on <html>, app/brand.py)
function de_en(de, en) { var r = document.documentElement, t = r.lang === "en" ? en : de, n = r.getAttribute ? r.getAttribute("data-brand") : null; return n && n !== "Askurious" ? String(t).replace(/\bAskurious\b/g, n) : t; }

// A completed background report is a snapshot; refreshing the page starts a view of the current data.
if (typeof window !== "undefined" && window.location && !document.querySelector("[data-report-wait]")) {
  var reportPageUrl = new URL(window.location.href);
  if (reportPageUrl.searchParams.has("_report_job")) {
    reportPageUrl.searchParams.delete("_report_job");
    window.history.replaceState(null, "", reportPageUrl.toString());
  }
}

// A number in the language of the page, `places` decimals rounded half up (kaufmännisch) from its decimal text as
// the server rounds (units.fixed: 0.285 -> 0.29, 1.005 -> 1.01): a decimal comma in German, a point in English, no
// thousands separators (Mathe-Audit 2026-10-06, Z-7/Z-9)
function uiNum(v, places) {
  var r = (v < 0 ? -1 : 1) * Math.round(Number(Math.abs(v) + "e" + places)) / Math.pow(10, places);
  if (isNaN(r)) r = Math.round(v * Math.pow(10, places)) / Math.pow(10, places);
  var text = (r === 0 ? 0 : r).toFixed(places);
  return document.documentElement.lang === "en" ? text : text.replace(".", ",");
}
// a share as a whole percentage in the language of the page: 29 % in German, 29% in English (units.percent_text);
// times 100 in the decimal text, as units.percent (0.285 * 100 is 28.499999999999996 in binary)
function uiPercent(share) {
  var x = Number(share + "e2");
  return uiNum(isNaN(x) ? share * 100 : x, 0) + de_en(" %", "%");
}

// An action sent in the background (drag and drop in the editor and in the flow) did not work: say so where
// messages appear, instead of leaving the page as if nothing had been tried. status 0: no connection.
function actionFailed(status) {
  var text = pageError((status ? de_en("Das Verschieben wurde nicht übernommen (Fehler " + status + ").", "The move was not applied (error " + status + ").")
                               : de_en("Keine Verbindung zum Server: Das Verschieben wurde nicht übernommen.", "No connection to the server: the move was not applied.")) +
    de_en(" Die Seite zeigt möglicherweise nicht mehr den gespeicherten Stand. ", " The page may no longer show the saved state. "));
  if (!text) return;
  var reload = document.createElement("a");
  reload.href = location.pathname + location.search;
  reload.textContent = de_en("Seite neu laden", "Reload page");
  text.appendChild(reload);
}

// A POST that changes the page and then goes where the server sends it (dragging and "+" in the questionnaire editor
// and the flow): asked with X-Navigate, the server answers its redirect with {"location": ...} instead of drawing the
// target page for the script and the browser once each (app/__init__.py _navigate; code review 2026-10-07, F4)
function postAndGo(url, body) {
  return fetch(url, { method: "POST", body: body, credentials: "same-origin", headers: { "X-Navigate": "1" } })
    .then(function (r) {
      if (!r.ok) { actionFailed(r.status); return; }
      if (r.headers.get("X-Navigate")) return r.json().then(function (d) { window.location.href = d.location; });
      window.location.href = r.url;  // no redirect: the page the server answered with
    }).catch(function () { actionFailed(0); });
}

// Small things without inline code (the CSP allows no inline script), for every page: a select or field with
// data-submit-on-change saves its form at once (requestSubmit, so a question on the form, data-confirm, is asked; on
// the document, so it also holds for parts drawn anew; a number as its value waits that many milliseconds after the
// last change); a field with a link to copy selects all of it when clicked or entered (data-select-all); a button
// prints the page (data-print); a form or link that only shows how something looks does nothing (data-inert). They
// used to come with participant.js, which the researchers' pages no longer load; the participants' pages have the
// same lines there, bound once by whichever script comes first (code review 2026-10-07, F9)
if (!window.qSmallHelpers) {
  window.qSmallHelpers = true;
  document.addEventListener("change", function (e) {
    var el = e.target, form = el.form;
    if (!el.matches || !el.matches("select[data-submit-on-change], input[data-submit-on-change]") || !form) return;
    var send = function () { if (form.requestSubmit) form.requestSubmit(); else form.submit(); };
    var wait = parseInt(el.getAttribute("data-submit-on-change"), 10);
    if (!(wait > 0)) { send(); return; }
    clearTimeout(form._submitLater);
    form._submitLater = setTimeout(send, wait);
  });
  // a field with data-no-drop takes nothing dropped on it: a slide dragged by its text landed in a block name as its
  // address and was saved at once (Ralf, 2026-10-16)
  document.addEventListener("drop", function (e) {
    if (e.target.closest && e.target.closest("[data-no-drop]")) e.preventDefault();
  });
  // a search field with data-filter-select="<id of a select>" shows only the options holding what is typed; one left
  // over is chosen, and Enter chooses the first instead of sending the form (lead of a new group, Ralf, 2026-10-16).
  // With data-filter-keep the choice stays as it is while typing (a select that saves itself, e.g. "Betreuung" on an
  // account's page): only a click on an option or Enter chooses, and that sends "change" so it is saved
  function filterSelect(field) {
    var select = document.getElementById(field.getAttribute("data-filter-select"));
    if (!select) return null;
    var words = field.value.toLowerCase().split(/\s+/).filter(Boolean), shown = [];
    Array.prototype.forEach.call(select.options, function (o) {
      if (!o.value) return;  // "nobody chosen" stays
      var text = o.textContent.toLowerCase();
      o.hidden = !words.every(function (w) { return text.indexOf(w) >= 0; });
      if (!o.hidden) shown.push(o);
    });
    if (field.hasAttribute("data-filter-keep")) {
      // the chosen one stays in view while searching, so the list never looks as if nobody were chosen (2026-10-18)
      var chosen = select.selectedOptions[0];
      if (chosen && chosen.value) chosen.hidden = false;
      return shown;
    }
    if (shown.length === 1 || (words.length && select.selectedOptions[0] && select.selectedOptions[0].hidden)) select.value = shown.length === 1 ? shown[0].value : "";
    return shown;
  }
  document.addEventListener("input", function (e) {
    if (e.target.matches && e.target.matches("[data-filter-select]")) filterSelect(e.target);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || !e.target.matches || !e.target.matches("[data-filter-select]")) return;
    e.preventDefault();
    var shown = filterSelect(e.target), select = document.getElementById(e.target.getAttribute("data-filter-select"));
    if (shown && shown.length && select.value !== shown[0].value) {
      select.value = shown[0].value;
      select.dispatchEvent(new Event("change", {bubbles: true}));
    }
  });
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-select-all], [data-print], a[data-inert]");
    if (!el) return;
    if (el.hasAttribute("data-print")) window.print();
    else if (el.hasAttribute("data-inert")) e.preventDefault();
    else el.select();
  });
  document.addEventListener("focusin", function (e) { if (e.target.matches && e.target.matches("[data-select-all]")) e.target.select(); });
  document.addEventListener("submit", function (e) { if (e.target.matches("form[data-inert]")) e.preventDefault(); });
}

// A message where the server's messages appear (never a browser alert); kind as flash(): "error", "warning" ...
// An error is brought into view; each can be closed and leaves like the server's (flashControls). Returns its text element.
function flash(message, kind) {
  var main = document.querySelector("main");
  if (!main) return null;
  var box = main.querySelector("[data-flashes]");
  if (!box) {
    box = document.createElement("div");
    box.className = "flashes";
    box.setAttribute("data-flashes", "");
    // under the row of tabs (survey, administration) like the server's messages, else at the top
    var tabs = main.querySelector(".tabpage-head, .workspace-head, .admin-tabs");
    if (tabs) tabs.after(box);
    else main.insertBefore(box, main.firstChild);
  }
  var note = document.createElement("div");
  note.className = "flash flash-" + kind;
  note.setAttribute("role", kind === "error" ? "alert" : "status");
  var text = document.createElement("span");
  text.className = "flash-text";
  text.textContent = message;
  note.appendChild(text);
  box.appendChild(note);
  flashControls(note);
  if (kind === "error") box.scrollIntoView({ block: "nearest" });
  return text;
}

// The × of a message and its leaving: information and confirmations go by themselves after 10 s (Ralf,
// 2026-10-15), errors and warnings stay until closed. The clock stands while the pointer or the focus is on the
// message or the tab is hidden, and starts afresh after (WCAG 2.2.1); without motion (prefers-reduced-motion) it
// vanishes without fading (app.css).
var FLASH_SECONDS = 10;
function flashControls(note) {
  if (note.querySelector(".flash-close")) return;
  function leave() {
    note.classList.add("is-leaving");
    setTimeout(function () {
      var box = note.parentNode;
      note.remove();
      if (box && !box.children.length) box.remove();
    }, 400);
  }
  var close = document.createElement("button");
  close.type = "button";
  close.className = "icon-btn flash-close";
  close.setAttribute("aria-label", de_en("Meldung schließen", "Close message"));
  close.title = de_en("Meldung schließen", "Close message");
  close.textContent = "×";
  close.addEventListener("click", leave);
  note.appendChild(close);
  if (note.classList.contains("flash-error") || note.classList.contains("flash-warning")) return;
  var timer = null;
  function stop() { clearTimeout(timer); timer = null; }
  function start() {
    stop();
    if (!note.isConnected || document.hidden || note.matches(":hover") || note.contains(document.activeElement)) return;
    timer = setTimeout(leave, FLASH_SECONDS * 1000);
  }
  note.addEventListener("mouseenter", stop);
  note.addEventListener("focusin", stop);
  note.addEventListener("mouseleave", start);
  note.addEventListener("focusout", function () { setTimeout(start, 0); });
  document.addEventListener("visibilitychange", function () { if (document.hidden) stop(); else start(); });
  start();
}
function pageError(message) { return flash(message, "error"); }

// The arrows ↶ and ↷ of the undo bar after a save: what they take back or do again (app/undo.py)
function undoArrows(undo, redo) {
  [["[data-undo]", undo, de_en("Rückgängig: ", "Undo: "), de_en(" (Strg+Z)", " (Ctrl+Z)"), de_en("Rückgängig: noch keine Änderung", "Undo: no change yet")],
   ["[data-redo]", redo, de_en("Wiederholen: ", "Redo: "), de_en(" (Strg+Y)", " (Ctrl+Y)"), de_en("Wiederholen: nichts zurückgenommen", "Redo: nothing undone")]].forEach(function (a) {
    var button = document.querySelector(a[0]);
    if (!button) return;
    button.disabled = !a[1];
    button.title = a[1] ? a[2] + a[1] + a[3] : a[4];
    button.setAttribute("aria-label", button.title);
  });
  var bar = document.querySelector(".undo-bar");
  if (bar) bar.dataset.stale = "1";  // the list of steps is drawn anew when it is opened
}

// The saved state in the head of every page of a survey (UI/UX audit 2026-10-07, S2/W29; surveys/_tabs.html,
// [data-autosave-head]). Every form and script that saves by itself reports to it, each under its own key (its
// form, row or box): saveState(source, kind, text, place), kind = idle | busy | ok | warn | error | conflict | invalid.
// The head keeps the state of each source: an error, a conflict or a warning stays until that very source has saved
// again; a later good save of another form does not cover it. Several: "⚠ 2 nicht gespeichert" (the ⚠ from app.css,
// as in the lines of the forms) and a link to the
// first place. Without any save on this page it says what the template put there (the person's last change, or
// that changes save by themselves), never "gespeichert" without a save. Returns true when the head took the report:
// the script then shows its own line only for trouble.
// saveHeadNext and saveHeadSummary hold the rules without the page (tests/test_ui8_s2_head.py runs them in Node).
var SAVE_TROUBLE = { error: 1, conflict: 1, invalid: 1 };
function saveHeadNext(s, kind, text, seq) {  // one source's state after a report
  if (kind === "busy") s.busy = true;
  else if (kind === "ok") { s.busy = false; s.trouble = null; s.ok = text; s.at = seq; }
  else if (kind === "warn" || SAVE_TROUBLE[kind]) { s.busy = false; s.trouble = { kind: kind, text: text }; s.at = seq; }
  return s;
}
function saveHeadSummary(list) {  // list: the sources in the order of the page -> what the head says
  var bad = list.filter(function (s) { return s.trouble && SAVE_TROUBLE[s.trouble.kind]; });
  var warn = list.filter(function (s) { return s.trouble && s.trouble.kind === "warn"; });
  if (bad.length > 1) return { kind: "error", long: de_en(bad.length + " nicht gespeichert", bad.length + " not saved"),
                               short: de_en(bad.length + " Fehler", bad.length + " not saved"),  // fits beside ↶ ↷ on a phone
                               title: bad.map(function (s) { return s.trouble.text; }).join("\n"), place: bad[0] };
  if (bad.length) {
    var t = bad[0].trouble;
    return { kind: t.kind, long: t.kind === "conflict" ? de_en("Nicht gespeichert: Jemand anderes hat geändert", "Not saved: someone else changed it")
                                                       : de_en("Nicht gespeichert", "Not saved"),
             short: de_en("nicht gespeichert", "not saved"), title: t.text, place: bad[0] };
  }
  if (warn.length) return { kind: "warn", long: de_en("Gespeichert, mit Hinweis", "Saved, with a note"), short: de_en("Hinweis", "note"),
                            title: warn.map(function (s) { return s.trouble.text; }).join("\n"), place: warn[0] };
  if (list.some(function (s) { return s.busy; })) return { kind: "busy", long: de_en("Wird gespeichert …", "Saving …"), short: de_en("speichert …", "saving …") };
  var last = list.filter(function (s) { return s.ok; }).sort(function (a, b) { return b.at - a.at; })[0];
  if (last) return { kind: "ok", long: last.ok, short: de_en("gespeichert", "saved") };
  return null;  // nothing saved on this page yet: the words of the template stay
}
// end of the rules of the save head
function saveClock() { return new Date().toLocaleTimeString(de_en("de-DE", "en-GB"), { hour: "2-digit", minute: "2-digit" }); }
function saveState() { return false; }  // a page without the head (outside a survey); below: with it
(function () {
  var head = document.querySelector("[data-autosave-head]");
  if (!head) return;
  var long = head.querySelector("[data-autosave-long]"), short = head.querySelector("[data-autosave-short]");
  var go = head.querySelector("[data-autosave-goto]"), list = [], seq = 0, target = null;
  function draw() {
    var say = saveHeadSummary(list);
    head.className = "autosave-status autosave-page" + (say ? " is-" + say.kind : "");
    // an error or a conflict is said once, by the form's line (role=alert, below); the head is silent then
    // (UI/UX audit 2026-10-16, 30)
    head.setAttribute("aria-live", say && SAVE_TROUBLE[say.kind] ? "off" : "polite");
    long.textContent = say ? say.long : head.getAttribute("data-neutral");
    short.textContent = say ? say.short : "";
    if (say && say.title) head.title = say.title; else head.removeAttribute("title");
    target = say && say.place ? say.place.place : null;
    go.hidden = !target;
  }
  go.addEventListener("click", function (e) {
    e.preventDefault();
    if (!target) return;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    var b = target.querySelector && target.querySelector("button:not([hidden]), input, textarea, select");
    if (b) b.focus({ preventScroll: true });
  });
  saveState = function (source, kind, text, place) {
    var s = list.filter(function (x) { return x.source === source; })[0];
    if (!s) {
      s = { source: source, place: place || source, busy: false, trouble: null, ok: null, at: 0 };
      list.push(s);
      // in the order of the page: the link goes to the first place that has something to say
      list.sort(function (a, b) {
        return a.place.compareDocumentPosition && a.place.compareDocumentPosition(b.place) & 2 ? 1 : -1;  // 2: b precedes a
      });
    }
    if (place) s.place = place;
    head.hidden = false;
    saveHeadNext(s, kind, text, ++seq);
    draw();
    return true;
  };
})();

// HTML-escaped text, also for attribute values (quotes)
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
}
// an address fragment as text: a broken escape (#%E0) is taken as it stands instead of throwing a URIError that
// stopped the rest of the script (audit 2026-10-18, INJ-5)
function safeDecode(text) { try { return decodeURIComponent(text); } catch (e) { return text; } }
// the CSRF token of the page (meta csrf-token in base.html), for requests sent from scripts
function csrfToken() { return (document.querySelector('meta[name="csrf-token"]') || {}).content; }
// A signed-in researcher's CSRF token counts CSRF_HOURS (config, audit 2026-10-18, INJ-4), and an editor stays open
// longer: the page fetches a fresh token (data-refresh of the meta tag) every half hour while it is shown and when it
// is shown again, puts it into its forms, and every request of a script and every form sends the newest one.
(function () {
  var meta = document.querySelector('meta[name="csrf-token"][data-refresh]');
  if (!meta || !window.fetch || !window.Promise) return;
  var url = meta.getAttribute("data-refresh"), at = Date.now(), pending = null, send = window.fetch.bind(window);
  var EVERY = 30 * 60 * 1000, BEFORE_SENDING = 2 * 3600 * 1000;
  function spread(token) {
    meta.content = token; at = Date.now();
    document.querySelectorAll('input[name="csrf_token"]').forEach(function (field) { field.value = token; });
    document.querySelectorAll("[data-csrf]").forEach(function (el) { el.setAttribute("data-csrf", token); });
  }
  function refresh() {
    if (!pending) {
      pending = send(url, { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) { if (data && data.token) spread(data.token); }, function () { /* offline: next time */ })
        .then(function () { pending = null; });
    }
    return pending;
  }
  function newest(init) {
    var token = meta.content;
    if (!init) return init;
    if (typeof FormData !== "undefined" && init.body instanceof FormData && init.body.has("csrf_token")) init.body.set("csrf_token", token);
    if (init.body instanceof URLSearchParams && init.body.has("csrf_token")) init.body.set("csrf_token", token);
    var h = init.headers;
    if (h && typeof Headers !== "undefined" && h instanceof Headers) { if (h.has("X-CSRFToken")) h.set("X-CSRFToken", token); }
    else if (h && typeof h === "object" && Object.prototype.hasOwnProperty.call(h, "X-CSRFToken")) h["X-CSRFToken"] = token;
    return init;
  }
  window.fetch = function (input, init) {
    if (Date.now() - at > BEFORE_SENDING) return refresh().then(function () { return send(input, newest(init)); });
    return send(input, newest(init));
  };
  function due() { if (document.visibilityState !== "hidden" && Date.now() - at > EVERY) refresh(); }
  setInterval(due, 5 * 60 * 1000);
  document.addEventListener("visibilitychange", due);
  window.addEventListener("focus", due);
  // a plain form sends the newest token too (capture: before any other handler reads the form)
  document.addEventListener("submit", function (e) {
    var field = e.target && e.target.querySelector && e.target.querySelector('input[name="csrf_token"]');
    if (field && meta.content) field.value = meta.content;
  }, true);
})();

// Questions before a step that cannot be taken back: one dialog in the app's look, never the browser's own
// question box. (What "Rückgängig" takes back is not asked at all, see app/undo.py.)
//   askConfirm({title, note, ok, danger, type}) -> Promise of true (go on) or false
// In templates, on a form or on one of its submit buttons:
//   data-confirm="Frage?"  data-confirm-note="what follows"  data-confirm-ok="Löschen"  data-confirm-danger
//   data-confirm-type="uzevun": for what nothing brings back, the yes button works only once this word is typed
//   in the dialog (Ralf, 2026-10-05: instead of the "Sicherheitsriegel")
// Declined: nothing is sent, and a select with data-current in the form shows its saved value again.
(function () {
  var dialog = null, answer = null, last = Promise.resolve();
  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    dialog.setAttribute("aria-labelledby", "confirm-title");
    dialog.setAttribute("aria-describedby", "confirm-note");
    dialog.innerHTML = '<h2 id="confirm-title"></h2><p id="confirm-note"></p>' +
      '<label class="confirm-type" hidden><span data-type-text></span><input type="text" data-type-input autocomplete="off" spellcheck="false"></label>' +
      '<div class="confirm-actions"><button type="button" class="button button-secondary" data-no>' + de_en("Abbrechen", "Cancel") + '</button>' +
      '<button type="button" class="button" data-yes></button></div>';
    dialog.querySelector("[data-yes]").addEventListener("click", function () { dialog.close("yes"); });
    dialog.querySelector("[data-type-input]").addEventListener("input", function (e) {
      dialog.querySelector("[data-yes]").disabled = e.target.value.trim().toLowerCase() !== (dialog.dataset.expect || "").toLowerCase();
    });
    dialog.querySelector("[data-no]").addEventListener("click", function () { dialog.close("no"); });
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close("no"); });  // beside it
    dialog.addEventListener("cancel", function () { dialog.returnValue = "no"; });                     // Escape
    dialog.addEventListener("close", function () {
      var done = answer;
      answer = null;
      if (done) done(dialog.returnValue === "yes");
    });
    document.body.appendChild(dialog);
  }
  window.askConfirm = function (opts) {
    if (!window.HTMLDialogElement) {  // very old browsers: their own question is better than none
      return Promise.resolve(window.confirm(opts.title + (opts.note ? "\n\n" + opts.note : "")));
    }
    // One question at a time: a second one, asked while the first is still open, waits for its answer. (Closing
    // the first instead does not work: its "close" event comes later and would answer the second with no.)
    function next() { return ask(opts); }
    return last = last.then(next, next);  // also after a question that could not be shown
  };
  function ask(opts) {
    if (!dialog) build();
    var yes = dialog.querySelector("[data-yes]"), note = dialog.querySelector("#confirm-note");
    dialog.querySelector("#confirm-title").textContent = opts.title;
    note.textContent = opts.note || "";
    note.hidden = !opts.note;
    yes.textContent = opts.ok || "OK";
    yes.classList.toggle("button-danger", !!opts.danger);
    var typed = dialog.querySelector(".confirm-type"), input = dialog.querySelector("[data-type-input]");
    typed.hidden = !opts.type;
    input.value = "";
    dialog.dataset.expect = opts.type || "";
    dialog.querySelector("[data-type-text]").textContent = opts.type ? de_en("Zum Bestätigen „" + opts.type + "“ eintippen", "Type “" + opts.type + "” to confirm") : "";
    yes.disabled = !!opts.type;  // with a word to type: only once it is there
    dialog.returnValue = "no";
    return new Promise(function (resolve) {
      answer = resolve;
      dialog.style.width = dialog.style.marginLeft = dialog.style.marginTop = "";
      dialog.showModal();
      // A page wider than a phone's screen: the browser would centre the dialog in the whole page width,
      // half of it out of sight. Centre it in what is visible.
      var vv = window.visualViewport;
      if (vv && vv.width < window.innerWidth - 1) {
        var w = Math.min(460, vv.width - 32);
        dialog.style.width = w + "px";
        dialog.style.marginLeft = (vv.offsetLeft + (vv.width - w) / 2) + "px";
        dialog.style.marginTop = (vv.offsetTop + Math.max(16, (vv.height - dialog.offsetHeight) / 2)) + "px";
      }
      (opts.type ? input : opts.danger ? dialog.querySelector("[data-no]") : yes).focus();  // Enter never deletes by accident
    });
  }
})();

// The questionnaire on a phone: "⋯" of a row opens its tools (↑ ↓, other block, bin) at its top right; a click
// elsewhere or Escape closes them (UI audit 2026-10-12, U56)
(function () {
  function close(except) {
    document.querySelectorAll(".item-row.tools-open").forEach(function (row) {
      if (row === except) return;
      row.classList.remove("tools-open");
      var b = row.querySelector("[data-row-menu]");
      if (b) b.setAttribute("aria-expanded", "false");
    });
  }
  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;
    if (e.target.closest(".item-row.tools-open > .toolbar")) return;  // a tool: its form goes as it is
    var btn = e.target.closest("[data-row-menu]");
    var row = btn && btn.closest(".item-row");
    close(row);
    if (!row) return;
    var open = row.classList.toggle("tools-open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var open = document.querySelector(".item-row.tools-open");
    if (!open) return;
    close(null);
    var b = open.querySelector("[data-row-menu]");
    if (b) b.focus();
  });
})();

// Before every other listener (capture): the form is only sent, and only then seen by them, after a yes
document.addEventListener("submit", function (e) {
  var form = e.target, by = e.submitter;
  var src = by && by.hasAttribute("data-confirm") ? by : form.hasAttribute("data-confirm") ? form : null;
  if (!src) return;
  if (form.confirmedOnce) { form.confirmedOnce = false; return; }
  e.preventDefault();
  e.stopPropagation();
  askConfirm({ title: src.dataset.confirm, note: src.dataset.confirmNote, ok: src.dataset.confirmOk,
               danger: src.hasAttribute("data-confirm-danger"), type: src.dataset.confirmType }).then(function (yes) {
    if (!yes) {
      form.querySelectorAll("select[data-current]").forEach(function (s) { s.value = s.dataset.current; });
      // a tick that asks first (the seal chain, U16): "Nein" puts it back as it was
      form.querySelectorAll("input[type=checkbox][data-current]").forEach(function (c) { c.checked = c.dataset.current === "1"; });
      return;
    }
    form.confirmedOnce = true;
    if (form.requestSubmit) form.requestSubmit(by || undefined); else form.submit();
  });
}, true);

// Dragging with the pointer: mouse, pen and finger alike (questionnaire editor, flow page). The browser's own
// drag and drop ends at once in Chromium when the page layout changes at its start, cannot take a button, and
// most touch screens do not have it.
// Mouse and pen: press and move. Finger: on a handle (.drag-handle) at once, elsewhere after holding still for
// a moment, since a finger that moves at once scrolls the page. Escape ends a drag without a drop.
//   source(el)  what is taken when the pointer goes down on `el`: an object, or nothing
//   start(d)    the drag begins; d.ghost, if set here, follows the pointer
//   move(d)     the pointer is at d.x, d.y (also called when the page scrolled along)
//   drop(d)     released
//   end(d)      the drag is over, with or without a drop
//   scroller()  the element that scrolls along near its upper and lower edge; nothing: the window
function pointerDrag(opts) {
  var SLOP = 6, TOUCH_SLOP = 10, HOLD = 350, EDGE = 56;
  var d = null;  // what is held: the object from source() plus pointer, x, y, touch, active
  var stopped = null;  // after Escape, until the pointer is released: { pointer, touch }

  function move() {
    if (d.ghost) d.ghost.style.transform = "translate(" + (d.x + 16) + "px, " + (d.y + 18) + "px)";
    opts.move(d);
  }
  function start() {
    clearTimeout(d.timer);
    d.active = true;
    if (window.getSelection) window.getSelection().removeAllRanges();  // pressing on text had begun to select it
    document.body.classList.add("drag-active");
    opts.start(d);
    if (d.ghost) { d.ghost.classList.add("drag-ghost"); document.body.appendChild(d.ghost); }
    if (d.touch && navigator.vibrate) navigator.vibrate(10);
    move();
    requestAnimationFrame(scrollAlong);
  }
  function scrollAlong() {
    if (!d || !d.active) return;
    var inner = opts.scroller ? opts.scroller() : null, box = inner ? inner.getBoundingClientRect() : null;
    var top = box ? box.top : 0, bottom = box ? box.bottom : window.innerHeight;
    var by = d.y < top + EDGE ? d.y - top - EDGE : d.y > bottom - EDGE ? d.y - bottom + EDGE : 0;
    if (by) {
      by = Math.max(-18, Math.min(18, by / 3));
      if (inner) inner.scrollTop += by; else window.scrollBy(0, by);
      move();
    }
    requestAnimationFrame(scrollAlong);
  }
  function finish() {
    if (!d) return;
    clearTimeout(d.timer);
    if (d.ghost) d.ghost.remove();
    document.body.classList.remove("drag-active");
    if (d.active) opts.end(d);
    d = null;
  }
  function swallowClick(ms) {  // the release at the end of a drag is not a click on what lies under it
    function eat(e) { e.stopPropagation(); e.preventDefault(); }
    window.addEventListener("click", eat, true);
    setTimeout(function () { window.removeEventListener("click", eat, true); }, ms);
  }

  document.addEventListener("pointerdown", function (e) {
    if (d || !e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) return;
    stopped = null;
    var source = opts.source(e.target);
    if (!source) return;
    d = source;
    d.pointer = e.pointerId;
    d.x = d.x0 = e.clientX; d.y = d.y0 = e.clientY;
    d.touch = e.pointerType === "touch";
    d.hold = d.touch && !e.target.closest(".drag-handle");
    if (d.hold) d.timer = setTimeout(function () { if (d === source && !d.active) start(); }, HOLD);
  });
  document.addEventListener("pointermove", function (e) {
    if (!d || e.pointerId !== d.pointer) return;
    d.x = e.clientX; d.y = e.clientY;
    if (!d.active) {
      if (Math.abs(d.x - d.x0) + Math.abs(d.y - d.y0) < (d.touch ? TOUCH_SLOP : SLOP)) return;
      if (d.hold) { finish(); return; }  // the finger moved before the hold was over: the page scrolls
      start();
    }
    e.preventDefault();
    move();
  });
  document.addEventListener("pointerup", function (e) {
    if (stopped && e.pointerId === stopped.pointer) { swallowClick(stopped.touch ? 400 : 0); stopped = null; return; }
    if (!d || e.pointerId !== d.pointer) return;
    if (d.active) { swallowClick(d.touch ? 400 : 0); move(); opts.drop(d); }
    finish();
  });
  document.addEventListener("pointercancel", function (e) { if (d && e.pointerId === d.pointer) finish(); });
  document.addEventListener("keydown", function (e) {  // Escape: back to where it was
    if (e.key !== "Escape" || !d || !d.active) return;
    stopped = { pointer: d.pointer, touch: d.touch };
    finish();
  });
  // a drag by touch must not scroll the page or open the context menu
  document.addEventListener("touchmove", function (e) { if (d && d.active && e.cancelable) e.preventDefault(); }, { passive: false });
  document.addEventListener("contextmenu", function (e) { if (d && d.touch) e.preventDefault(); });
}

// Tabs: <div data-tabs> with buttons role="tab" aria-controls="panel-id"
document.querySelectorAll("[data-tabs]").forEach(function (box) {
  var tabs = Array.prototype.slice.call(box.querySelectorAll('[role="tab"]'));
  function select(tab) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
  }
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { select(t); });
    t.addEventListener("keydown", function (e) {
      var d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (d) { var n = tabs[(i + d + tabs.length) % tabs.length]; select(n); n.focus(); }
    });
  });
});

// Drop zones: show the chosen file name, highlight on drag
document.querySelectorAll(".dropzone").forEach(function (zone) {
  var input = zone.querySelector("input[type=file]");
  var name = zone.querySelector(".dropzone-file");
  input.addEventListener("change", function () {
    var n = input.files.length;
    name.textContent = n > 1 ? de_en(n + " Dateien", n + " files") : n ? input.files[0].name : "";
  });
  ["dragenter", "dragover"].forEach(function (ev) { zone.addEventListener(ev, function () { zone.classList.add("drag"); }); });
  ["dragleave", "drop"].forEach(function (ev) { zone.addEventListener(ev, function () { zone.classList.remove("drag"); }); });
});

// Rows ordered by hand outside the generic tables of the element editors (MaxDiff statements, CBC/ACBC levels, the
// loop table; macro row_tools, W32): ↑ ↓ × and the handle (dragging below, with the generic rows). The first row
// cannot go up, the last not down; the list hears "rows:changed" (numbers anew). Autosave hears the button click.
var X_ICON = '<svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
function rowToolsCell(removeLabel) {  // the cell of the macro row_tools, for rows made here
  var td = document.createElement("td");
  td.className = "cj-row-tools row-tools";
  var handle = document.createElement("span");
  handle.className = "drag-handle row-drag";
  handle.setAttribute("aria-hidden", "true");  // the keyboard has ↑ ↓
  handle.title = de_en("Ziehen zum Umsortieren", "Drag to reorder");
  handle.textContent = "⋮⋮";
  td.appendChild(handle);
  [["up", "↑", de_en("nach oben", "move up")], ["down", "↓", de_en("nach unten", "move down")], ["remove", "", removeLabel]].forEach(function (t) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "icon-btn" + (t[0] === "remove" ? " danger" : "");
    b.setAttribute("data-row", t[0]);
    b.title = t[2];
    b.setAttribute("aria-label", t[2]);
    if (t[0] === "remove") b.innerHTML = X_ICON; else b.textContent = t[1];
    td.appendChild(b);
  });
  return td;
}
function rowEdges(rows) {
  var list = rows.children;
  Array.prototype.forEach.call(list, function (tr, n) {
    var up = tr.querySelector('[data-row="up"]'), down = tr.querySelector('[data-row="down"]');
    if (up) up.disabled = n === 0;
    if (down) down.disabled = n === list.length - 1;
  });
}
function rowsChanged(rows) {
  rowEdges(rows);
  rows.dispatchEvent(new CustomEvent("rows:changed", { bubbles: true }));
}
document.querySelectorAll("[data-row-list]").forEach(rowEdges);
document.addEventListener("click", function (e) {
  var b = e.target.closest && e.target.closest("[data-row-list] > tr > td > [data-row]");
  if (!b) return;
  var tr = b.closest("tr"), rows = tr.parentNode, action = b.getAttribute("data-row");
  if (action === "remove") tr.remove();
  else if (action === "up" && tr.previousElementSibling) rows.insertBefore(tr, tr.previousElementSibling);
  else if (action === "down" && tr.nextElementSibling) rows.insertBefore(tr.nextElementSibling, tr);
  rowsChanged(rows);
  if (action !== "remove") b.focus();
});

// "In anderer Befragung verwenden": no survey chosen beforehand (W27); as a copy the button stays locked until one
// is; linked it inserts the linked element there, or without a survey only makes the question linkable; the name
// field only for "Verknüpft" (Ralf, 2026-10-08)
document.querySelectorAll("[data-copy-target]").forEach(function (select) {
  var form = select.form, go = form.querySelector("[data-copy-go]"), name = form.querySelector("[data-use-name]");
  function sync() {
    var mode = form.querySelector("[data-use-mode]:checked");
    var link = !!mode && mode.value === "link";
    go.disabled = !select.value && !link;
    go.textContent = link ? (select.value ? go.dataset.linkWord : go.dataset.onlyWord) : go.dataset.copyWord;
    if (name) name.hidden = !link;
  }
  form.addEventListener("change", sync);
  sync();
});

// Results, settings and data collection page: open or close every item at once with one button (_macros.html,
// fold_all_button): "aufklappen" while an item is closed, else "zuklappen"; also when single items are folded by
// hand (UI audit 2026-10-12, U10).
var FOLDS = "details.res-fold, details.settings-group";
var foldAll = document.querySelectorAll("[data-fold-all]");
function foldAllShow() {
  var closed = !!document.querySelector("details.res-fold:not([open]), details.settings-group:not([open])");
  foldAll.forEach(function (btn) {
    btn.dataset.foldAction = closed ? "open" : "close";
    btn.textContent = closed ? btn.dataset.labelOpen : btn.dataset.labelClose;
    btn.setAttribute("aria-expanded", closed ? "false" : "true");
  });
}
foldAll.forEach(function (btn) {
  btn.addEventListener("click", function () {
    var open = btn.dataset.foldAction === "open";
    document.querySelectorAll(FOLDS).forEach(function (d) { d.open = open; });
    foldAllShow();
  });
});
// Results page: a link to a place inside folds ("Unterschiede auf einen Blick" to the folded comparison of a question,
// UI/UX audit 2026-10-07, S6; an address with #lc-12 or #holdout-12 in the folded parts of a choice model, S7) opens
// every fold around it first, then goes there.
(function () {
  var page = document.querySelector(".results-page");
  if (!page) return;
  function reveal(id) {
    var target = id && document.getElementById(id);
    if (!target || !page.contains(target)) return false;
    for (var d = target; d; d = d.parentElement && d.parentElement.closest("details")) {
      if (d.tagName === "DETAILS") d.open = true;
    }
    if (target.tagName === "DETAILS") target.open = true;
    foldAllShow();
    target.scrollIntoView({ block: "start" });
    return true;
  }
  page.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    var id = safeDecode(a.getAttribute("href").slice(1));
    var target = id && document.getElementById(id);
    if (!target || !target.closest("details:not([open])") && !(target.tagName === "DETAILS" && !target.open)) return;
    e.preventDefault();
    try { history.pushState(history.state, "", "#" + id); } catch (err) { /* only the address */ }
    reveal(id);
  });
  if (location.hash) reveal(safeDecode(location.hash.slice(1)));
  window.addEventListener("hashchange", function () { reveal(safeDecode(location.hash.slice(1))); });
})();
// Settings and Datenerhebung: the groups come open; which ones were folded is remembered by the browser, per
// page; a link to an anchor in a group (also one of the page before the groups, e.g. #panel or #end-texts) opens
// that group and scrolls to the anchor. Groups the server opens (a refused form, the people search:
// data-open-now) stay open.
(function () {
  var groups = document.querySelectorAll("details.settings-group");
  if (!groups.length) return;
  var KEY = "folded:" + (/\/field$/.test(location.pathname) ? "field" : "settings"), saved = null;
  try { saved = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { saved = null; }
  if (Array.isArray(saved)) groups.forEach(function (d) { if (saved.indexOf(d.id) >= 0 && !d.hasAttribute("data-open-now")) d.open = false; });
  function toAnchor() {
    var id = safeDecode(location.hash.slice(1)), target = id && document.getElementById(id);
    var group = target && target.closest("details.settings-group");
    if (!group) return;
    group.open = true;
    target.scrollIntoView({ block: "start" });
  }
  toAnchor();
  window.addEventListener("hashchange", toAnchor);
  document.addEventListener("click", function (e) {  // a link to an anchor of this page, e.g. „Teilnahmelinks und Panels“
    var a = e.target.closest && e.target.closest('a[href*="#"]');
    if (a && a.pathname === location.pathname && a.hash === location.hash) setTimeout(toAnchor);
  });
  groups.forEach(function (d) {
    d.addEventListener("toggle", function () {
      var folded = [];
      groups.forEach(function (g) { if (!g.open) folded.push(g.id); });
      try { localStorage.setItem(KEY, JSON.stringify(folded)); } catch (e) { /* remembered for this visit only */ }
    });
  });
})();
foldAllShow();
// Results page: a running hierarchical Bayes estimation is followed by static/hb.js (in place, no reload)
// Maps and charts inside a folded item measure themselves on window resize
document.addEventListener("toggle", function (e) {
  if (!e.target.matches || !e.target.matches(FOLDS)) return;
  if (e.target.open) window.dispatchEvent(new Event("resize"));
  foldAllShow();
}, true);

// Undo and redo (editor/_undo.html): Ctrl+Z and Ctrl+Y (or Ctrl+Shift+Z) press ↶ and ↷ unless the cursor is
// in a field, where the browser's own undo for typed characters stays; the list unfolds its older steps
document.addEventListener("keydown", function (e) {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
  var key = e.key.toLowerCase(), t = e.target;
  if (key !== "z" && key !== "y") return;
  if (t && t.closest && t.closest("input, textarea, select, [contenteditable]")) return;
  var btn = document.querySelector(key === "y" || e.shiftKey ? "[data-redo]" : "[data-undo]");
  if (!btn || document.querySelector("dialog[open]")) return;
  e.preventDefault();
  if (!btn.disabled) btn.click();
});
document.addEventListener("click", function (e) {
  var more = e.target.closest && e.target.closest("[data-undo-more]");
  if (!more) return;
  more.parentNode.querySelectorAll("[data-undo-older]").forEach(function (li) { li.hidden = false; });
  more.remove();
});

// Close open <details class="menu"> when clicking elsewhere
document.addEventListener("click", function (e) {
  document.querySelectorAll("details.menu[open]").forEach(function (d) { if (!d.contains(e.target)) d.open = false; });
});
// The menu in the title bar also closes with Escape; the focus goes back to its button
document.addEventListener("keydown", function (e) {
  var menu = e.key === "Escape" && document.querySelector("details[data-nav-menu][open]");
  if (!menu) return;
  menu.open = false;
  menu.querySelector("summary").focus();
});

// ---------------------------------------------------------------------------
// Questionnaire editor: palette clicks, dragging of new and existing items, and of blocks
(function () {
  var grid = document.querySelector("[data-editor]");
  if (!grid) return;
  var csrf = csrfToken();
  var addUrl = grid.dataset.addUrl;      // .../blocks/0/items?lang=..
  var placeUrl = grid.dataset.placeUrl;  // .../items/0/place?lang=..
  var blockPlaceUrl = grid.dataset.blockPlaceUrl;  // .../blocks/0/place?lang=..

  function post(url, data) {
    var body = new FormData();
    Object.keys(data).forEach(function (k) { body.append(k, data[k]); });
    body.append("csrf_token", csrf);
    return postAndGo(url, body);  // the server redirects to the right place
  }
  function urlFor(template, id) { return template.replace(/\/0\//, "/" + id + "/"); }

  // "Skala mit KI entwerfen": a dialog asks for the construct idea, then the server drafts the items
  var dialog = document.querySelector("[data-ai-dialog]");
  function openAiDialog(blockId, position, idea) {
    if (!dialog) return;
    var form = dialog.querySelector("[data-ai-form]");
    form.action = urlFor(form.dataset.url, blockId);
    form.querySelector("[name=position]").value = position;
    if (idea) form.querySelector("[name=idea]").value = idea;
    dialog.showModal();
    form.querySelector("[name=idea]").focus();
  }
  window.openAiDialog = function (idea) {
    var blocks = document.querySelectorAll(".block[data-block-id]");
    var block = document.querySelector(".block.is-active[data-block-id]") || blocks[blocks.length - 1];
    if (block) openAiDialog(block.dataset.blockId, "", idea);
  };
  if (dialog) dialog.querySelector("[data-ai-cancel]").addEventListener("click", function () { dialog.close(); });

  // The "active" block receives clicked palette items
  function activate(block) {
    document.querySelectorAll(".block.is-active").forEach(function (b) { b.classList.remove("is-active"); });
    block.classList.add("is-active");
    showInsertionTarget();
  }
  function showInsertionTarget() {
    var blocks = grid.querySelectorAll(".block[data-block-id]");
    var block = grid.querySelector(".block.is-active[data-block-id]") || blocks[blocks.length - 1];
    var hint = grid.querySelector("[data-insertion-target]");
    if (hint) hint.textContent = block ? de_en("Einfügen am Ende von: ", "Insert at the end of: ") +
      ((block.querySelector('[name="title"]') || {}).value || block.dataset.insertionLabel || block.id) :
      de_en("Zuerst einen Block anlegen.", "Create a block first.");
  }
  showInsertionTarget();
  grid.addEventListener("input", function (e) { if (e.target.matches("[data-block-name]")) showInsertionTarget(); });
  document.querySelectorAll(".block[data-block-id]").forEach(function (b) {
    b.addEventListener("mousedown", function () { activate(b); });
    b.addEventListener("focusin", function () { activate(b); });
  });

  // "+" next to a palette element adds it at the end of the highlighted block
  // (delegated: construct rows are re-rendered by the sidebar search)
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".palette-add");
    if (!btn) return;
    var blocks = document.querySelectorAll(".block[data-block-id]");
    var block = document.querySelector(".block.is-active[data-block-id]") || blocks[blocks.length - 1];
    if (!block) { alertNoBlock(); return; }
    if (btn.dataset.type === "ai_scale") { openAiDialog(block.dataset.blockId, ""); return; }
    post(urlFor(addUrl, block.dataset.blockId), { type: btn.dataset.type });
  });
  function alertNoBlock() {
    var empty = document.querySelector(".empty-state");
    if (empty) empty.classList.add("flash-warning");
  }

  // ---- Dragging: palette elements, the questionnaire's elements, and blocks (pointerDrag above) ----
  // An element is taken anywhere on its row and a block anywhere on its head, except on what is clicked or
  // typed into. Nothing moves on the page while dragging: lines show where the drop goes, the server does
  // the move.
  var canvas = grid.querySelector(".canvas");
  var movedKey = "editor-moved:" + location.pathname;
  var CONTROLS = "button, input, select, textarea, label, summary, a.button, [contenteditable=true]";
  var REACH = 10, OUTSIDE = 26;
  var drag = null;  // what is being dragged: { kind: "new" | "item" | "block", el, type, x, y, target, ... }

  var marker = document.createElement("li");  // between the elements of a block
  marker.className = "drop-marker";
  var blockMarker = document.createElement("div");  // between the blocks, for a dragged block
  blockMarker.className = "block-drop-marker";
  var gapMarker = document.createElement("div");  // between the blocks, for an element: it gets a block of its own
  gapMarker.className = "block-gap-marker";
  gapMarker.innerHTML = "<span>" + de_en("Neuer Block", "New block") + "</span>";

  function keepInPlace(el, change) {  // run `change`, then scroll so that `el` stays where it is on screen
    var top = el.getBoundingClientRect().top;
    change();
    canvas.scrollTop += el.getBoundingClientRect().top - top;
    window.scrollBy(0, el.getBoundingClientRect().top - top);  // narrow screens: the page scrolls, not the canvas
  }
  function isBlock(el) { return el && el.classList.contains("block") ? el : null; }
  function rowsOf(list) {  // the block's elements without the dragged one
    return Array.prototype.filter.call(list.querySelectorAll(":scope > .item-row"), function (r) { return !drag || r !== drag.el; });
  }
  function indexAt(list, y) {
    var rows = rowsOf(list);
    for (var i = 0; i < rows.length; i++) {
      var box = rows[i].getBoundingClientRect();
      if (y < box.top + box.height / 2) return { index: i, before: rows[i] };
    }
    return { index: rows.length, before: list.querySelector(":scope > .drop-hint") };
  }

  function sourceAt(el) {
    if (!el || !el.closest) return null;
    if (!el.closest(".drag-handle") && el.closest(CONTROLS)) return null;
    var piece = el.closest(".palette-item");
    if (piece) return { kind: "new", el: piece, type: piece.dataset.type };
    var row = el.closest(".item-row[data-drag]");
    if (row) return { kind: "item", el: row };
    var head = el.closest(".block-head");
    if (head && head.querySelector("[data-block-handle]")) return { kind: "block", el: head.closest(".block") };
    return null;
  }

  // Where an element would go: into a block's list, or between two blocks (also before the first and after
  // the last one of an area) into a new block. The strip between two blocks reaches a little into both.
  function itemTarget() {
    var area = canvas.getBoundingClientRect(), x = drag.x, y = drag.y;
    if (x < area.left || x > area.right || y < area.top || y > area.bottom) return null;
    var home = drag.kind === "item" ? drag.el.closest(".block") : null;
    var alone = home && home.querySelectorAll(".item-row").length === 1;  // a block of its own already
    var blocks = canvas.querySelectorAll(".block[data-drag-id]");
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i], box = b.getBoundingClientRect();
      if (!box.height) continue;  // in a closed section
      var prev = isBlock(b.previousElementSibling), next = isBlock(b.nextElementSibling);
      var below = next ? next.getBoundingClientRect().top : box.bottom + 18;
      var gap = null;
      if (!prev && y >= box.top - OUTSIDE && y < box.top + REACH) gap = { block: b, mode: "before", y: box.top - 9 };
      else if (y >= box.bottom - REACH && y < (next ? below + REACH : box.bottom + OUTSIDE)) gap = { block: b, mode: "after", y: (box.bottom + below) / 2 };
      if (gap) {
        if (drag.type === "ai_scale") return null;  // its dialog adds to an existing block
        if (alone && (b === home || (gap.mode === "after" && next === home))) return null;
        return gap;
      }
      if (y < box.top || y >= box.bottom) continue;
      var list = b.querySelector("[data-dropzone]");
      if (!list) return null;  // the consent stays alone in its block
      var pos = indexAt(list, y);
      // dropped where it already is: nothing to do
      if (b === home && pos.index === Array.prototype.indexOf.call(list.querySelectorAll(":scope > .item-row"), drag.el)) return null;
      return { block: b, list: list, index: pos.index, before: pos.before };
    }
    return null;
  }
  // Where a block would go: before or after the nearest block. Meanwhile the blocks show only their heads.
  function blockTarget() {
    var target = null, mode = null;
    Array.prototype.some.call(canvas.querySelectorAll(".block[data-drag-id]"), function (b) {
      var box = b.getBoundingClientRect();
      if (!box.height) return false;  // in a closed section
      target = b;
      mode = drag.y < box.top + box.height / 2 ? "before" : "after";
      return mode === "before";
    });
    return target && target !== drag.el ? { block: target, mode: mode } : null;  // onto itself: stays
  }

  function clearMarks() {
    [marker, blockMarker, gapMarker].forEach(function (m) { if (m.parentNode) m.parentNode.removeChild(m); });
  }
  function update() {
    // a closed separated section opens under the pointer, so that something can be dropped there
    var under = document.elementFromPoint(drag.x, drag.y);
    var closed = under && under.closest("details[data-area]:not([open]):not(.trash-area)");
    if (closed) closed.open = true;
    clearMarks();
    var t = drag.target = drag.kind === "block" ? blockTarget() : itemTarget();
    if (!t) return;
    if (drag.kind === "block") t.block.parentNode.insertBefore(blockMarker, t.mode === "before" ? t.block : t.block.nextSibling);
    else if (t.list) t.list.insertBefore(marker, t.before);
    else {
      var area = canvas.getBoundingClientRect(), box = t.block.getBoundingClientRect();
      gapMarker.style.top = (t.y - area.top + canvas.scrollTop) + "px";
      gapMarker.style.left = (box.left - area.left + canvas.scrollLeft) + "px";
      gapMarker.style.width = box.width + "px";
      canvas.appendChild(gapMarker);
    }
  }

  function drop() {
    var t = drag.target;
    if (!t) return;
    var blockId = t.block.dataset.dragId;
    // after the reload the page shows what was moved, marked briefly
    try { if (drag.kind !== "new") sessionStorage.setItem(movedKey, drag.el.id); } catch (err) { /* storage unavailable */ }
    if (drag.kind === "block") { post(urlFor(blockPlaceUrl, drag.el.dataset.dragId), { target: blockId, mode: t.mode }); return; }
    var where = t.list ? { position: t.index } : { new_block: t.mode };
    if (drag.kind === "item") {
      where.block_id = blockId;
      post(urlFor(placeUrl, drag.el.dataset.itemId), where);
    } else if (drag.type === "ai_scale") {
      openAiDialog(blockId, t.index);
    } else {
      where.type = drag.type;
      post(urlFor(addUrl, blockId), where);
    }
  }

  pointerDrag({
    source: sourceAt,
    scroller: function () { return /auto|scroll/.test(getComputedStyle(canvas).overflowY) ? canvas : null; },  // narrow screens: the window
    start: function (d) {
      drag = d;
      d.el.classList.add("is-dragged");
      d.ghost = document.createElement("div");
      if (d.kind === "block") {
        var name = d.el.querySelector("[data-block-name]"), meta = d.el.querySelector(".block-meta, .label-title");
        d.ghost.textContent = (name && name.value.trim()) || (meta ? meta.textContent.split("·")[0].trim() : de_en("Block", "Block"));
        keepInPlace(d.el, function () { grid.classList.add("dragging-block"); });
      } else {
        d.ghost.appendChild((d.el.querySelector(".item-main") || d.el).cloneNode(true));
      }
    },
    move: update,
    drop: drop,
    end: function (d) {
      clearMarks();
      if (grid.classList.contains("dragging-block")) keepInPlace(d.el, function () { grid.classList.remove("dragging-block"); });
      d.el.classList.remove("is-dragged");
      drag = null;
    },
  });
  // not the browser's own dragging of links and images in a row
  grid.addEventListener("dragstart", function (e) { if (sourceAt(e.target)) e.preventDefault(); });

  // Keep the scroll position when the page reloads after an action
  var key = "editor-scroll:" + location.pathname;
  try {
    // on wide screens the middle column scrolls, not the window (editor as an app shell)
    var canvas = document.querySelector(".canvas");
    var inner = canvas && /auto|scroll/.test(getComputedStyle(canvas).overflowY) ? canvas : null;
    var y = sessionStorage.getItem(key);
    if (y && !location.hash) { if (inner) inner.scrollTop = parseInt(y, 10); else window.scrollTo(0, parseInt(y, 10)); }
    window.addEventListener("beforeunload", function () {
      sessionStorage.setItem(key, String(inner ? inner.scrollTop : window.scrollY));
    });
    // ... and after a drag, the page comes back showing what was moved, marked briefly
    var moved = document.getElementById(sessionStorage.getItem(movedKey) || "");
    sessionStorage.removeItem(movedKey);
    if (moved) {
      moved.scrollIntoView({ block: moved.classList.contains("block") ? "center" : "nearest" });
      moved.classList.add("is-here");
      setTimeout(function () { moved.classList.remove("is-here"); }, 2500);
    }
  } catch (err) { /* storage unavailable */ }
})();

// Item editor: show fields only for the selected option (data-show-for select + data-when="a b")
document.querySelectorAll("select[data-show-for]").forEach(function (sel) {
  var form = sel.form;
  function update() {
    form.querySelectorAll("[data-when]").forEach(function (el) {
      var hide = el.getAttribute("data-when").split(" ").indexOf(sel.value) < 0;
      el.hidden = hide;
      // hidden fields must not submit (e.g. two "points" selects for different styles)
      el.querySelectorAll("input, select, textarea").forEach(function (f) { if (!sel.disabled) f.disabled = hide; });
    });
  }
  sel.addEventListener("change", update);
  update();
});

// Item editor: fields that only count while a tick is set (data-needs="<name of the checkbox>"): read-only and pale
// while it is off; still sent, so their values stay (W31, as the quotas in V19)
document.querySelectorAll("[data-needs]").forEach(function (box) {
  var form = box.closest("form"), check = form && form.elements[box.dataset.needs];
  if (!check || check.disabled) return;  // not editable at all: the fields are read-only already
  function sync() {
    box.querySelectorAll("input").forEach(function (f) { f.readOnly = !check.checked; });
    box.classList.toggle("needs-off", !check.checked);
  }
  check.addEventListener("change", sync);
  sync();
});

// Item editor, holdout tasks of CBC and MaxDiff (app/holdouts.py): show as many as "Anzahl" says. The hidden
// ones are still sent; the server reads only that many.
document.querySelectorAll("[data-ho]").forEach(function (box) {
  var count = box.querySelector("[data-ho-count]");
  if (!count) return;
  function update() {
    box.querySelectorAll("[data-ho-slot]").forEach(function (slot) {
      slot.hidden = Number(slot.getAttribute("data-ho-slot")) > Number(count.value);
    });
  }
  count.addEventListener("change", update);
  update();
});

// Regex tester (JavaScript regex syntax is close to, not identical with, the server check)
document.querySelectorAll("[data-regex-test]").forEach(function (input) {
  var form = input.form, out = form.querySelector("[data-regex-result]");
  function test() {
    var pattern = form.elements.pattern.value;
    if (!input.value) { out.textContent = ""; return; }
    try {
      var ok = new RegExp("^(?:" + pattern + ")$").test(input.value);
      out.textContent = ok ? de_en("✓ passt", "✓ matches") : de_en("✗ passt nicht", "✗ does not match");
      out.style.color = ok ? "var(--ok)" : "var(--danger)";
    } catch (e) { out.textContent = de_en("Ausdruck ungültig", "Invalid expression"); out.style.color = "var(--danger)"; }
  }
  input.addEventListener("input", test);
  form.elements.pattern.addEventListener("input", test);
});

// One polite live region for short confirmations a screen reader should hear ("Kopiert"; WCAG 4.1.3, UI audit R75)
function say(text) {
  var box = document.getElementById("sr-say");
  if (!box) { box = document.createElement("div"); box.id = "sr-say"; box.className = "visually-hidden"; box.setAttribute("role", "status"); document.body.appendChild(box); }
  box.textContent = ""; setTimeout(function () { box.textContent = text; }, 50);
}

// Text to the clipboard as a promise; where the Clipboard API is missing (plain http) or refuses, a hidden text
// area and execCommand("copy") instead (decisions.md, "Schleifentabelle auffindbar", 2026-10-10)
function copyText(text) {
  function legacy() {
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea"), ok = false;
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed"; area.style.top = "0"; area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      area.remove();
      if (ok) resolve(); else reject();
    });
  }
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).catch(legacy);
  return legacy();
}

// "Kopieren" of a loop table on the Export page: <button data-copy-tsv="tab separated rows">, pastes as columns
document.addEventListener("click", function (e) {
  var b = e.target.closest && e.target.closest("[data-copy-tsv]");
  if (!b) return;
  if (b.dataset.label === undefined) b.dataset.label = b.textContent;
  function shown(ok) {
    b.textContent = ok ? de_en("Kopiert ✓", "Copied ✓") : de_en("Kopieren nicht möglich", "Copying not possible");
    say(ok ? de_en("Kopiert", "Copied") : de_en("Kopieren nicht möglich", "Copying not possible"));
    setTimeout(function () { b.textContent = b.dataset.label; }, ok ? 1500 : 4000);
  }
  // the attribute arrives with \n only (HTML folds \r\n); Excel writes \r\n
  copyText(b.dataset.copyTsv.replace(/\r?\n/g, "\r\n")).then(function () { shown(true); }, function () { shown(false); });
});

// Copy buttons: <button data-copy="#input-id">
function bindCopy(btn) {
  btn.addEventListener("click", function () {
    var input = document.querySelector(btn.dataset.copy);
    var done = function () { var t = btn.textContent; btn.textContent = de_en("Kopiert ✓", "Copied ✓"); say(de_en("Kopiert", "Copied")); setTimeout(function () { btn.textContent = t; }, 1500); };
    if (navigator.clipboard) { navigator.clipboard.writeText(input.value).then(done); }
    else {  // a hidden field (surveys/quota_state.html) is shown for the moment of copying
      var hidden = input.classList.contains("visually-hidden");
      input.classList.remove("visually-hidden"); input.select(); document.execCommand("copy");
      if (hidden) input.classList.add("visually-hidden");
      done();
    }
  });
}
document.querySelectorAll("[data-copy]").forEach(bindCopy);

// the allowed values of a parameter with many values or links per person show as a count; "bearbeiten" shows the list
document.addEventListener("click", function (e) {
  var b = e.target.closest && e.target.closest("[data-allowed-edit]");
  if (!b) return;
  var sum = b.closest("[data-allowed-sum]"), input = sum.parentNode.querySelector('input[name="param_allowed"]');
  sum.hidden = true; input.hidden = false; input.focus();
});

// "Datenerhebung" → "Teilnahmelink": the language picked next to the link goes into the link to copy, "Öffnen" and the
// QR code as ?lang= (was a table with a link per language)
document.querySelectorAll("[data-link-lang]").forEach(function (select) {
  var row = select.closest(".copy-row"), input = row.querySelector("[data-base]");
  var open = row.querySelector("[data-link-open]"), qr = row.querySelector("[data-link-qr]");
  var qrBase = qr && qr.getAttribute("href");
  select.addEventListener("change", function () {
    var base = input.getAttribute("data-base"), code = select.value;
    var url = code ? base + (base.indexOf("?") < 0 ? "?" : "&") + "lang=" + encodeURIComponent(code) : base;
    input.value = url;
    if (open) open.href = url;
    if (qr) qr.href = code ? qrBase + (qrBase.indexOf("?") < 0 ? "?" : "&") + "name=lang&value=" + encodeURIComponent(code) : qrBase;
  });
});

// State of the quotas on the fieldwork page: while the survey runs, the tables and the text to copy are read
// again every 20 seconds (only while the page is visible).
(function () {
  var box = document.querySelector("[data-quota-live]");
  if (!box || !window.fetch || !window.DOMParser) return;
  setInterval(function () {
    if (document.hidden) return;
    fetch(box.dataset.quotaLive, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : null; }).then(function (html) {
      if (!html) return;
      var doc = new DOMParser().parseFromString(html, "text/html");
      var tables = doc.querySelector("[data-quota-tables]"), text = doc.querySelector("#quota-state-text"), stamp = doc.querySelector("[data-quota-stamp]");
      if (tables) {
        // what was unfolded stays unfolded, and the copy buttons inside work again
        var here = box.querySelector("[data-quota-tables]"), open = {};
        here.querySelectorAll("details[data-key]").forEach(function (d) { if (d.open) open[d.dataset.key] = true; });
        here.innerHTML = tables.innerHTML;
        here.querySelectorAll("details[data-key]").forEach(function (d) { if (open[d.dataset.key]) d.open = true; });
        here.querySelectorAll("[data-copy]").forEach(bindCopy);
      }
      if (text) box.querySelector("#quota-state-text").value = text.value;
      if (stamp) box.querySelector("[data-quota-stamp]").textContent = stamp.textContent;
    }).catch(function () {});
  }, 20000);
})();

// Conditions (page "Quoten" and the element "Bedingung" on the Ablauf page): a condition is picked, not typed.
// Choosing a question shows its fields: answers to tick, or "from … to" for numbers and dates; where the page
// offers it, also what is asked about the question (is, is not, answered, ...). The templates render the same
// fields for stored conditions (app/conditions.py). On the page "Quoten", buttons add conditions and Teilquoten,
// and the builder for cross quotas lists the answers of each attribute.
// A page may hold more than one place with conditions, each with the questions it offers (the element editor: the
// statements' conditions and "Nur zeigen, wenn …"); each is set up on its own.
document.querySelectorAll("[data-cond-page]").forEach(function (page) {
  var data, store = page.querySelector("script[data-cond-items]") || document.querySelector("script[data-cond-items]");
  try { data = JSON.parse(store.textContent); } catch (e) { return; }
  var items = data.items || {}, maxConds = data.max_conds || 8, maxRanges = data.max_ranges || 12;
  function nm(prefix, field) { return esc(prefix ? prefix + "_" + field : field); }
  function rangeFields(prefix, kind) {
    var extra = kind === "number" ? ' inputmode="decimal"' : ' placeholder="' + esc(data.date_hint || "") + '"';
    var from = de_en("von", "from"), to = de_en("bis", "to");
    return '<span class="quota-range">' + from + ' <input name="' + nm(prefix, "from") + '" aria-label="' + from + '"' + extra + '> ' + to + ' <input name="'
      + nm(prefix, "to") + '" aria-label="' + to + '"' + extra + "></span>";
  }
  // the fields for a question; mode: what is asked about it (where the page lets one choose), else its default
  function controls(prefix, ref, mode) {
    var info = items[ref];
    if (!info || mode === "answered" || mode === "empty" || info.kind === "other") return "";
    if (info.kind === "choice") {
      return '<span class="quota-options">' + info.options.map(function (o) {
        return '<label class="check"><input type="checkbox" name="' + nm(prefix, "codes") + '" value="' + esc(o[0]) + '"> ' + esc(o[1]) + "</label>";
      }).join("") + "</span>";
    }
    if (info.kind === "number" || info.kind === "date") {
      if (mode !== "lt" && mode !== "gt") return rangeFields(prefix, info.kind);
      return '<input name="' + nm(prefix, "value") + '" aria-label="' + de_en("Wert", "Value") + '" class="narrow"'
        + (info.kind === "number" ? ' inputmode="decimal"' : ' placeholder="' + esc(data.date_hint || "") + '"') + ">";
    }
    return '<input name="' + nm(prefix, "value") + '" placeholder="' + de_en("Wert", "Value") + '" aria-label="' + de_en("Wert", "Value") + '" maxlength="200">';
  }
  // a new line as _condition.html draws it (cond_line, removable): with the choice of what is asked unless the
  // place has none (Teilquoten: data-conds-plain on the button)
  var removeIcon = (document.querySelector("[data-cond-remove]") || {}).innerHTML;  // the symbol the server draws
  function condRow(prefix, withMode) {
    var options = (data.order || []).map(function (ref) { return '<option value="' + esc(ref) + '">' + esc(items[ref].label) + "</option>"; }).join("");
    return '<div class="cond-line" data-cond data-prefix="' + esc(prefix) + '"><select name="' + nm(prefix, "item")
      + '" aria-label="' + de_en("Frage, URL-Parameter oder Quote", "Question, URL parameter or quota") + '" data-cond-item><option value="">'
      + de_en("– Frage wählen –", "– Choose a question –") + '</option>' + options + "</select>"
      + (withMode ? '<select name="' + nm(prefix, "mode") + '" data-cond-mode aria-label="' + de_en("Vergleich", "Comparison") + '"></select>' : "")
      + '<button type="button" class="icon-btn danger" data-cond-remove title="' + de_en("Bedingung entfernen", "Remove condition")
      + '" aria-label="' + de_en("Bedingung entfernen", "Remove condition") + '">'
      + (removeIcon || "×") + "</button>"
      + '<span class="cond-value" data-cond-value></span></div>';
  }
  // after a line came or went: "+ Bedingung" up to the most, and from two lines on whether all or one must hold
  function recount(box) {
    var n = box.querySelectorAll("[data-cond]").length, add = box.querySelector("[data-add-cond]"), rule = box.querySelector("[data-cond-rule]");
    if (add) add.hidden = n >= (parseInt(add.dataset.condsMax, 10) || maxConds);
    if (rule) rule.hidden = n < 2;
  }
  function share(name) {
    return '<label class="quota-share">' + de_en("Anteil", "Share") + ' <input type="number" name="' + esc(name) + '" min="0" step="any" aria-label="'
      + de_en("Anteil in Prozent", "Share in per cent") + '"> %</label>';
  }
  function rangeRow(prefix, n, kind) {
    return '<div class="quota-cat">' + rangeFields(prefix + "_r" + n, kind) + share(prefix + "_r" + n + "_share") + "</div>";
  }
  function categories(gen) {
    var box = gen.querySelector("[data-gen-cats]"), prefix = box.dataset.prefix, info = items[gen.querySelector("[data-gen-item]").value];
    if (!info) { box.innerHTML = ""; return; }
    if (info.kind === "choice") {
      box.innerHTML = '<p class="muted small">' + de_en("Jede angekreuzte Antwort wird eine Ausprägung.", "Each ticked answer becomes a category.")
        + '</p><div class="quota-cats">' + info.options.map(function (o) {
        return '<div class="quota-cat"><label class="check"><input type="checkbox" name="' + esc(prefix) + '_codes" value="' + esc(o[0]) + '" checked> '
          + esc(o[1]) + "</label>" + share(prefix + "_share_" + o[0]) + "</div>";
      }).join("") + "</div>";
    } else if (info.kind === "number" || info.kind === "date") {
      box.innerHTML = '<p class="muted small">' + de_en("Ein Bereich je Zeile; die Grenzen zählen mit. Nur „von“ heißt „ab“, nur „bis“ heißt „bis“.",
        "One range per line; the limits are included. Only “from” means “from … on”, only “to” means “up to”.") + '</p>'
        + '<div class="quota-cats" data-ranges data-kind="' + info.kind + '">' + rangeRow(prefix, 0, info.kind) + rangeRow(prefix, 1, info.kind)
        + rangeRow(prefix, 2, info.kind) + '</div><button type="button" class="button button-small button-secondary" data-add-range>'
        + de_en("+ Bereich", "+ Range") + '</button>';
    } else {
      box.innerHTML = '<p class="muted small">' + de_en("Für diesen URL-Parameter sind keine erlaubten Werte hinterlegt (Einstellungen, „Teilnahmelinks und Panels“); "
        + "ohne sie gibt es nichts anzukreuzen.", "No allowed values are stored for this URL parameter (settings, “Participation links and panels”); "
        + "without them there is nothing to tick.") + "</p>";
    }
  }
  page.addEventListener("change", function (e) {
    var t = e.target;
    if (t.matches("[data-cond-item]")) {
      var row = t.closest("[data-cond]"), raw = row.querySelector('input[type="hidden"]'), modes = row.querySelector("[data-cond-mode]");
      if (raw) raw.remove();  // the stored condition belonged to the question chosen before
      if (modes) {
        modes.innerHTML = (items[t.value] ? data.modes[items[t.value].modes] || [] : []).map(function (m) {
          return '<option value="' + esc(m[0]) + '">' + esc(m[1]) + "</option>";
        }).join("");
      }
      row.querySelector("[data-cond-value]").innerHTML = controls(row.dataset.prefix, t.value, modes ? modes.value : "");
    } else if (t.matches("[data-cond-mode]")) {
      var line = t.closest("[data-cond]");
      line.querySelector("[data-cond-value]").innerHTML = controls(line.dataset.prefix, line.querySelector("[data-cond-item]").value, t.value);
    } else if (t.matches("[data-gen-item]")) {
      categories(t.closest("[data-gen]"));
    }
  });
  // Rows of a matrix shown only to some (items/_row_conditions.html): "the same question for all" picks it in
  // every line; where the wording of one of its answers is the text of the row, that answer is ticked
  page.addEventListener("change", function (e) {
    if (!e.target.matches("[data-cond-all]") || !e.target.value) return;
    var ref = e.target.value;
    page.querySelectorAll("[data-cond][data-row-label]").forEach(function (row) {
      var pick = row.querySelector("[data-cond-item]");
      if (pick.disabled || pick.value === ref) return;
      pick.value = ref;
      pick.dispatchEvent(new Event("change", { bubbles: true }));
      var name = (row.dataset.rowLabel || "").trim().toLowerCase();
      row.querySelectorAll('input[type="checkbox"]').forEach(function (box) {
        if (name && box.parentNode.textContent.trim().toLowerCase() === name) {
          box.checked = true;
          box.dispatchEvent(new Event("change", { bubbles: true }));  // the form saves itself on a change
        }
      });
    });
    e.target.value = "";
  });
  // the tolerance counts only for a soft quota: locked while the quota is hard, its value kept (the server keeps it too)
  page.addEventListener("change", function (e) {
    if (!e.target.matches("[data-quota-mode]")) return;
    e.target.form.querySelectorAll("[data-soft-only]").forEach(function (field) { field.disabled = e.target.value !== "soft"; });
  });
  page.addEventListener("input", function (e) {
    if (e.target.matches("[data-gen-total]")) e.target.closest("form").classList.toggle("with-shares", e.target.value !== "");
  });
  page.addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    if (btn.matches("[data-add-cond]")) {
      // the next free number: lines may have been taken away in between, and the server reads each number it finds
      var box = btn.closest("[data-conds]"), base = btn.dataset.condsBase, next = 0;
      box.querySelectorAll("[data-cond]").forEach(function (line) {
        var k = parseInt(String(line.dataset.prefix).slice(base.length), 10);
        if (!isNaN(k) && k >= next) next = k + 1;
      });
      var anchor = box.querySelector("[data-cond-rule]") || btn;
      anchor.insertAdjacentHTML("beforebegin", condRow(base + next, !btn.hasAttribute("data-conds-plain")));
      recount(box);
      var pick = anchor.previousElementSibling.querySelector("[data-cond-item]");
      if (pick) pick.focus();
    } else if (btn.matches("[data-cond-remove]")) {
      var holder = btn.closest("[data-conds]");
      btn.closest("[data-cond]").remove();
      if (holder) recount(holder);
    } else if (btn.matches("[data-add-cell]")) {
      var form = btn.closest("form"), count = form.querySelector('input[name="cells"]'), next = parseInt(count.value, 10) || 0;
      form.querySelector("[data-cells]").insertAdjacentHTML("beforeend", form.querySelector("template[data-cell-template]").innerHTML.replace(/__N__/g, String(next)));
      count.value = String(next + 1);
      var added = form.querySelector("[data-cells]").lastElementChild.querySelector("input:not([type=hidden])");
      if (added) added.focus();
    } else if (btn.matches("[data-toggle-codes]")) {
      var on = btn.closest(".quota").classList.toggle("show-codes");
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? de_en("Export-Codes ausblenden", "Hide export codes") : de_en("Export-Codes zeigen", "Show export codes");
    } else if (btn.matches("[data-add-range]")) {
      var ranges = btn.previousElementSibling, k = ranges.children.length;
      if (k < maxRanges) ranges.insertAdjacentHTML("beforeend", rangeRow(btn.closest("[data-gen-cats]").dataset.prefix, k, ranges.dataset.kind));
      if (k + 1 >= maxRanges) btn.hidden = true;
    }
  });
});

// Editor sidebars (left palette, right pre-launch check): drag to resize, button to collapse.
// Width and state are remembered per browser.
(function () {
  var grid = document.querySelector("[data-editor]");
  if (!grid) return;
  var CONF = { left: { min: 180, max: 440, def: 280, dir: 1 }, right: { min: 220, max: 480, def: 270, dir: -1 } };
  function load(key, fallback) { try { var v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; } }
  function save(key, v) { try { localStorage.setItem(key, v); } catch (e) { /* storage unavailable */ } }
  Object.keys(CONF).forEach(function (side) {
    var c = CONF[side], prop = "--" + side + "-w";
    var handle = document.querySelector('[data-sidebar-resizer="' + side + '"]');
    var toggle = document.querySelector('[data-sidebar-toggle="' + side + '"]');
    var bar = document.querySelector('[data-sidebar="' + side + '"]');
    if (!handle || !toggle || !bar) return;
    function width() { return parseInt(getComputedStyle(grid).getPropertyValue(prop), 10); }
    // no wider than the column is shown (app.css caps it at a share of the window): a width past the cap would
    // change nothing visible, and dragging back would first have to undo it
    function setWidth(w) {
      w = Math.max(c.min, Math.min(c.max, Math.round(w)));
      grid.style.setProperty(prop, w + "px");
      var shown = Math.round(bar.getBoundingClientRect().width);
      if (shown && shown < w) { w = Math.max(c.min, shown); grid.style.setProperty(prop, w + "px"); }
      return w;
    }
    function setCollapsed(on) {
      grid.classList.toggle(side + "-collapsed", on);
      toggle.setAttribute("aria-expanded", on ? "false" : "true");
      if (on) grid.style.removeProperty(prop); else setWidth(parseInt(load("editor-" + side + "-w", c.def), 10));
    }
    setCollapsed(load("editor-" + side + "-collapsed", "0") === "1");
    toggle.addEventListener("click", function () {
      var on = !grid.classList.contains(side + "-collapsed");
      setCollapsed(on); save("editor-" + side + "-collapsed", on ? "1" : "0");
    });
    var startX, startW;
    handle.addEventListener("pointerdown", function (e) {
      startX = e.clientX; startW = bar.getBoundingClientRect().width;
      handle.setPointerCapture(e.pointerId); grid.classList.add("resizing"); e.preventDefault();
    });
    handle.addEventListener("pointermove", function (e) {
      if (grid.classList.contains("resizing")) setWidth(startW + c.dir * (e.clientX - startX));
    });
    handle.addEventListener("pointerup", function () { grid.classList.remove("resizing"); save("editor-" + side + "-w", width()); });
    handle.addEventListener("dblclick", function () { save("editor-" + side + "-w", setWidth(c.def)); });
    handle.addEventListener("keydown", function (e) {
      var d = e.key === "ArrowLeft" ? -20 : e.key === "ArrowRight" ? 20 : 0;
      if (!d) return;
      save("editor-" + side + "-w", setWidth(width() + c.dir * d)); e.preventDefault();
    });
  });
})();

// Image style picker: every tile starts with a random research context; every 3 s one random
// tile fades to a different context (no rotation with "reduce motion"). The tiles are looked up anew each
// time: a save on the settings page draws the group again, and the old tiles are gone then.
(function () {
  function tiles() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-style-frame]"))
      .filter(function (f) { return f.querySelectorAll("img").length > 1; });
  }
  function show(frame, index) {
    frame.querySelectorAll("img").forEach(function (img, i) { img.classList.toggle("on", i === index); });
    frame.dataset.current = index;
  }
  function start(frames) {  // tiles not seen before (the first ones, or drawn again): a random context each
    frames.forEach(function (f) { if (f.dataset.current == null) show(f, Math.floor(Math.random() * f.querySelectorAll("img").length)); });
  }
  if (!tiles().length) return;
  start(tiles());
  if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  var last = null;
  setInterval(function () {
    if (document.hidden) return;
    var frames = tiles();
    start(frames);
    if (!frames.length) return;
    var choices = frames.filter(function (f) { return f !== last; });
    var frame = choices[Math.floor(Math.random() * choices.length)];
    var n = frame.querySelectorAll("img").length, current = parseInt(frame.dataset.current, 10) || 0;
    show(frame, (current + 1 + Math.floor(Math.random() * (n - 1))) % n);  // always a different context
    last = frame;
  }, 3000);
})();

// Conjoint images: generate missing images one after another (each request is one image)
(function () {
  var btn = document.querySelector("[data-batch]");
  var csrf = csrfToken();
  function generate(url, key, force) {
    var body = new FormData();
    body.append("csrf_token", csrf); body.append("key", key); if (force) body.append("force", "1");
    return window.aiFetch(url, { method: "POST", body: body, credentials: "same-origin" }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; });
    });
  }
  document.querySelectorAll("[data-regen]").forEach(function (b) {
    b.addEventListener("click", function () {
      var url = (btn || {}).dataset ? btn.dataset.url : null;
      if (!url) return;
      var label = b.innerHTML;
      b.disabled = true; b.textContent = "…";
      generate(url, b.dataset.key, true).then(function () { location.reload(); })
        .catch(function (e) { pageError(de_en("Das Bild wurde nicht erzeugt: ", "The image was not created: ") + e.message); b.disabled = false; b.innerHTML = label; });
    });
  });
  if (!btn) return;
  var box = document.querySelector("[data-batch-progress]");
  var cancelled = false;
  box.querySelector("[data-cancel]").addEventListener("click", function () { cancelled = true; });
  btn.addEventListener("click", function () {
    var keys = JSON.parse(btn.dataset.missing || "[]");
    if (!keys.length) return;
    if (btn.dataset.warn === "1") {  // many images: say first what it takes
      var minutes = Math.ceil(keys.length * 30 / 60);
      askConfirm({ title: de_en(keys.length + " Bilder erzeugen?", "Create " + keys.length + " images?"), ok: de_en("Bilder erzeugen", "Create images"),
                   note: de_en("Das dauert etwa " + minutes + " Minuten und verursacht Kosten bei OpenAI.",
                               "This takes about " + minutes + " minutes and costs money at OpenAI.") })
        .then(function (yes) { if (yes) run(keys); });
    } else run(keys);
  });
  function run(keys) {
    btn.disabled = true; box.hidden = false; cancelled = false;
    var bar = box.querySelector("[data-bar]"), status = box.querySelector("[data-status]");
    var done = 0, failed = 0;
    (function next(i) {
      if (i >= keys.length || cancelled) {
        status.textContent = cancelled ? de_en("Abgebrochen: ", "Cancelled: ") : de_en("Fertig: ", "Done: ");
        status.textContent += de_en(done + " erzeugt", done + " created") + (failed ? ", " + de_en(failed + " Fehler", failed + " errors") : "");
        setTimeout(function () { location.reload(); }, 800);
        return;
      }
      status.textContent = de_en("Bild " + (i + 1) + " von " + keys.length + " …", "Image " + (i + 1) + " of " + keys.length + " …");
      generate(btn.dataset.url, keys[i]).then(function () { done++; }).catch(function () { failed++; })
        .then(function () { bar.style.width = ((i + 1) / keys.length * 100) + "%"; next(i + 1); });
    })(0);
  }
})();

// Conjoint images: several own images at once, assigned by file name ("look-2_colour-1.jpg"). A preview first
// says which file goes where, which replaces an image and which fits nothing; then the files go one per request
// (the limit of one upload and the memory per request stay as for a single image).
(function () {
  var box = document.querySelector("[data-multi-upload]");
  if (!box || !window.fetch) return;
  var input = box.querySelector("[data-multi-files]"), preview = box.querySelector("[data-multi-preview]");
  var tbody = box.querySelector("[data-multi-rows]"), go = box.querySelector("[data-multi-go]");
  var progress = box.querySelector("[data-multi-progress]");
  var names = JSON.parse(box.dataset.names), keys = JSON.parse(box.dataset.keys);
  var labels = JSON.parse(box.dataset.labels), taken = JSON.parse(box.dataset.taken);
  var plan = [];
  function stem(file) { return file.name.replace(/\.[^.]*$/, "").trim().toLowerCase(); }
  function cell(row, text, cls) { var td = document.createElement("td"); td.textContent = text; if (cls) td.className = cls; row.appendChild(td); }
  input.addEventListener("change", function () {
    plan = []; tbody.textContent = "";
    var seen = {};
    Array.prototype.forEach.call(input.files, function (file) {
      var i = names.indexOf(stem(file)), row = document.createElement("tr"), note;
      cell(row, file.name);
      if (i < 0) { cell(row, de_en("passt zu keiner Kombination", "matches no combination"), "multi-upload-miss"); note = de_en("wird übersprungen", "is skipped"); }
      else if (seen[i]) { cell(row, labels[i]); note = de_en("doppelt, wird übersprungen", "duplicate, is skipped"); }
      else {
        seen[i] = true; cell(row, labels[i]);
        note = taken.indexOf(keys[i]) >= 0 ? de_en("ersetzt das bisherige Bild", "replaces the current image") : de_en("neu", "new");
        plan.push({ file: file, key: keys[i] });
      }
      cell(row, note, "muted small");
      tbody.appendChild(row);
    });
    go.textContent = plan.length === 1 ? de_en("1 Bild hochladen", "Upload 1 image") : de_en(plan.length + " Bilder hochladen", "Upload " + plan.length + " images");
    go.disabled = !plan.length; preview.hidden = false;
  });
  box.querySelector("[data-multi-cancel]").addEventListener("click", function () { input.value = ""; plan = []; preview.hidden = true; });
  go.addEventListener("click", function () {
    var bar = progress.querySelector("[data-bar]"), status = progress.querySelector("[data-status]");
    var done = 0, errors = [];
    go.disabled = true; progress.hidden = false;
    (function next(i) {
      if (i >= plan.length) {
        status.textContent = de_en("Fertig: " + done + " hochgeladen", "Done: " + done + " uploaded")
          + (errors.length ? ", " + de_en(errors.length + " nicht: ", errors.length + " not: ") + errors.join("; ") : "");
        progress.classList.toggle("is-failed", !!errors.length);
        setTimeout(function () { location.reload(); }, errors.length ? 4000 : 800);
        return;
      }
      status.textContent = de_en("Bild " + (i + 1) + " von " + plan.length + " …", "Image " + (i + 1) + " of " + plan.length + " …");
      var body = new FormData();
      body.append("csrf_token", csrfToken()); body.append("ajax", "1"); body.append("key", plan[i].key); body.append("file", plan[i].file);
      fetch(box.dataset.url, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) { return r.json().catch(function () { return { error: de_en("Fehler ", "Error ") + r.status }; }); })
        .then(function (j) { if (j.ok) done++; else errors.push(plan[i].file.name + " (" + (j.error || de_en("Fehler", "Error")) + ")"); })
        .catch(function () { errors.push(plan[i].file.name); })
        .then(function () { bar.style.width = ((i + 1) / plan.length * 100) + "%"; next(i + 1); });
    })(0);
  });
})();

// Dummy data: created in portions (each request a few simulated persons), so the page can show how far
// it is and a large number does not hang in one long request. Without JavaScript the form does it at once.
(function () {
  var form = document.querySelector("[data-dummy-form]");
  var box = document.querySelector("[data-dummy-progress]");
  if (!form || !box || !window.fetch) return;
  var input = form.querySelector("[name=n]"), btn = form.querySelector("button");
  var bar = box.querySelector("[data-bar]"), meter = box.querySelector("[role=progressbar]");
  var status = box.querySelector("[data-status]"), cancel = box.querySelector("[data-cancel]");
  var count = document.querySelector("[data-dummy-count]");
  var cancelled = false, running = false;
  cancel.addEventListener("click", function () { cancelled = true; cancel.disabled = true; });
  window.addEventListener("beforeunload", function (e) { if (running) { e.preventDefault(); e.returnValue = ""; } });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (running) return;
    var total = Math.max(1, Math.min(parseInt(input.value, 10) || 50, 500));
    var asked = 0, created = 0, size = 5;
    function show(text) {
      var pct = Math.round(asked / total * 100);
      bar.style.width = pct + "%"; meter.setAttribute("aria-valuenow", pct); status.textContent = text;
    }
    function finish(text, failed) {
      running = false; btn.disabled = false; input.disabled = false; cancel.hidden = true;
      box.classList.toggle("is-failed", !!failed);
      if (!failed && !cancelled) asked = total;
      show(text);
    }
    function summary() {
      return de_en(created + " Dummy-Teilnahmen erzeugt (source = dummy)", created + " dummy participations created (source = dummy)") +
        (asked > created ? ", " + de_en((asked - created) + " weitere nicht abgeschlossen", (asked - created) + " more not completed") : "") + ".";
    }
    running = true; cancelled = false; btn.disabled = true; cancel.disabled = false; cancel.hidden = false; box.hidden = false;
    box.classList.remove("is-failed");
    show(de_en("Dummy-Teilnahme 1 von " + total + " …", "Dummy participation 1 of " + total + " …"));
    (function next() {
      if (asked >= total || cancelled) { finish((cancelled ? de_en("Abgebrochen: ", "Cancelled: ") : de_en("Fertig: ", "Done: ")) + summary()); return; }
      var n = Math.min(size, total - asked), body = new FormData(form), started = Date.now();
      body.set("n", n);
      input.disabled = true;  // after reading the form: disabled fields are not part of it
      fetch(form.action, { method: "POST", body: body, credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || de_en("Fehler ", "Error ") + r.status); return j; },
            function () { throw new Error(r.ok ? de_en("Die Anmeldung ist abgelaufen oder die Rechte fehlen.", "The sign-in has expired or the rights are missing.")
                                               : de_en("Fehler ", "Error ") + r.status); });
        })
        .then(function (j) {
          asked += n; created += j.created;
          if (count) count.textContent = Number(count.textContent) + j.created;
          // aim at about one second per request: fast surveys get larger portions, slow ones smaller
          size = Math.max(1, Math.min(Math.round(size * 1000 / Math.max(Date.now() - started, 100)), 50));
          show(asked < total ? de_en("Dummy-Teilnahme " + (asked + 1) + " von " + total + " …", "Dummy participation " + (asked + 1) + " of " + total + " …") : "");
          next();
        })
        .catch(function (err) {
          finish(de_en("Abgebrochen nach " + created + " Dummy-Teilnahmen: ", "Stopped after " + created + " dummy participations: ")
            + (err instanceof TypeError ? de_en("keine Verbindung zum Server.", "no connection to the server.") : err.message), true);
        });
    })();
  });
})();

// Palette: a feedback that just became possible (the IAT-Ergebnis once there is an IAT) says "neu" and its group
// count lights up, until it is used once; the browser remembers that (a convenience: without storage it says "neu")
(function () {
  var rows = document.querySelectorAll("[data-palette-new]");
  if (!rows.length) return;
  var seen = [];
  try { seen = JSON.parse(localStorage.getItem("askurious-palette-seen") || localStorage.getItem("queryous-palette-seen") || "[]"); } catch (e) { seen = []; }
  rows.forEach(function (row) {
    var key = row.getAttribute("data-palette-new");
    if (seen.indexOf(key) >= 0) return;
    row.querySelector(".palette-new-badge").hidden = false;
    var count = row.closest("[data-palette-group]") && row.closest("[data-palette-group]").querySelector("[data-pg-count]");
    if (count) count.classList.add("pg-count-new");
    function used() {
      if (seen.indexOf(key) < 0) seen.push(key);
      try { localStorage.setItem("askurious-palette-seen", JSON.stringify(seen)); } catch (e) { /* stays "neu" */ }
    }
    row.addEventListener("click", used);
    row.addEventListener("dragstart", used);
  });
})();

// Page "Elementtypen" (/itemtypes): the types are folded; an address with #<type> opens that one (X11)
(function () {
  if (!document.querySelector("details.element-type")) return;
  function open() {
    var target = location.hash && document.getElementById(safeDecode(location.hash.slice(1)));
    if (target && target.matches("details.element-type")) { target.open = true; target.scrollIntoView({ block: "start" }); }
  }
  open();
  window.addEventListener("hashchange", open);
})();

// Phones (one column, the palette below the whole questionnaire): "+ Element" in a block shows the palette right under
// that block, and its "+" adds there (the click made the block the active one). Pressed again, or on a wide screen, the
// palette goes back to its column (UI/UX audit 2026-10-09, X29).
(function () {
  var side = document.querySelector(".editor-grid > .sidebar-left");
  if (!side || !document.querySelector("[data-block-palette]")) return;
  var home = document.createComment("palette");
  side.parentNode.insertBefore(home, side);
  function back() {
    if (home.nextSibling !== side) home.parentNode.insertBefore(side, home.nextSibling);
    document.querySelectorAll("[data-block-palette]").forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
  }
  document.addEventListener("click", function (e) {
    var button = e.target.closest("[data-block-palette]");
    if (!button) return;
    var open = button.getAttribute("aria-expanded") === "true";
    back();
    if (open) return;
    button.closest(".block").after(side);
    button.setAttribute("aria-expanded", "true");
    side.scrollIntoView({ block: "start" });
  });
  var wide = window.matchMedia("(min-width: 801px)");
  if (wide.addEventListener) wide.addEventListener("change", back);
})();

// Palette: one search for all groups (types filtered here, constructs full-text on the server),
// collapsible groups (state remembered in this browser)
(function () {
  var input = document.querySelector("[data-palette-search]");
  if (!input) return;
  var groups = Array.prototype.slice.call(document.querySelectorAll("[data-palette-group]"));
  var KEY = "palette-collapsed";
  function loadCollapsed() { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; } }
  function saveCollapsed(list) { try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) {} }
  function setOpen(group, open) {
    group.classList.toggle("is-collapsed", !open);
    group.querySelector("[data-pg-toggle]").setAttribute("aria-expanded", open ? "true" : "false");
  }
  var collapsed = loadCollapsed();
  groups.forEach(function (g) {
    setOpen(g, collapsed.indexOf(g.dataset.paletteGroup) < 0);
    g.querySelector("[data-pg-toggle]").addEventListener("click", function () {
      var open = g.classList.contains("is-collapsed");
      setOpen(g, open);
      if (!input.value.trim()) {  // remember only the user's own choice, not the search state
        var list = loadCollapsed().filter(function (k) { return k !== g.dataset.paletteGroup; });
        if (!open) list.push(g.dataset.paletteGroup);
        saveCollapsed(list);
      }
    });
  });
  function norm(s) { return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
  var typeRows = document.querySelectorAll("[data-palette] .palette-row");
  typeRows.forEach(function (r) { r.dataset.norm = norm(r.dataset.search || ""); });
  function groupOf(el) { return el.closest("[data-palette-group]"); }
  function applyGroupState(q) {
    groups.forEach(function (g) {
      var rows = g.querySelectorAll(".palette-row");
      var shown = Array.prototype.filter.call(rows, function (r) { return !r.hidden; }).length;
      var empty = g.querySelector("[data-pg-empty]");
      if (q) {
        g.querySelector("[data-pg-count]").textContent = shown;
        empty.hidden = shown > 0;
        setOpen(g, shown > 0);               // while searching: groups with hits open, others closed
      } else {
        empty.hidden = true;
        if (g.dataset.paletteGroup !== "constructs") g.querySelector("[data-pg-count]").textContent = rows.length;
        setOpen(g, loadCollapsed().indexOf(g.dataset.paletteGroup) < 0);
      }
      g.classList.toggle("no-hits", !!q && !shown);
    });
  }
  // constructs: server-side full-text search
  var results = document.querySelector("[data-construct-results]");
  var initial = results ? results.innerHTML : "";
  var initialCount = results ? groupOf(results).querySelector("[data-pg-count]").textContent : "";
  var timer = null, seq = 0;
  var parts = results ? results.closest(".pg-body").querySelector("[data-construct-icons]") : null;
  function part(name) { var el = parts && parts.content.querySelector("[data-part=" + name + "]"); return el ? el.innerHTML : ""; }
  function rowsHtml(rows) {
    var icon = '<span class="item-icon">' + part("icon") + '</span>', plus = part("plus"), star = part("star");
    return rows.map(function (c) {
      return '<li class="palette-row"><div class="palette-item" data-type="construct:' + esc(c.key) +
        '" title="' + esc(c.name) + de_en(' (ziehen)', ' (drag)') + '">' + icon + '<span><strong>' + esc(c.name) + '</strong><span class="desc">' +
        esc(c.source) + (c.by && !c.starred ? esc(de_en(" · empfohlen von " + c.by, " · recommended by " + c.by)) : "") +
        '</span></span></div><button type="button" class="icon-btn star-btn" data-star="' + esc(c.star_url) +
        '" aria-pressed="' + (c.starred ? "true" : "false") + '" title="' +
        (c.starred ? de_en("Favorit: Steht in dieser Liste. Klicken entfernt den Stern.", "Favourite: listed here. A click removes the star.")
                   : de_en("Als Favorit merken: Steht dann in dieser Liste", "Mark as favourite: it is then listed here")) +
        '" aria-label="' + esc(c.name) + ": " + (c.starred ? de_en("Favorit entfernen", "remove favourite") : de_en("als Favorit merken", "mark as favourite")) + '">' + star +
        '</button><button type="button" class="icon-btn palette-add" data-type="construct:' +
        esc(c.key) + '" title="' + de_en(esc(c.name) + ' am Ende des zuletzt angeklickten Blocks einfügen (sonst des letzten)',
                                         'Insert ' + esc(c.name) + ' at the end of the block clicked last (otherwise of the last block)') +
        '" aria-label="' + de_en(esc(c.name) + ' hinzufügen', 'Add ' + esc(c.name)) + '">' + plus + '</button></li>';
    }).join("");
  }
  function render(rows) { results.innerHTML = rowsHtml(rows); }
  // star: favourites are the constructs listed without a search; the server answers with that list
  var csrf = csrfToken();
  if (results) results.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-star]");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    var body = new FormData(); body.append("csrf_token", csrf); body.append("json", "1");
    var boxEl = document.querySelector("[data-construct-box]");  // the survey: its organisation's list comes back too
    if (boxEl && boxEl.dataset.survey) body.append("survey", boxEl.dataset.survey);
    fetch(btn.dataset.star, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        initial = rowsHtml(data.sidebar);
        if (!input.value.trim()) { results.innerHTML = initial; return; }
        btn.disabled = false;  // while searching the hits stay; only this star changes
        btn.setAttribute("aria-pressed", data.starred ? "true" : "false");
        btn.title = data.starred ? de_en("Favorit: Steht in dieser Liste. Klicken entfernt den Stern.", "Favourite: listed here. A click removes the star.")
                                 : de_en("Als Favorit merken: Steht dann in dieser Liste", "Mark as favourite: it is then listed here");
      })
      .catch(function () { btn.disabled = false; actionFailed(0); });
  });
  function searchConstructs(q, now) {
    clearTimeout(timer);
    var mine = ++seq;
    if (!results) return Promise.resolve();
    if (!q.trim()) {
      results.innerHTML = initial;
      groupOf(results).querySelector("[data-pg-count]").textContent = initialCount;
      return Promise.resolve();
    }
    return new Promise(function (resolve) {
      timer = setTimeout(function () {
        fetch(results.dataset.url + "?limit=8&q=" + encodeURIComponent(q), { credentials: "same-origin" })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (rows) {
            if (mine !== seq) return;  // a newer search is under way
            render(rows);
            applyGroupState(input.value.trim());
            resolve();
          })
          .catch(function () {  // server not reachable or signed out: say so instead of showing old hits
            if (mine !== seq) return;
            results.innerHTML = '<li class="area-empty">' + de_en("Die Suche in der Sammlung ist gerade nicht erreichbar. Bitte später erneut versuchen.",
                                                                  "The search in the collection cannot be reached right now. Please try again later.") + '</li>';
            resolve();
          });
      }, now ? 0 : 150);
    });
  }
  function filterTypes() {
    var words = norm(input.value).split(/\s+/).filter(Boolean);
    typeRows.forEach(function (r) {
      r.hidden = !words.every(function (w) { return r.dataset.norm.indexOf(w) >= 0; });
    });
  }
  input.addEventListener("input", function () {
    filterTypes();
    if (!input.value.trim()) searchConstructs("");
    applyGroupState(input.value.trim());
    if (input.value.trim()) searchConstructs(input.value);  // updates the construct group when the answer arrives
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      input.value = "";
      filterTypes();
      searchConstructs("");
      applyGroupState("");
    }
    if (e.key === "Enter") e.preventDefault(); // Search never changes the questionnaire.
    if (e.key === "ArrowDown") {
      e.preventDefault();
      var btn = Array.prototype.find.call(document.querySelectorAll("[data-palette-group] .palette-add"), function (b) {
        return !b.closest(".palette-row").hidden && !b.closest(".palette-group").classList.contains("is-collapsed");
      });
      if (btn) btn.focus(); // Choose a named result; Enter then activates that button.
    }
  });
})();

// Conjoint editor: add/remove attributes and levels before saving (new ids are created here)
(function () {
  var editor = document.querySelector("[data-cj-editor]");
  if (!editor) return;
  var attrTpl = editor.querySelector("[data-cj-attr-template]");
  var levelTpl = editor.querySelector("[data-cj-level-template]");
  function id() { return Math.random().toString(16).slice(2, 8); }
  function fromTemplate(tpl, aid, container) {
    // table rows must be parsed inside a <tbody>, otherwise the parser drops <tr>/<td>
    // the id comes from data-aid (decoded text): escaped before it goes into HTML (audit 2026-10-13, XS-1)
    var html = tpl.innerHTML.split("__AID__").join(esc(aid)).replace(/__LID__/g, function () { return id(); });
    var box = document.createElement(container || "div");
    box.innerHTML = html.trim();
    return box;
  }
  editor.addEventListener("click", function (e) {
    var t = e.target.closest("button");
    if (!t) return;
    if (t.hasAttribute("data-cj-add-attr")) {
      var card = fromTemplate(attrTpl, id()).firstElementChild;
      editor.querySelector("[data-cj-attrs]").appendChild(card);
      card.querySelector('input[name="attr_label"]').focus();
    } else if (t.hasAttribute("data-cj-add-level")) {
      var attr = t.closest("[data-aid]");
      var rows = fromTemplate(levelTpl, attr.dataset.aid, "tbody");
      var row = rows.querySelector("tr");
      attr.querySelector("[data-cj-levels]").appendChild(row);
      rowEdges(attr.querySelector("[data-cj-levels]"));
      row.querySelector("input:not([type=hidden])").focus();
    } else if (t.hasAttribute("data-cj-remove-attr")) {
      t.closest(".cj-attr").remove();  // no question: nothing is gone before saving, and saving can be taken back
    }
  });
})();

// MaxDiff editor: add, remove and move statements before saving (new ids are created here)
(function () {
  var editor = document.querySelector("[data-md-editor]");
  if (!editor) return;
  var rows = editor.querySelector("[data-md-rows]");
  var tpl = editor.querySelector("[data-md-template]");
  function renumber() {
    rows.querySelectorAll("tr").forEach(function (tr, n) { tr.querySelector(".md-num").textContent = n + 1; });
  }
  editor.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.hasAttribute("data-md-add") && tpl) {
      var box = document.createElement("tbody");  // table rows must be parsed inside a <tbody>
      box.innerHTML = tpl.innerHTML.split("__SID__").join("s" + Math.random().toString(16).slice(2, 8)).trim();
      var row = box.querySelector("tr");
      rows.appendChild(row);
      row.querySelector('input[name="md_text"]').focus();
      rowEdges(rows);
    }
    renumber();
  });
  rows.addEventListener("rows:changed", renumber);  // ↑ ↓ × and dragging (rowsChanged above)
  renumber();
})();

// "Konkrete Fragen" of a Leitfrage (items/guide_question/editor.html, concept bedingungen-leitfaden): → indents a row as
// a Nachfrage of the row above, ← outdents it (the hidden probe_sub; autosave hears the button's click). The first
// row is never one. The choice "nur stellen, wenn …" draws its groups of aspects and of earlier concrete questions anew
// from the rows of the form when it is opened, so an aspect or a question typed a moment ago can be picked at once.
(function () {
  document.querySelectorAll("[data-probe-rows]").forEach(function (rows) {
    function settle() {
      Array.prototype.forEach.call(rows.children, function (tr, n) {
        var flag = tr.querySelector('input[name="probe_sub"]');
        if (!flag) return;
        if (n === 0 && flag.value === "1") flag.value = "";
        tr.classList.toggle("is-probe-sub", flag.value === "1");
        var b = tr.querySelector("[data-probe-indent]");
        if (b) b.disabled = n === 0 && flag.value !== "1";
      });
    }
    rows.addEventListener("click", function (e) {
      var b = e.target.closest("[data-probe-indent]");
      if (!b) return;
      var flag = b.closest("tr").querySelector('input[name="probe_sub"]');
      flag.value = flag.value === "1" ? "" : "1";
      settle();
      if (flag.value === "1") { var when = b.closest("tr").querySelector('input[name="probe_when"]'); if (when) when.focus(); }
    });
    rows.addEventListener("rows:changed", function () { setTimeout(settle, 0); });
    new MutationObserver(settle).observe(rows, { childList: true });
    settle();
    var form = rows.closest("form");
    function aspects() {
      var out = [];
      if (!form) return out;
      var ids = form.querySelectorAll('input[name="aspect_id"]'), labels = form.querySelectorAll('input[name="aspect_label"]');
      Array.prototype.forEach.call(ids, function (id, n) {
        var text = labels[n] ? labels[n].value.trim() : "";
        if (text) out.push([id.value, text]);
      });
      return out;
    }
    function earlier(select) {  // the concrete questions above this row (no Nachfragen), K1, K2, … (E)
      var group = select.querySelector("optgroup[data-probe-earlier]");
      if (!group) return;
      var mine = select.closest("tr"), n = 0;
      group.innerHTML = "";
      Array.prototype.some.call(rows.children, function (tr) {
        if (tr === mine) return true;
        var id = tr.querySelector('input[name="probe_id"]'), label = tr.querySelector('input[name="probe_label"]');
        var flag = tr.querySelector('input[name="probe_sub"]');
        var text = label ? label.value.trim() : "";
        if (!id || !text) return false;
        if (n && flag && flag.value === "1") return false;
        n += 1;
        var q = text.length <= 50 ? text : text.slice(0, 49).trim() + "…";
        [["yes", group.getAttribute("data-yes")], ["no", group.getAttribute("data-no")]].forEach(function (m) {
          var o = document.createElement("option");
          o.value = "p:" + id.value + ":" + m[0];
          o.textContent = m[1].replace("{n}", n).replace("{q}", q);
          group.appendChild(o);
        });
        return false;
      });
      group.hidden = !group.children.length;
    }
    function redraw(select) {
      var group = select.querySelector("optgroup[data-probe-aspects]");
      if (!group) return;
      var value = select.value;
      earlier(select);
      group.innerHTML = "";
      aspects().forEach(function (a) {
        [["absent", group.getAttribute("data-absent")], ["present", group.getAttribute("data-present")]].forEach(function (m) {
          var o = document.createElement("option");
          o.value = "a:" + a[0] + ":" + m[0];
          o.textContent = m[1].replace("{a}", a[1]);
          group.appendChild(o);
        });
      });
      group.hidden = !group.children.length;
      if (Array.prototype.some.call(select.options, function (o) { return o.value === value; })) select.value = value;
    }
    rows.addEventListener("focusin", function (e) { if (e.target.matches && e.target.matches("select[name=probe_if]")) redraw(e.target); });
    rows.addEventListener("mousedown", function (e) { if (e.target.matches && e.target.matches("select[name=probe_if]")) redraw(e.target); });
  });
})();

// Lists of options, statements and elements in the element editors (every table with ".new-row" rows):
// one way of editing for all of them. Each row gets a handle to drag it, ↑ ↓ and × (the same as the rows of the
// questionnaire and the flow; UI audit 2026-10-04, R92), and "+" below the table adds a row; the server
// reads the rows in the order of the page and drops empty ones, so nothing else has to change. Without
// JavaScript the three empty rows of the page remain, as before.
(function () {
  document.querySelectorAll("tbody").forEach(function (rows) {
    var blank = rows.querySelector("tr.new-row");
    if (!blank || rows.hasAttribute("data-md-rows")) return;
    var table = rows.closest("table");
    var max = parseInt(blank.getAttribute("data-max-rows"), 10) || Infinity;
    var template = blank.cloneNode(true);  // pristine, before the tools are added
    function idOf(tr) { var h = tr.querySelector('input[type=hidden][name$="_id"]'); return h ? h.value : ""; }
    function tools(tr) {
      if (tr.querySelector(".row-tools")) return;
      tr.appendChild(rowToolsCell(de_en("Zeile entfernen", "Remove row")));
    }
    function renumber() {
      var list = Array.prototype.slice.call(rows.children);
      list.forEach(function (tr, n) {
        var up = tr.querySelector('[data-row="up"]'), down = tr.querySelector('[data-row="down"]');
        if (up) up.disabled = n === 0;
        if (down) down.disabled = n === list.length - 1;
      });
      // a code left empty gets the next free number (shown as placeholder, filled in when the form is sent):
      // rows can be moved and removed, so the position is no safe code any more
      var codes = list.map(function (tr) { return tr.querySelector('input[name$="_code"]'); })
        .filter(function (c) { return c && (c.value.trim() === "" ? /^\d*$/.test(c.placeholder) : true); });
      var used = {};
      codes.forEach(function (c) { if (c.value.trim()) used[c.value.trim()] = true; });
      var next = 1;
      codes.forEach(function (c) {
        if (c.value.trim() || !/^\d+$/.test(c.placeholder)) return;
        while (used[String(next)]) next += 1;
        c.placeholder = String(next);
        used[String(next)] = true;
      });
      add.disabled = list.length >= max;
    }
    var form = rows.closest("form");
    if (form) form.addEventListener("submit", function () {
      Array.prototype.forEach.call(rows.children, function (tr) {
        var code = tr.querySelector('input[name$="_code"]');
        if (!code || code.value.trim() || !/^\d+$/.test(code.placeholder)) return;
        var filled = Array.prototype.some.call(tr.querySelectorAll("input[type=text], input:not([type])"), function (i) {
          return i !== code && i.value.trim() !== "";
        });
        if (filled) code.value = code.placeholder;
      });
    });
    rows.addEventListener("input", function (e) { if (/_code$/.test(e.target.name || "")) renumber(); });
    function fresh() {
      var tr = template.cloneNode(true), old = idOf(tr), id = Math.random().toString(16).slice(2, 8);
      tr.querySelectorAll("input, select, textarea").forEach(function (el) {
        // a field named after its row (option_prompt_<id>, the image description) follows the new id
        if (old && el.name && el.name.slice(-old.length - 1) === "_" + old) el.name = el.name.slice(0, -old.length) + id;
        if (el.type === "hidden" && /_id$/.test(el.name)) el.value = id;
        else if (el.type === "checkbox" || el.type === "radio") { if (el.value === old) el.value = id; el.checked = false; }
        else if (el.tagName !== "SELECT") el.value = "";
      });
      tools(tr);
      return tr;
    }
    var head = table.querySelector("thead tr");
    if (head) { var th = document.createElement("th"); th.className = "cj-row-tools row-tools"; head.appendChild(th); }
    // one empty row is enough to start with, now that rows can be added
    Array.prototype.slice.call(rows.querySelectorAll("tr.new-row")).slice(1).forEach(function (tr) { tr.remove(); });
    Array.prototype.forEach.call(rows.children, tools);
    var add = document.createElement("button");
    add.type = "button";
    add.className = "button button-small button-secondary row-add";
    // the word of the first field's placeholder („neue Option“, "new option"; templates write it in the page language)
    var what = (blank.querySelector('input[placeholder^="neue"], input[placeholder^="new "]') || {}).placeholder || "";
    add.textContent = "+ " + (what.replace(/^(neue[sr]?|new) /, "") || de_en("Zeile", "row"));
    table.insertAdjacentElement("afterend", add);
    add.addEventListener("click", function () {
      var tr = fresh();
      rows.appendChild(tr);
      renumber();
      var first = tr.querySelector("input:not([type=hidden]):not([type=checkbox])");
      if (first) first.focus();
    });
    // a row that has text is a real row: it loses the dashed grey look of an empty one (and gets it back when emptied)
    function textFields(tr) {
      return Array.prototype.filter.call(tr.querySelectorAll("input:not([type]), input[type=text], textarea"), function (i) {
        return !/_code$/.test(i.name || "");
      });
    }
    function mark(tr) {
      var filled = textFields(tr).some(function (i) { return i.value.trim() !== ""; });
      if (tr.querySelector('input[type=hidden][name$="_id"]') && (filled || tr.hasAttribute("data-was-new"))) {
        tr.setAttribute("data-was-new", "");
        tr.classList.toggle("new-row", !filled);
      }
    }
    rows.addEventListener("input", function (e) { var tr = e.target.closest("tr"); if (tr && tr.parentNode === rows) mark(tr); });
    // Tab out of the text of the last row, once it has one: a new row, so a list can be typed without the mouse
    rows.addEventListener("keydown", function (e) {
      if (e.key !== "Tab" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || add.disabled) return;
      var tr = e.target.closest("tr");
      if (!tr || tr !== rows.lastElementChild || textFields(tr)[0] !== e.target || !e.target.value.trim()) return;
      e.preventDefault();
      add.click();
    });
    rows.renumberRows = renumber;  // for the dragging below
    rows.addEventListener("click", function (e) {
      var b = e.target.closest("[data-row]");
      if (!b) return;
      var tr = b.closest("tr"), action = b.getAttribute("data-row");
      if (action === "remove") tr.remove();
      else if (action === "up" && tr.previousElementSibling) rows.insertBefore(tr, tr.previousElementSibling);
      else if (action === "down" && tr.nextElementSibling) rows.insertBefore(tr.nextElementSibling, tr);
      renumber();
      if (action !== "remove") b.focus();
    });
    renumber();
  });
  // dragging a row by its handle, with the shared dragging (mouse, pen and finger; Escape ends it): the form hears a
  // change afterwards, so it saves and the preview follows as after ↑ ↓
  pointerDrag({
    source: function (el) {
      var tr = el.closest && el.closest(".row-drag") && el.closest("tr");
      return tr && (tr.parentNode.renumberRows || tr.parentNode.hasAttribute("data-row-list")) ? { el: tr } : null;
    },
    start: function (d) { d.el.classList.add("is-dragging"); },
    move: function (d) {
      var over = document.elementFromPoint(d.x, d.y), other = over && over.closest("tr"), rows = d.el.parentNode;
      if (!other || other === d.el || other.parentNode !== rows) return;
      var box = other.getBoundingClientRect();
      rows.insertBefore(d.el, d.y < box.top + box.height / 2 ? other : other.nextSibling);
    },
    drop: function (d) {
      var rows = d.el.parentNode;
      if (rows.renumberRows) rows.renumberRows();
      else rowsChanged(rows);
      var field = d.el.querySelector("input, select, textarea");
      if (field) field.dispatchEvent(new Event("change", { bubbles: true }));
    },
    end: function (d) { d.el.classList.remove("is-dragging"); },
  });
})();

// Copy chips ([data-copy-text][role=button]: loop and vignette placeholders, the pool paragraph): click (or
// Enter / space) copies; if the browser refuses, the text is selected for Ctrl+C. Delegated on document, so it
// works on every page and for chips drawn anew after an autosave
(function () {
  function chipOf(e) {
    return e.target.closest ? e.target.closest("[data-copy-text][role=button]") : null;
  }
  function copyChip(chip) {
    if (chip.dataset.copyTitle === undefined) chip.dataset.copyTitle = chip.title;
    var title = chip.dataset.copyTitle;
    function shown(ok) {
      chip.classList.add(ok ? "copied" : "copy-failed");
      if (!ok) {
        var range = document.createRange();
        range.selectNodeContents(chip);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        chip.title = de_en("Kopieren nicht möglich. Der Text ist markiert: bitte mit Strg+C kopieren.", "Copying is not possible. The text is selected: please copy it with Ctrl+C.");
      }
      setTimeout(function () { chip.classList.remove("copied", "copy-failed"); chip.title = title; }, ok ? 900 : 4000);
    }
    if (!navigator.clipboard) return shown(false);
    navigator.clipboard.writeText(chip.dataset.copyText).then(function () { shown(true); }, function () { shown(false); });
  }
  document.addEventListener("click", function (e) {
    var chip = chipOf(e);
    if (chip) copyChip(chip);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var chip = chipOf(e);
    if (chip) { e.preventDefault(); copyChip(chip); }
  });
})();

// Loop table editor: add/remove rows and columns before saving (new ids are created here)
(function () {
  var form = document.querySelector("[data-loop-editor]");
  if (!form) return;
  var rows = form.querySelector("[data-rows]");
  var headRow = form.querySelector("thead tr");
  function nid(p) { return p + Math.random().toString(16).slice(2, 8); }
  function colIds() { return Array.prototype.map.call(form.querySelectorAll("[data-col-head]"), function (th) { return th.dataset.colHead; }); }
  function cell(rid, cid) {
    var td = document.createElement("td");
    td.dataset.col = cid;
    var input = document.createElement("input");
    input.name = "cell_" + rid + "_" + cid;
    td.appendChild(input);
    return td;
  }
  function renumber() {
    rows.querySelectorAll("tr").forEach(function (tr, n) {
      var num = tr.querySelector(".loop-rownum");
      num.lastChild.textContent = n + 1;
    });
  }
  function tsvCell(v) {  // as Excel writes a cell with a tab, line break or quote
    return /[\t\n\r"]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  }
  function tableText() {  // "#", the column names, then one line per pass: pastes as a table into Excel and Word
    var head = ["#"].concat(Array.prototype.map.call(form.querySelectorAll('thead input[name="col_name"]'), function (i) { return i.value; }));
    var lines = [head.map(tsvCell).join("\t")];
    rows.querySelectorAll("tr").forEach(function (tr, n) {
      var cells = Array.prototype.map.call(tr.querySelectorAll("td[data-col] input"), function (i) { return i.value; });
      lines.push([String(n + 1)].concat(cells).map(tsvCell).join("\t"));
    });
    return lines.join("\r\n") + "\r\n";
  }
  function copyTable(b) {
    if (b.dataset.label === undefined) b.dataset.label = b.textContent;
    function shown(ok) {
      b.textContent = ok ? de_en("Kopiert", "Copied") : de_en("Kopieren nicht möglich", "Copying not possible");
      setTimeout(function () { b.textContent = b.dataset.label; }, ok ? 1500 : 4000);
    }
    copyText(tableText()).then(function () { shown(true); }, function () { shown(false); });
  }
  form.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.hasAttribute("data-copy-table")) {
      copyTable(b);
    } else if (b.hasAttribute("data-add-row")) {
      var rid = nid("r"), tr = document.createElement("tr");
      tr.innerHTML = '<td class="loop-rownum"><input type="hidden" name="row_id" value="' + rid + '"> </td>';
      colIds().forEach(function (cid) { tr.appendChild(cell(rid, cid)); });
      tr.appendChild(rowToolsCell(de_en("Zeile entfernen", "Remove row")));
      rows.appendChild(tr);
      renumber();
      rowEdges(rows);
      var first = tr.querySelector("td[data-col] input");
      if (first) first.focus();
    } else if (b.hasAttribute("data-remove-col")) {
      var cid = b.getAttribute("data-remove-col");
      b.closest("th").remove();
      form.querySelectorAll('td[data-col="' + cid + '"]').forEach(function (td) { td.remove(); });
    } else if (b.hasAttribute("data-add-col")) {
      var cid2 = nid("c"), th = document.createElement("th");
      th.dataset.colHead = cid2;
      th.innerHTML = '<input type="hidden" name="col_id" value="' + cid2 + '">' +
        '<input name="col_name" class="loop-colname" placeholder="' + de_en("Spaltenname", "Column name") + '" aria-label="'
        + de_en("Spaltenname (Platzhalter)", "Column name (placeholder)") + '">' +
        '<label class="check small"><input type="checkbox" name="col_hidden" value="' + cid2 + '"> ' + de_en("nur intern", "internal only") + '</label>' +
        '<button type="button" class="icon-btn icon-btn-word danger" data-remove-col="' + cid2 + '" aria-label="' + de_en("Spalte entfernen", "Remove column")
        + '">× ' + de_en("Spalte entfernen", "Remove column") + '</button>';
      b.closest("th").before(th);
      rows.querySelectorAll("tr").forEach(function (tr) {
        var rid2 = tr.querySelector('input[name="row_id"]').value;
        var last = tr.lastElementChild;  // the remove button cell stays last
        tr.insertBefore(cell(rid2, cid2), last);
      });
      th.querySelector('input[name="col_name"]').focus();
    }
  });
  rows.addEventListener("rows:changed", renumber);  // ↑ ↓ × and dragging (rowsChanged): the pass numbers anew
})();

// Loop variables and answers to earlier questions (piping): a chip can be dragged into a text of the item
// (question, statements, scale labels, poles, options; not into variable names or codes), where the browser
// inserts {{name}} at the drop point; clicking inserts it at the cursor of the text field clicked last, else
// of the question text
(function () {
  var bar = document.querySelector("[data-loop-vars]");
  if (!bar) return;
  var dragging = false, last = null;
  function textField(el) {
    return el && el.matches && el.matches(".item-editor textarea, .item-editor input:not([type]), .item-editor input[type=text]")
      && !el.hasAttribute("data-varname") && !el.readOnly && !el.disabled && el.name !== "code" && el.name !== "pattern";
  }
  document.querySelectorAll(".item-editor textarea, .item-editor input").forEach(function (el) {
    if (textField(el)) el.setAttribute("data-var-target", "");
  });
  document.addEventListener("focusin", function (e) {
    if (textField(e.target)) { e.target.setAttribute("data-var-target", ""); last = e.target; }
    else if (e.target.chipInput) last = e.target.chipInput;  // a one-line field with chips (richtext.js)
    else if (e.target.isContentEditable) last = null;  // the question text as rich text: the default below
  });
  bar.addEventListener("dragstart", function (e) {
    var chip = e.target.closest("[data-insert]");
    if (!chip) return;
    e.dataTransfer.setData("text/plain", chip.dataset.insert);
    e.dataTransfer.effectAllowed = "copy";
    dragging = true;
    document.body.classList.add("dragging-var");
  });
  function stop() { dragging = false; document.body.classList.remove("dragging-var"); }
  document.addEventListener("dragend", stop);
  document.addEventListener("dragover", function (e) {
    if (dragging && !(e.target.closest && e.target.closest("[data-var-target]"))) e.dataTransfer.dropEffect = "none";
  });
  document.addEventListener("drop", function (e) {
    if (!dragging) return;
    var target = e.target.closest && e.target.closest("[data-var-target]");
    if (!target) e.preventDefault();  // no variables in codes, labels and the like
    else setTimeout(function () { target.focus(); }, 0);
    stop();
  }, true);
  bar.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-insert]");
    var field = (last && document.contains(last)) ? last
      : document.querySelector(".item-editor textarea[name=question]") || document.querySelector(".item-editor [data-var-target]");
    if (!chip || !field) return;
    if (field.richText && field.richText.active()) { field.richText.insert(chip.dataset.insert); return; }  // richtext.js
    var text = chip.dataset.insert, start = field.selectionStart, end = field.selectionEnd;
    if (start === undefined || start === null) start = end = field.value.length;
    field.value = field.value.slice(0, start) + text + field.value.slice(end);
    field.focus();
    field.selectionStart = field.selectionEnd = start + text.length;
    field.dispatchEvent(new Event("input", { bubbles: true }));  // saved, and shown as a chip (below)
  });
})();

// The plain one-line fields of an element (statements, answer options, scale labels): once one holds a
// placeholder, it shows it as a chip with a name, as the question text does (richtext.js, attachLine)
(function () {
  if (!document.querySelector("script[data-rich-vars]")) return;
  function lineField(el) {
    return el && el.matches && el.matches(".item-editor input:not([type]), .item-editor input[type=text]") && el.name
      && !el.hasAttribute("data-varname") && !el.readOnly && !el.disabled && !el.required && !el.closest(".rt-pop")
      && el.name !== "code" && el.name !== "pattern" && !/_code$/.test(el.name);
  }
  function attach(el) {
    if (window.QRichText && QRichText.attachLine && !el.richText && lineField(el) && QRichText.hasPlaceholder(el.value)) QRichText.attachLine(el);
  }
  document.addEventListener("DOMContentLoaded", function () { document.querySelectorAll(".item-editor input").forEach(attach); });
  document.addEventListener("input", function (e) { attach(e.target); });
})();

// Image style: choosing a style fills the description; editing it switches to "custom" (highlighted)
(function () {
  var form = document.querySelector("[data-style-form]");
  if (!form) return;
  var text = form.querySelector("[data-style-text]");
  var customCard = form.querySelector("[data-custom-card]");
  var customRadio = customCard.querySelector("input");
  var ownText = customRadio.checked ? text.value : "";
  form.querySelectorAll('input[name="key"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      if (radio === customRadio) { if (ownText) text.value = ownText; return; }
      text.value = radio.dataset.prompt || "";
    });
  });
  text.addEventListener("input", function () {
    var chosen = form.querySelector('input[name="key"]:checked');
    ownText = text.value;
    if (chosen !== customRadio && text.value !== (chosen ? chosen.dataset.prompt : "")) {
      customRadio.checked = true;
      customCard.classList.remove("just-switched");
      void customCard.offsetWidth;  // restart the highlight animation
      customCard.classList.add("just-switched");
    }
  });
})();

// Day/night mode toggle in the menu at the name (signed out: in the title bar). Remembered in this browser and,
// signed in, with the account (app/display.py), so that every device shows it; the panel "Darstellung" follows.
document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var root = document.documentElement;
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) { /* storage unavailable */ }
    var token = csrfToken(), url = btn.dataset.modeUrl;
    if (!token || !url) return;
    root.setAttribute("data-mode", next);
    var radio = document.querySelector('[data-display-form] input[name="mode"][value="' + next + '"]');
    if (radio) radio.checked = true;
    var body = new FormData(); body.append("mode", next); body.append("csrf_token", token);
    fetch(url, { method: "POST", body: body, credentials: "same-origin" }).catch(function () {});
  });
});

// Panel "Darstellung" on the page "Konto": the page takes the new look at once; the form saves itself (autosave)
document.querySelectorAll("[data-display-form]").forEach(function (form) {
  form.addEventListener("change", function () {
    var root = document.documentElement, data = new FormData(form);
    var mode = data.get("mode"), look = data.get("look"), size = data.get("size");
    var dark = mode === "dark" || (mode === "auto" && window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    root.setAttribute("data-mode", mode);
    root.setAttribute("data-theme", dark ? "dark" : "light");
    if (look === "askurious" && window.matchMedia && matchMedia("(prefers-contrast: more)").matches) look = "kontrast";
    root.setAttribute("data-look", look);
    if (size && size !== "100") root.setAttribute("data-size", size); else root.removeAttribute("data-size");
    try { if (mode !== "auto") localStorage.setItem("theme", mode); } catch (e) { /* storage unavailable */ }
  });
});

// Ablauf page: after an action the page comes back at the element it was about (?at=<id>), marked briefly
(function () {
  var page = document.querySelector("[data-flow-page]");
  if (!page) return;
  var at = page.dataset.at && document.getElementById("node-" + page.dataset.at);
  if (at) {
    at.scrollIntoView({ block: "center" });
    at.classList.add("is-here");
    setTimeout(function () { at.classList.remove("is-here"); }, 2500);
  }
})();

// Ablauf page, an end: its triggers are cards (flow/index.html, case_card). The fields of a card follow its
// trigger, a trigger "Teilquote voll" lists the Teilquoten of the quota chosen, cards move up, and "+ Auslöser"
// adds one from the template. The order of the cards in the form is the order the triggers are checked in.
(function () {
  var page = document.querySelector("[data-flow-page]");
  if (!page) return;
  var data = document.querySelector("[data-quota-cells]");
  var cells = {};
  try { cells = JSON.parse(data ? data.textContent : "{}"); } catch (err) { /* no list: the server's stays */ }

  function show(card) {
    var when = card.querySelector("[data-case-when]").value;
    card.querySelectorAll("[data-case-show]").forEach(function (el) {
      el.hidden = el.getAttribute("data-case-show").split(" ").indexOf(when) < 0;
    });
  }
  // keep: the Teilquoten ticked so far stay ticked (the whole list of the same quota is asked for)
  function listCells(card, keep) {
    var select = card.querySelector("[data-case-quota]"), box = card.querySelector("[data-case-cells]");
    if (!select || !box) return;
    var ticked = keep ? Array.prototype.map.call(box.querySelectorAll("input:checked"), function (input) { return input.value; }) : [];
    var more = card.querySelector("[data-case-all]");
    if (more) more.hidden = true;
    box.textContent = "";
    (cells[select.value] || []).forEach(function (cell) {
      var label = document.createElement("label"), input = document.createElement("input");
      label.className = "check";
      input.type = "checkbox";
      input.name = box.dataset.name;
      input.value = cell[0];
      input.checked = ticked.indexOf(cell[0]) >= 0;
      label.appendChild(input);
      label.appendChild(document.createTextNode(" " + cell[1]));
      box.appendChild(label);
    });
  }
  function removable(list) {
    var cards = list.querySelectorAll("[data-case]");
    cards.forEach(function (card) {
      var remove = card.querySelector("[data-case-remove]");
      if (remove) remove.hidden = cards.length < 2;
    });
  }
  function changed(list) {
    var form = list.closest("form");
    if (form) form.dispatchEvent(new Event("input", { bubbles: true }));  // the form counts as changed (dirty guard)
  }

  page.addEventListener("change", function (e) {
    var card = e.target.closest("[data-case]");
    if (!card) return;
    if (e.target.matches("[data-case-when]")) show(card);
    if (e.target.matches("[data-case-quota]")) listCells(card);
  });
  page.addEventListener("click", function (e) {
    var up = e.target.closest("[data-case-up]"), add = e.target.closest("[data-case-add]"), all = e.target.closest("[data-case-all]");
    if (all) {
      listCells(all.closest("[data-case]"), true);
    } else if (up) {
      var card = up.closest("[data-case]"), before = card.previousElementSibling;
      if (before && before.matches("[data-case]")) {
        card.parentNode.insertBefore(card, before);
        changed(card.parentNode);
      }
    } else if (add) {
      var list = add.closest("[data-cases]"), template = list.querySelector("[data-case-template]");
      var next = 0;
      list.querySelectorAll('input[name="case"]').forEach(function (input) { next = Math.max(next, parseInt(input.value, 10) + 1 || 0); });
      var holder = document.createElement("div");
      holder.innerHTML = template.innerHTML.replace(/__K__/g, String(next));
      var fresh = holder.firstElementChild, number = fresh.querySelector('input[name="case' + next + '_id"]');
      if (number) number.value = "c" + Math.random().toString(16).slice(2, 8).padEnd(6, "0");
      fresh.open = true;
      list.insertBefore(fresh, template);
      show(fresh);
      removable(list);
      changed(list);
      fresh.querySelector("[data-case-when]").focus();
    }
  });
})();

// Ablauf page: drag blocks and groups before/after another node or into a group; and drag a new element from
// „Hinzufügen“ to its place: between two nodes, into a group, or (loop, randomizer, condition) onto a block,
// which then goes into the new element
(function () {
  var page = document.querySelector("[data-flow-page]");
  if (!page) return;
  var csrf = csrfToken();
  var url = page.dataset.placeUrl, dragged = null, target = null, mode = null;
  var adding = null;  // the kind of new element on its way from „Hinzufügen“ (pointerDrag)
  // what holds blocks, and what the block under the pointer is told then
  var HOLDS = { loop: de_en("kommt in die neue Schleife", "goes into the new loop"), rand: de_en("kommt in die neue Randomisierung", "goes into the new randomisation"),
                branch: de_en("kommt in „dann“ der neuen Bedingung", "goes into “then” of the new condition") };
  function clear() {
    page.querySelectorAll(".drop-before, .drop-after, .drop-into, .drop-wrap").forEach(function (n) {
      n.classList.remove("drop-before", "drop-after", "drop-into", "drop-wrap");
      n.removeAttribute("data-drop-note");
    });
  }
  // Where a drop at this element and height goes: sets target and mode and marks the place. Decided afresh on
  // every move: never drop on a stale target.
  function aim(el, y) {
    clear();
    target = null; mode = null;
    if (!el || !el.closest || !page.contains(el)) return;
    function set(t, m, mark) { target = t; mode = m; mark.classList.add("drop-" + m); }
    var held = dragged;  // the node being moved; none while a new element comes in
    var empty = el.closest(".fl-empty");
    var node = el.closest(".fl-node");
    var slot = el.closest(".fl-slot");
    var slotList = slot ? slot.querySelector(":scope > .fl-list") : null;  // the „wenn“ field has no list
    if (empty && !(held && held.contains(empty))) {
      set(empty.parentNode.dataset.parent, "into", slotList ? slot : (empty.classList.contains("fl-root-end") ? empty : empty.parentNode.parentNode));
      return;
    }
    if (!node && el.classList.contains("fl-root")) {  // in the gap between two nodes: the nearer one
      var rows = el.querySelectorAll(":scope > .fl-node"), after = true;
      for (var i = 0; i < rows.length && !node; i++) {
        if (y < rows[i].getBoundingClientRect().top) { node = rows[i]; after = false; }
      }
      node = node || rows[rows.length - 1];
      if (node && node !== held) set(node.dataset.nid, after ? "after" : "before", node);
      return;
    }
    if (!node || node === held || (held && held.contains(node))) return;
    if (slotList && node.contains(slot) && !(held && slot.contains(held))) {  // over „dann“/„sonst“ of a condition
      set(slotList.dataset.parent, "into", slot);
      return;
    }
    var box = node.querySelector(".fl-head").getBoundingClientRect();
    if (node.dataset.group === "1" && y > box.bottom) set(node.dataset.nid, "into", node);
    else if (adding && HOLDS[adding] && node.hasAttribute("data-wrap") && y > box.top + box.height * 0.3 && y < box.bottom - box.height * 0.3) {
      set(node.dataset.nid, "wrap", node);  // onto a block: it goes into the new element
      node.setAttribute("data-drop-note", HOLDS[adding]);
    }
    else if (y < box.top + box.height / 2) set(node.dataset.nid, "before", node);
    else set(node.dataset.nid, "after", node);  // also over the „wenn“ field: after the condition
  }
  function send(to, data) {
    var body = new FormData();
    body.append("csrf_token", csrf);
    Object.keys(data).forEach(function (k) { body.append(k, data[k]); });
    postAndGo(to, body);
  }

  page.querySelectorAll(".fl-node[draggable=true]").forEach(function (node) {
    node.addEventListener("dragstart", function (e) {
      e.stopPropagation();
      dragged = node;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", node.dataset.nid);
      setTimeout(function () { node.classList.add("is-dragged"); page.classList.add("dragging"); }, 0);
    });
    node.addEventListener("dragend", function () {
      node.classList.remove("is-dragged"); page.classList.remove("dragging"); clear(); dragged = null;
    });
  });
  page.addEventListener("dragover", function (e) {
    if (!dragged) return;
    e.preventDefault();
    aim(e.target, e.clientY);
  });
  page.addEventListener("drop", function (e) {
    if (!dragged || !target) return;
    e.preventDefault();
    send(url.replace("__NID__", dragged.dataset.nid), { target: target, mode: mode });
  });

  // A card of „Hinzufügen“: a click adds the element at the end (the form), dragging puts it where it is dropped
  var form = page.querySelector("[data-flow-add]");
  if (!form) return;
  pointerDrag({
    source: function (el) {
      var card = el.closest && el.closest(".fl-add");
      return card && form.contains(card) ? { el: card, kind: card.value } : null;
    },
    start: function (d) {
      adding = d.kind;
      d.el.classList.add("is-dragged");
      page.classList.add("dragging");
      d.ghost = document.createElement("div");
      d.ghost.textContent = d.el.querySelector("span").textContent + " " + d.el.querySelector("strong").textContent;
    },
    move: function (d) { aim(document.elementFromPoint(d.x, d.y), d.y); },
    drop: function (d) { if (target) send(form.action, { kind: d.kind, target: target, mode: mode }); },
    end: function (d) {
      d.el.classList.remove("is-dragged"); page.classList.remove("dragging"); clear(); adding = null;
    },
  });
})();

// Konstrukte detail page: insert into the chosen block
(function () {
  var form = document.querySelector("[data-insert-form]");
  if (!form) return;
  var select = form.querySelector("[data-insert-block]");
  function sync() { form.action = select.value; }
  select.addEventListener("change", sync);
  sync();
})();

// Block names: save on Enter or when leaving the field; a new block ("#rename-<id>") gets its name selected
(function () {
  // The form holds the name and all ticks as the page showed them. "changed" names the field the person changed,
  // and only that one is saved: a page opened a while ago does not put the others back (editor.update_block).
  // A tick clicked while the name is being typed sends both names: the second sending replaces the first.
  function save(field) {
    var mark = document.createElement("input");
    mark.type = "hidden";
    mark.name = "changed";
    mark.value = field.name;
    field.form.appendChild(mark);
    field.form.submit();
  }
  window.addEventListener("pageshow", function (e) {  // back to this page as it was left: nothing is changed yet
    if (e.persisted) document.querySelectorAll(".block-form input[name=changed]").forEach(function (m) { m.remove(); });
  });
  document.querySelectorAll("[data-block-name]").forEach(function (input) {
    var initial = input.value;
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); if (input.value !== initial) save(input); else input.blur(); }
      if (e.key === "Escape") { input.value = initial; input.blur(); }
    });
    input.addEventListener("blur", function () { if (input.value.trim() !== initial.trim()) save(input); });
  });
  // the ticks in a block's head save at once, like its name
  document.querySelectorAll("[data-block-option]").forEach(function (box) {
    box.addEventListener("change", function () { save(box); });
  });
  var m = location.hash.match(/^#rename-(\d+)$/);
  if (m) {
    var block = document.getElementById("block-" + m[1]);
    var input = block && block.querySelector("[data-block-name]");
    if (input) { block.scrollIntoView({ block: "center" }); input.focus(); input.select(); }
  }
})();

// Feedback: the bug icon opens a dialog; the current page is sent along. Other buttons with data-feedback-open do the
// same (an error page, "an die Admins der Plattform schreiben"; UI/UX audit 2026-10-09, X68 and X72)
(function () {
  var dialog = document.querySelector("[data-feedback-dialog]");
  var opens = document.querySelectorAll("[data-feedback-open]");
  if (!dialog || !opens.length) return;
  opens.forEach(function (open) { open.addEventListener("click", function () {
    dialog.querySelector("[data-feedback-page]").value = location.pathname + location.search;
    if (dialog.showModal) dialog.showModal(); else location.href = "/feedback/new?page=" + encodeURIComponent(location.pathname);
    var text = dialog.querySelector("textarea");
    if (text) text.focus();
  }); });
  dialog.querySelector("[data-feedback-close]").addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });  // click outside
})();

// A bookmark of the former panel "Gruppen" on Administration › Personen (/admin/#gruppen) leads to the tab
// "Gruppen" (Ralf 2026-10-10); the server never sees the part after "#".
(function () {
  if (location.hash === "#gruppen" && /\/admin\/?$/.test(location.pathname)) {
    location.replace(location.pathname.replace(/\/$/, "") + "/groups#gruppen");
  }
})();

// "Person hinzufügen …" (Administration › Personen, UI/UX audit 2026-10-07, S10): the panel with the two ways becomes a
// dialog the button opens; without scripts it stays a panel the button leads to (#person-hinzufuegen). Esc closes it
// (the browser's own), the focus goes into the first field and back to the button. A form refused by the server
// (W84) comes back with its field marked: the dialog opens by itself, at that field. So it does after signing in
// again for this action (accounts.need_fresh), which leads back to #person-hinzufuegen. "Neue Organisation …" and
// "Neue Gruppe …" work the same way (Ralf 2026-10-20): each panel belongs to the button that leads to its id.
document.querySelectorAll("[data-person-add-panel]").forEach(function (panel) {
  var open = document.querySelector('[data-person-add][href="#' + panel.id + '"]');
  if (!open || typeof HTMLDialogElement === "undefined") return;
  var dialog = document.createElement("dialog");
  dialog.className = "person-dialog" + (panel.classList.contains("add-dialog-narrow") ? " is-narrow" : "");
  dialog.setAttribute("aria-labelledby", panel.getAttribute("aria-labelledby"));
  var refused = panel.querySelector(".flash-error");
  while (panel.firstChild) dialog.appendChild(panel.firstChild);
  dialog.id = panel.id;
  panel.replaceWith(dialog);
  var close = dialog.querySelector("[data-person-add-close]");
  close.hidden = false;
  function show(field) {
    if (!dialog.open) dialog.showModal();
    (field || dialog.querySelector("input:not([type=hidden]):not([hidden])")).focus();
  }
  open.addEventListener("click", function (e) { e.preventDefault(); show(); });
  close.addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("close", function () { open.focus(); });
  // the way first (Ralf 2026-10-18): only the form of the chosen way, in one column; a refused form chooses its way
  var choice = dialog.querySelector("[data-person-way-choice]");
  if (choice) {
    var ways = dialog.querySelectorAll("[data-way]");
    var showWay = function (way) {
      ways.forEach(function (s) { s.hidden = s.dataset.way !== way; });
      dialog.querySelector(".person-ways").classList.add("is-one");
    };
    var start = refused && refused.closest("[data-way]") ? refused.closest("[data-way]").dataset.way : "orcid";
    choice.querySelector("input[value=" + start + "]").checked = true;
    choice.hidden = false;
    showWay(start);
    choice.addEventListener("change", function (e) { showWay(e.target.value); });
  }
  // "Neue Organisation" and "Neue Gruppe" are refused by the server with a message at the top (a redirect): the dialog
  // keeps what was typed for that one return and opens again with it and the message, as "Person hinzufügen" does
  var one = dialog.querySelectorAll("form").length === 1 && !choice ? dialog.querySelector("form") : null;
  if (one && !refused) {
    var key = "add-dialog:" + location.pathname + "#" + dialog.id, draft = null;
    try { draft = JSON.parse(sessionStorage.getItem(key) || "null"); sessionStorage.removeItem(key); } catch (e) { /* none */ }
    var message = document.querySelector("[data-flashes] .flash-error");
    if (draft && message && Date.now() - draft.t < 60000) {
      Object.keys(draft.f).forEach(function (name) {
        var field = one.elements[name];
        if (!field || field.type === "hidden") return;
        if (field.type === "checkbox") field.checked = draft.f[name] === true; else field.value = draft.f[name];
      });
      var flashes = message.parentNode;
      one.insertBefore(message, one.firstChild);
      if (!flashes.querySelector(".flash")) flashes.remove();
      refused = message;
    }
    one.addEventListener("submit", function () {
      var f = {};
      Array.prototype.forEach.call(one.elements, function (field) {
        if (field.name && field.type !== "hidden") f[field.name] = field.type === "checkbox" ? field.checked : field.value;
      });
      try { sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), f: f })); } catch (e) { /* none */ }
    });
  }
  if (refused) {
    var form = refused.closest("form");
    show(form.querySelector("[aria-invalid=true]") || form.querySelector("input:not([type=hidden]):not([type=search])"));
  } else if (location.hash === "#" + dialog.id) {
    show();
  }
});

// The period of an account (select valid_mode, Ralf 2026-10-18): the number only for months or weeks, the date only
// for "bis zum Datum"; without scripts both stay. In "Person hinzufügen" of the platform the user types follow the
// chosen organisation (data-org on each option): before, the types of all organisations stood there, and a type of
// another one silently moved the new account there (admin.new_account).
(function () {
  document.querySelectorAll("form select[name=valid_mode]").forEach(function (mode) {
    var form = mode.form;
    var n = form.querySelector("input[name=valid_n]");
    var date = form.querySelector("input[name=valid_date]");
    function update() {
      if (n) n.hidden = mode.value !== "m" && mode.value !== "w";
      if (date) date.hidden = mode.value !== "d";
    }
    mode.addEventListener("change", update);
    update();
  });
  document.querySelectorAll("form select[name=org]").forEach(function (org) {
    var types = org.form.querySelector("select[name=account_type]");
    if (!types || !types.querySelector("option[data-org]")) return;
    function update() {
      var options = Array.prototype.slice.call(types.options);
      options.forEach(function (o) {
        var off = o.dataset.org !== undefined && o.dataset.org !== org.value;
        o.hidden = off;
        o.disabled = off;
      });
      var current = types.options[types.selectedIndex];
      if (!current || current.disabled) {
        var pick = options.filter(function (o) { return !o.disabled && o.dataset.orgDefault !== undefined; })[0] ||
                   options.filter(function (o) { return !o.disabled; })[0];
        if (pick) pick.selected = true;
      }
    }
    org.addEventListener("change", update);
    update();
  });
})();

// Survey description: live word count with a gentle nudge (used for consent and AI assistance)
(function () {
  document.querySelectorAll("[data-description-field]").forEach(function (area) {
    var out = area.parentElement.querySelector("[data-description-count]");
    if (!out) return;
    function update() {
      var n = area.value.trim() ? area.value.trim().split(/\s+/).length : 0;
      out.textContent = n < 15 ? de_en(n + " Wörter: Etwas mehr hilft Teilnehmenden und der KI (ab etwa 15 Wörtern).",
                                       n + " words: a little more helps participants and the AI (from about 15 words).")
                               : de_en(n + " Wörter ✓", n + " words ✓");
      out.classList.toggle("is-ok", n >= 15);
    }
    area.addEventListener("input", update);
    update();
  });
})();

// Consent: fill the duration from the questionnaire estimate (upper end of the range)
document.querySelectorAll("[data-duration-estimate]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var input = btn.closest(".duration-field").querySelector("[data-duration-input]");
    input.value = btn.dataset.durationEstimate;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    btn.textContent = "✓ " + de_en(btn.dataset.durationEstimate + " Minuten übernommen", btn.dataset.durationEstimate + " minutes applied");
  });
});

// Likert editor: choosing an answer scale or number of points fills in the standard labels;
// editing a label afterwards switches the scale to "custom". With "nur Endpunkte verbalisiert" the steps
// in between stay empty and locked, so no wording is suggested for them.
(function () {
  var list = document.querySelector("[data-scale-labels]");
  var anchors = document.querySelector("[data-anchor-select]");
  var points = document.querySelector("[data-points-select]");
  var labeling = document.querySelector("[data-labeling-select]");
  if (!list || !anchors || !points) return;
  var presets = JSON.parse(list.dataset.presets);
  var anchored = list.dataset.anchored === "1";  // a construct without an answer scale gets no preset wording
  function resize(old, n) {  // own labels: ends stay, steps in between kept from the front or added empty
    if (old.length < 2) return (old.concat(new Array(n).fill(""))).slice(0, n);
    var middle = old.slice(1, -1).concat(new Array(n).fill("")).slice(0, Math.max(n - 2, 0));
    return [old[0]].concat(middle, [old[old.length - 1]]).slice(0, n);
  }
  function endsOnly() { return labeling && labeling.value === "endpoints"; }
  function draw(labels) {
    var ends = endsOnly();
    list.innerHTML = "";
    labels.forEach(function (l, i) {
      var li = document.createElement("li"), input = document.createElement("input");
      var middle = i > 0 && i < labels.length - 1;
      input.name = "label"; input.value = ends && middle ? "" : l;
      input.setAttribute("aria-label", de_en("Beschriftung Stufe ", "Label of point ") + (i + 1));
      if (ends && middle) {
        input.readOnly = true; input.className = "dimmed"; input.placeholder = de_en("ohne Beschriftung", "no label");
        input.title = de_en("Bei „nur Endpunkte verbalisiert“ bleiben die Stufen dazwischen ohne Beschriftung",
                            "With “only end points labelled” the points in between stay without a label");
      }
      li.appendChild(input); list.appendChild(li);
    });
  }
  function current() { return Array.prototype.map.call(list.querySelectorAll("input"), function (i) { return i.value; }); }
  function fill() {
    anchored = true;
    var key = anchors.value === "custom" ? null : anchors.value;
    var n = parseInt(points.value, 10);
    var old = current();
    // a preset refills; own labels ("custom") are never overwritten, only resized
    var labels = key ? presets[key][n] : (old.length === n ? old : resize(old, n));
    draw(labels);
  }
  anchors.addEventListener("change", fill);
  points.addEventListener("change", fill);
  if (labeling) labeling.addEventListener("change", function () {
    var labels = current(), key = anchors.value === "custom" ? null : anchors.value;
    if (!endsOnly() && key && anchored) {  // back to all steps: empty steps in between take the answer scale's wording
      var preset = presets[key][labels.length] || [];
      labels = labels.map(function (l, i) { return l || (i > 0 && i < labels.length - 1 ? preset[i] || "" : l); });
    }
    draw(labels);
  });
  list.addEventListener("input", function () { anchors.value = "custom"; });
})();

// Side-by-side matrix editor (items/side_by_side/editor.html): per column card, the fields of its kind of answer;
// an answer scale or a number of answers fills in the standard labels (single choice) or resizes own labels.
document.querySelectorAll("[data-sbs-col-editor]").forEach(function (card) {
  var box = card.closest("[data-sbs-presets]");
  var presets = box ? JSON.parse(box.dataset.sbsPresets || "{}") : {};
  var kindSel = card.querySelector("[data-col-kind]"), points = card.querySelector("[data-col-points]");
  var anchors = card.querySelector("[data-col-anchors]"), labeling = card.querySelector("[data-col-labeling]");
  var list = card.querySelector("[data-col-labels]");
  function kind() { return kindSel ? kindSel.value : "single"; }
  function show() {
    var k = kind();
    card.dataset.kind = k;
    card.querySelectorAll("[data-for-kinds]").forEach(function (el) {
      el.hidden = el.dataset.forKinds.split(" ").indexOf(k) < 0;
    });
    if (points) points.querySelectorAll("[data-list-only]").forEach(function (o) { o.disabled = k === "single"; });
  }
  function current() { return Array.prototype.map.call(list.querySelectorAll("input"), function (i) { return i.value; }); }
  function resize(old, n) {
    if (old.length < 2) return (old.concat(new Array(n).fill(""))).slice(0, n);
    var middle = old.slice(1, -1).concat(new Array(n).fill("")).slice(0, Math.max(n - 2, 0));
    return [old[0]].concat(middle, [old[old.length - 1]]).slice(0, n);
  }
  function draw(labels) {
    var ends = kind() === "single" && labeling && labeling.value === "endpoints";
    list.innerHTML = "";
    labels.forEach(function (l, i) {
      var li = document.createElement("li"), input = document.createElement("input");
      var middle = i > 0 && i < labels.length - 1;
      input.name = list.dataset.name; input.value = ends && middle ? "" : l;
      input.setAttribute("aria-label", de_en("Antwort ", "Answer ") + (i + 1));
      if (ends && middle) { input.readOnly = true; input.className = "dimmed"; input.placeholder = de_en("ohne Beschriftung", "no label"); }
      li.appendChild(input); list.appendChild(li);
    });
  }
  function fill() {
    if (!list || !points) return;
    var n = parseInt(points.value, 10), key = kind() === "single" && anchors && anchors.value !== "custom" ? anchors.value : null;
    if (kind() === "single" && n > 10) { n = 10; points.value = "10"; }
    var old = current();
    draw(key && presets[key] && presets[key][n] ? presets[key][n] : (old.length === n ? old : resize(old, n)));
  }
  if (kindSel) kindSel.addEventListener("change", function () { show(); fill(); });
  if (points) points.addEventListener("change", fill);
  if (anchors) anchors.addEventListener("change", fill);
  if (labeling) labeling.addEventListener("change", fill);
  if (list) list.addEventListener("input", function () { if (anchors) anchors.value = "custom"; });
  show();
  if (list && !list.querySelector("input")) fill();
});

// Mark an image (editor): all marks so far drawn semi-transparently over the image
document.querySelectorAll("[data-mark-pool]").forEach(function (box) {
  var data = JSON.parse(box.dataset.markPool), img = box.querySelector("img"), canvas = box.querySelector("canvas");
  function draw() {
    var w = img.clientWidth, h = img.clientHeight, dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr; canvas.height = h * dpr;
    var g = canvas.getContext("2d");
    g.scale(dpr, dpr);
    g.globalAlpha = Math.max(0.08, Math.min(0.5, 6 / Math.sqrt(data.marks.length + 1)));
    g.fillStyle = g.strokeStyle = data.color || "#ffd400";
    g.lineCap = g.lineJoin = "round";
    g.lineWidth = w * 0.03;
    data.marks.forEach(function (m) {
      g.beginPath();
      if (data.mode === "lines") { m.forEach(function (p, i) { g[i ? "lineTo" : "moveTo"](p[0] * w, p[1] * h); }); g.stroke(); }
      else { g.arc(m[0] * w, m[1] * h, w * 0.018, 0, Math.PI * 2); g.fill(); }
    });
  }
  if (img.complete && img.naturalWidth) draw(); else img.addEventListener("load", draw);
  window.addEventListener("resize", draw);
});

// Mark an image, hot spot (editor, items/image_mark.py): every row of the areas is a rectangle over the image (its hidden
// field area_box holds "x,y,w,h" relative to the image). A drag on the image draws a rectangle: it goes to the row whose
// "neu ziehen" was pressed, else to the first row without one, else to a new row ("+" of the row list). A click on a
// rectangle puts the cursor into its label. Changes report "change" on the field, so autosave saves them.
document.querySelectorAll("[data-hot-editor]").forEach(function (box) {
  var stage = box.querySelector("[data-hot-edit-stage]"), rows = box.querySelector("[data-hot-rows]");
  if (!rows) return;
  var target = null, fixed = !stage || stage.hasAttribute("data-fixed");
  function field(tr) { return tr.querySelector('input[name="area_box"]'); }
  function parse(v) {
    var b = (v || "").split(",").map(parseFloat);
    return b.length === 4 && !b.some(isNaN) ? b : null;
  }
  function pct(v) { return uiNum(v * 100, 0); }
  function draw() {
    if (stage) Array.prototype.slice.call(stage.querySelectorAll(".hot-edit-area")).forEach(function (el) { el.remove(); });
    Array.prototype.forEach.call(rows.children, function (tr, n) {
      var f = field(tr), b = f && parse(f.value), where = tr.querySelector("[data-hot-where]");
      var label = tr.querySelector('input[name="area_label"]'), code = tr.querySelector('input[name="area_code"]');
      tr.classList.toggle("hot-target", tr === target);
      if (where) where.textContent = b ? pct(b[0]) + "–" + pct(b[0] + b[2]) + de_en(" % von links, ", " % from the left, ")
        + pct(b[1]) + "–" + pct(b[1] + b[3]) + de_en(" % von oben", " % from the top") : de_en("noch keins", "none yet");
      // the four numbers follow the rectangle, except the one being typed into (X22)
      Array.prototype.forEach.call(tr.querySelectorAll("[data-hot-num]"), function (input) {
        if (input === document.activeElement) return;
        input.value = b ? Math.round(b[+input.getAttribute("data-hot-num")] * 100) : "";
      });
      if (!b || !stage) return;
      var el = document.createElement("div");
      el.className = "hot-edit-area" + (tr === target ? " is-target" : "");
      el.style.left = b[0] * 100 + "%"; el.style.top = b[1] * 100 + "%";
      el.style.width = b[2] * 100 + "%"; el.style.height = b[3] * 100 + "%";
      var name = document.createElement("span");
      name.textContent = (code && (code.value || code.placeholder)) + (label && (label.value || label.placeholder) ? " · " + (label.value || label.placeholder) : "");
      el.appendChild(name);
      el.addEventListener("pointerdown", function (e) { e.stopPropagation(); });
      el.addEventListener("click", function () { if (label) label.focus(); });
      stage.appendChild(el);
    });
  }
  function rowFor() {
    if (target && target.parentNode === rows) return target;
    var free = Array.prototype.filter.call(rows.children, function (tr) { var f = field(tr); return f && !parse(f.value); })[0];
    if (free) return free;
    var add = rows.closest("table").nextElementSibling;
    if (add && add.classList.contains("row-add") && !add.disabled) { add.click(); return rows.lastElementChild; }
    return null;
  }
  rows.addEventListener("click", function (e) {
    var b = e.target.closest("[data-hot-redraw]");
    if (!b) return;
    var tr = b.closest("tr");
    target = target === tr ? null : tr;
    draw();
  });
  // the rectangle typed as four numbers (% left, top, width, height): written into area_box once all four make one of
  // at least 1 % (as the server keeps it); "change" on area_box saves it (autosave)
  rows.addEventListener("change", function (e) {
    if (!e.target.matches || !e.target.matches("[data-hot-num]")) return;
    var tr = e.target.closest("tr"), f = field(tr);
    var v = Array.prototype.map.call(tr.querySelectorAll("[data-hot-num]"), function (input) {
      return Math.min(Math.max(parseFloat(String(input.value).replace(",", ".")), 0), 100) / 100;
    });
    if (v.some(isNaN) || v[2] < 0.01 || v[3] < 0.01) return;
    v[2] = Math.min(v[2], 1 - v[0]); v[3] = Math.min(v[3], 1 - v[1]);
    f.value = v.map(function (x) { return Math.round(x * 10000) / 10000; }).join(",");
    draw();
  });
  rows.addEventListener("input", draw);
  rows.addEventListener("rows:changed", draw);
  new MutationObserver(draw).observe(rows, { childList: true });
  if (stage && !fixed) {
    var start = null, ghost = null;
    function at(e) {
      var r = stage.getBoundingClientRect();
      return [Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1)];
    }
    function rect(a, b) { return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1])]; }
    stage.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      start = at(e);
      ghost = document.createElement("div");
      ghost.className = "hot-edit-area is-ghost";
      stage.appendChild(ghost);
      stage.setPointerCapture(e.pointerId);
    });
    stage.addEventListener("pointermove", function (e) {
      if (!start) return;
      var b = rect(start, at(e));
      ghost.style.left = b[0] * 100 + "%"; ghost.style.top = b[1] * 100 + "%";
      ghost.style.width = b[2] * 100 + "%"; ghost.style.height = b[3] * 100 + "%";
    });
    function end(e) {
      if (!start) return;
      var b = rect(start, at(e));
      start = null;
      if (ghost) ghost.remove();
      if (b[2] < 0.01 || b[3] < 0.01) return;  // a click, no rectangle (the server keeps none under 1 %)
      var tr = rowFor();
      if (!tr) return;
      var f = field(tr);
      f.value = b.map(function (v) { return Math.round(v * 10000) / 10000; }).join(",");
      target = null;
      draw();
      f.dispatchEvent(new Event("change", { bubbles: true }));
      var label = tr.querySelector('input[name="area_label"]');
      if (label && !label.value) label.focus();
    }
    stage.addEventListener("pointerup", end);
    stage.addEventListener("pointercancel", function () { start = null; if (ghost) ghost.remove(); });
  }
  draw();
});

// Quality assurance page: a movable speeder threshold (Leiner 2019) over the completion times as a histogram
// with the median (a box without <svg> shows the numbers only). The slider belongs to the form that saves
// the threshold; data-form names the data pool form whose sources decide which times are shown.
document.querySelectorAll("[data-speed]").forEach(function (box) {
  var data = JSON.parse(box.dataset.speed);
  var range = box.querySelector("[data-speed-range]"), svg = box.querySelector("svg");
  var realBox = box.querySelector("[data-speed-real]"), base = box.querySelector("[data-speed-base]");
  var NS = "http://www.w3.org/2000/svg", W = 600, H = 166, PAD = 26, TOP = 20;  // TOP: room for the label of the median
  var times, median, top, bw, nb = 30, bars, fast, binned, ymax, cut, shade;
  // every duration of the box as "1:36 Min.", as the median (UI/UX audit 2026-10-09, X62: not "1,6 Min." beside "3:13 Min.")
  function fmt(s) {
    var r = Math.round(s);
    return r >= 60 ? Math.floor(r / 60) + ":" + String(r % 60).padStart(2, "0") + de_en(" Min.", " min") : r + " s";
  }
  function el(name, attrs, text) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text) e.textContent = text;
    svg.appendChild(e); return e;
  }
  function x(t) { return PAD + (W - 2 * PAD) * Math.min(t, top) / top; }
  function draw() {
    var set = realBox.checked ? data.real : data.all;
    base.value = realBox.checked ? "real" : "all";
    times = set.times; median = set.median;
    var empty = !times.length;
    box.querySelector("[data-speed-empty]").hidden = !empty;
    box.querySelector("[data-speed-body]").hidden = empty;
    if (empty) return;
    // a duration as on the dashboards and the timeline: "3:09 Min." (UI/UX audit 2026-10-05, V73)
    box.querySelector("[data-speed-median]").textContent = fmt(median);
    if (!svg) return update();
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    top = Math.max(median * 3, times[Math.floor(times.length * 0.95)] || 0, 60);  // long tail in the last bin
    bw = top / nb;
    var bins = new Array(nb).fill(0);
    binned = bins.map(function () { return []; });
    times.forEach(function (t) { var i = Math.min(nb - 1, Math.floor(t / bw)); bins[i] += 1; binned[i].push(t); });
    ymax = Math.max.apply(null, bins) || 1;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    shade = el("rect", { x: PAD, y: TOP, height: H - PAD - TOP, class: "speed-zone" });
    bars = bins.map(function (n, i) {
      var h = (H - PAD - TOP - 4) * n / ymax;
      return el("rect", { x: x(i * bw) + 1, y: H - PAD - h, width: Math.max(1, x(bw) - PAD - 2), height: h, class: "speed-bar" });
    });
    // the speeders of a bin in red, as high as their number: a bar the limit cuts is red in part, not neutral
    fast = bins.map(function (n, i) {
      return el("rect", { x: x(i * bw) + 1, y: H - PAD, width: Math.max(1, x(bw) - PAD - 2), height: 0, class: "speed-bar is-speeder" });
    });
    el("line", { x1: x(median), x2: x(median), y1: TOP, y2: H - PAD, class: "speed-median" });
    // above the plot, so it never sits on a bar
    el("text", { x: x(median), y: TOP - 6, class: "speed-label", "text-anchor": "middle" }, "Median");
    cut = el("line", { y1: TOP, y2: H - PAD, class: "speed-cut" });
    el("line", { x1: PAD, x2: W - PAD, y1: H - PAD, y2: H - PAD, class: "speed-axis" });
    // counts: grid lines at the highest bar and its half, labelled on the left (UI/UX audit 2026-10-05, V77)
    [ymax, Math.round(ymax / 2)].forEach(function (v) {
      if (!v) return;
      var y = H - PAD - (H - PAD - TOP - 4) * v / ymax;
      el("line", { x1: PAD, x2: W - PAD, y1: y, y2: y, class: "speed-grid" });
      el("text", { x: PAD - 4, y: y + 4, class: "speed-tick", "text-anchor": "end" }, String(v));
    });
    // a stroke every minute (every 2, 5 or 10 on long surveys), labelled where there is room
    var step = [1, 2, 5, 10, 15, 30].find(function (m) { return top / 60 / m <= 8; }) || 60;
    for (var m = step; m * 60 < top; m += step) {
      el("line", { x1: x(m * 60), x2: x(m * 60), y1: H - PAD, y2: H - PAD + 4, class: "speed-axis" });
      if (x(top) - x(m * 60) > 50) el("text", { x: x(m * 60), y: H - 6, class: "speed-tick", "text-anchor": "middle" }, m + de_en(" Min.", " min"));
    }
    el("text", { x: x(0), y: H - 6, class: "speed-tick", "text-anchor": "start" }, "0");
    el("text", { x: x(top), y: H - 6, class: "speed-tick", "text-anchor": "end" }, "≥ " + fmt(top));
    // the same as a table and a sentence for screen readers
    var table = box.querySelector("[data-speed-table]");
    if (table) {
      table.innerHTML = bins.map(function (n, i) {
        var a = i * bw, b = (i + 1) * bw;
        return n ? "<tr><td>" + (i === nb - 1 ? de_en("ab ", "from ") + fmt(a) : fmt(a) + de_en(" bis unter ", " to under ") + fmt(b)) + "</td><td>" + n + "</td></tr>" : "";
      }).join("");
    }
    update();
  }
  function update() {
    if (!times.length) return;
    var pct = parseInt(range.value, 10), limit = median * pct / 100;
    var n = times.filter(function (t) { return t < limit; }).length;
    box.querySelector("[data-speed-pct]").textContent = pct;
    box.querySelector("[data-speed-cut]").textContent = de_en("unter ", "under ") + fmt(limit);
    box.querySelector("[data-speed-n]").textContent = n + de_en(" von ", " of ") + times.length + " (" + Math.round(n / times.length * 100) + de_en(" %)", "%)");
    if (!svg) return;
    svg.setAttribute("aria-label", de_en("Bearbeitungszeiten von " + times.length + " Teilnahmen, Median " + fmt(median) +
      ", Schwelle " + fmt(limit) + ": " + n + " darunter", "Completion times of " + times.length + " participations, median " + fmt(median) +
      ", threshold " + fmt(limit) + ": " + n + " below"));
    cut.setAttribute("x1", x(limit)); cut.setAttribute("x2", x(limit));
    shade.setAttribute("width", Math.max(0, x(limit) - PAD));
    fast.forEach(function (b, i) {
      var h = (H - PAD - TOP - 4) * binned[i].filter(function (t) { return t < limit; }).length / ymax;
      b.setAttribute("y", H - PAD - h); b.setAttribute("height", h);
    });
  }
  range.addEventListener("input", update);
  realBox.addEventListener("change", draw);
  // the data pool (checkboxes "source" of the same form) changes the times at once
  var form = box.dataset.form && document.getElementById(box.dataset.form);
  if (form && data.by_source) {
    form.addEventListener("change", function (e) {
      if (e.target.name !== "source") return;
      var chosen = Array.prototype.map.call(form.querySelectorAll('input[name="source"]:checked'), function (c) { return c.value; });
      var t = [];
      chosen.forEach(function (src) { t = t.concat(data.by_source[src] || []); });
      t.sort(function (a, b) { return a - b; });
      var m = t.length ? (t.length % 2 ? t[(t.length - 1) / 2] : (t[t.length / 2 - 1] + t[t.length / 2]) / 2) : null;
      data.real = data.all = { times: t, median: m };
      draw();
    });
  }
  draw();
});

// The speeder rule (radios "speeder_rule"): only the part of the chosen rule is shown.
document.querySelectorAll("[data-speed-rule]").forEach(function (group) {
  var section = group.closest("section");
  group.addEventListener("change", function (e) {
    if (e.target.name !== "speeder_rule") return;
    section.querySelectorAll("[data-rule-box]").forEach(function (b) { b.hidden = b.dataset.ruleBox !== e.target.value; });
  });
});

// Relative speed index (Leiner 2019) of every completed participation as a histogram from 0 to 3, with the
// typical speed (1) and the limit: who lies above it is a speeder (red; a bar the limit cuts is red in part).
document.querySelectorAll("[data-rsi]").forEach(function (box) {
  var values = JSON.parse(box.dataset.rsi).values, range = box.querySelector("[data-rsi-range]"), svg = box.querySelector("svg");
  var NS = "http://www.w3.org/2000/svg", W = 600, H = 166, PAD = 22, TOP = 20, TOPV = 3, nb = 30, bw = TOPV / nb;
  var fast = [], binned = [], ymax = 1, cut, shade;
  function de(v) { return uiNum(v, 1); }
  function el(name, attrs, text) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text) e.textContent = text;
    svg.appendChild(e); return e;
  }
  function x(v) { return PAD + (W - 2 * PAD) * Math.min(v, TOPV) / TOPV; }
  if (svg && values.length) {
    var bins = new Array(nb).fill(0);
    binned = bins.map(function () { return []; });
    // whole hundredths: 2.00 sits in the bin from 2.0 (floating point would put it below)
    values.forEach(function (v) { var i = Math.min(nb - 1, Math.floor(Math.round(v * 100) / 10)); bins[i] += 1; binned[i].push(v); });
    ymax = Math.max.apply(null, bins) || 1;
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    shade = el("rect", { y: TOP, height: H - PAD - TOP, class: "speed-zone" });
    bins.forEach(function (n, i) {
      var h = (H - PAD - TOP - 4) * n / ymax;
      el("rect", { x: x(i * bw) + 1, y: H - PAD - h, width: Math.max(1, x(bw) - PAD - 2), height: h, class: "speed-bar" });
    });
    fast = bins.map(function (n, i) {
      return el("rect", { x: x(i * bw) + 1, y: H - PAD, width: Math.max(1, x(bw) - PAD - 2), height: 0, class: "speed-bar is-speeder" });
    });
    el("line", { x1: x(1), x2: x(1), y1: TOP, y2: H - PAD, class: "speed-median" });
    el("text", { x: x(1), y: TOP - 6, class: "speed-label", "text-anchor": "middle" }, de_en("typisch (1)", "typical (1)"));
    cut = el("line", { y1: TOP, y2: H - PAD, class: "speed-cut" });
    el("line", { x1: PAD, x2: W - PAD, y1: H - PAD, y2: H - PAD, class: "speed-axis" });
    [0, 1, 2, 3].forEach(function (v, i) {
      el("text", { x: x(v), y: H - 6, class: "speed-tick", "text-anchor": i === 0 ? "start" : i === 3 ? "end" : "middle" }, de(v));
    });
  }
  function update() {
    var limit = parseFloat(range.value);
    box.querySelector("[data-rsi-limit]").textContent = de(limit);
    var count = box.querySelector("[data-rsi-n]");
    if (!values.length || !count) return;
    var n = values.filter(function (v) { return v > limit; }).length;
    count.textContent = n + de_en(" von ", " of ") + values.length + " (" + Math.round(n / values.length * 100) + de_en(" %)", "%)");
    if (!svg) return;
    cut.setAttribute("x1", x(limit)); cut.setAttribute("x2", x(limit));
    shade.setAttribute("x", x(limit)); shade.setAttribute("width", Math.max(0, W - PAD - x(limit)));
    fast.forEach(function (b, i) {
      var h = (H - PAD - TOP - 4) * binned[i].filter(function (v) { return v > limit; }).length / ymax;
      b.setAttribute("y", H - PAD - h); b.setAttribute("height", h);
    });
  }
  range.addEventListener("input", update);
  update();
});

// Slider editor: a preset fills from/to/step/unit
document.querySelectorAll("[data-slider-preset]").forEach(function (sel) {
  sel.addEventListener("change", function () {
    if (!sel.value) return;
    var p = sel.value.split("|"), form = sel.form;
    form.querySelector("[name=min]").value = p[0];
    form.querySelector("[name=max]").value = p[1];
    form.querySelector("[name=step]").value = p[2];
    var f = form.querySelector("[name=format]");
    f.value = p[3]; f.dispatchEvent(new Event("change"));
    sel.value = "";
  });
});

// Conjoint and MaxDiff editor: tasks or sets needed for a planned sample (the sample size rule solved the other
// way round, as tasks_needed() in conjoint_design.py and sets_needed() in maxdiff_design.py)
document.querySelectorAll("[data-cj-reverse]").forEach(function (box) {
  var input = box.querySelector("[data-cj-reverse-n]"), levels = parseInt(box.dataset.levels, 10);
  var targets = box.dataset.targets.split(","), max = parseInt(box.dataset.max, 10), long = parseInt(box.dataset.long, 10);
  input.addEventListener("input", function () {
    var n = parseInt(input.value, 10);
    box.querySelectorAll("tr[data-alts]").forEach(function (row) {
      var alts = parseInt(row.dataset.alts, 10), cells = row.querySelectorAll("td");
      targets.forEach(function (target, k) {
        var t = n > 0 ? Math.max(1, Math.ceil(target * levels / (n * alts))) : null, cell = cells[k + 1];
        cell.textContent = t === null ? "–" : t > max ? de_en("über ", "over ") + max : t;
        cell.className = t > max ? "cj-over" : t > long ? "cj-long" : "";
      });
    });
  });
});

// Pre-launch check: construct ideas from the AI, only on request
(function () {
  var box = document.querySelector("[data-ai-ideas]");
  if (!box) return;
  var btn = box.querySelector("[data-ai-ideas-go]"), list = box.querySelector("[data-ai-ideas-list]");
  var csrf = csrfToken();
  function li(html) { var e = document.createElement("li"); e.innerHTML = html; list.appendChild(e); return e; }
  var writing = box.querySelector("[data-ai-ideas-writing]");
  btn.addEventListener("click", function () {
    btn.disabled = true; btn.textContent = de_en("✨ Sucht …", "✨ Searching …");
    list.innerHTML = "";
    var stop = writing && window.aiWriting ? window.aiWriting(writing, 600) : function () {};
    var body = new FormData(); body.append("csrf_token", csrf);
    window.aiFetch(box.dataset.url, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.cancelled) return;
        if (data.error) {
          li('<span>' + esc(data.error) + '</span>' + (data.settings ? ' <a href="' + data.settings + '">' + de_en("Zu den Einstellungen", "To the settings") + '</a>' : ""))
            .classList.add("ai-idea-note");
          return;
        }
        data.ideas.forEach(function (idea) {
          var e = li('<strong>' + esc(idea.name) + '</strong><span class="small muted">' + esc(idea.why) + '</span>' +
            '<span class="ai-idea-actions"><a class="small" href="' + box.dataset.constructs + '&q=' + encodeURIComponent(idea.name) + '">'
            + de_en("In der Sammlung suchen", "Search the collection") + '</a>' +
            ' · <button type="button" class="link small" data-draft>' + de_en("Skala mit KI entwerfen", "Draft a scale with AI") + '</button></span>');
          e.querySelector("[data-draft]").addEventListener("click", function () {
            if (window.openAiDialog) window.openAiDialog(idea.name + (idea.why ? ": " + idea.why : ""));
          });
        });
      })
      .catch(function () { li('<span>' + de_en("Keine Verbindung. Bitte erneut versuchen.", "No connection. Please try again.") + '</span>').classList.add("ai-idea-note"); })
      .then(function () {
        stop(); btn.disabled = false; btn.textContent = de_en("✨ Neue Vorschläge", "✨ New suggestions");
        list.scrollIntoView({block: "nearest", behavior: (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth")});  // the answer right under the button, in view
      });
  });
})();

// Link label: live availability check; setting one asks for confirmation, then registers after 3 seconds
(function () {
  document.querySelectorAll("[data-label-form]").forEach(function (box) {
    var form = box.tagName === "FORM" ? box : box.closest("form");
    var input = box.querySelector("[data-label-input]");
    var state = box.querySelector("[data-label-state]");
    if (!form || !input) return;
    var timer = null, confirmed = false;
    function show(text, cls) { state.textContent = text; state.className = "small label-state " + (cls || "muted"); }
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var value = input.value.trim().toLowerCase();
      if (!value) { show(""); return; }
      timer = setTimeout(function () {
        fetch(box.dataset.labelCheck + "?label=" + encodeURIComponent(value), { headers: { Accept: "application/json" } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (input.value.trim().toLowerCase() !== d.label) return;
            if (d.error) show(d.error, "bad"); else show(de_en("„" + d.label + "“ ist frei.", "“" + d.label + "” is free."), "ok");
          })
          .catch(function () {});
      }, 300);
    });
    form.addEventListener("submit", function (ev) {
      var value = input.value.trim().toLowerCase();
      if (confirmed || !value) return;
      ev.preventDefault();
      var submitter = ev.submitter;
      askConfirm({ title: de_en("Kurzname „" + value + "“ festlegen?", "Set the short name “" + value + "”?"), ok: de_en("Kurzname festlegen", "Set short name"),
                   note: de_en("Ein einmal genutzter Kurzname bleibt dauerhaft vergeben und lässt sich nicht zurücknehmen " +
                               "oder an eine andere Befragung weitergeben.",
                               "A short name once used stays taken for good and cannot be withdrawn or passed on to another survey.") }).then(function (yes) {
        if (!yes) return;
        var hidden = form.querySelector('input[name="confirm"]');
        if (!hidden) { hidden = document.createElement("input"); hidden.type = "hidden"; hidden.name = "confirm"; form.appendChild(hidden); }
        hidden.value = "1";
        var buttons = form.querySelectorAll("button");
        buttons.forEach(function (b) { b.disabled = true; });
        var left = 3;
        function tick() {
          if (left === 0) {
            confirmed = true;
            buttons.forEach(function (b) { b.disabled = false; });
            if (form.requestSubmit) form.requestSubmit(submitter || undefined); else form.submit();
            return;
          }
          show(de_en("Kurzname „" + value + "“ wird registriert … ", "Short name “" + value + "” is being registered … ") + left, "muted");
          left -= 1;
          setTimeout(tick, 1000);
        }
        tick();
      });
    });
  });
})();

// Landing page carousel: arrows, dots and arrow keys; the track itself scrolls natively (swipe, trackpad)
(function () {
  document.querySelectorAll("[data-carousel]").forEach(function (box) {
    var track = box.querySelector(".shots-track");
    var slides = box.querySelectorAll(".shot");
    var dots = box.querySelectorAll("[data-dot]");
    var prev = box.querySelector("[data-prev]"), next = box.querySelector("[data-next]");
    function current() { return Math.round(track.scrollLeft / Math.max(1, slides[0].offsetWidth + 16)); }
    function go(i) { i = Math.max(0, Math.min(slides.length - 1, i)); track.scrollTo({ left: slides[i].offsetLeft - track.offsetLeft }); }
    function update() {
      var i = current();
      dots.forEach(function (d, k) { d.setAttribute("aria-current", k === i ? "true" : "false"); });
      prev.disabled = i === 0; next.disabled = i === slides.length - 1;
    }
    prev.addEventListener("click", function () { go(current() - 1); });
    next.addEventListener("click", function () { go(current() + 1); });
    dots.forEach(function (d) { d.addEventListener("click", function () { go(+d.dataset.dot); }); });
    track.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") { e.preventDefault(); go(current() + 1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); go(current() - 1); }
    });
    var t = null;
    track.addEventListener("scroll", function () { clearTimeout(t); t = setTimeout(update, 60); });
    window.addEventListener("resize", update);
    update();
    // data-autoplay (landing head): the next screenshot every few seconds, round; it rests while the pointer or the
    // focus is on it and stops for good once someone steers it; never with reduced motion
    if (box.hasAttribute("data-autoplay") && !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches)) {
      var resting = false, timer = setInterval(function () {
        if (!resting && !document.hidden) go(current() + 1 < slides.length ? current() + 1 : 0);
      }, 6000);
      box.addEventListener("mouseenter", function () { resting = true; });
      box.addEventListener("mouseleave", function () { resting = false; });
      box.addEventListener("focusin", function () { resting = true; });
      box.addEventListener("focusout", function () { resting = false; });
      [prev, next].concat(Array.prototype.slice.call(dots)).forEach(function (b) {
        b.addEventListener("click", function () { clearInterval(timer); });
      });
      track.addEventListener("touchstart", function () { clearInterval(timer); }, { passive: true });
    }
    // a click on a screenshot shows it over the page (a dialog); a click anywhere, Esc or × closes it, the arrow
    // keys page through the screenshots. Without the script the link opens the image itself.
    var links = box.querySelectorAll(".shot a");
    if (!links.length || !window.HTMLDialogElement) return;
    var view = document.createElement("dialog");
    view.className = "shot-view";
    view.setAttribute("aria-label", de_en("Screenshot groß", "Large screenshot"));
    view.innerHTML = '<button type="button" class="shot-view-close" aria-label="' + de_en("Schließen", "Close") + '">×</button><img alt=""><p></p>';
    document.body.appendChild(view);
    var big = view.querySelector("img"), caption = view.querySelector("p"), at = 0;
    function show(i) {
      at = (i + links.length) % links.length;
      var small = links[at].querySelector("img"), fig = links[at].closest("figure").querySelector("figcaption");
      big.src = links[at].href;
      big.alt = small ? small.alt : "";
      caption.textContent = fig ? fig.textContent : "";
    }
    links.forEach(function (a, i) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        show(i);
        view.showModal();
        box.dispatchEvent(new Event("mouseenter"));  // the carousel rests meanwhile
      });
    });
    view.addEventListener("click", function () { view.close(); });
    view.addEventListener("close", function () { box.dispatchEvent(new Event("mouseleave")); links[at].focus(); go(at); });
    view.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") { e.preventDefault(); show(at + 1); }
      if (e.key === "ArrowLeft") { e.preventDefault(); show(at - 1); }
    });
  });
})();

// After closing a survey by hand: the recommendation to upload to Zenodo, once (base.html, surveys._suggest_zenodo)
(function () {
  var dialog = document.querySelector("[data-zenodo-hint]");
  if (!dialog || !dialog.showModal) return;
  dialog.querySelector("[data-zenodo-hint-close]").addEventListener("click", function () { dialog.close(); });
  dialog.showModal();
})();

// Confetti across the whole window after a survey was started (once; skipped with reduced motion)
(function () {
  var canvas = document.querySelector("[data-confetti]");
  if (!canvas) return;
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) { canvas.remove(); return; }
  var ctx = canvas.getContext("2d"), dpr = window.devicePixelRatio || 1, w, h;
  function size() { w = window.innerWidth; h = window.innerHeight; canvas.width = w * dpr; canvas.height = h * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); }
  size(); window.addEventListener("resize", size);
  var colors = ["#2957a4", "#7ea6e6", "#e0a526", "#d9534f", "#3aa76d", "#8e6bd1", "#f08bb4"];
  var pieces = [], n = Math.min(260, Math.round(w / 5));
  function spawn(fromLeft) {
    // shot upwards from both lower corners towards the middle
    var x = fromLeft ? -10 : w + 10, angle = -Math.PI / 2 + (fromLeft ? 0.55 : -0.55) + (Math.random() - 0.5) * 0.5;
    var speed = 9 + Math.random() * 9;
    return { x: x, y: h * (0.55 + Math.random() * 0.25), vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
             size: 6 + Math.random() * 7, rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3,
             tilt: Math.random() * Math.PI, color: colors[Math.floor(Math.random() * colors.length)], round: Math.random() < 0.3 };
  }
  for (var i = 0; i < n; i++) pieces.push(spawn(i % 2 === 0));
  var start = performance.now(), duration = 5200;
  function frame(now) {
    var t = now - start;
    ctx.clearRect(0, 0, w, h);
    var fade = Math.max(0, Math.min(1, (duration - t) / 900));
    pieces.forEach(function (p) {
      // rise fast, then flutter down slowly with a little sway
      p.vy = Math.min(p.vy + 0.22, 2.6); p.vx *= 0.975; p.x += p.vx + Math.sin(p.tilt) * 0.6; p.y += p.vy; p.rot += p.vr; p.tilt += 0.08;
      ctx.save(); ctx.globalAlpha = fade; ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillStyle = p.color;
      if (p.round) { ctx.beginPath(); ctx.arc(0, 0, p.size / 2.5, 0, Math.PI * 2); ctx.fill(); }
      else ctx.fillRect(-p.size / 2, -p.size / 4 * Math.abs(Math.cos(p.tilt)) - 1, p.size, p.size / 2 * Math.abs(Math.cos(p.tilt)) + 2);
      ctx.restore();
    });
    if (t < duration) requestAnimationFrame(frame); else canvas.remove();
  }
  requestAnimationFrame(frame);
})();

// Separated section and trash: dragging something over a closed area opens it, so it can be dropped there
document.addEventListener("dragenter", function (e) {
  var area = e.target.closest && e.target.closest("details[data-area]");
  if (area && !area.open) area.open = true;
});

// Variable names: check the scheme while typing (letter first, then letters, digits and _; parts appended to a
// variable name may also start with a digit); the item's variable name must also not be taken yet
(function () {
  var RULES = {
    full: { re: /^[A-Za-z][A-Za-z0-9_]{0,63}$/, msg: de_en("Buchstabe am Anfang, dann nur Buchstaben, Ziffern und _ (keine Umlaute, Leerzeichen oder Bindestriche)",
                                                           "A letter first, then only letters, digits and _ (no umlauts, spaces or hyphens)") },
    column: { re: /^[A-Za-z_][A-Za-z0-9_]{0,40}$/, msg: de_en("Buchstabe oder _ am Anfang, dann nur Buchstaben, Ziffern und _",
                                                              "A letter or _ first, then only letters, digits and _") },
    part: { re: /^[A-Za-z0-9_]{0,40}$/, msg: de_en("nur Buchstaben, Ziffern und _ (wird an den Variablennamen angehängt)",
                                                   "only letters, digits and _ (appended to the variable name)") },
  };
  function check(input) {
    var rule = RULES[input.dataset.varname] || RULES.full, value = input.value.trim(), problem = "";
    if (value && !rule.re.test(value)) problem = rule.msg;
    else if (value && input.dataset.taken) {
      try { if (JSON.parse(input.dataset.taken).indexOf(value) >= 0) problem = de_en("„" + value + "“ ist schon vergeben", "“" + value + "” is already taken"); } catch (e) {}
    }
    var msg = input.parentNode.querySelector(":scope > .varname-msg");
    if (problem && !msg) { msg = document.createElement("span"); msg.className = "varname-msg"; input.insertAdjacentElement("afterend", msg); }
    if (msg) { msg.textContent = problem; msg.hidden = !problem; }
    input.classList.toggle("is-invalid", !!problem);
    input.setCustomValidity(problem);
  }
  document.addEventListener("input", function (e) { if (e.target.matches && e.target.matches("input[data-varname]")) check(e.target); });
  document.querySelectorAll("input[data-varname]").forEach(function (i) { if (i.value) check(i); });
})();

// Element editor: live preview. While typing, the form goes (after a short pause) to the server, which
// renders the element with these values on trial and discards them. The preview itself saves nothing: a draft
// saves by itself (forms that save by themselves, below), a survey in the field with "Speichern".
(function () {
  var form = document.querySelector("[data-live-form]"), frame = document.querySelector("[data-live-frame]");
  if (!form || !frame) return;
  var dirtyNote = document.querySelector("[data-live-dirty]"), timer = null, seq = 0, dirty = false, submitting = false;
  var saves = form.hasAttribute("data-autosave");  // a draft saves by itself (below): nothing stays unsaved
  function fit() {
    try {
      var doc = frame.contentDocument;
      // the body's own height, not the document's: that one never falls below the frame, so a frame that once grew
      // kept its empty space under a shorter question (UI audit 2026-10-04, R93)
      if (!doc || !doc.body) return;
      var style = doc.defaultView.getComputedStyle(doc.body);
      var h = doc.body.getBoundingClientRect().height + parseFloat(style.marginTop) + parseFloat(style.marginBottom);
      frame.style.height = Math.max(120, Math.ceil(h) + 4) + "px";
    } catch (e) { /* not ready */ }
  }
  frame.addEventListener("load", function () {
    fit();
    try { new ResizeObserver(fit).observe(frame.contentDocument.body); } catch (e) { /* older browsers: once */ }
  });
  function refresh() {
    var mine = ++seq;
    fetch(frame.dataset.liveUrl, { method: "POST", body: new FormData(form), headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) { if (html && mine === seq) frame.srcdoc = html; })
      .catch(function () { /* keep the last preview */ });
  }
  function changed(e) {
    if (e.target.type === "file" || e.target.closest("[data-no-live]")) return;
    dirty = !saves;
    if (dirtyNote && !saves) dirtyNote.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(refresh, 400);
  }
  form.addEventListener("input", changed);
  form.addEventListener("change", changed);
  // buttons in the editor that add or remove rows change the form without typing
  form.addEventListener("click", function (e) {
    var b = e.target.closest("button[type=button]");
    // tabs change nothing, the buttons over a formatted text (richtext.js) report their changes themselves
    if (b && !b.closest("[data-cj-tabs], .rt-bar, [data-term], [data-learn-hide]")) setTimeout(function () { changed({ target: form }); }, 50);
  });
  form.addEventListener("submit", function () { submitting = true; });
  window.addEventListener("beforeunload", function (e) { if (dirty && !submitting) { e.preventDefault(); e.returnValue = ""; } });
  // shown again after a conflict (app/edit_guard.py): the inputs are not saved, the preview shows them
  if (form.querySelector("[data-unsaved]")) changed({ target: form });
})();

// A video or audio file of a stimulus (items/stimulus.py, editor.item_media_upload), sent in pieces of data-chunk
// bytes so that no request is larger than a page may be. Before sending, the browser opens the file: its length and
// whether it has a picture (video) go with the last piece; a file this browser cannot open is still sent (the server
// checks its first bytes), with a warning. At the end the page is drawn anew with the file.
document.addEventListener("change", function (e) {
  var pick = e.target, url = pick.dataset && pick.dataset.mediaUpload;
  if (!url || !pick.files || !pick.files[0]) return;
  var file = pick.files[0], chunk = parseInt(pick.dataset.chunk, 10), most = parseFloat(pick.dataset.max) * 1048576;
  var status = pick.closest(".media-file").querySelector("[data-media-status]");
  var upload = Array.prototype.map.call(crypto.getRandomValues(new Uint8Array(16)), function (b) {
    return ("0" + b.toString(16)).slice(-2); }).join("");
  function say(text, bad) { status.textContent = text; status.classList.toggle("finding-warning", !!bad); }
  if (file.size > most) {
    say(de_en("Die Datei ist größer als {mb} MB. Bitte verkleinern (z. B. HandBrake, 720p).",
              "The file is larger than {mb} MB. Please make it smaller (e.g. HandBrake, 720p).").replace("{mb}", pick.dataset.max), true);
    pick.value = "";
    return;
  }
  function probe() {
    return new Promise(function (done) {
      var el = document.createElement(file.type.indexOf("audio") === 0 ? "audio" : "video"), src = URL.createObjectURL(file);
      var finish = function (meta) { URL.revokeObjectURL(src); done(meta); };
      el.preload = "metadata";
      el.onloadedmetadata = function () {
        var kind = el.videoWidth ? "video" : "audio";
        if (isFinite(el.duration)) { finish({ seconds: el.duration, kind: kind }); return; }
        // a WebM straight from a recorder names no length: seeking far past the end makes the browser find it
        el.ondurationchange = function () {
          if (isFinite(el.duration)) { el.ondurationchange = null; finish({ seconds: el.duration, kind: kind }); }
        };
        el.currentTime = 1e101;
      };
      el.onerror = function () { finish(null); };
      el.src = src;
    });
  }
  pick.disabled = true;
  probe().then(function (meta) {
    var n = Math.max(1, Math.ceil(file.size / chunk)), warned = !meta;
    function send(i) {
      var body = new FormData();
      body.append("csrf_token", csrfToken());
      body.append("upload", upload);
      body.append("index", i);
      body.append("key", pick.dataset.key || "");
      body.append("file", file.slice(i * chunk, (i + 1) * chunk), file.name);
      if (i === n - 1) {
        body.append("last", "1");
        if (meta) { body.append("seconds", meta.seconds); body.append("kind", meta.kind); }
      }
      say(de_en("Wird hochgeladen … {p} %", "Uploading … {p}%").replace("{p}", Math.round(100 * i / n)));
      return fetch(url, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; }); })
        .then(function () { return i + 1 < n ? send(i + 1) : null; });
    }
    send(0).then(function () {
      say(warned ? de_en("Hochgeladen. Achtung: Dieser Browser kann die Datei nicht abspielen; bitte in anderen Browsern prüfen.",
                         "Uploaded. Note: this browser cannot play the file; please check in other browsers.")
                 : de_en("Hochgeladen.", "Uploaded."), warned);
      setTimeout(function () { window.location.reload(); }, warned ? 2500 : 300);
    }).catch(function (err) {
      say(err.message || de_en("Hochladen fehlgeschlagen.", "Upload failed."), true);
      pick.disabled = false;
      pick.value = "";
    });
  });
});

// A CSV file read into a text field (data-table-file = id of the field; the table of a drill-down): UTF-8, else
// Windows-1252 as Excel writes it on German systems; the field then reports a change, so the editor saves it.
document.addEventListener("change", function (e) {
  var pick = e.target, id = pick.dataset && pick.dataset.tableFile;
  if (!id || !pick.files || !pick.files[0]) return;
  var field = document.getElementById(id);
  pick.files[0].arrayBuffer().then(function (buf) {
    var text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(buf); }
    catch (err) { text = new TextDecoder("windows-1252").decode(buf); }
    field.value = text.replace(/^﻿/, "");
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
});

// Forms that save by themselves: the element editor (a draft; templates/editor/item.html, form[data-autosave]) and
// the forms with the bar of the macro autosave_bar (form[data-autosave-form]); the routes answer through
// app/autosave.py. Every change goes to the server at once: a click or a choice right away, a text when its field
// is left and, so that nothing is lost, after a pause while typing. The line in the bar says what happened. One
// visit to a text field is one step for "Rückgängig" (the server joins the saves of a visit: `merge`); `what`
// names the field. After a save, everything marked data-live (lines that describe what the form holds, e.g. a
// condition in words) is drawn anew from the page. In the element editor the variable name is only saved when its
// field is left: other texts follow a new name (app/piping.py).
(function () {
  var forms = Array.prototype.slice.call(document.querySelectorAll("form[data-autosave-form], form[data-autosave]"));
  if (!forms.length) return;
  var TEXT = "textarea, input:not([type]), input[type=text], input[type=number], input[type=url], input[type=email], " +
             "input[type=search], input[type=date], input[type=time], input[type=datetime-local]";
  var META = { base: 1, csrf_token: 1, autosave: 1, merge: 1, what: 1, what_done: 1 };
  var page = Date.now().toString(36), field = null, visit = 0, drawing = 0, all = [];
  var stale = false;  // the element editor's panels below the form were drawn from an older state
  var panelsAsked = 0;
  // the panels below the element form (images, design) after a save that changes them: fetched anew in place, so
  // "Anzeige je Option" → "Tabelle mit Bildern je Stufe" shows its image buttons at once (pre-test 2026-10-23)
  function redrawPanels() {
    var holder = document.querySelector("[data-item-panels]");
    if (!holder || !window.fetch || !window.DOMParser) return;
    var asked = ++panelsAsked;
    fetch(location.pathname + location.search, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html || asked !== panelsAsked) return;  // a later save asks again
        var fresh = new DOMParser().parseFromString(html, "text/html").querySelector("[data-item-panels]");
        if (!fresh) return;
        var on = document.querySelector('[data-cj-tabs] [data-tab][aria-selected="true"]');
        fresh.querySelectorAll("[data-cj-tab]").forEach(function (el) {
          if (on) el.hidden = el.dataset.cjTab !== on.dataset.tab;
          el.setAttribute("role", "tabpanel");
        });
        holder.innerHTML = fresh.innerHTML;
      })
      .catch(function () { /* the next tab change loads the page anew */ });
  }
  // The saved state stands in the head of the survey (S2, saveState above): the lines of the forms stay out of
  // sight until one of them has something to say (not saved, a conflict, a warning: "Erneut versuchen", "Änderung
  // der anderen Person laden"). Pages outside a survey (Konto, Designs) keep one line of their own for several forms (R89, W100).
  var bars = document.querySelectorAll("[data-autosave-bar]");
  var inHead = !!document.querySelector("[data-autosave-head]");
  var pageLine = !inHead && bars.length > 1 ? document.querySelector("[data-autosave-page]") : null;
  if (inHead || pageLine) bars.forEach(function (b) { b.classList.add("is-quiet"); });
  if (pageLine) pageLine.hidden = false;

  function ownText(node) {
    var out = "";
    Array.prototype.forEach.call(node.childNodes, function (c) {
      if (c.nodeType === 3) out += c.textContent;
      else if (c.nodeType === 1 && !c.matches("input, select, textarea, button, .hint, .badge, small, svg")) out += " " + ownText(c);
    });
    return out.replace(/\s+/g, " ").trim();
  }
  // the name of a field as the page shows it: its label, else what it is read out as, else its group
  function what(form, el) {
    if (!el || !el.closest || el === form) return "";
    var text = "", group = el.closest(".field"), label = el.closest("label");
    if (group && (el.type === "radio" || !label) && group.querySelector(".label-title")) text = ownText(group.querySelector(".label-title"));
    if (!text && label) text = ownText(label.querySelector(".label-title") || label);
    if (!text) text = el.getAttribute("aria-label") || "";
    if (!text) { var set = el.closest("fieldset"), legend = set && set.querySelector("legend"); if (legend) text = ownText(legend); }
    return text.replace(/[\s*?:]+$/, "").slice(0, 50);
  }
  // what the page says about the saved state outside of the fields: fetched anew, part by part
  function redraw() {
    if (!document.querySelector("[data-live]")) return;
    var mine = ++drawing;
    fetch(location.href, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html || mine !== drawing) return;
        var fresh = new DOMParser().parseFromString(html, "text/html");
        document.querySelectorAll("[data-live]").forEach(function (el) {
          var now = fresh.querySelector('[data-live="' + el.getAttribute("data-live") + '"]');
          if (!now || el.contains(document.activeElement) || el.innerHTML === now.innerHTML) return;
          el.innerHTML = now.innerHTML;
        });
      })
      .catch(function () { /* the old lines stay until the page loads again */ });
  }

  function setup(form) {
    var bar = form.querySelector("[data-autosave-bar]") ||
              (form.id && document.querySelector('[data-autosave-bar="' + form.id + '"]'));  // e.g. Qualitätssicherung
    if (!bar) return null;
    var item = form.hasAttribute("data-autosave");  // the element editor
    function mine_(el) { return el === form || (el && (el.form === form || form.contains(el))); }
    var line = bar.querySelector("[data-autosave-status]"), retry = bar.querySelector("[data-autosave-retry]");
    var theirs = bar.querySelector("[data-autosave-theirs]"), mine = bar.querySelector("[data-autosave-mine]");
    saveState(form, "idle", "", bar);  // the head shows up on a page without ↶ ↷ (Export, interviews)
    var url = form.getAttribute("action") || location.href;
    var timer = null, waiting = null, saving = null, again = false, failed = false, conflict = false;
    // the element editor's variable name: while it is being typed, the saved one goes along
    var code = item && form.elements.code && !form.elements.code.disabled ? form.elements.code : null;
    var savedCode = code ? code.value : null, typingCode = false;
    var reloadSaved = false;  // a field marked data-reload-saved was changed: the page loads anew once it is saved

    function values() {
      var data = new FormData(form);
      if (code && typingCode) data.set("code", savedCode);
      return data;
    }
    function key(data) {
      var out = [];
      data.forEach(function (value, name) { if (!META[name]) out.push(name + "=" + (typeof value === "string" ? value : "")); });
      return out.join("\n");
    }
    var saved = key(values());

    function say(text, kind) {
      // only an error or a conflict is announced here, at once; all else by the head or the page's line (30)
      if (SAVE_TROUBLE[kind]) line.setAttribute("role", "alert"); else line.removeAttribute("role");
      line.textContent = text;
      line.className = "autosave-status" + (kind ? " is-" + kind : "");
      if (!saveState(form, kind || "busy", text, bar) && pageLine && (!kind || kind === "ok")) {
        pageLine.textContent = text;
        pageLine.className = "autosave-status autosave-page" + (kind ? " is-" + kind : "");
      }
      retry.hidden = kind !== "error";
      theirs.hidden = mine.hidden = kind !== "conflict";
    }
    function meta(el) {
      var out = { what: what(form, el), merge: "" };
      if (el && el.matches && el.matches(TEXT) && el.name) {
        var same = form.querySelectorAll('[name="' + el.name + '"]');
        out.merge = el.name + "#" + Array.prototype.indexOf.call(same, el) + "@" + page + visit;
      }
      return out;
    }
    // what is still missing in the element (the line above the editor)
    function problems(list) {
      var box = document.querySelector("[data-item-problems]");
      if (!box || !list) return;
      box.hidden = !list.length;
      box.querySelector("[data-item-problems-text]").textContent = list.join("; ");
    }
    // parts of the page outside the form the save changed, drawn anew by the server (autosave.part): the week
    // preview of a diary's schedule shows the saved times without a reload (pre-test 2026-10-20, 20)
    function parts(list) {
      Object.keys(list || {}).forEach(function (name) {
        var old = document.querySelector('[data-autosave-part="' + name + '"]');
        var fresh = old && new DOMParser().parseFromString(list[name], "text/html").body.firstElementChild;
        if (fresh) old.replaceWith(fresh);
      });
    }
    // the first field that must not be empty and is (save sends nothing then, nor does leaving the page)
    function emptyRequired() {
      return Array.prototype.filter.call(form.querySelectorAll("[required]"), function (el) {
        return el !== code && !el.disabled && !el.value.trim();
      })[0];
    }
    function save(info) {
      clearTimeout(timer); timer = null; waiting = null;
      if (conflict) return Promise.resolve();
      if (saving) { again = info || true; return saving; }
      var data = values(), sent = key(data);
      if (sent === saved && !failed) return Promise.resolve();
      // a field that must not be empty is not sent empty: the server would keep (and a redraw bring back) the old
      // value; the variable name is the server's to refuse (it keeps the old one and saves the rest)
      var empty = emptyRequired();
      var invalidTime = Array.prototype.find.call(form.querySelectorAll('[data-time-editor] input'), function (el) {
        var group = el.closest('[data-trigger-for]');
        return !el.disabled && (!group || !group.hidden) && !el.validity.valid;
      });
      if (invalidTime) {
        say(de_en("Noch nicht gespeichert: Prüfen Sie die Uhrzeiten und Zeitfenster.", "Not saved yet: check the times and time windows."), "invalid");
        return Promise.resolve();
      }
      if (empty) {
        say(de_en("Noch nicht gespeichert: „" + (what(form, empty) || "Pflichtfeld") + "“ ist leer.",
                  "Not saved yet: “" + (what(form, empty) || "required field") + "” is empty."), "invalid");
        return Promise.resolve();
      }
      data.append("autosave", "1");
      if (info && info.merge) data.append("merge", info.merge);
      if (info && info.what) data.append("what", info.what);
      if (info && info.done) data.append("what_done", "1");
      say(de_en("Speichert …", "Saving …"));
      saving = fetch(url, { method: "POST", body: data, credentials: "same-origin", keepalive: sent.length < 40000,
                            headers: { "X-Requested-With": "fetch", Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          if (d.status === "conflict") {
            conflict = d;
            say(de_en("Nicht gespeichert: " + d.who + (item ? " hat dieses Element" : " hat das hier") + (d.when ? " um " + d.when + " Uhr" : "") + " geändert.",
                      "Not saved: " + d.who + (item ? " changed this element" : " changed this") + (d.when ? " at " + d.when : "") + "."), "conflict");
            return;
          }
          if (d.status === "invalid") {  // the form as a whole was refused: it is sent again with the next change
            failed = true;
            say(/^(Nicht gespeichert|Not saved)/.test(d.message) ? d.message : de_en("Nicht gespeichert: ", "Not saved: ") + d.message, "invalid");
            return;
          }
          failed = false; saved = sent;
          if (form.elements.base && d.base) {
            form.elements.base.value = d.base;
            // other forms on the page over the same part (the settings page has three for "settings:meta"):
            // they now start from this save too, else their next save reports a conflict with this one (E7)
            var guard = form.elements.base.getAttribute && form.elements.base.getAttribute("data-guard-key");
            if (guard) document.querySelectorAll('input[name="base"][data-guard-key]').forEach(function (other) {
              if (other !== form.elements.base && other.getAttribute("data-guard-key") === guard) other.value = d.base;
            });
          }
          if (code && !d.code_problem && d.code) savedCode = d.code;
          // numbers of rows this save made go into their (still empty) fields: the next save changes those rows
          var same = key(values()) === sent;
          Object.keys(d.fill || {}).forEach(function (name) {
            var input = form.elements[name];
            if (input && !input.value) input.value = d.fill[name];
          });
          if (same) saved = key(values());
          // a form the server draws (data-autosave-redraw): take it, unless something was changed meanwhile
          // (then the next save brings it)
          if (same && d.form && form.hasAttribute("data-autosave-redraw")) { anew(d.form); saved = key(values()); }
          problems(d.problems);
          (d.notes || []).forEach(function (n) { flash(n, "warning"); });
          undoArrows(d.undo, d.redo);
          if (d.stale) { stale = true; if (same) redrawPanels(); }
          if (d.code_problem) say(de_en("Gespeichert bis auf den Variablennamen. ", "Saved except for the variable name. ") + d.code_problem, "warn");
          else say(de_en("Gespeichert ", "Saved ") + d.at, "ok");
          redraw();
          parts(d.parts);
          form.dispatchEvent(new CustomEvent("autosave:saved", { bubbles: true }));
          // a field that changes rows drawn above it (an option of a construct taken out or put back): the page anew
          if (same && reloadSaved) location.reload();
        })
        .catch(function () {
          failed = true;
          say(de_en("Nicht gespeichert: keine Verbindung zum Server, oder die Anmeldung ist abgelaufen.",
                    "Not saved: no connection to the server, or the sign-in has expired."), "error");
        })
        .then(function () {
          saving = null;
          if (again && !conflict) { var next = again === true ? null : again; again = false; return save(next); }
          again = false;
        });
      return saving;
    }
    function settled() { return key(values()) === saved && !saving && !failed && !conflict; }
    // Put the server's drawing of the form in place of its contents. What was open stays open (details, by the
    // data-key of their row, else by their place), and the cursor stays in its field at its place.
    function anew(html) {
      var doc = new DOMParser().parseFromString(html, "text/html"), fresh = doc.querySelector("form");
      if (!fresh) return;
      function mark(el, list) { var row = el.closest("[data-key]"); return row ? "k:" + row.dataset.key : "n:" + Array.prototype.indexOf.call(list, el); }
      var folds = form.querySelectorAll("details"), open = {};
      Array.prototype.forEach.call(folds, function (el) { if (el.open) open[mark(el, folds)] = true; });
      var at = document.activeElement, focus = null;
      function named(name) { return Array.prototype.filter.call(form.elements, function (el) { return el.name === name; }); }
      if (at && at.name && (at.form === form || form.contains(at))) {
        focus = { name: at.name, n: named(at.name).indexOf(at) };
        try { focus.from = at.selectionStart; focus.to = at.selectionEnd; } catch (e) { /* not a text field */ }
      }
      form.innerHTML = fresh.innerHTML;
      folds = form.querySelectorAll("details");
      Array.prototype.forEach.call(folds, function (el) { if (open[mark(el, folds)]) el.open = true; });
      var back = focus && named(focus.name)[focus.n];
      if (back && back.focus) {
        back.focus();
        try { if (focus.from != null) back.setSelectionRange(focus.from, focus.to); } catch (e) { /* not a text field */ }
      }
    }

    // on the document: a field with form="…" stands outside of its form and its events never pass through it
    document.addEventListener("input", function (e) {
      if (!mine_(e.target)) return;
      // scripts that change the form without typing (rows moved, colours picked) report it on the form itself
      if (e.target === form) { setTimeout(function () { save(null); }, 60); return; }
      if (e.target.type === "file" || !e.target.matches(TEXT)) return;
      if (code && e.target === code) { typingCode = true; return; }  // saved when the field is left (change)
      // S15: a field whose saved state the page draws (a pasted list becomes rows) loads the page anew after the
      // save after a pause in typing too, not only after leaving it (X18)
      if (e.target.hasAttribute("data-reload-saved")) reloadSaved = true;
      waiting = meta(e.target);
      clearTimeout(timer);
      // a survey in the field (data-autosave-on-leave): a text reaches participants as soon as it is saved, so
      // it is saved when the field is left, never half typed in a pause
      if (form.hasAttribute("data-autosave-on-leave")) { timer = -1; return; }
      timer = setTimeout(function () { save(waiting); }, 1500);
    });
    document.addEventListener("change", function (e) {
      if (!mine_(e.target) || e.target === form || e.target.type === "file") return;
      if (code && e.target === code) typingCode = false;
      if (e.target.hasAttribute("data-reload-saved")) reloadSaved = true;
      save(meta(e.target));
    });
    document.addEventListener("focusout", function (e) { if (timer && mine_(e.target)) save(waiting); });
    // buttons that add, remove or move rows change the form without typing; tabs change nothing, the buttons
    // over a formatted text (richtext.js) report their changes themselves
    form.addEventListener("click", function (e) {
      var b = e.target.closest("button[type=button]");
      // the drawing of the model (model.js) saves through its own button (data-model-save), not with each of its buttons
      if (!b || b.closest("[data-cj-tabs], .rt-bar, [data-autosave-bar], [data-term], [data-learn-hide], .plan-model")) return;
      // a button may name its step in full ("Spalte 2 entfernt", data-undo-what) instead of "… geändert"
      var done = b.getAttribute("data-undo-what");
      setTimeout(function () { save({ what: done || what(form, b), merge: "", done: !!done }); }, 60);
    });
    // Enter in a field: save, do not load the page anew; buttons with a name are actions of their own (e.g. a draft text)
    form.addEventListener("submit", function (e) {
      if (e.submitter && e.submitter.name) return;
      e.preventDefault();
      typingCode = false;
      save(null);
    });
    retry.addEventListener("click", function () { save(null); });
    theirs.addEventListener("click", function () { saved = key(values()); location.reload(); });
    mine.addEventListener("click", function () {
      if (form.elements.base) form.elements.base.value = conflict.base;
      conflict = false; failed = true;  // send even if nothing was typed since
      save(null);
    });
    return {
      settled: settled,
      // an empty required field counts like a failed save: "Rückgängig" asks first, leaving sends nothing (the
      // beacon would otherwise write the state from before the undo over its result)
      stuck: function () { return (failed || conflict || !!emptyRequired()) && key(values()) !== saved; },
      flush: function () { return Promise.resolve(save(waiting)); },
      last: function () {  // leaving the page: what is not sent yet goes along
        if (conflict || failed || emptyRequired()) return;
        var data = values(), sent = key(data);
        if (sent === saved) return;
        data.append("autosave", "1");
        // a save still under way carries the base this beacon has too: the server lets the beacon build on that
        // own save instead of counting it as someone else's change (code review 2026-10-15, I1)
        if (saving) data.append("after_own", "1");
        if (waiting && waiting.merge) data.append("merge", waiting.merge);
        if (waiting && waiting.what) data.append("what", waiting.what);
        if (navigator.sendBeacon && navigator.sendBeacon(url, data)) saved = sent;
      },
    };
  }

  document.addEventListener("focusin", function (e) { if (e.target !== field) { field = e.target; visit += 1; } });
  forms.forEach(function (form) { var one = setup(form); if (one) all.push(one); });
  // leaving the page: what is not sent yet goes along; only what could not be saved asks
  window.addEventListener("pagehide", function () { all.forEach(function (f) { f.last(); }); });
  window.addEventListener("beforeunload", function (e) {
    if (all.some(function (f) { return f.stuck(); })) { e.preventDefault(); e.returnValue = ""; }
  });
  // "Rückgängig", "Wiederholen", "Fertig" and the tabs of a stale element editor wait for the save that is under way
  document.addEventListener("click", function (e) {
    var go = e.target.closest("[data-undo], [data-redo], [data-autosave-done], .undo-list button");
    var tab = stale && e.target.closest("[data-cj-tabs] [data-tab]");
    if (!go && !tab) return;
    if (!tab && all.every(function (f) { return f.settled(); })) return;
    if (all.some(function (f) { return f.stuck(); })) return;
    e.preventDefault();
    Promise.all(all.map(function (f) { return f.flush(); })).then(function () {
      if (all.some(function (f) { return f.stuck(); })) return;
      if (tab) { setTimeout(function () { location.reload(); }, 30); return; }  // the panels were drawn from the old state
      if (go.matches("a")) location.href = go.href;
      else if (go.form.requestSubmit) go.form.requestSubmit(go); else go.form.submit();
    });
  }, true);
  // the list of the last changes was drawn when the page loaded: fetch it anew when it is opened after a save
  document.addEventListener("toggle", function (e) {
    var bar = e.target.closest && e.target.closest(".undo-bar");
    if (!bar || !e.target.open || !bar.dataset.stale) return;
    fetch(location.href, { credentials: "same-origin" }).then(function (r) { return r.text(); }).then(function (html) {
      var fresh = new DOMParser().parseFromString(html, "text/html").querySelector(".undo-bar");
      if (!fresh) return;
      bar.innerHTML = fresh.innerHTML;
      delete bar.dataset.stale;
      var menu = bar.querySelector(".undo-menu");
      if (menu) menu.open = true;
    }).catch(function () { /* the old list stays */ });
  }, true);
})();

// Editors with tabs (ItemType.editor_tabs, e.g. conjoint: content, design, display & images) over one form
// and the panels below it
(function () {
  var bar = document.querySelector("[data-cj-tabs]");
  if (!bar) return;
  var key = "cj-tab:" + bar.dataset.item;
  var tabs = Array.prototype.map.call(bar.querySelectorAll("[data-tab]"), function (b) { return b.dataset.tab; });
  var anchors = { design: "design", images: "display", bilder: "display" };  // e.g. after "Versuchsplan erzeugen"
  function show(tab) {
    document.querySelectorAll("[data-cj-tab]").forEach(function (el) { el.hidden = el.dataset.cjTab !== tab; });
    bar.querySelectorAll("[data-tab]").forEach(function (b) {
      b.setAttribute("aria-selected", b.dataset.tab === tab ? "true" : "false");
      b.tabIndex = b.dataset.tab === tab ? 0 : -1;  // one tab stop for the row, arrows inside (WCAG 2.1.1, UI audit R76)
    });
    document.querySelectorAll("[data-cj-tab]").forEach(function (el) { el.setAttribute("role", "tabpanel"); });
    try { sessionStorage.setItem(key, tab); } catch (e) { /* ignore */ }
  }
  bar.addEventListener("keydown", function (e) {
    var d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    var buttons = Array.prototype.slice.call(bar.querySelectorAll("[data-tab]"));
    var i = buttons.indexOf(document.activeElement);
    if (i < 0) return;
    var next = buttons[(i + d + buttons.length) % buttons.length];
    show(next.dataset.tab); next.focus(); e.preventDefault();
  });
  bar.addEventListener("click", function (e) {
    var b = e.target.closest("[data-tab]");
    if (b) { show(b.dataset.tab); history.replaceState(null, "", "#tab-" + b.dataset.tab); }
  });
  // a field with an error in a hidden tab: show that tab, so the browser can point at it
  document.addEventListener("invalid", function (e) {
    var holder = e.target.closest && e.target.closest("[data-cj-tab]");
    if (holder && holder.hidden) show(holder.dataset.cjTab);
  }, true);
  var hash = location.hash.replace("#", ""), start = null;
  if (hash.indexOf("tab-") === 0 && tabs.indexOf(hash.slice(4)) >= 0) start = hash.slice(4);
  else if (anchors[hash]) start = anchors[hash];
  if (!start) { try { start = sessionStorage.getItem(key); } catch (e) { /* ignore */ } }
  show(tabs.indexOf(start) >= 0 ? start : "content");
  // a link to "#tab-…" on the same page (the empty preview, W39): that tab, without loading the page anew
  window.addEventListener("hashchange", function () {
    var h = location.hash.replace("#", "");
    if (h.indexOf("tab-") === 0 && tabs.indexOf(h.slice(4)) >= 0) { show(h.slice(4)); bar.scrollIntoView({ block: "nearest" }); }
  });
})();

// Feld page: who is in the survey right now. One dot per participant on a horizontal line (position in the questionnaire,
// 0 % to 100 %), jittered vertically; deep blue right after a page, fading out after 10 minutes. Drawn at the real
// width (no stretching), again when the window is resized.
(function () {
  var box = document.querySelector("[data-live-strip]");
  if (!box) return;
  var svg = box.querySelector("svg"), NS = "http://www.w3.org/2000/svg", last = null;
  function el(name, attrs) { var e = document.createElementNS(NS, name); for (var k in attrs) e.setAttribute(k, attrs[k]); svg.appendChild(e); return e; }
  function draw(data) {
    last = data;
    if (data.outside) {  // who is in an external section right now (app/external.py)
      var now = document.querySelector("[data-outside-now]"), gone = document.querySelector("[data-outside-gone]");
      if (now) now.textContent = data.outside.now;
      if (gone) gone.textContent = data.outside.gone;
    }
    var W = Math.max(280, svg.clientWidth || box.clientWidth), Hh = 124, L = 12, R = W - 14, TOP = 12, BOT = 76;
    svg.setAttribute("viewBox", "0 0 " + W + " " + Hh);
    svg.innerHTML = "";
    el("line", { x1: L, x2: R, y1: BOT + 6, y2: BOT + 6, class: "live-axis" });
    var step = (R - L) >= 420 ? 20 : 25;  // 0 % to 100 % in steps of 20 (or 25 on narrow screens)
    for (var pct = 0; pct <= 100; pct += step) {
      var x = L + (R - L) * pct / 100;
      el("line", { x1: x, x2: x, y1: BOT + 3, y2: BOT + 10, class: "live-tick" });
      el("text", { x: x, y: BOT + 26, class: "live-label" + (pct === 0 ? " live-label-start" : pct === 100 ? " live-label-end" : "") }).textContent = pct + de_en(" %", "%");
    }
    el("text", { x: (L + R) / 2, y: BOT + 44, class: "live-label live-axis-title" }).textContent = de_en("Position im Fragebogen", "Position in the questionnaire");
    data.people.forEach(function (p) {
      var fresh = Math.max(0, 1 - p.a / data.window);  // 1 = just now, 0 = ten minutes without a new page
      if (fresh <= 0) return;
      el("circle", { cx: L + (R - L) * p.p, cy: TOP + (BOT - TOP) * p.j, r: 5, class: "live-person",
                     style: "fill-opacity:" + (0.08 + 0.92 * fresh).toFixed(2) });
    });
  }
  function load() {
    fetch(box.dataset.url, { headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(draw).catch(function () {});
  }
  load();
  setInterval(function () { if (!document.hidden) load(); }, 30000);
  var t = null;
  window.addEventListener("resize", function () { clearTimeout(t); t = setTimeout(function () { if (last) draw(last); }, 150); });
})();

// Field end: length of the field time in days, today and the end day included, shown next to the date ("(30 Tage)")
document.querySelectorAll("[data-days-from]").forEach(function (input) {
  var out = input.parentNode.querySelector("[data-days-out]");
  function update() {
    var from = new Date(input.dataset.daysFrom + "T00:00:00"), to = new Date(input.value + "T00:00:00");
    if (!out) return;
    if (!input.value || isNaN(to)) { out.textContent = ""; return; }
    var days = Math.round((to - from) / 86400000) + 1;  // today and the end day both count: today to today is 1 day
    out.textContent = "(" + (days === 1 ? de_en("1 Tag", "1 day") : de_en(days + " Tage", days + " days")) + ")";
  }
  input.addEventListener("input", update);
  input.addEventListener("change", update);
  update();
});

// Van Westendorp charts (results page): crosshair and tooltip with every curve at the nearest price
(function () {
  document.querySelectorAll("[data-psm-chart]").forEach(function (fig) {
    var d = JSON.parse(fig.querySelector("[data-psm-data]").textContent);
    var svg = fig.querySelector("svg"), hit = fig.querySelector("[data-psm-hit]");
    var cross = fig.querySelector("[data-psm-cross]"), tip = fig.querySelector("[data-psm-tip]");
    if (!d.rows.length) return;
    // half up (kaufmännisch) from the decimal text, as the server rounds (1.005 -> 1,01); always `places` places, so
    // prices of the same chart read alike (2,50 next to 2,75, not 2,5); 2.50 on an English page (Z-2)
    function num(v, places) { return uiNum(v, places); }
    // unit and curve names are researcher text: escaped, never markup
    function show(evt) {
      var pt = svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
      var loc = pt.matrixTransform(svg.getScreenCTM().inverse());
      var price = d.lo + (loc.x - d.x0) / (d.x1 - d.x0) * (d.hi - d.lo);
      var row = d.rows.reduce(function (best, r) { return Math.abs(r[0] - price) < Math.abs(best[0] - price) ? r : best; }, d.rows[0]);
      var x = d.x0 + (row[0] - d.lo) / ((d.hi - d.lo) || 1) * (d.x1 - d.x0);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.removeAttribute("hidden");
      tip.innerHTML = "<b>" + num(row[0], 2) + (d.unit ? " " + esc(d.unit) : "") + "</b>" + d.keys.map(function (k, i) {
        var v = row[i + 1];
        return "<br>" + esc(d.names[k]) + ": <b>" + (d.fmt === "pct" ? uiPercent(v) : num(v, 2)) + "</b>";
      }).join("");
      tip.hidden = false;
      var box = fig.getBoundingClientRect(), px = x / svg.viewBox.baseVal.width * box.width;
      tip.style.left = Math.min(Math.max(px + 12, 0), box.width - tip.offsetWidth) + "px";
    }
    hit.addEventListener("mousemove", show);
    hit.addEventListener("mouseleave", function () { tip.hidden = true; cross.setAttribute("hidden", ""); });
  });
})();

// Open editors report themselves (app/edit_guard.py), so others see who else has the same part open
(function () {
  var conflict = document.querySelector(".edit-conflict");
  if (conflict) {  // after the layout settled, so no restored scroll position hides the note
    if ("scrollRestoration" in history) history.scrollRestoration = "manual";
    window.addEventListener("load", function () { conflict.scrollIntoView({ block: "center" }); });
  }
  document.querySelectorAll("[data-presence]").forEach(function (box) {
    var csrf = csrfToken();
    var names = box.querySelector("[data-presence-names]");
    function body(leave) {
      var b = new FormData();
      b.append("csrf_token", csrf); b.append("key", box.dataset.key); if (leave) b.append("leave", "1");
      return b;
    }
    function ping() {
      if (document.hidden) return;
      fetch(box.dataset.url, { method: "POST", body: body(false), credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (!d) return; names.textContent = d.others.join(", "); box.hidden = !d.others.length; })
        .catch(function () { /* try again with the next beat */ });
    }
    ping();
    setInterval(ping, (Number(box.dataset.every) || 15) * 1000);
    document.addEventListener("visibilitychange", ping);
    window.addEventListener("pagehide", function () { if (navigator.sendBeacon) navigator.sendBeacon(box.dataset.url, body(true)); });
  });
})();

// Messages after an action (base.html): each can be closed, information and confirmations leave by themselves
// (flashControls).
document.querySelectorAll("[data-flashes] .flash").forEach(flashControls);

// Forms with their own "Speichern" (data-dirty-guard: where nothing saves by itself, e.g. the loop table, flow
// elements, quotas and the look of a survey in the field): a changed form says "ungespeichert" next to its
// button, and leaving the page asks first.
// Saving one form while another one is changed asks too, since the page reloads.
(function () {
  var forms = Array.prototype.slice.call(document.querySelectorAll("form[data-dirty-guard]"));
  if (!forms.length) return;
  var leaving = null;  // the form being sent
  function dirtyOthers() { return forms.filter(function (f) { return f !== leaving && f.classList.contains("is-dirty"); }); }
  forms.forEach(function (form) {
    function mark(e) {
      if (e && e.target && (e.target.type === "file" || e.target.type === "hidden")) return;
      if (form.classList.contains("is-dirty")) return;
      form.classList.add("is-dirty");
      var button = form.querySelector("button:not([type=button])");
      if (!button) return;
      var note = document.createElement("span");
      note.className = "badge badge-dirty";
      note.setAttribute("role", "status");
      note.textContent = de_en("ungespeichert", "unsaved");
      button.insertAdjacentElement("afterend", note);
    }
    form.addEventListener("input", mark);
    form.addEventListener("change", mark);
    // buttons that add or remove rows change the form without typing
    form.addEventListener("click", function (e) { if (e.target.closest("button[type=button]:not(.rt-btn):not([data-term]):not([data-learn-hide])")) setTimeout(mark, 0); });
    form.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      leaving = form;
      if (form.loseOthers || !dirtyOthers().length) { form.loseOthers = false; return; }
      e.preventDefault();
      leaving = null;
      var by = e.submitter;
      askConfirm({ title: de_en("Trotzdem speichern?", "Save anyway?"), ok: de_en("Diesen Abschnitt speichern", "Save this section"), danger: true,
                   note: de_en("Auf dieser Seite gibt es weitere ungespeicherte Änderungen in einem anderen Abschnitt. " +
                               "Sie gehen verloren, wenn Sie jetzt diesen Abschnitt speichern.",
                               "This page has more unsaved changes in another section. They are lost if you save this section now.") }).then(function (yes) {
        if (!yes) return;
        form.loseOthers = form.confirmedOnce = true;  // asked: neither this question nor the form's own comes again
        if (form.requestSubmit) form.requestSubmit(by || undefined); else form.submit();
      });
    });
    if (form.querySelector("[data-unsaved]")) mark();  // shown again after a conflict or an error: not saved yet
  });
  window.addEventListener("beforeunload", function (e) {
    if (!leaving && dirtyOthers().length) { e.preventDefault(); e.returnValue = ""; }
  });
})();

// A form that came back with a problem (conflict or error): bring the note into view
(function () {
  var note = document.querySelector("[data-scroll-here]");
  if (note) note.scrollIntoView({ block: "center" });
})();

// Results page: results that have to be computed first (what they rest on has changed) take a while with many
// participations. The server then sends a small page with this form, which asks for the results at once. Until
// they arrive a bar shows that the server is working: first a moving piece, then the share the server reports
// (app/progress.py; the cookie tells it which note is ours).
(function () {
  var form = document.getElementById("results-form");
  var box = document.querySelector("[data-results-progress]");
  if (!form || !box) return;
  var bar = box.querySelector("[data-bar]"), meter = box.querySelector("[role=progressbar]");
  var status = box.querySelector("[data-status]");
  var path = box.dataset.url.replace(/\/progress$/, ""), timer = null;
  function cookie(value, age) {
    document.cookie = "results_progress=" + value + "; path=" + path + "; max-age=" + age + "; SameSite=Lax";
  }
  function buttons(off) {
    document.querySelectorAll('button[form="results-form"]').forEach(function (b) { b.disabled = off; });
  }
  function poll() {
    fetch(box.dataset.url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (d) {
        if (typeof d.share !== "number") return;  // not started yet, or the page is on its way
        var pct = Math.round(d.share * 100);
        box.classList.remove("is-waiting");
        bar.style.width = pct + "%"; meter.setAttribute("aria-valuenow", pct); status.textContent = d.text + " …";
      })
      .catch(function () { /* the moving bar stays; the next beat tries again */ })
      .then(function () { if (timer !== null) timer = setTimeout(poll, 400); });
  }
  function stop() {
    if (timer !== null) clearTimeout(timer);
    timer = null; box.hidden = true; buttons(false); cookie("", 0);
  }
  cookie("", 0);  // the page is there: the note of the request that brought it is gone
  form.addEventListener("submit", function () {
    if (timer !== null) return;
    var token = "";
    while (token.length < 16) token += Math.random().toString(36).slice(2);
    cookie(token.slice(0, 16), 300);
    bar.style.width = "0"; meter.removeAttribute("aria-valuenow"); status.textContent = de_en("Die Ergebnisse werden berechnet …", "The results are being computed …");
    box.classList.add("is-waiting"); box.hidden = false;
    setTimeout(function () { buttons(true); }, 0);  // after the form has been sent
    timer = setTimeout(poll, 300);
  });
  // back from another page (kept in the browser's memory): no computation is running any more
  window.addEventListener("pageshow", function (e) { if (e.persisted) stop(); });
  if (form.hasAttribute("data-auto")) {
    // the place the address pointed at (#hb-12 after starting an estimation) goes along with the form
    if (window.location.hash) form.action = window.location.pathname + window.location.hash;
    if (form.requestSubmit) form.requestSubmit(); else { form.dispatchEvent(new Event("submit")); form.submit(); }
  }
})();


// A checkbox with data-reveal="id" shows that block only while ticked; unticking empties its text fields,
// so nothing of it ends up anywhere (the server ignores them as well)
document.querySelectorAll("input[type=checkbox][data-reveal]").forEach(function (box) {
  var target = document.getElementById(box.dataset.reveal);
  if (!target) return;
  box.addEventListener("change", function () {
    target.hidden = !box.checked;
    if (!box.checked) target.querySelectorAll("input[type=text], input:not([type]), textarea").forEach(function (f) { f.value = ""; });
    else { var first = target.querySelector("input, textarea"); if (first) first.focus(); }
  });
});

// A checkbox with data-toggle-cols="id" and data-col="name" shows the cells ".col-<name>" of that table only while
// ticked (the table's class "off-<name>" hides them, so rows added later follow); unticking empties them, e.g. the
// subscales or the attention checks of a matrix
document.querySelectorAll("input[type=checkbox][data-toggle-cols]").forEach(function (box) {
  var table = document.getElementById(box.dataset.toggleCols), col = box.dataset.col;
  if (!table || !col) return;
  box.addEventListener("change", function () {
    table.classList.toggle("off-" + col, !box.checked);
    if (!box.checked) table.querySelectorAll(".col-" + col + " input, .col-" + col + " select").forEach(function (f) {
      f.value = f.tagName === "SELECT" ? (f.options[0] || {}).value || "" : "";
    });
    else { var first = table.querySelector("tbody .col-" + col + " input, tbody .col-" + col + " select"); if (first) first.focus(); }
  });
});

// Buttons and links that show only an icon carry their meaning in "title": give screen readers the same
// words as name (aria-label), unless the element already has one or has text of its own.
document.querySelectorAll("button[title], a[title]").forEach(function (el) {
  if (el.hasAttribute("aria-label") || el.hasAttribute("aria-labelledby")) return;
  if (el.textContent.trim().replace(/[↑↓×✕⋮]/g, "") !== "") return;
  el.setAttribute("aria-label", el.title);
});


// AI at work: a violet "being written" box with blurred text that grows to `chars` within 30 s.
// window.aiWriting(box, chars) starts it and returns a function that stops and hides it again.
// A submit button with data-ai uses it (data-ai-writing="#id", else the first [data-ai-writing-box];
// data-ai-hide="#id" hides e.g. the old text meanwhile); the page then comes back with the real text.
(function () {
  var words = de_en("Die Teilnahme die Befragung Stichprobe erfolgte im Zeitraum Daten wurden erhoben Personen Skala Items " +
    "Reliabilität Median Minuten ausgeschlossen bereinigte vollständig Angaben anonym freiwillig Einwilligung " +
    "Ergebnisse Methode Instrument Studie Fragebogen online insgesamt jeweils sowie zudem",
    "The participation the survey sample took place in the period data were collected people scale items " +
    "reliability median minutes excluded cleaned complete answers anonymous voluntary consent " +
    "results method instrument study questionnaire online overall each as well as also").split(" ");
  window.aiWriting = function (box, chars) {
    var out = box.querySelector(".ai-writing-text");
    box.hidden = false;
    out.textContent = "";
    box.scrollIntoView({block: "nearest", behavior: (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth")});
    var started = Date.now(), n = 0;
    var timer = setInterval(function () {
      var share = Math.min((Date.now() - started) / 30000, 1);
      while (out.textContent.length < share * chars) {
        n += 1;
        out.textContent += words[(n * 7 + n % 5) % words.length] + (n % 13 === 0 ? ".\n\n" : (n % 9 === 0 ? ". " : " "));
      }
      if (share >= 1) clearInterval(timer);
    }, 150);
    return function () { clearInterval(timer); box.hidden = true; out.textContent = ""; };
  };
  document.addEventListener("submit", function (e) {
    var by = e.submitter;
    if (!by || !by.hasAttribute("data-ai") || e.defaultPrevented) return;
    var box = by.dataset.aiWriting ? document.querySelector(by.dataset.aiWriting) : document.querySelector("[data-ai-writing-box]");
    if (!box) return;
    if (by.dataset.aiHide) { var old = document.querySelector(by.dataset.aiHide); if (old) old.hidden = true; }
    var target = parseInt(by.dataset.aiChars || box.dataset.aiChars || "1400", 10);
    var lenSel = by.form && by.form.querySelector("input[name=length]:checked");
    if (lenSel) target = {short: 650, medium: 1400, long: 2600}[lenSel.value] || target;
    window.aiWriting(box, target);
    // the buttons are disabled after the submit has taken their value
    setTimeout(function () { (by.form ? by.form.querySelectorAll("button") : []).forEach(function (b) { b.disabled = true; }); }, 0);
  });
})();

// Page "Erscheinungsbild": the sample page in the frame and the contrast list follow the form while it is
// changed (the server derives the tones and checks the contrast); the form saves by itself in a draft, in the
// field with "Speichern".
(function () {
  var page = document.querySelector("[data-design]");
  if (!page) return;
  var form = page.querySelector("[data-design-form]"), frame = page.querySelector("[data-design-frame]");
  var list = page.querySelector("[data-design-checks]"), nameEl = page.querySelector("[data-design-name]");
  var LIGHT = ["accent", "background", "text", "surface"];
  var DARK = LIGHT.map(function (key) { return "dark_" + key; }), KEYS = LIGHT.concat(DARK);
  var HEX = /^#[0-9a-f]{6}$/i, timer = null, seq = 0, show = "";
  var presetField = form.querySelector("[data-preset-field]"), fixedNote = form.querySelector("[data-fixed-note]");
  var darkBox = form.querySelector("[data-design-dark]"), showBtn = page.querySelector("[data-design-show]");
  function field(key) { return form.querySelector('[data-colour="' + key + '"]'); }
  function pick(key) { return form.querySelector('[data-colour-pick="' + key + '"]'); }
  function value(key) { return field(key).value.trim().toLowerCase(); }
  function auto() { return form.elements.scheme && form.elements.scheme.value === "auto"; }
  function query() {
    var q = new URLSearchParams();
    LIGHT.forEach(function (key) { q.set(key, value(key)); });
    if (auto()) { q.set("scheme", "auto"); DARK.forEach(function (key) { q.set(key, value(key)); }); }
    ["preset", "font", "font_scale", "radius", "maps", "logo_position", "logo_size"].forEach(function (name) {
      if (form.elements[name]) q.set(name, form.elements[name].value);  // the logo fields exist only with a logo
    });
    return q;
  }
  function valid() { return (auto() ? KEYS : LIGHT).every(function (key) { return HEX.test(value(key)); }); }
  // A template of an institution is chosen by its button (its colours typed in are an own design); the open
  // templates are recognised by their colours.
  function markPreset() {
    var chosen = null;
    page.querySelectorAll("[data-preset]").forEach(function (btn) {
      var same = LIGHT.every(function (key) { return btn.dataset[key] === value(key); });
      var on = same && (!btn.dataset.font || presetField.value === btn.dataset.preset);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      if (on) chosen = btn;
    });
    presetField.value = chosen ? chosen.dataset.preset : "custom";
    var fixed = !!(chosen && chosen.dataset.font);
    if (fixedNote) fixedNote.hidden = !fixed;
    form.querySelectorAll("[data-fixable]").forEach(function (el) {
      // really disabled, so nobody chooses a font that is not taken; the server sets the fixed parts anyway
      el.disabled = fixed;
      if (fixed && el.name === "font") el.value = chosen.dataset.font;
      if (fixed && el.name === "radius") el.value = chosen.dataset.radius;
      if (fixed && el.name === "logo_position") el.value = chosen.dataset.logoPosition || "right";
    });
  }
  function setColour(key, colour) {
    field(key).value = colour;
    if (HEX.test(colour)) pick(key).value = colour.toLowerCase();
  }
  function changed() { form.dispatchEvent(new Event("input", { bubbles: true })); }  // "ungespeichert"
  function showChecks(data) {
    if (nameEl) nameEl.textContent = data.name;
    list.textContent = "";
    data.checks.forEach(function (c) {
      var li = document.createElement("li");
      li.className = c.ok ? "is-ok" : "is-" + c.severity;
      var sample = document.createElement("span");
      sample.className = "design-check-sample"; sample.setAttribute("aria-hidden", "true"); sample.textContent = "Aa";
      sample.style.color = c.fg; sample.style.background = c.bg;
      var label = document.createElement("span"); label.className = "design-check-label"; label.textContent = c.label;
      var ratio = document.createElement("span"); ratio.className = "design-check-ratio"; ratio.textContent = c.ratio + " : 1";
      var state = document.createElement("span"); state.className = "design-check-state";
      state.textContent = c.ok ? de_en("in Ordnung", "fine") : (c.severity === "error" ? de_en("zu schwach", "too weak") : de_en("schwach", "weak"));
      li.append(sample, label, ratio, state);
      var keys = Object.keys(c.fix || {});
      if (keys.length && !form.querySelector("fieldset[disabled]")) {
        var fix = document.createElement("button");
        fix.type = "button"; fix.className = "chip design-check-fix";
        var fixes = keys.map(function (k) { return c.fix[k]; }).join(", ");
        fix.textContent = de_en("Nächste lesbare Farbe übernehmen (" + fixes + ")", "Use the nearest readable colour (" + fixes + ")");
        fix.addEventListener("click", function () { keys.forEach(function (k) { setColour(k, c.fix[k]); }); changed(); refresh(); });
        li.append(fix);
      }
      list.append(li);
    });
  }
  function refresh() {
    markPreset();
    // the dark colours and the button that shows the dark side exist only for a light look that follows the device
    var light = HEX.test(value("background")) && parseInt(value("background").slice(1, 3), 16) * 0.2126
      + parseInt(value("background").slice(3, 5), 16) * 0.7152 + parseInt(value("background").slice(5, 7), 16) * 0.0722 > 110;
    if (darkBox) darkBox.hidden = !auto();
    if (showBtn) { showBtn.hidden = !(auto() && light); if (showBtn.hidden) { show = ""; showBtn.setAttribute("aria-pressed", "false"); } }
    if (!valid()) return;
    var q = query(), mine = ++seq, top = 0;
    try { top = frame.contentWindow.scrollY; } catch (e) { /* not loaded yet */ }
    frame.addEventListener("load", function () { try { frame.contentWindow.scrollTo(0, top); } catch (e) { /* gone */ } }, { once: true });
    var sample = new URLSearchParams(q);
    if (auto()) sample.set("show", show || "light");
    frame.src = page.dataset.sample + "?" + sample.toString();
    fetch(page.dataset.check + "?" + q.toString(), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data && mine === seq) showChecks(data); })
      .catch(function () { /* the list keeps its last state; the pre-launch check looks again on the server */ });
  }
  function later() { clearTimeout(timer); timer = setTimeout(refresh, 250); }
  KEYS.forEach(function (key) {
    if (!field(key)) return;
    pick(key).addEventListener("input", function () { field(key).value = pick(key).value; later(); });
    field(key).addEventListener("input", function () { if (HEX.test(value(key))) pick(key).value = value(key); later(); });
  });
  form.querySelectorAll("[data-design-field]").forEach(function (el) { el.addEventListener("change", later); });
  page.querySelectorAll("[data-preset]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      LIGHT.forEach(function (key) { setColour(key, btn.dataset[key]); });
      // a template with a dark partner brings its dark colours along
      DARK.forEach(function (key) { if (btn.dataset[key] && field(key)) setColour(key, btn.dataset[key]); });
      presetField.value = btn.dataset.preset;
      // a template with a look of its own sets font, corners and text size too (still changeable)
      [["font", "lookFont"], ["radius", "lookRadius"], ["font_scale", "lookFontScale"]].forEach(function (pair) {
        var el = form.querySelector("[name=" + pair[0] + "]");
        if (el && !el.disabled && btn.dataset[pair[1]]) el.value = btn.dataset[pair[1]];
      });
      changed();
      refresh();
    });
  });
  page.querySelectorAll("[data-design-width]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      page.querySelectorAll("[data-design-width]").forEach(function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
      frame.classList.toggle("is-phone", btn.dataset.designWidth !== "");
    });
  });
  // "Farben aus dem Logo": the main colours of the logo as buttons; one click takes a colour as the accent
  var logoBox = page.querySelector("[data-logo-colours]");
  if (logoBox) logoBox.querySelector("[data-logo-colours-load]").addEventListener("click", function () {
    var out = logoBox.querySelector("[data-logo-colours-list]");
    out.textContent = "…";
    fetch(logoBox.dataset.logoColours, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : { colours: [] }; })
      .then(function (data) {
        out.textContent = "";
        if (!data.colours.length) { out.textContent = de_en("Im Logo wurden keine Farben außer Weiß, Schwarz und Grau gefunden.", "No colours other than white, black and grey were found in the logo."); return; }
        data.colours.forEach(function (c) {
          var btn = document.createElement("button");
          btn.type = "button"; btn.className = "design-logo-swatch"; btn.style.background = c.colour;
          btn.title = de_en(c.colour + " als Akzent übernehmen (Kontrast auf Weiß " + c.on_white + " : 1)", "Use " + c.colour + " as the accent (contrast on white " + c.on_white + " : 1)");
          btn.setAttribute("aria-label", btn.title);
          btn.addEventListener("click", function () { setColour("accent", c.colour); changed(); refresh(); field("accent").focus(); });
          out.append(btn);
        });
        out.append(document.createTextNode(de_en("anklicken: als Akzent übernehmen", "click: use as the accent")));
      })
      .catch(function () { out.textContent = de_en("Die Farben konnten nicht gelesen werden.", "The colours could not be read."); });
  });
  if (showBtn) showBtn.addEventListener("click", function () {
    show = show === "dark" ? "" : "dark";
    showBtn.setAttribute("aria-pressed", show === "dark" ? "true" : "false");
    refresh();
  });
})();

// data-submit-on-change, data-select-all and data-print: participant.js (also loaded on these pages and on the
// participant pages, which have no app.js)

// After a save in an embedded editor (base.html, data-live-item-saved) the embedding page refreshes its list
// (live/manage.html)
if (document.querySelector("[data-live-item-saved]")) {
  try { parent.postMessage("live-item-saved", location.origin); } catch (e) { /* not embedded */ }
}

// Buttons that tick or untick every box of a name in their form (data-check-all="survey", data-check-none="survey")
document.addEventListener("click", function (e) {
  var el = e.target.closest && e.target.closest("[data-check-all], [data-check-none]");
  if (!el || !el.form) return;
  var on = el.hasAttribute("data-check-all"), name = el.getAttribute(on ? "data-check-all" : "data-check-none");
  el.form.querySelectorAll('input[type="checkbox"]').forEach(function (box) { if (box.name === name && !box.disabled) box.checked = on; });
});

// Forms that wait for the server a while (data-busy="Wird berechnet …"): the button says so and is not pressed a
// second time. Back in the browser history (the page comes from its cache) the button is as it was.
(function () {
  var busy = [];
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || !form.matches || !form.matches("form[data-busy]")) return;  // e.g. a "no" to data-confirm
    var button = e.submitter || form.querySelector("button[type=submit], button:not([type])");
    if (!button) return;
    // the text beside an icon is its own span; else the button's whole text
    var label = button.lastElementChild && button.lastElementChild.tagName === "SPAN" ? button.lastElementChild : button;
    busy.push({ button: button, label: label, text: label.textContent });
    setTimeout(function () {  // after the form's data were taken, the button's name among them
      button.disabled = true;
      label.textContent = (label === button ? "" : " ") + form.dataset.busy;
    }, 0);
  });
  window.addEventListener("pageshow", function () {
    busy.forEach(function (b) { b.button.disabled = false; b.label.textContent = b.text; });
    busy = [];
  });
})();

// Translation page (app/translations.py): a translation is saved when its field is left; filters by status;
// "Alles Offene mit KI übersetzen" asks the server for one chunk after the other until nothing is open, the
// open fields shimmer violet meanwhile, then the page comes back with the translations.
(function () {
  var page = document.querySelector("[data-tr-page]");
  if (!page) return;
  var csrf = csrfToken();
  var labels = de_en({missing: "fehlt", changed: "zu prüfen", ai: "KI, ungeprüft", ok: "übersetzt", validated: "validierte Fassung"},
                     {missing: "missing", changed: "to check", ai: "AI, unchecked", ok: "translated", validated: "validated version"});
  // rows without a field for this person (texts only admins change, data-tr-locked) are not the AI's for them
  var FOR_AI = '[data-tr-row][data-status="missing"]:not([data-tr-locked]), [data-tr-row][data-status="changed"][data-ai="1"]:not([data-tr-locked])';
  function setCounts(counts) {
    Object.keys(counts).forEach(function (k) {
      document.querySelectorAll('[data-tr-count="' + k + '"]').forEach(function (el) { el.textContent = counts[k]; });
    });
    // what the AI would translate: what is missing, and its own translations whose source changed (not the
    // ones a person wrote or confirmed; translations.pending)
    var open = document.querySelectorAll(FOR_AI).length;
    document.querySelectorAll('[data-tr-count="open"]').forEach(function (el) { el.textContent = open; });
    var ai = page.querySelector("[data-tr-ai]");
    if (ai) ai.disabled = !open;
  }
  function setStatus(row, status, label) {
    row.dataset.status = status;
    if (status === "ai" || status === "ok") row.dataset.ai = status === "ai" ? "1" : "0";
    var badge = row.querySelector("[data-tr-status]");
    badge.className = "tr-status is-" + status;
    badge.textContent = label || labels[status];
    var diff = row.querySelector(".tr-diff");
    if (diff && status !== "changed") diff.hidden = true;
  }
  document.querySelectorAll("textarea[data-unit]").forEach(function (box) {
    var saved = box.value;
    box.addEventListener("change", function () {
      if (box.value === saved) return;
      var row = box.closest("[data-tr-row]"), body = new FormData();
      body.append("unit", box.dataset.unit); body.append("path", box.dataset.path);
      body.append("text", box.value); body.append("csrf_token", csrf);
      row.classList.add("is-saving");
      saveState(row, "busy", "", box);  // the head of the page says it too (S2), each row a source of its own
      fetch(page.dataset.save, {method: "POST", body: body, credentials: "same-origin"})
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          saved = box.value; setStatus(row, d.status, d.label); setCounts(d.counts);
          saveState(row, "ok", de_en("Gespeichert " + saveClock(), "Saved " + saveClock()));
        })
        .catch(function () {
          setStatus(row, row.dataset.status, de_en("nicht gespeichert", "not saved")); row.querySelector("[data-tr-status]").classList.add("is-error");
          saveState(row, "error", de_en("Übersetzung nicht gespeichert: keine Verbindung zum Server, oder die Anmeldung ist abgelaufen.",
                                        "Translation not saved: no connection to the server, or the sign-in has expired."));
        })
        .finally(function () { row.classList.remove("is-saving"); });
    });
  });
  var filter = "all";
  function applyFilter() {
    document.querySelectorAll("[data-tr-row]").forEach(function (row) {
      row.hidden = filter !== "all" && row.dataset.status !== filter;
    });
    document.querySelectorAll("[data-tr-unit]").forEach(function (u) { u.hidden = !u.querySelector("[data-tr-row]:not([hidden])"); });
    document.querySelectorAll("[data-tr-group]").forEach(function (g) { g.hidden = !g.querySelector("[data-tr-unit]:not([hidden])"); });
  }
  page.querySelectorAll("[data-tr-filter]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      filter = chip.dataset.trFilter;
      page.querySelectorAll("[data-tr-filter]").forEach(function (c) { c.classList.toggle("is-on", c === chip); c.setAttribute("aria-pressed", c === chip ? "true" : "false"); });
      applyFilter();
    });
  });
  var aiButton = page.querySelector("[data-tr-ai]"), say = page.querySelector("[data-tr-ai-status]");
  if (!aiButton) return;
  aiButton.addEventListener("click", function () {
    // In the field what the AI writes is what participants read at once, before anyone has looked at it
    // (data-tr-live): asked before, as every step that cannot be taken back
    var sure = aiButton.hasAttribute("data-tr-live")
      ? window.askConfirm({title: de_en("Die Befragung läuft: jetzt mit KI übersetzen?", "The survey is running: translate with AI now?"),
                           note: de_en("Die Übersetzungen sind sofort für Teilnehmende sichtbar, bevor jemand sie geprüft hat.",
                                       "The translations are visible to participants at once, before anyone has checked them."),
                           ok: de_en("Übersetzen", "Translate")})
      : Promise.resolve(true);
    sure.then(function (yes) { if (yes) translate(); });
  });
  function translate() {
    var rows = document.querySelectorAll(FOR_AI);
    rows.forEach(function (r) { r.classList.add("is-ai-working"); });
    aiButton.disabled = true;
    var total = rows.length, done = 0;
    say.textContent = de_en("Die KI übersetzt " + total + " Texte …", "The AI translates " + total + " texts …");
    function next() {
      var body = new FormData();
      body.append("csrf_token", csrf);
      window.aiFetch(page.dataset.ai, {method: "POST", body: body, credentials: "same-origin"})
        .then(function (r) { return r.json().then(function (d) { return {ok: r.ok, d: d}; }); })
        .then(function (res) {
          if (!res.ok) throw new Error(res.d.error || de_en("Fehler", "Error"));
          done += res.d.done;
          if (res.d.left > 0 && res.d.done > 0) {
            say.textContent = de_en("Die KI übersetzt … " + done + " von " + total + " fertig", "The AI translates … " + done + " of " + total + " done");
            next();
          } else {
            say.textContent = de_en(done + " Texte übersetzt. Die Seite lädt neu …", done + " texts translated. The page reloads …");
            window.location.reload();
          }
        })
        .catch(function (e) {
          rows.forEach(function (r) { r.classList.remove("is-ai-working"); });
          aiButton.disabled = false;
          say.textContent = (done ? de_en(done + " Texte übersetzt, dann: ", done + " texts translated, then: ") : "")
            + (e.message || de_en("Die KI ist gerade nicht erreichbar.", "The AI cannot be reached right now."));
          if (done) window.setTimeout(function () { window.location.reload(); }, 2500);
        });
    }
    next();
  }
})();

// Dashboard designer (templates/boards/designer.html). The form saves by itself and comes back anew with every
// save ("Forms that save by themselves", data-autosave-redraw); here is what only the designer has: rows ordered
// with the arrows or by dragging, tiles taken off or added, and the preview next to the form, which asks for the
// new state after every save and reports tiles dragged or removed in it. The list to tick ("Auswählen") stands in
// the left column with the tiles; every element not yet on the dashboard stands below them with a "+" (no list to tick,
// Ralf 2026-10-03). The name stands in the right column and belongs to the form (form="board-form").
(function () {
  var page = document.querySelector("[data-board-designer]");
  if (!page) return;
  // the columns scroll on their own (app.css): they reach from where they start down to the footer
  function boardTop() { page.style.setProperty("--board-top", Math.round(page.getBoundingClientRect().top + window.scrollY) + "px"); }
  boardTop();
  window.addEventListener("resize", boardTop);
  var wrap = page.querySelector("[data-board-form-wrap]"), frame = page.querySelector("[data-board-frame]");
  // the preview at the width of the link on a wide screen, scaled into its column (V82): the same grid of tiles
  var box = page.querySelector("[data-board-box]"), WIDE = 1220;
  function fitPreview() {
    if (!box || !frame) return;
    // scaled down to the box as a page preview, the whole dashboard in view (UI/UX audit 2026-10-09, X66: at 0.7
    // a third was cut off): never below 0.5, the page is then drawn a little narrower, still in the columns of a
    // wide screen; it never scrolls sideways
    var s = Math.max(0.5, Math.min(1, box.clientWidth / WIDE));
    frame.style.width = (box.clientWidth / s) + "px";
    frame.style.height = (box.clientHeight / s) + "px";
    frame.style.transform = s < 1 ? "scale(" + s + ")" : "";
  }
  fitPreview();
  window.addEventListener("resize", fitPreview);

  function form() { return wrap.querySelector("form"); }
  function rows() { return Array.prototype.slice.call(wrap.querySelectorAll(".board-row")); }
  function setOrder() { form().elements.order.value = rows().map(function (r) { return r.dataset.key; }).join(","); }
  function refreshPreview() {
    try {
      if (frame.contentWindow && frame.contentWindow.boardRefresh) frame.contentWindow.boardRefresh();
      else frame.contentWindow.location.reload();
    } catch (e) { frame.src = frame.src; }
  }
  // the form was changed without typing or a click in it (a drag, the preview): it is sent as after any change
  function send() { form().dispatchEvent(new Event("input", { bubbles: true })); }
  wrap.addEventListener("autosave:saved", refreshPreview);

  function take(row) {  // off the dashboard (its "pick" goes with the row): an element comes back below, a heading or text is gone
    row.remove(); setOrder(); send();
  }
  // the buttons only change the form: a click on a button of the form sends it (the shared part)
  page.addEventListener("click", function (e) {
    var button = e.target.closest("button");
    if (!button || !wrap.contains(button)) return;
    var row = button.closest(".board-row");
    if (button.dataset.boardMove && row) {
      var other = button.dataset.boardMove === "up" ? row.previousElementSibling : row.nextElementSibling;
      if (!other) return;
      if (button.dataset.boardMove === "up") row.parentNode.insertBefore(row, other); else row.parentNode.insertBefore(other, row);
      setOrder(); send();
    } else if (button.hasAttribute("data-board-remove") && row) {
      take(row);
    } else if (button.dataset.boardAdd) {
      // a heading or a text: a tile with a key of its own, at the end
      var key = "text:" + Math.random().toString(36).slice(2, 10).replace(/[^a-z0-9]/g, "x").padEnd(6, "0");
      [["pick", key], ["kind-" + key, button.dataset.boardAdd]].forEach(function (pair) {
        var input = document.createElement("input");
        input.type = "hidden"; input.name = pair[0]; input.value = pair[1];
        form().appendChild(input);
      });
      send();
    } else if (button.dataset.boardInclude) {  // "+" of an element below: a tile at the end
      var pickIt = document.createElement("input");
      pickIt.type = "hidden"; pickIt.name = "pick"; pickIt.value = button.dataset.boardInclude;
      form().appendChild(pickIt);
      button.disabled = true;
      send();
    }
  });

  // a tile was dragged to another place in the preview (static/board.js)
  window.addEventListener("message", function (e) {
    if (e.origin !== location.origin || e.source !== frame.contentWindow || !e.data) return;
    if (form().querySelector(".board-fieldset").disabled) return;
    if (e.data.type === "board-order" && Array.isArray(e.data.keys)) {
      form().elements.order.value = e.data.keys.join(",");
      Object.keys(e.data.starts || {}).forEach(function (key) {  // the column each tile begins in
        var select = form().elements["x-" + key];
        if (!select) return;
        var value = String(e.data.starts[key]);
        if (!Array.prototype.some.call(select.options, function (o) { return o.value === value; })) return;
        select.value = value;
      });
      send();
    } else if (e.data.type === "board-remove") {  // the x of a tile in the preview
      var row = rows().filter(function (r) { return r.dataset.key === e.data.key; })[0];
      if (row) take(row);
    }
  });

  var line = null;
  function target(y) {
    var list = rows().filter(function (r) { return !r.classList.contains("is-dragged"); });
    for (var i = 0; i < list.length; i++) {
      var box = list[i].getBoundingClientRect();
      if (y < box.top + box.height / 2) return list[i];
    }
    return null;
  }
  pointerDrag({
    source: function (el) {
      var handle = el.closest && el.closest(".board-row .drag-handle");
      return handle && !form().querySelector(".board-fieldset").disabled ? { el: handle.closest(".board-row") } : null;
    },
    start: function (d) {
      d.el.classList.add("is-dragged");
      d.ghost = document.createElement("div");
      d.ghost.textContent = d.el.querySelector(".board-row-name").textContent.trim();
      line = document.createElement("li");
      line.className = "board-drop-line";
    },
    move: function (d) { d.el.parentNode.insertBefore(line, target(d.y)); },
    drop: function (d) {
      if (line && line.parentNode) { line.parentNode.insertBefore(d.el, line); setOrder(); send(); }
    },
    end: function (d) { d.el.classList.remove("is-dragged"); if (line) line.remove(); line = null; },
  });
})();

// AI buttons that send a form (check texts or prompts, write a consent, a methods section ...): the page waits for
// the model, often several seconds; the button shows that it works (a glint running across it, app.css .ai-busy)
// and does not send the form a second time. Back in the browser history the button is idle again.
(function () {
  document.addEventListener("submit", function (e) {
    var button = e.submitter;
    if (!button || !button.classList.contains("button-ai")) return;
    if (button.classList.contains("ai-busy") || e.target.querySelector(".ai-busy")) { e.preventDefault(); return; }  // sent already
    if (e.defaultPrevented) return;  // e.g. a question (data-confirm) that was answered with "no"
    button.classList.add("ai-busy");
    button.setAttribute("aria-busy", "true");
  });
  window.addEventListener("pageshow", function () {
    document.querySelectorAll(".ai-busy").forEach(function (b) { b.classList.remove("ai-busy"); b.removeAttribute("aria-busy"); });
  });
})();

// × on a part of a form that saves by itself (a Teilquote, a trigger of an end): marks the part as removed in its
// hidden field and hides it; the click on the button saves the form (autosave), "Rückgängig" brings it back.
document.addEventListener("click", function (e) {
  var button = e.target.closest("[data-remove-flag]");
  if (!button) return;
  var part = button.closest(button.getAttribute("data-remove-flag"));
  var flag = part && part.querySelector("[data-remove-value]");
  if (!flag) return;
  flag.value = "1";
  part.hidden = true;
});

// Rows that are added and removed in place ([data-rows] with a <template data-row-template> of an empty row, a
// "+ …" button [data-row-add] and × [data-row-remove] in each row [data-row]): link parameters, link rules, values
// of an external section. The click on the button saves a form that saves by itself (autosave) or marks a form
// with its own "Speichern" as changed (data-dirty-guard).
document.addEventListener("click", function (e) {
  var button = e.target.closest("[data-row-add], [data-row-remove]");
  if (!button) return;
  if (button.hasAttribute("data-row-add")) {
    var template = button.closest("[data-rows]").querySelector("template[data-row-template]");
    if (!template) return;
    // "__N__" in the template (field names of a line, e.g. the rules "r__N___…") becomes a number of its own
    var holder = document.createElement(template.parentNode.tagName === "TBODY" ? "tbody" : "div");  // a <tr> needs a table body
    holder.innerHTML = template.innerHTML.replace(/__N__/g, String(Date.now() % 100000000));
    var row = holder.firstElementChild;
    template.parentNode.insertBefore(row, template);
    var first = row.querySelector("input:not([type=hidden]), select, textarea");
    renumberHypotheses(row.parentNode);
    if (first) first.focus();
  } else {
    var gone = button.closest("[data-row]");
    if (!gone) return;
    var next = gone.nextElementSibling, rows = gone.parentNode;
    gone.remove();
    renumberHypotheses(rows);
    var focus = next && next.matches("[data-row]") ? next.querySelector("[data-row-remove]") : null;
    if (focus) focus.focus();
  }
});

// A new hypothesis shows its number at once ("H3", not "neu"), as the server numbers them by position (UI/UX
// audit 2026-10-09, X8); removing one moves the later ones up.
function renumberHypotheses(holder) {
  if (!holder) return;
  var marks = holder.querySelectorAll(":scope > [data-row] .plan-hyp-n");
  Array.prototype.forEach.call(marks, function (mark, i) { mark.textContent = "H" + (i + 1); });
}

// Answer options of a choice (templates/items/_choice_editor.html, UI/UX audit 2026-10-05, V16): a new option
// labelled like "keine Angabe" or "weiß nicht" is ticked as "fehlend" when its label is left; one that was there
// before keeps what was chosen.
document.addEventListener("change", function (e) {
  var label = e.target.closest && e.target.closest('input[name="option_label"]');
  var row = label && label.closest("tr.new-row");
  var box = row && row.querySelector('input[name="option_missing"]');
  if (!box || box.checked || box.dataset.suggested) return;
  if (/^\s*(keine angabe|k\.\s*a\.|weiß nicht|weiss nicht|kann ich nicht sagen|möchte ich nicht sagen|keine antwort|don'?t know|prefer not to (say|answer)|no answer)\s*\.?\s*$/i.test(label.value)) {
    box.checked = true;
    box.dataset.suggested = "1";
    box.dispatchEvent(new Event("change", { bubbles: true }));
  }
});

// The study plan (templates/surveys/_fields_plan.html, UI/UX audit 2026-10-05). V1: the question chosen as what
// explains is greyed out among what is explained and the other way round; one question on both sides gets the
// message at the row. V6: the method finder answers as soon as the goal is picked, from the data on the page; an
// empty "Design" takes the suggestion, another one gets a button "übernehmen".
(function () {
  function vars(row) {
    var iv = row.querySelector('select[name="h_iv"]'), dv = row.querySelector('select[name="h_dv"]');
    if (!iv || !dv) return;
    // "Unterschied" and "Wirkung" compare groups: a scale or a number (data-metric) is no choice under "Gruppen" /
    // "Bedingung"; one chosen before stays, and the line under "Verfahren" warns (study_plan.group_mismatch, X3)
    var kind = row.querySelector('select[name="h_kind"]');
    var groups = !!kind && (kind.value === "difference" || kind.value === "causal");
    // a descriptive assumption names one variable: the hidden other one blocks nothing (study_plan.ONE_VARIABLE)
    var one = !!kind && kind.value === "descriptive";
    [[iv, dv], [dv, iv]].forEach(function (pair) {
      Array.prototype.forEach.call(pair[1].options, function (o) {
        var metric = groups && pair[1] === iv && o.hasAttribute("data-metric");
        o.disabled = (!one && !!o.value && o.value === pair[0].value && o.value !== pair[1].value) || (metric && o.value !== iv.value);
        if (o.hasAttribute("data-metric") && pair[1] === iv)
          o.title = groups ? de_en("eine Skala oder Zahl bildet keine Gruppen", "a scale or number forms no groups") : "";
      });
    });
    var same = !one && !!iv.value && iv.value === dv.value;
    var msg = row.querySelector("[data-plan-same]");
    if (msg) msg.hidden = !same;
    iv.classList.toggle("is-invalid", same);
    dv.classList.toggle("is-invalid", same);
  }
  document.addEventListener("change", function (e) {
    var sel = e.target.closest && e.target.closest("select[data-plan-var]");
    if (sel && sel.closest(".plan-hyp")) vars(sel.closest(".plan-hyp"));
    // the kind "Vermittlung" shows the field for the mediator
    var kind = e.target.closest && e.target.closest('select[name="h_kind"]');
    var mv = kind && kind.closest(".plan-hyp") && kind.closest(".plan-hyp").querySelector("[data-plan-mv]");
    if (mv) mv.hidden = kind.value !== "mediation";
    // and "Moderation" the field for the moderator (stage 2 of the drawing, model.js)
    var wv = kind && kind.closest(".plan-hyp") && kind.closest(".plan-hyp").querySelector("[data-plan-wv]");
    if (wv) wv.hidden = kind.value !== "moderation";
    // a descriptive assumption names one variable only, the measured one (study_plan.ONE_VARIABLE)
    var ivField = kind && kind.closest(".plan-hyp") && kind.closest(".plan-hyp").querySelector("[data-plan-iv]");
    if (ivField) ivField.hidden = kind.value === "descriptive";
    // the names of the two variables follow the kind (study_plan.roles: Variable A/B, Gruppen/Verglichen wird, ...)
    var named = document.querySelector("[data-plan-roles]");
    if (kind && named && kind.closest(".plan-hyp")) {
      var all = JSON.parse(named.textContent || "{}"), names = all[kind.value] || all[""] || {};
      kind.closest(".plan-hyp").querySelectorAll("[data-role]").forEach(function (span) {
        var text = names[span.getAttribute("data-role")];
        if (!text) return;
        span.textContent = text;
        var sel = span.parentNode.querySelector("select");
        if (sel) sel.setAttribute("aria-label", text);
      });
    }
    // the direction is named by the kind: "positiv/negativ" for a relationship, "A größer/kleiner" for groups (X14)
    var dirs = document.querySelector("[data-plan-dirs]");
    if (kind && dirs && kind.closest(".plan-hyp")) {
      var labels = (JSON.parse(dirs.textContent || "{}"))[kind.value] || {};
      var dir = kind.closest(".plan-hyp").querySelector('select[name="h_dir"]');
      if (dir) Array.prototype.forEach.call(dir.options, function (o) { if (labels[o.value]) o.textContent = labels[o.value]; });
    }
    if (kind && kind.closest(".plan-hyp")) vars(kind.closest(".plan-hyp"));
    var row = e.target.closest && e.target.closest('select[name="h_kind"], select[name="h_analysis"]');
    if (row && row.closest(".plan-hyp")) fields(row.closest(".plan-hyp"));
  });
  document.querySelectorAll(".plan-hyp").forEach(vars);
  // UI/UX audit 2026-10-07, W10: no power analysis for "offen" and "nur beschreiben" (the values stay), and the
  // estimation of the structural equation model only while a hypothesis uses it
  function fields(row) {
    var kind = row && row.querySelector('select[name="h_kind"]'), method = row && row.querySelector('select[name="h_analysis"]');
    var power = row && row.querySelector("[data-plan-power]");
    // the automatic method is "nur beschreiben" only for "offen" and "beschreibende Annahme" (study_plan.suggest)
    var untested = !!kind && (kind.value === "open" || kind.value === "descriptive");  // study_plan.UNTESTED
    if (power && kind && method) power.hidden = untested || method.value === "describe";
    // an open question shows neither direction nor method (UI/UX audit 2026-10-09, X14); the values stay
    var dirField = row && row.querySelector("[data-plan-dir]"), methodField = row && row.querySelector(".plan-method");
    if (kind && dirField) dirField.hidden = untested;
    if (kind && methodField) methodField.hidden = untested;
    var sem = document.querySelector("[data-plan-sem]");
    if (sem) {
      sem.hidden = !Array.prototype.some.call(document.querySelectorAll(".plan-hyp"), function (r) {
        var k = r.querySelector('select[name="h_kind"]'), m = r.querySelector('select[name="h_analysis"]');
        return (k && (k.value === "mediation" || k.value === "effect" || k.value === "moderation")) || (m && m.value === "sem");
      });
    }
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest(".plan-hyps [data-row-remove]")) setTimeout(function () { fields(null); }, 0);
  });
  // the bootstrap of the structural equation model (results page, surveys/_sem.html): the bar follows the status;
  // when it has ended the page loads again (its results are kept, so that is quick)
  document.querySelectorAll("[data-sem-running]").forEach(function (box) {
    var bar = box.querySelector("[data-bar]"), meter = box.querySelector("[role=progressbar]");
    var text = box.querySelector("[data-status]"), failed = 0;
    (function poll() {
      fetch(box.getAttribute("data-url"), { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          failed = 0;
          if (d.state !== "running") { window.location.reload(); return; }
          var pct = Math.round(d.progress * 100);
          bar.style.width = pct + "%"; meter.setAttribute("aria-valuenow", pct);
          if (text) text.textContent = pct + de_en(" %", "%");
          setTimeout(poll, 2000);
        })
        .catch(function () { if (++failed < 5) setTimeout(poll, 4000); });
    })();
  });
  // "Stand gegen die Poweranalyse" on the page "Datenerhebung" (PA4): fetched after the page
  document.querySelectorAll("[data-power-progress]").forEach(function (box) {
    fetch(box.getAttribute("data-power-progress"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.text() : ""; })
      .then(function (html) { if (html.trim()) { box.innerHTML = html; box.removeAttribute("aria-busy"); } else box.remove(); })
      .catch(function () { box.remove(); });
  });
  // "verwertbar" in the table of links per value (concept "Werbelinks", 2026-10-08): fetched after the page, since
  // it reads the quality assurance
  document.querySelectorAll("[data-value-usable]").forEach(function (table) {
    fetch(table.getAttribute("data-value-usable"), { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (data) {
        var counts = data.usable || {}, flags = data.flags || {};
        table.querySelectorAll("[data-usable]").forEach(function (cell) {
          var n = counts[cell.getAttribute("data-usable")];
          cell.textContent = n == null ? "–" : String(n);
        });
        // conspicuous links (panel._flags): a mark next to the name, the reasons in its title
        table.querySelectorAll("[data-flag]").forEach(function (slot) {
          var why = flags[slot.getAttribute("data-flag")];
          if (!why || !why.length) return;
          slot.hidden = false;
          slot.title = why.join("; ");
          slot.setAttribute("aria-label", de_en("auffällig: ", "conspicuous: ") + why.join("; "));
        });
      })
      .catch(function () {});
  });
  // "Als Stichprobenziel übernehmen" of the power analysis (PA1): the number goes into the field, which saves
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-plan-take-target]");
    var field = b && b.closest("form") && b.closest("form").querySelector('input[name="sample_target"]');
    if (!field) return;
    field.value = b.getAttribute("data-plan-take-target");
    field.dispatchEvent(new Event("change", { bubbles: true }));
    b.hidden = true;
  });

  var data = document.querySelector("[data-plan-finder-data]");
  var box = document.querySelector("[data-plan-finder]");
  if (!data || !box) return;
  var finder = JSON.parse(data.textContent || "{}");
  var form = box.closest("form");
  // several goals (ticks, Ralf 2026-10-06): one suggestion per ticked goal
  var goals = form ? form.querySelectorAll('input[name="goal"]') : [], design = form && form.querySelector('select[name="design"]');
  if (!goals.length || !design) return;
  function ticked() {
    return Array.prototype.filter.call(goals, function (g) { return g.checked && finder[g.value]; });
  }
  function draw() {
    var picked = ticked();
    if (!picked.length) { box.innerHTML = '<p class="muted small">' + de_en("Mit dem Erkenntnisziel schlägt Askurious ein passendes Design vor.", "With the research goal, Askurious suggests a suitable design.") + "</p>"; return; }
    box.innerHTML = picked.map(function (g) {
      var f = finder[g.value], label = g.parentNode.textContent.trim();
      var where = f.url ? '<a href="' + esc(f.url) + '">' + esc(f.where) + "</a>" : esc(f.where);
      var html = '<p class="muted small"><strong>' + esc(label) + ": " + esc(f.name) + ".</strong> " + esc(f.text) +
                 ' <span class="hint">' + de_en("In Askurious: ", "In Askurious: ") + where + ".</span>";
      if (design.value && design.value !== f.design) {
        var chosen = design.options[design.selectedIndex].text;
        html += " " + de_en("Gewählt ist „{name}“; das kann gute Gründe haben.", "“{name}” is chosen; there may be good reasons for this.").replace("{name}", function () { return esc(chosen); }) + " " +
                '<button type="button" class="button button-secondary button-small plan-finder-take" data-plan-take="' +
                esc(f.design) + '">' + de_en("„{name}“ übernehmen", "Use “{name}”").replace("{name}", function () { return esc(f.name); }) + "</button>";
      }
      return html + "</p>";
    }).join("");
  }
  function take(value) {
    design.value = value;
    design.dispatchEvent(new Event("change", { bubbles: true }));  // saves where the form saves by itself
  }
  Array.prototype.forEach.call(goals, function (g) {
    g.addEventListener("change", function () {
      var f = g.checked && finder[g.value];
      if (f && !design.value) take(f.design);
      draw();
    });
  });
  design.addEventListener("change", draw);
  box.addEventListener("click", function (e) {
    var b = e.target.closest("[data-plan-take]");
    if (b) take(b.getAttribute("data-plan-take"));
  });
})();

// Parts of a form that only matter for some value of another field (data-show-when="name:value" or "name:!value",
// e.g. the details of the tempo hint only while it is on): hidden otherwise; their fields are still sent.
(function () {
  var parts = document.querySelectorAll("[data-show-when]");
  if (!parts.length) return;
  function update() {
    parts.forEach(function (part) {
      var rule = part.getAttribute("data-show-when").split(":"), form = part.closest("form");
      var field = form && form.elements[rule[0]];
      if (!field) return;
      var value = field.value, not = rule[1].charAt(0) === "!", want = not ? rule[1].slice(1) : rule[1];
      part.hidden = not ? value === want : value !== want;
    });
  }
  document.addEventListener("change", update);
  update();
})();

// On a narrow screen the survey tabs scroll sideways: the current one is brought into view. The tabs of the
// administration do the same (UI audit 2026-10-07, W83).
// The mobile section selector navigates only when its Open button is activated.
document.querySelectorAll("select[data-nav-select]").forEach(function (sel) {
  var open = sel.parentElement.querySelector("[data-nav-open]");
  if (open) open.addEventListener("click", function () { if (sel.value) window.location.href = sel.value; });
});
document.querySelectorAll(".survey-tabs, .admin-tabs").forEach(function (nav) {
  var current = nav.querySelector('[aria-current="page"]');
  function mark() { nav.classList.toggle("is-scrolling", nav.scrollWidth > nav.clientWidth + 1); }  // fade at the ends: there is more
  mark(); window.addEventListener("resize", mark);
  if (current && nav.scrollWidth > nav.clientWidth) {
    var at = current.getBoundingClientRect(), box = nav.getBoundingClientRect();
    nav.scrollLeft += at.left - box.left - (nav.clientWidth - at.width) / 2;
  }
});

// On a Mac the shortcuts are ⌘Z and ⌘⇧Z (the handler above takes both): the tooltips of ↶ ↷ say so, also after
// a save has rewritten them (the server and the save write "Strg+Z" / "Strg+Y")
(function () {
  if (!/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) return;
  function fix(el) {
    ["title", "aria-label"].forEach(function (name) {
      var v = el.getAttribute(name);
      if (v && v.indexOf("Strg+") >= 0) el.setAttribute(name, v.replace(/Strg\+Z/g, "⌘Z").replace(/Strg\+Y/g, "⌘⇧Z"));
    });
  }
  var buttons = document.querySelectorAll("[data-undo], [data-redo]");
  buttons.forEach(function (el) {
    fix(el);
    new MutationObserver(function () { fix(el); }).observe(el, { attributes: true, attributeFilter: ["title"] });
  });
})();

// Placeholders for link fields (_link_chips.html): a click puts {{pid}} & co. at the cursor of the link field of the
// same form that had the focus last (else its first one); the field then counts as changed and saves.
(function () {
  var last = null;
  document.addEventListener("focusin", function (e) { if (e.target.matches && e.target.matches("[data-link-field]")) last = e.target; });
  document.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-link-chips] [data-insert]");
    if (!chip) return;
    var scope = chip.closest("form") || document;
    var field = last && scope.contains(last) ? last : (scope.querySelector("[data-link-row]:not([hidden]) [data-link-field]") || scope.querySelector("[data-link-field]"));
    if (!field || field.disabled) return;
    var text = chip.dataset.insert, at = field.selectionStart == null ? field.value.length : field.selectionStart;
    var end = field.selectionEnd == null ? at : field.selectionEnd;
    field.value = field.value.slice(0, at) + text + field.value.slice(end);
    field.focus();
    field.setSelectionRange(at + text.length, at + text.length);
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
})();

// Learning aids (_learn.html, why()): „Nicht mehr zeigen“ in an open fold hides this fold for the person (all of its
// places on the page at once); „Ausgeblendete Erklärungen wieder zeigen“ on Konto brings them back (E7). In the place
// of the fold a line „Erklärung ausgeblendet · Rückgängig“ stays for some seconds and takes the focus (UI/UX audit
// 2026-10-07, W13): „Rückgängig“ shows the fold again, here and for the person.
document.addEventListener("click", function (e) {
  var btn = e.target.closest && e.target.closest("[data-learn-hide]");
  if (!btn) return;
  e.preventDefault();
  var body = new FormData();
  body.append("csrf_token", csrfToken());
  fetch(btn.dataset.url, { method: "POST", body: body, credentials: "same-origin" }).then(function (r) {
    if (!r.ok) return;
    var gone = [];  // [fold, the node it stood before, its parent]
    document.querySelectorAll('[data-learn-hide="' + btn.dataset.learnHide + '"]').forEach(function (b) {
      var fold = b.closest("details.learn-why");
      if (fold) { gone.push([fold, fold.nextSibling, fold.parentNode]); }
    });
    var mine = btn.closest("details.learn-why");
    var note = document.createElement("p");
    note.className = "learn-hidden-note small";
    note.setAttribute("role", "status");
    note.tabIndex = -1;
    note.textContent = de_en("Erklärung ausgeblendet", "Explanation hidden") + " · ";
    var undo = document.createElement("button");
    undo.type = "button";
    undo.className = "link";
    undo.textContent = de_en("Rückgängig", "Undo");
    note.appendChild(undo);
    if (mine) mine.parentNode.insertBefore(note, mine);
    gone.forEach(function (g) { g[0].remove(); });
    note.focus();
    // the line goes after some seconds, not while the focus is in it
    function fade() { if (note.contains(document.activeElement)) timer = setTimeout(fade, 4000); else note.remove(); }
    var timer = setTimeout(fade, 8000);
    undo.addEventListener("click", function () {
      clearTimeout(timer);
      var again = new FormData();
      again.append("csrf_token", csrfToken());
      fetch(btn.dataset.unhideUrl, { method: "POST", body: again, credentials: "same-origin" }).then(function (r2) {
        if (!r2.ok) return;
        gone.forEach(function (g) { g[2].insertBefore(g[0], g[1] && g[1].parentNode === g[2] ? g[1] : null); });
        if (note.parentNode) note.remove();
        var summary = mine && mine.querySelector("summary");
        if (summary) summary.focus();
      });
    });
  });
});

// Glossary page (learning/glossary.html): the search field hides every term and decision that does not contain
// the typed words, and every section left empty (concept „Lernhilfen: dritte Runde“, F6).
(function () {
  var field = document.querySelector("[data-learn-search]");
  if (!field) return;
  var none = document.querySelector("[data-learn-search-none]");
  field.addEventListener("input", function () {
    var words = field.value.toLowerCase().split(/\s+/).filter(Boolean), any = false;
    document.querySelectorAll("[data-learn-section]").forEach(function (section) {
      var shown = 0;
      section.querySelectorAll(".learn-glossary dt").forEach(function (dt) {
        var dd = dt.nextElementSibling, text = (dt.textContent + " " + (dd ? dd.textContent : "")).toLowerCase();
        var hit = words.every(function (w) { return text.indexOf(w) >= 0; });
        dt.hidden = !hit;
        if (dd) dd.hidden = !hit;
        if (hit) shown += 1;
      });
      // the groups of "Entscheidungen" (Planen, Erheben, Analyse): a heading goes with its list (H3, fünfte Runde)
      section.querySelectorAll("h3 + .learn-glossary").forEach(function (list) {
        list.previousElementSibling.hidden = !list.querySelector("dt:not([hidden])");
      });
      section.hidden = shown === 0;
      if (shown) any = true;
    });
    if (none) none.hidden = any;
  });
})();

// Learning aids (_learn.html, term()): the "?" at a statistic opens a small box in the same window with the
// explanation and „im Glossar ↗“; Escape, a click elsewhere or leaving it with the keyboard closes it (UI/UX audit
// 2026-10-05, V27).
(function () {
  var pop = null, owner = null;
  // one kind of help at a "?" (Ralf, 2026-10-06): the browser's dark hover text of the cell around it (a table head
  // with title="…") goes into the box as its own line, so hovering shows nothing and a click shows everything
  function adopt(btn) {
    // the cell the "?" stands in (a table head, a label), only if that cell itself has the hover text
    var cell = btn.parentElement && btn.parentElement.closest("th, td, dt, dd, label, span[title], p");
    var holder = cell && cell.hasAttribute("title") ? cell : null;
    if (holder && holder.title) {
      btn.dataset.termNote = holder.title;
      holder.removeAttribute("title");
    }
    btn.setAttribute("data-term-adopted", "");
  }
  document.querySelectorAll("[data-term]").forEach(adopt);
  // parts drawn later (HB, segments, simulator): before their first hover or focus
  ["mouseover", "focusin"].forEach(function (type) {
    document.addEventListener(type, function (e) {
      var btn = e.target.closest && e.target.closest("[data-term]:not([data-term-adopted])");
      if (btn) adopt(btn);
    });
  });
  function close() {
    if (!pop) return;
    pop.hidden = true;
    if (owner) owner.setAttribute("aria-expanded", "false");
    owner = null;
  }
  function open(btn) {
    if (!pop) {
      pop = document.createElement("div");
      pop.className = "learn-pop";
      pop.id = "learn-pop";
      pop.setAttribute("role", "dialog");
      document.body.appendChild(pop);
    }
    pop.textContent = "";
    var head = document.createElement("strong");
    head.textContent = btn.dataset.termName;
    var text = document.createElement("p");
    text.textContent = btn.dataset.termText;
    var link = document.createElement("a");
    link.href = btn.dataset.termHref; link.target = "_blank"; link.rel = "noopener";
    link.textContent = de_en("im Glossar ↗", "in the glossary ↗");
    pop.append(head, text);
    if (btn.dataset.termNote) {  // what the table says about its own column (n from 10, n − 1, …)
      var note = document.createElement("p");
      note.className = "learn-pop-note";
      note.textContent = btn.dataset.termNote;
      pop.append(note);
    }
    if (btn.dataset.termExample) {  // the example of the glossary (concept „Lernhilfen: Erweiterungen“, E6)
      var example = document.createElement("p");
      example.className = "learn-pop-example";
      example.textContent = de_en("Beispiel: ", "Example: ") + btn.dataset.termExample;
      pop.append(example);
    }
    pop.append(link);
    pop.setAttribute("aria-label", btn.dataset.termName);
    pop.hidden = false;
    var r = btn.getBoundingClientRect(), w = Math.min(320, document.documentElement.clientWidth - 32);
    pop.style.width = w + "px";
    pop.style.left = Math.max(16, Math.min(r.left + window.scrollX - 12, window.scrollX + document.documentElement.clientWidth - w - 16)) + "px";
    pop.style.top = (r.bottom + window.scrollY + 6) + "px";
    owner = btn;
    btn.setAttribute("aria-expanded", "true");
    btn.setAttribute("aria-controls", "learn-pop");
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-term]");
    if (btn) { e.preventDefault(); if (owner === btn) close(); else open(btn); return; }
    if (pop && !pop.contains(e.target)) close();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && owner) { var back = owner; close(); back.focus(); }
  });
  document.addEventListener("focusin", function (e) {
    if (owner && e.target !== owner && !(pop && pop.contains(e.target))) close();
  });
})();

// Help folds as popups (Ralf, 2026-10-21, concept „Hilfen als Popup“, variant C): "Warum?", "Wie gerechnet?",
// "So funktioniert es", "Was zählt hier?" (details.learn-why) and "Mehr dazu" (details.hint-more) no longer fold
// open. Their summary stays as a quiet link; a click moves the content into a popup under it, or into a side panel
// from the right when the popup would be taller than 60 % of the window (only the long method texts today); on a
// phone both come up as a sheet from below. The details stay in the page: without script they fold as before, and
// printing opens them. One help at a time; a click beside it, Esc or the link again close it and the content goes
// back into its fold (so "Nicht mehr zeigen" finds it there).
(function () {
  var SEL = "details.learn-why, details.hint-more";
  var box = null, fold = null, moved = [];
  document.querySelectorAll(SEL).forEach(function (d) { d.open = false; });  // "So funktioniert es" was open while empty
  function narrow() { return window.matchMedia("(max-width: 700px)").matches; }
  function close(back) {
    if (!box || !fold) return;
    moved.forEach(function (n) { fold.appendChild(n); });
    moved = [];
    box.hidden = true;
    var summary = fold.querySelector("summary");
    summary.setAttribute("aria-expanded", "false");
    fold = null;
    if (back) summary.focus();
  }
  function open(d) {
    if (!box) {
      box = document.createElement("div");
      box.id = "help-pop";
      box.setAttribute("role", "dialog");
      document.body.appendChild(box);
      box.addEventListener("click", function (e) {
        if (e.target.closest(".help-pop-close")) { e.preventDefault(); close(true); }
      });
    }
    var summary = d.querySelector("summary");
    box.textContent = "";
    var x = document.createElement("button");
    x.type = "button";
    x.className = "help-pop-close";
    x.setAttribute("aria-label", de_en("Schließen", "Close"));
    x.textContent = "×";
    var head = document.createElement("strong");
    head.className = "help-pop-head";
    head.textContent = summary.textContent.trim();
    box.append(x, head);
    moved = Array.prototype.filter.call(d.childNodes, function (n) { return n !== summary; });
    moved.forEach(function (n) { box.appendChild(n); });
    box.setAttribute("aria-label", head.textContent);
    fold = d;
    summary.setAttribute("aria-expanded", "true");
    box.className = "help-pop";
    box.style.left = box.style.top = box.style.width = "";
    box.hidden = false;
    if (narrow()) {
      box.className = "help-pop help-sheet";
    } else {
      var w = Math.min(440, document.documentElement.clientWidth - 32);
      box.style.width = w + "px";
      if (box.offsetHeight > window.innerHeight * 0.6) {
        box.className = "help-pop help-side";  // a long text: beside the page, which stays in sight
        box.style.width = "";
      } else {
        var r = summary.getBoundingClientRect();
        box.style.left = Math.max(16, Math.min(r.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - w - 16)) + "px";
        box.style.top = (r.bottom + window.scrollY + 6) + "px";
      }
    }
    x.focus({ preventScroll: true });  // into the help, on its close button
  }
  document.addEventListener("click", function (e) {
    var summary = e.target.closest && e.target.closest("summary");
    var d = summary && summary.parentElement;
    if (d && d.matches(SEL)) {
      e.preventDefault();
      if (fold === d) close(false); else { close(false); open(d); }
      return;
    }
    if (box && fold && !box.contains(e.target)) close(false);
  });
  // "Nicht mehr zeigen" looks for its fold: the content goes back first (capture, before that handler)
  document.addEventListener("click", function (e) {
    if (box && fold && e.target.closest && e.target.closest("[data-learn-hide]") && box.contains(e.target)) close(false);
  }, true);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && fold) close(true);
  });
  // a new width places it anew (a phone's address bar changes only the height)
  var width = window.innerWidth;
  window.addEventListener("resize", function () {
    if (window.innerWidth === width) return;
    width = window.innerWidth;
    if (fold) close(false);
  });
  // printing shows every help written out, as before
  var opened = [];
  window.addEventListener("beforeprint", function () {
    close(false);
    opened = Array.prototype.filter.call(document.querySelectorAll(SEL), function (d) { return !d.open; });
    opened.forEach(function (d) { d.open = true; });
  });
  window.addEventListener("afterprint", function () { opened.forEach(function (d) { d.open = false; }); opened = []; });
})();

// Tables that sort by any column in the browser (data-sort-table, e.g. the items of a construct by subscale; Ralf,
// 2026-10-07): a click on a head sorts, a second one reverses; equal cells keep the order of the questionnaire
// (data-order of the row), the column of ticks sorts by data-reversed.
document.querySelectorAll("[data-sort-table]").forEach(function (table) {
  var heads = Array.prototype.slice.call(table.querySelectorAll("thead [data-sort-col]"));
  var body = table.tBodies[0];
  heads.forEach(function (btn, col) {
    btn.addEventListener("click", function () {
      var th = btn.closest("th");
      var dir = th.getAttribute("aria-sort") === "ascending" ? -1 : 1;
      heads.forEach(function (b) {
        b.closest("th").removeAttribute("aria-sort");
        var mark = b.querySelector(".sort-arrow");
        if (mark) { mark.textContent = "↕"; mark.classList.add("is-off"); }
        b.classList.remove("is-active");
      });
      th.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending");
      var arrow = btn.querySelector(".sort-arrow");
      if (arrow) { arrow.textContent = dir > 0 ? "▲" : "▼"; arrow.classList.remove("is-off"); }
      btn.classList.add("is-active");
      var last = col === heads.length - 1 && body.rows[0] && body.rows[0].hasAttribute("data-reversed");
      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {
        var x = last ? a.dataset.reversed : (a.cells[col] ? a.cells[col].textContent.trim() : "");
        var y = last ? b.dataset.reversed : (b.cells[col] ? b.cells[col].textContent.trim() : "");
        var c = x.localeCompare(y, document.documentElement.lang || "de", { numeric: true, sensitivity: "base" });
        return c ? c * dir : (Number(a.dataset.order) - Number(b.dataset.order));
      });
      rows.forEach(function (r) { body.appendChild(r); });
    });
  });
});

// Languages (surveys/_fields_languages.html): the source language offers only the ticked languages; once the
// questionnaire has an element, choosing another one asks first, before the form saves (UI/UX audit 2026-10-05, V26).
(function () {
  document.addEventListener("change", function (e) {
    var box = e.target.closest && e.target.closest("fieldset#languages");
    if (!box || !e.target.matches('input[name="languages"]')) return;
    var pick = box.querySelector("[data-lang-default]");
    if (!pick) return;
    var ticked = {};
    box.querySelectorAll('input[name="languages"]:checked').forEach(function (c) { ticked[c.value] = true; });
    Array.prototype.forEach.call(pick.options, function (o) {
      var on = ticked[o.value] || o.selected;  // the source language stays until another one is chosen
      o.hidden = o.disabled = !on;
    });
  }, true);
  var asked = false;
  document.addEventListener("change", function (e) {
    var pick = e.target;
    if (!pick.matches || !pick.matches("select[data-lang-default][data-lang-confirm]") || asked) return;
    var before = pick.getAttribute("data-was") || pick.querySelector("option[selected]").value;
    if (pick.value === before) return;
    e.stopPropagation();  // the form saves only after the answer
    var name = pick.options[pick.selectedIndex].text;
    askConfirm({ title: de_en("Ausgangssprache wechseln?", "Change the source language?"), ok: de_en("In " + name + " bearbeiten", "Edit in " + name),
                 note: de_en("Der Fragebogen wird dann in " + name + " bearbeitet; die anderen Sprachen gelten als Übersetzungen davon.",
                             "The questionnaire is then edited in " + name + "; the other languages count as translations of it.") }).then(function (yes) {
      if (!yes) { pick.value = before; return; }
      pick.setAttribute("data-was", pick.value);
      asked = true;
      pick.dispatchEvent(new Event("change", { bubbles: true }));
      asked = false;
    });
  }, true);
})();

// Completion links of the settings (surveys/detail.html, group „Teilnahmelinks und Panels“): a line only for an outcome
// with a link; "+ Abschlusslink" shows the line of the outcome picked next to it, and the kind of forwarding shows
// once the line has a link (UI/UX audit 2026-10-05, V20).
(function () {
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-link-add]");
    if (!btn) return;
    var box = btn.closest("[data-link-rows]"), pick = box.querySelector("[data-link-pick]");
    var row = pick && pick.value ? box.querySelector('[data-link-row="' + pick.value + '"]') : null;
    if (!row) return;
    row.hidden = false;
    pick.querySelector('option[value="' + pick.value + '"]').remove();
    if (!pick.options.length) btn.closest(".link-add").hidden = true;
    row.querySelector("[data-link-field]").focus();
  });
  document.addEventListener("input", function (e) {
    if (!e.target.matches || !e.target.matches("[data-link-row] [data-link-field]")) return;
    var mode = e.target.closest("[data-link-row]").querySelector(".link-mode");
    if (mode) mode.hidden = !e.target.value.trim();
  });
})();

// Live vote, editing page (live/manage.html): drag a question or a page break by its handle to another place, also
// into another block (mouse and finger); saved with the editor's place_item (undo), then the page is loaded anew. After a
// save in the embedded item editor (it sends "live-item-saved") the list on the left is loaded anew.
(function () {
  var left = document.querySelector("[data-live-left]");
  if (!left) return;
  // a page in the editor frame that is not the embedded editor (after Rückgängig, the AI text check or a link):
  // it comes with its own header and footer, so it opens as the whole page instead of inside the column
  // the quiz box of a question saves by itself (control, action quiz_set); its title says what is set
  // (the same words as the bar of the other forms that save by themselves); Enter in a field saves, it does not
  // send the page
  document.querySelectorAll("[data-live-quiz]").forEach(function (quizForm) {
    var status = quizForm.querySelector("[data-live-quiz-status]");
    // the state goes to the head of the page (S2, saveState); the box's own line only shows a failure
    function say(text, kind) {
      status.textContent = text; status.className = "autosave-status flush is-" + kind;
      status.hidden = saveState(quizForm, kind, text, status) && kind !== "error";
    }
    status.hidden = saveState(quizForm, "idle", "", status);
    // one request at a time, as the shared autosave does: a change made while one is under way is sent after it,
    // with the form as it is then; two at once could arrive in the wrong order and keep the older state (F8)
    var saving = false, again = false;
    function send() {
      if (saving) { again = true; return; }
      saving = true;
      say(de_en("Speichert …", "Saving …"), "busy");
      fetch(quizForm.getAttribute("action"), { method: "POST", body: new FormData(quizForm), credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          if (!again) say(de_en("Gespeichert " + saveClock(), "Saved " + saveClock()), "ok");
        })
        .catch(function () { say(de_en("Nicht gespeichert: keine Verbindung zum Server, oder die Anmeldung ist abgelaufen.",
                                       "Not saved: no connection to the server, or the sign-in has expired."), "error"); })
        .then(function () {
          saving = false;
          if (again) { again = false; send(); }
        });
    }
    quizForm.addEventListener("change", send);
    quizForm.addEventListener("submit", function (e) { e.preventDefault(); send(); });
  });
  var frame = document.querySelector("[data-live-editor]");
  if (frame) frame.addEventListener("load", function () {
    try {
      var url = new URL(frame.contentWindow.location.href);
      if (url.searchParams.get("embed") === "1") return;
      var item = url.pathname.match(/\/editor\/items\/(\d+)/);  // the full editor of a question: this page with it beside the list
      if (url.pathname === location.pathname) {  // this page itself (after Rückgängig): keep the question chosen, once;
        var last = 0;                            // a second time within seconds (the question is gone): without it
        try { last = +sessionStorage.getItem("live-frame-reload") || 0; sessionStorage.setItem("live-frame-reload", String(Date.now())); } catch (e) { /* no storage */ }
        if (Date.now() - last > 5000) location.reload(); else location.href = location.pathname;
      } else location.href = item ? location.pathname + "?item=" + item[1] : url.href;
    } catch (e) { /* another origin: leave it */ }
  });
  var token = document.querySelector("input[name=csrf_token]");
  var target = null, where = null;
  function clear() {
    document.querySelectorAll(".drop-before, .drop-after, .drop-into, .drop-onto").forEach(function (el) { el.classList.remove("drop-before", "drop-after", "drop-into", "drop-onto"); });
  }
  // a Leitfrage of an interview guide onto the middle half of another one (or of an Unterfrage of another one):
  // it becomes an Unterfrage there (data-onto, interview/guide.html); the outer quarters still place before or after
  function onto(row, dragged, y, box) {
    return dragged.hasAttribute("data-onto") && row.hasAttribute("data-onto") && row.dataset.ontoMain !== dragged.dataset.onto
      && y > box.top + box.height / 4 && y < box.bottom - box.height / 4;
  }
  function aim(x, y, dragged) {
    clear();
    var el = document.elementFromPoint(x, y);
    var row = el && el.closest("[data-live-row]");
    if (row && row !== dragged) {
      var box = row.getBoundingClientRect();
      if (onto(row, dragged, y, box)) {
        row.setAttribute("data-drop-label", de_en("als Unterfrage einfügen", "insert as a sub-question"));
        row.classList.add("drop-onto");
        target = row; where = "onto";
        return;
      }
      where = y < box.top + box.height / 2 ? "before" : "after";
      row.classList.add("drop-" + where);
      target = row;
      return;
    }
    var section = el && el.closest("[data-live-section]");
    if (section && !row) { section.classList.add("drop-into"); target = section; where = "into"; return; }
    target = null;
  }
  function drop(row) {
    if (!target) return;
    var section = target.closest("[data-live-section]");
    var others = Array.prototype.filter.call(section.querySelectorAll("[data-live-row]"), function (r) { return r !== row; });
    var pos = where === "into" ? others.length : others.indexOf(target) + (where === "after" ? 1 : 0);
    var body = new FormData();
    body.append("csrf_token", token ? token.value : "");
    body.append("block_id", section.dataset.liveSection);
    body.append("position", String(where === "onto" ? 0 : pos));
    if (where === "onto") body.append("onto", target.dataset.onto);
    fetch(row.dataset.place, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { if (r.ok) location.reload(); else actionFailed(r.status); })
      .catch(function () { actionFailed(0); });
  }
  // the shared dragging (pointerDrag): mouse, pen and finger, Escape or a cancelled pointer end without a drop.
  // A slide is taken anywhere on its row, not only at ⋮⋮ (a click without moving still opens it), and the slide
  // itself follows the pointer as a card (Ralf, 2026-10-16)
  pointerDrag({
    source: function (el) {
      var row = el.closest && !el.closest("button, input, select, textarea, form") && el.closest("[data-live-row]");
      return row && left.contains(row) ? { el: row } : null;
    },
    start: function (d) {
      target = null;
      d.ghost = d.el.cloneNode(true);
      d.ghost.removeAttribute("id");
      d.ghost.classList.add("live-item-ghost");
      d.ghost.style.width = d.el.getBoundingClientRect().width + "px";
      d.el.classList.add("is-dragging");
    },
    move: function (d) { aim(d.x, d.y, d.el); },
    drop: function (d) { var row = d.el; clear(); drop(row); },
    end: function (d) { d.el.classList.remove("is-dragging"); clear(); },
  });
  window.addEventListener("message", function (e) {
    if (e.origin !== location.origin || e.data !== "live-item-saved") return;
    fetch(location.href, { credentials: "same-origin" }).then(function (r) { return r.text(); }).then(function (html) {
      var fresh = new DOMParser().parseFromString(html, "text/html").querySelector("[data-live-left]");
      if (fresh) left.innerHTML = fresh.innerHTML;  // its data-submit-on-change selects work on (on the document)
    }).catch(function () { /* the old list stays until the page loads again */ });
  });
})();

// A question just added (?neu=1, the live vote's editor column): the cursor in its question text, ready to type
(function () {
  if (new URLSearchParams(location.search).get("neu") !== "1") return;
  setTimeout(function () {  // after richtext.js has put its field in place of the textarea
    var area = document.querySelector('textarea[name="question"]');
    if (!area) return;
    var field = area.parentNode && area.parentNode.querySelector('[contenteditable="true"]');
    (field || area).focus();
  }, 60);
})();

// short references in APA style (templates/_sources.html): a click opens the folded reference list and shows the entry
document.addEventListener("click", function (e) {
  var a = e.target.closest && e.target.closest('a.cite[href^="#ref-"]');
  if (!a) return;
  var target = document.getElementById(a.getAttribute("href").slice(1));
  if (!target) return;
  for (var fold = target.closest("details"); fold; fold = fold.parentElement && fold.parentElement.closest("details")) fold.open = true;
});

// short references written into a text by hand ("Kish, 1965", "Orme (2010", "Lakens und Caldwell (2021)"): linked
// to their entry when the page's reference list has exactly one work of that first author and year (app/apa.py: the
// anchor is ref-<first author><year>-<first word of the title>)
(function () {
  var entries = document.querySelectorAll('[id^="ref-"]');
  if (!entries.length) return;
  var byKey = {};
  entries.forEach(function (li) {
    var key = li.id.slice(4).replace(/-.*$/, "");
    byKey[key] = byKey[key] === undefined ? li.id : null;  // two works with the same author and year: not linked
  });
  var plain = function (s) {
    return s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/ß/g, "ss").replace(/[^a-z0-9]/g, "");
  };
  var pattern = /([A-ZÄÖÜ][\p{L}\u0027’-]+)(?: et al\.| (?:&|und) [A-ZÄÖÜ][\p{L}\u0027’-]+)?(?:, | \()((?:19|20)\d\d[a-z]?)(?=[\s,;:)])/gu;
  var walker = document.createTreeWalker(document.querySelector("main") || document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: function (node) {
      var p = node.parentElement;
      if (!p || p.closest("a, script, style, textarea, code, ul.refs, .learn-sources, [contenteditable]")) return NodeFilter.FILTER_REJECT;
      pattern.lastIndex = 0;
      return pattern.test(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }
  });
  var nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(function (node) {
    var text = node.nodeValue, at = 0, frag = document.createDocumentFragment(), linked = false, m;
    pattern.lastIndex = 0;
    while ((m = pattern.exec(text))) {
      var id = byKey[plain(m[1]) + m[2]];
      if (!id) continue;
      frag.appendChild(document.createTextNode(text.slice(at, m.index)));
      var a = document.createElement("a");
      a.className = "cite";
      a.href = "#" + id;
      a.textContent = m[0];
      frag.appendChild(a);
      at = m.index + m[0].length;
      linked = true;
    }
    if (!linked) return;
    frag.appendChild(document.createTextNode(text.slice(at)));
    node.parentNode.replaceChild(frag, node);
  });
})();

// A form whose request takes long (data-busy-form, e.g. the upload to Zenodo): on sending, the buttons are
// disabled and a running progress bar with a note appears (data-busy-note); back in the history it is idle again.
(function () {
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("[data-busy-form]") || e.defaultPrevented) return;
    var note = form.querySelector("[data-busy-note]");
    if (note) note.hidden = false;
    setTimeout(function () {  // after the submit took the clicked button's name and value
      form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    }, 0);
  });
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("[data-busy-form]").forEach(function (form) {
      var note = form.querySelector("[data-busy-note]");
      if (note) note.hidden = true;
      form.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
    });
  });
})();

// Long lists (_listing.html): the search runs while typing, after a short pause, as in the lists of the editor
// palette. The page loads anew with the result; the cursor is back at the end of the search field.
(function () {
  var KEY = "list-search-focus";
  document.querySelectorAll("form[data-live-search]").forEach(function (form) {
    var field = form.querySelector("input[type=search]"), button = form.querySelector("[data-live-search-button]");
    if (!field) return;
    if (button) button.hidden = true;
    var timer = null, last = field.value;
    try {
      if (sessionStorage.getItem(KEY) === field.name) {
        sessionStorage.removeItem(KEY);
        field.focus();
        field.setSelectionRange(field.value.length, field.value.length);
      }
    } catch (e) { /* no storage: the cursor stays where the browser puts it */ }
    field.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (field.value.trim() === last.trim()) return;
        try { sessionStorage.setItem(KEY, field.name); } catch (e) { /* see above */ }
        form.submit();
      }, 500);
    });
  });
})();

// Interview guide (interview/guide.html): a Themenblock dragged by its handle before or after another one
// (interview.control, action "place"); ↑ ↓ stay for the keyboard
(function () {
  if (!document.querySelector(".guide-block-drag")) return;
  var token = document.querySelector("input[name=csrf_token]"), target = null, mode = null;
  function clear() { document.querySelectorAll(".guide-block.drop-before, .guide-block.drop-after").forEach(function (s) { s.classList.remove("drop-before", "drop-after"); }); }
  pointerDrag({
    source: function (el) {
      var handle = el.closest && el.closest(".guide-block-drag");
      return handle ? { el: handle.closest(".guide-block"), url: handle.dataset.placeBlock, block: handle.dataset.block } : null;
    },
    start: function (d) { target = null; d.el.classList.add("is-dragging"); },
    move: function (d) {
      clear();
      var over = document.elementFromPoint(d.x, d.y), section = over && over.closest(".guide-part-guide");
      if (!section || section === d.el) { target = null; return; }
      var box = section.getBoundingClientRect();
      mode = d.y < box.top + box.height / 2 ? "before" : "after";
      section.classList.add("drop-" + mode);
      target = section;
    },
    drop: function (d) {
      if (!target) return;
      var body = new FormData();
      body.append("csrf_token", token ? token.value : "");
      body.append("action", "place");
      body.append("block", d.block);
      body.append("target", target.dataset.liveSection);
      body.append("mode", mode);
      fetch(d.url, { method: "POST", body: body, credentials: "same-origin" })
        .then(function (r) { if (r.ok) location.reload(); else actionFailed(r.status); })
        .catch(function () { actionFailed(0); });
    },
    end: function (d) { d.el.classList.remove("is-dragging"); clear(); },
  });
})();

// Deleting an account (accounts/delete.html): the red button works only once the name of the account is typed,
// case and spaces aside, as the server compares it; without the script the server alone checks (UI audit W85)
document.querySelectorAll("[data-match-name]").forEach(function (input) {
  var form = input.form, button = form && form.querySelector("[data-match-button]"), hint = form && form.querySelector("[data-match-hint]");
  if (!button) return;
  var norm = function (v) { return v.split(/\s+/).filter(Boolean).join(" ").toLowerCase(); };
  function check() {
    var ok = norm(input.value) === norm(input.dataset.matchName);
    button.disabled = !ok;
    if (hint) hint.hidden = ok;
  }
  input.addEventListener("input", check);
  check();
});

// "Meine Befragungen": search and sort in the browser, shown once the cards reach below the window or a search is
// typed (Ralf, 2026-10-05); pinned cards stay first; the sort is remembered in this browser
(function () {
  var find = document.querySelector("[data-dash-find]");
  var list = document.querySelector("[data-dash-list]");  // one's own list, not the section of example surveys
  if (!find || !list) return;
  var search = find.querySelector("[data-dash-search]"), sort = find.querySelector("[data-dash-sort]");
  var none = document.querySelector("[data-dash-none]"), count = document.querySelector("[data-dash-count]");
  var cards = Array.prototype.slice.call(list.children);
  cards.forEach(function (c, i) { c.dataset.order = i; });
  function needed() {
    if (!find.hidden) return;
    if (list.getBoundingClientRect().bottom > window.innerHeight) find.hidden = false;
  }
  function apply() {
    var q = search.value.trim().toLowerCase(), key = sort.value, shown = 0;
    cards.forEach(function (c) {
      // title, project, code, people and state only, never the labels of the buttons (UI audit W76)
      var hit = !q || (c.dataset.search || "").indexOf(q) !== -1;
      c.hidden = !hit;
      if (hit) shown++;
    });
    if (none) none.hidden = shown > 0;
    if (count) count.textContent = !q ? "" : shown === 1 ? de_en("1 Befragung gefunden", "1 survey found")
      : de_en("{n} Befragungen gefunden", "{n} surveys found").replace("{n}", shown);
    var sorted = cards.slice().sort(function (a, b) {
      var p = (+b.dataset.pinned) - (+a.dataset.pinned);
      if (p) return p;
      if (key === "title") return a.dataset.title.localeCompare(b.dataset.title, "de");
      if (key === "created") return (b.dataset.created || "").localeCompare(a.dataset.created || "");
      if (key === "done") return (+b.dataset.done) - (+a.dataset.done) || (+a.dataset.order) - (+b.dataset.order);
      return (+a.dataset.order) - (+b.dataset.order);
    });
    sorted.forEach(function (c) { list.appendChild(c); });
  }
  try { var kept = localStorage.getItem("dash-sort"); if (kept && sort.querySelector('option[value="' + kept + '"]')) { sort.value = kept; find.hidden = false; } } catch (e) {}
  search.addEventListener("input", apply);
  search.addEventListener("keydown", function (e) { if (e.key === "Escape") { search.value = ""; apply(); } });
  sort.addEventListener("change", function () { try { localStorage.setItem("dash-sort", sort.value); } catch (e) {} apply(); });
  window.addEventListener("resize", needed);
  needed();
  apply();
})();

// "Meine Befragungen": the section of example surveys stays as one left it, open or folded, in this browser; without
// a choice the server decides (open while one has no survey of one's own; demo-befragungen-konzept.md, 2b)
(function () {
  var section = document.querySelector("[data-dash-examples]");
  if (!section) return;
  try { var kept = localStorage.getItem("dash-examples"); if (kept) section.open = kept === "open"; } catch (e) {}
  section.addEventListener("toggle", function () {
    try { localStorage.setItem("dash-examples", section.open ? "open" : "closed"); } catch (e) { /* this visit only */ }
  });
})();

// Images on comments from test runs (editor/comments.html): a click shows the image over the page, in the dialog of
// the screenshots on the start page (.shot-view); a click anywhere, Esc or × closes it. Without the script the link
// opens the image itself.
(function () {
  var links = document.querySelectorAll("[data-comment-shot]");
  if (!links.length || !window.HTMLDialogElement) return;
  var view = document.createElement("dialog"), from = null;
  view.className = "shot-view";
  view.setAttribute("aria-label", de_en("Bild zum Kommentar", "Image of the comment"));
  view.innerHTML = '<button type="button" class="shot-view-close" aria-label="' + de_en("Schließen", "Close") + '">×</button><img alt=""><p></p>';
  document.body.appendChild(view);
  links.forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      from = a;
      view.querySelector("img").src = a.href;
      view.querySelector("img").alt = (a.querySelector("img") || {}).alt || "";
      view.querySelector("p").textContent = a.dataset.caption || "";
      view.showModal();
    });
  });
  view.addEventListener("click", function () { view.close(); });
  view.addEventListener("close", function () { if (from) from.focus(); });
})();

// End texts in the settings (surveys/detail.html, V88): the standard text stands as text; "Anpassen" puts it into
// the field (formatted, richtext.js) so one word can be changed, "Standard wiederherstellen" empties the field and
// shows the standard text again. The autosave of the form saves either (the page loads anew after a restore, the
// formatting field has no way back to empty).
document.addEventListener("click", function (e) {
  var adapt = e.target.closest("[data-end-adapt]"), restore = e.target.closest("[data-end-restore]");
  if (!adapt && !restore) return;
  var box = e.target.closest("[data-end-text]"), area = box.querySelector("textarea");
  var dflt = box.querySelector("[data-end-default]"), own = box.querySelector("[data-end-own]"), back = box.querySelector("[data-end-restore]");
  if (adapt) {
    area.value = area.getAttribute("data-standard") || "";
    area.setAttribute("data-rich", "block");
    dflt.hidden = true; own.hidden = false; if (back) back.hidden = false;
    if (window.QRichText && QRichText.attach) QRichText.attach(area);
    var ed = own.querySelector("[contenteditable]");
    (ed || area).focus();
    return;
  }
  area.value = "";
  area.setAttribute("data-reload-saved", "");
  own.hidden = true; dflt.hidden = false; restore.hidden = true;
  area.dispatchEvent(new Event("change", { bubbles: true }));
});

// "Jetzt prüfen" of the seal chain (tab "Export"): the block under the button opens at once and shows the result
// (surveys.seal_check renders it); without script the form is sent as before
(function () {
  document.querySelectorAll("[data-seal-check]").forEach(function (form) {
    var box = form.parentNode.querySelector("[data-seal-result]");
    if (!box || !window.fetch) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var button = form.querySelector("button");
      box.hidden = false;
      box.classList.add("is-busy");
      box.textContent = de_en("Prüfe Kette, Zeitstempel und Teilnahmen …", "Checking chain, timestamps and participations …");
      if (button) button.disabled = true;
      fetch(form.action, {method: "POST", body: new FormData(form), credentials: "same-origin",
                          headers: {"X-Requested-With": "fetch"}})
        // an answer of the check itself (X-Seal-Result: the result, nothing to check, checked too often, an error on
        // the server) is shown as it is; anything else (signed out, no answer) gets its own sentence (pre-test 2026-10-20, 30)
        .then(function (r) {
          if (r.headers.get("X-Seal-Result")) return r.text();
          var err = new Error(r.status); err.status = r.status; throw err;
        })
        .then(function (html) { box.classList.remove("is-busy"); box.innerHTML = html; })
        .catch(function (err) {
          box.classList.remove("is-busy");
          box.textContent = err && err.status
            ? de_en("Die Prüfung ist fehlgeschlagen (Antwort {s}). Bitte die Seite neu laden, ggf. neu anmelden, und noch einmal versuchen.",
                    "The check failed (answer {s}). Please reload the page, sign in again if needed, and try again.").replace("{s}", err.status)
            : de_en("Der Server hat keine Antwort gegeben (Verbindung unterbrochen). Bitte die Seite neu laden und noch einmal versuchen.",
                    "The server gave no answer (connection lost). Please reload the page and try again.");
        })
        .then(function () { if (button) button.disabled = false; });
    });
  });
})();

// "Kategorisieren" (templates/surveys/coding.html, coding_second.html; app/coding.py): a tick saves its row at once
// (all participations with that text), the keys 1 to 9 tick the categories of the row in focus, the search and the
// filters ("nur noch offene", "nur „neu prüfen“") work here on the page. Without the script every row has its own button.
(function () {
  document.querySelectorAll("[data-coding-split-pick]").forEach(function (pick) {
    // "Aufteilen": one form per category, the list in each of them shows the form of the category picked
    pick.addEventListener("change", function () {
      var shown = null;
      document.querySelectorAll("[data-coding-split]").forEach(function (f) {
        f.hidden = f.dataset.codingSplit !== pick.value;
        if (!f.hidden) shown = f;
      });
      if (shown) { var p = shown.querySelector("[data-coding-split-pick]"); p.value = pick.value; p.focus(); }
    });
  });
  document.querySelectorAll("[data-coding]").forEach(function (box) {
    var rows = Array.prototype.slice.call(box.querySelectorAll("[data-coding-row]"));
    if (!rows.length) return;
    var search = box.querySelector("[data-coding-search]"), onlyOpen = box.querySelector("[data-coding-open]");
    var onlyRecheck = box.querySelector("[data-coding-recheck]"), progress = box.querySelector("[data-coding-progress]");
    function isOpen(row) { return !row.querySelector("input[name=cat]:checked:not([value=''])"); }
    function count() {
      if (!progress) return;
      var k = rows.filter(function (r) { return !isOpen(r); }).length;
      progress.textContent = de_en(k + " von " + rows.length + " kategorisiert", k + " of " + rows.length + " categorised");
    }
    function filter() {
      var q = search ? search.value.trim().toLowerCase() : "";
      rows.forEach(function (r) {
        r.hidden = (q && r.dataset.text.indexOf(q) < 0) || (onlyOpen && onlyOpen.checked && !r.classList.contains("is-open")) ||
                   (onlyRecheck && onlyRecheck.checked && !r.classList.contains("is-recheck"));
      });
    }
    // one save per row at a time: a tick while the last one is under way is sent after it, with the row as it is
    // then (two saves of the same participations at once would meet on the same rows, and the second be refused)
    function save(row) {
      if (row.saving) { row.again = true; return; }
      row.saving = true;
      var form = row.querySelector("form");
      var done = function () { row.saving = false; if (row.again) { row.again = false; save(row); } };
      row.classList.add("is-saving");
      fetch(form.action, { method: "POST", body: new FormData(form), headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (res) {
          row.classList.remove("is-saving");
          if (!res.ok || res.data.status !== "saved") { row.classList.add("is-error"); say(res.data.message || de_en("Nicht gespeichert", "Not saved")); done(); return; }
          row.classList.remove("is-error", "is-recheck");
          row.querySelectorAll(".is-suggested").forEach(function (c) { c.classList.remove("is-suggested"); });
          var hint = row.querySelector(".coding-ai");
          if (hint) hint.remove();
          row.classList.toggle("is-open", isOpen(row));
          count();
          done();
        })
        .catch(function () { row.classList.remove("is-saving"); row.classList.add("is-error"); say(de_en("Nicht gespeichert", "Not saved")); done(); });
    }
    rows.forEach(function (row) {
      row.addEventListener("change", function (e) { if (e.target.name === "cat") save(row); });
      row.addEventListener("keydown", function (e) {
        if (e.target.matches("input[type=search], input[type=text], textarea") || e.ctrlKey || e.metaKey || e.altKey) return;
        var n = parseInt(e.key, 10);
        if (!(n >= 1 && n <= 9)) return;
        var input = row.querySelectorAll("input[name=cat]:not([value=''])")[n - 1];
        if (!input) return;
        e.preventDefault();
        input.checked = input.type === "radio" ? true : !input.checked;
        save(row);
      });
    });
    if (search) search.addEventListener("input", filter);
    if (onlyOpen) onlyOpen.addEventListener("change", filter);
    if (onlyRecheck) onlyRecheck.addEventListener("change", filter);
  });
})();
// AI calls without a banner (app/ai_fetch.py, Ralf 2026-10-10). aiFetch(url, init) sends a request with the header
// X-AI-Call; when the API reports its limit (429), a dialog says so and offers another model of the own access (or
// the platform's) with "Erneut versuchen", which sends the same request again, or "Abbrechen" (then the answer
// reads {cancelled: true, error: "Abgebrochen."}, status 409, so that the callers stop as after an error).
// Forms with an AI button (.button-ai, data-ai, data-ai-call) go through it as well: an error shows at the button,
// the page and what was typed stay; else the page comes back as after an ordinary post.
(function () {
  if (!window.fetch) return;
  var dialog = null;
  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog ai-limit-dialog";
    dialog.setAttribute("aria-labelledby", "ai-limit-title");
    dialog.setAttribute("aria-describedby", "ai-limit-text");
    dialog.innerHTML = '<h2 id="ai-limit-title"></h2><p id="ai-limit-text"></p>' +
      '<label class="ai-limit-model" hidden><span class="label-title">' + de_en("Modell", "Model") + '</span><select data-model></select></label>' +
      '<p class="ai-error" data-fail role="alert" hidden></p>' +
      '<div class="confirm-actions"><button type="button" class="button button-secondary" data-no>' + de_en("Abbrechen", "Cancel") + '</button>' +
      '<button type="button" class="button button-ai" data-yes>' + de_en("Erneut versuchen", "Try again") + '</button></div>';
    document.body.appendChild(dialog);
  }
  // info: the JSON of app/ai_fetch.py (status "limit"); resolves true for "Erneut versuchen"
  window.aiLimitDialog = function (info) {
    if (!window.HTMLDialogElement) return Promise.resolve(false);
    if (!dialog) build();
    var select = dialog.querySelector("[data-model]"), field = dialog.querySelector(".ai-limit-model");
    var fail = dialog.querySelector("[data-fail]"), yes = dialog.querySelector("[data-yes]"), no = dialog.querySelector("[data-no]");
    dialog.querySelector("#ai-limit-title").textContent = info.code === "quota" ? de_en("Kontingent erschöpft", "Quota used up")
      : de_en("Zu viele Anfragen", "Too many requests");
    dialog.querySelector("#ai-limit-text").textContent = info.message || "";
    select.innerHTML = "";
    (info.models || []).forEach(function (m) {
      var o = document.createElement("option");
      o.value = m;
      o.textContent = m + (m === info.current ? de_en(" – Limit erreicht", " – limit reached") : "");
      select.appendChild(o);
    });
    if (info.platform) {
      var p = document.createElement("option");
      p.value = "";
      p.textContent = de_en("Zugang der Plattform (OpenAI)", "The platform's access (OpenAI)");
      select.appendChild(p);
    }
    select.value = info.current || "";
    field.hidden = !info.own || select.options.length < 2;
    fail.hidden = true;
    yes.disabled = false;
    return new Promise(function (resolve) {
      function done(again) {
        yes.onclick = no.onclick = dialog.oncancel = null;
        dialog.close();
        resolve(again);
      }
      no.onclick = function () { done(false); };
      dialog.oncancel = function (e) { e.preventDefault(); done(false); };  // Escape
      yes.onclick = function () {
        if (field.hidden || select.value === (info.current || "")) { done(true); return; }
        // the chosen model is saved as the access's model (the same route as the choice on "Konto")
        var body = new FormData();
        body.append("csrf_token", csrfToken()); body.append("model", select.value); body.append("autosave", "1");
        yes.disabled = true;
        fetch(info.model_url, { method: "POST", body: body, credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.status === "saved") { done(true); return; }
            fail.textContent = d.message || de_en("Das Modell wurde nicht gespeichert.", "The model was not saved.");
            fail.hidden = false; yes.disabled = false;
          })
          .catch(function () {
            fail.textContent = de_en("Keine Verbindung. Bitte erneut versuchen.", "No connection. Please try again.");
            fail.hidden = false; yes.disabled = false;
          });
      };
      dialog.showModal();
      (field.hidden ? yes : select).focus();
    });
  };
  window.aiFetch = function (url, init) {
    init = init || {};
    var headers = new Headers(init.headers || {});
    headers.set("X-AI-Call", "1");
    var opts = {};
    Object.keys(init).forEach(function (k) { opts[k] = init[k]; });
    opts.headers = headers;
    if (!opts.credentials) opts.credentials = "same-origin";
    return fetch(url, opts).then(function (r) {
      if ((r.headers.get("Content-Type") || "").indexOf("application/json") < 0) return r;
      return r.clone().json().then(function (d) {
        if (!d || d.status !== "limit") return r;
        return window.aiLimitDialog(d).then(function (again) {
          if (again) return window.aiFetch(url, init);
          return new Response(JSON.stringify({ cancelled: true, error: de_en("Abgebrochen.", "Cancelled.") }),
                              { status: 409, headers: { "Content-Type": "application/json" } });
        });
      }, function () { return r; });
    });
  };

  // the address of a redirect after an AI call; the same page with only another #anchor (e.g. the consent text,
  // "…/items/1?lang=de#body") would merely jump to the anchor and leave the "being written" box standing: loaded anew
  window.aiGo = function (url, loc) {
    loc = loc || location;
    var to = new URL(url, loc.href);
    if (to.href.split("#")[0] !== loc.href.split("#")[0]) { loc.assign(to.href); return "assign"; }
    if (to.hash && to.hash !== loc.hash) history.replaceState(null, "", to.href);
    loc.reload();
    return "reload";
  };
  var AI = ".button-ai, [data-ai], [data-ai-call]";
  // the sentence of a failure, under the row of the button that was clicked
  function showError(by, form, text, accessUrl) {
    document.querySelectorAll("[data-ai-error]").forEach(function (el) { el.remove(); });
    var p = document.createElement("p");
    p.className = "ai-error small";
    p.setAttribute("role", "alert");
    p.setAttribute("data-ai-error", "");
    p.textContent = text;
    if (accessUrl) {
      var a = document.createElement("a");
      a.href = accessUrl;
      a.textContent = de_en("KI-Zugang öffnen", "Open AI access");
      p.appendChild(document.createTextNode(" "));
      p.appendChild(a);
    }
    var host = by.closest(".form-actions, .row, .toolbar, .wizard-nav, .button-row, .row-fields, .export-row-buttons, " +
                          ".ainstein-scale-actions, .ainstein-step-nav, .mgen-langs, .page-head-tools") || by;
    host.insertAdjacentElement("afterend", p);
  }
  document.addEventListener("submit", function (e) {
    var form = e.target, by = e.submitter;
    if (e.defaultPrevented || !by || !by.matches(AI) || (form.getAttribute("method") || "").toLowerCase() !== "post") return;
    if (form.target && form.target !== "_self") return;
    e.preventDefault();
    e.aiCall = true;  // for the scripts that look later whether the form was sent (ainstein.js)
    var data = new FormData(form);
    if (by.name) data.append(by.name, by.value);
    var label = by.innerHTML;
    var buttons = Array.prototype.map.call(form.querySelectorAll("button"), function (b) { return [b, b.disabled]; });
    buttons.push([by, by.disabled]);
    function restore() {
      by.innerHTML = label;
      buttons.forEach(function (s) { s[0].disabled = s[1]; });
      document.querySelectorAll(".ai-writing:not([hidden]), [data-ai-writing-box]:not([hidden])").forEach(function (box) { box.hidden = true; });
      if (by.dataset.aiHide) { var old = document.querySelector(by.dataset.aiHide); if (old) old.hidden = false; }
      form.querySelectorAll("[data-busy-note]").forEach(function (n) { n.hidden = true; });
      document.querySelectorAll(".is-ai-working").forEach(function (a) { a.classList.remove("is-ai-working"); a.removeAttribute("aria-busy"); });
    }
    window.aiFetch(by.getAttribute("formaction") || form.action, { method: "POST", body: data })
      .then(function (r) {
        if ((r.headers.get("Content-Type") || "").indexOf("application/json") < 0) {
          return r.text().then(function (html) {  // the page the route drew: as after an ordinary post
            if (r.url && r.url !== location.href) history.replaceState(null, "", r.url);
            document.open(); document.write(html); document.close();
          });
        }
        return r.json().then(function (d) {
          if (d.status === "redirect") { window.aiGo(d.url); return; }
          restore();
          if (d.cancelled) return;
          if (d.status === "error" || d.error) { showError(by, form, d.message || d.error, d.access_url); return; }
          location.reload();
        });
      })
      .catch(function () {
        restore();
        showError(by, form, de_en("Keine Verbindung zum Server. Bitte erneut versuchen.", "No connection to the server. Please try again."));
      });
  });
})();
// A part of a page that moved to another page (span[data-moved-anchor]): an old link to its anchor goes on there,
// e.g. "Konto#ki-zugang" to the page "KI-Nutzung" (2026-10-10)
(function () {
  document.querySelectorAll("[data-moved-anchor]").forEach(function (el) {
    if (location.hash === "#" + el.getAttribute("data-moved-anchor")) location.replace(el.getAttribute("data-moved-to"));
  });
})();

// The tab "Zeitplan" of a diary study (diary/schedule.html): each module shows only the fields of its trigger (fixed
// times, random, not timed: the button in the app). The page hides the rest for the stored trigger; this follows every
// change of the select. Hidden fields keep their values and are sent with the form (Philipp, 2026-10-12). The same for
// the period: days or no end.
(function () {
  document.querySelectorAll("[data-trigger-select]").forEach(function (select) {
    var box = select.closest(".diary-module");
    if (!box) return;
    function show() {
      box.querySelectorAll("[data-trigger-for]").forEach(function (el) {
        el.hidden = el.getAttribute("data-trigger-for").split(" ").indexOf(select.value) < 0;
      });
    }
    select.addEventListener("change", show);
  });
  // how people join (diary/_join_mode.html): the settings of the personal codes only with them
  document.querySelectorAll("form [data-join-for]").forEach(function (box) {
    var form = box.closest("form");
    form.addEventListener("change", function (event) {
      if (event.target.name !== "join") return;
      box.hidden = box.getAttribute("data-join-for") !== event.target.value;
    });
  });
  document.querySelectorAll("[data-period-select]").forEach(function (select) {
    var form = select.form;
    if (!form) return;
    select.addEventListener("change", function () {
      form.querySelectorAll("[data-period-for]").forEach(function (el) {
        el.hidden = el.getAttribute("data-period-for") !== select.value;
      });
    });
  });
})();

// Personal code (items/personal_code/editor.html): how likely participants share a code, live for the ticked parts
// and a sample size on a logarithmic slider (10 to 50,000). Same estimate as collision() in
// app/items/personal_code.py: codes grouped by their probability in bins of 1/100 of a decade, the parts independent.
function sgicCollision(partShares, n) {
  var bins = {0: [1, 1]};
  partShares.forEach(function (shares) {
    var out = {};
    Object.keys(bins).forEach(function (k) {
      var c = bins[k][0], m = bins[k][1], p = m / c;
      shares.forEach(function (q) {
        var b = Math.round(Math.log10(p * q) * 100), o = out[b] || (out[b] = [0, 0]);
        o[0] += c; o[1] += m * q;
      });
    });
    bins = out;
  });
  var q2 = 0, shared = 0;
  Object.keys(bins).forEach(function (k) {
    var c = bins[k][0], m = bins[k][1];
    q2 += m * m / c;
    if (n > 1) shared += m * -Math.expm1((n - 1) * Math.log1p(-m / c));
  });
  return {any: -Math.expm1(-n * (n - 1) / 2 * q2), shared: shared, pair: q2};
}
(function () {
  var box = document.querySelector("[data-sgic-collision]");
  if (!box) return;
  var form = box.closest("form") || document, all = JSON.parse(box.getAttribute("data-shares"));
  var sizes = JSON.parse(box.getAttribute("data-sizes"));
  var range = box.querySelector("[data-sgic-range]"), out = box.querySelector("[data-sgic-n]");
  var result = box.querySelector("[data-sgic-result]");
  var locale = document.documentElement.lang === "en" ? "en-GB" : "de-DE";
  function count(x) { return Math.round(x).toLocaleString(locale); }
  function share(x) {  // Preserve the first significant digits of small shares.
    if (x <= 0) return uiPercent(0);
    if (x >= 0.01) return uiPercent(x);
    var places = Math.min(6, Math.max(1, 1 - Math.floor(Math.log10(x * 100))));
    return uiNum(x * 100, places) + de_en(" %", "%");
  }
  function update() {
    var n = Math.max(10, Math.min(50000, Math.round(Math.pow(10, range.value / 100))));
    out.textContent = count(n);
    var keys = Array.prototype.filter.call(form.querySelectorAll("input[name=part]"), function (c) { return c.checked && all[c.value]; })
      .map(function (c) { return c.value; });
    result.textContent = "";
    if (!keys.length) return;
    var r = sgicCollision(keys.map(function (k) { return all[k]; }), n), people = r.shared * n;
    var universe = keys.reduce(function (u, k) { return u * sizes[k]; }, 1);
    [de_en("Mögliche Codes: ", "Possible codes: ") + count(universe) + " (" + keys.map(function (k) { return sizes[k]; }).join(" × ")
       + de_en("); wirksam etwa ", "); in effect about ") + count(1 / r.pair)
       + de_en(", weil nicht alle Antworten gleich häufig sind", ", as not all answers are equally common"),
     de_en("Stichprobe: ", "Sample: ") + count(n) + de_en(" Teilnehmende", " participants"),
     de_en("Wahrscheinlichkeit, dass mindestens zwei denselben Code haben: ",
           "Chance that at least two share a code: ") + share(r.any),
     de_en("Erwartet teilen ", "Expected to share their code: ") + share(r.shared)
       + de_en(" ihren Code mit jemandem (etwa ", " (about ") + uiNum(people, people < 10 ? 1 : 0)
       + de_en(" Personen); deren Wellen lassen sich nicht eindeutig verknüpfen.", " people); their waves cannot be linked unambiguously.")
    ].forEach(function (line) { var li = document.createElement("li"); li.textContent = line; result.appendChild(li); });
  }
  range.addEventListener("input", update);
  form.addEventListener("change", function (e) { if (e.target.name === "part") update(); });
  update();
})();

// Counts must follow the export's current selection, including quality exclusions.
(function () {
  var box = document.querySelector("[data-export-counts]");
  if (!box) return;
  var form = box.closest("form"), version = 0, timer = null, running = null;
  // The count of a large survey runs in a report process (app/report_jobs.py): 202 with the job, polled until ready,
  // then read; a kept count answers at once (performance audit 2026-10-18, PERF-01).
  function counted(url, signal) {
    return fetch(url, { credentials: "same-origin", headers: { "X-Report-Poll": "1" }, signal: signal }).then(function (r) {
      if (r.status === 202) {
        var next = url.split("&_report_job=")[0] + "&_report_job=" + encodeURIComponent(r.headers.get("X-Report-Job") || "");
        return new Promise(function (done) { setTimeout(done, 1000); }).then(function () { return counted(next, signal); });
      }
      if (r.status === 204 && r.headers.get("X-Report-Ready") === "1") {
        return fetch(url, { credentials: "same-origin", signal: signal }).then(function (ready) {
          if (!ready.ok) throw new Error(ready.status); return ready.json();
        });
      }
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
  }
  function soon() {  // a few clicks in a row ask once, and an older question is given up
    clearTimeout(timer);
    timer = setTimeout(update, 400);
  }
  function update() {
    var mine = ++version, line = box.querySelector("[data-count-line]");
    if (running) running.abort();
    running = window.AbortController ? new AbortController() : null;
    var sources = Array.prototype.filter.call(form.querySelectorAll('[name="source"]'), function (c) { return c.checked; });
    if (!sources.length) { var real = form.querySelector('[name="source"][value="real"]'); real.checked = true; sources = [real]; }
    var sourceSummary = form.querySelector("[data-pool-source-summary]");
    if (sourceSummary) sourceSummary.textContent = sources.map(function (c) { return c.closest("label").textContent.trim(); }).join(", ") +
      (form.querySelector('[name="all"]').checked ? de_en(", auch abgebrochene", ", also broken-off ones") : "");
    var summary = box.querySelector(".used-data");
    var used = sources.map(function (c) { return c.closest("label").textContent.trim(); }).join(", ");
    // German starts the phrase after the colon with a capital, English keeps lower case (pre-test 2026-10-20, 12)
    summary.textContent = de_en("Verwendete Daten: ", "Data used: ") + de_en(used.charAt(0).toUpperCase() + used.slice(1), used) +
      (form.querySelector('[name="all"]').checked ? de_en(" · auch abgebrochene Teilnahmen", " · including broken-off participations") : de_en(" · nur abgeschlossene Teilnahmen", " · completed participations only")) +
      (form.querySelector('[name="drop"]:checked').value === "1" ? de_en(" · Markierte ausgeschlossen", " · Marked participations excluded") : de_en(" · Markierte einbezogen", " · Marked participations included")) + de_en(" · ungewichtet", " · unweighted");
    line.setAttribute("aria-busy", "true");  // said once the count is in (UI/UX audit 2026-10-16, 31)
    line.textContent = de_en("Exportumfang wird berechnet …", "Calculating export selection …");
    counted(box.dataset.url + "?" + new URLSearchParams(new FormData(form)).toString(), running ? running.signal : undefined)
      .then(function (counts) {
        if (mine !== version) return;
        line.removeAttribute("aria-busy");
        line.textContent = de_en("Export: ", "Export: ") + counts.included + de_en(" Teilnahmen · ", " participations · ") +
          counts.marked + de_en(" markiert · ", " marked · ") +
          (form.querySelector('[name="drop"]:checked').value === "1" ? de_en("Markierte ausgeschlossen", "Marked participations excluded") : de_en("Markierte einbezogen", "Marked participations included")) +
          de_en(" · ungewichtet", " · unweighted");
      }).catch(function () { if (mine === version) { line.removeAttribute("aria-busy"); line.textContent = de_en("Fallzahl nicht erreichbar. Seite neu laden, um die Auswahl zu prüfen.", "Count unavailable. Reload the page to check the selection."); } });
  }
  form.addEventListener("change", soon);
  update();
})();

// Move between visible, named palette results without changing the questionnaire.
document.addEventListener("keydown", function (e) {
  if (!e.target.matches(".palette-add")) return;
  var search = document.querySelector("[data-palette-search]");
  if (e.key === "Escape") { e.preventDefault(); if (search) search.focus(); return; }
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  e.preventDefault();
  var buttons = Array.prototype.filter.call(document.querySelectorAll("[data-palette-group] .palette-add"), function (b) {
    return !b.closest(".palette-row").hidden && !b.closest(".palette-group").classList.contains("is-collapsed");
  });
  var next = buttons.indexOf(e.target) + (e.key === "ArrowDown" ? 1 : -1);
  if (next < 0 && search) search.focus();
  else if (buttons[next]) buttons[next].focus();
});

// Back from a fresh sign-in that an action asked for (accounts.need_fresh): the entries of the form that sent it
// are filled in again, so nothing is typed twice. The server keeps them encrypted and hands them over once.
(function () {
  var holder = document.querySelector("[data-fresh-refill]");
  if (!holder) return;
  var entry;
  try { entry = JSON.parse(holder.getAttribute("data-fresh-refill")); } catch (e) { return; }
  var values = {};
  (entry.fields || []).forEach(function (f) { (values[f[0]] = values[f[0]] || []).push(f[1]); });
  var skip = ["hidden", "submit", "button", "file", "password", "reset", "image"];
  // the form that sent them: same address; of several (one address, several actions), the one that knows most names
  var best = null, bestScore = -1;
  Array.prototype.forEach.call(document.forms, function (form) {
    var path;
    try { path = new URL(form.getAttribute("action") || location.href, location.href).pathname; } catch (e) { return; }
    if (path !== entry.action) return;
    var score = 0;
    Object.keys(values).forEach(function (name) {
      Array.prototype.forEach.call(form.elements, function (el) {
        if (el.name !== name) return;
        score += 1;
        if (skip.indexOf(el.type) >= 0 && values[name].indexOf(el.value) >= 0) score += 2;  // e.g. action=add
      });
    });
    if (score > bestScore) { best = form; bestScore = score; }
  });
  if (!best || bestScore <= 0) return;
  var used = {};
  Array.prototype.forEach.call(best.elements, function (el) {
    if (!el.name || skip.indexOf(el.type) >= 0 || /csrf|password|passwort|code|token|secret|key/i.test(el.name)) return;  // never kept (accounts._NOT_KEPT)
    var given = values[el.name];
    if (el.type === "checkbox") el.checked = !!given && given.indexOf(el.value || "on") >= 0;
    else if (!given) return;
    else if (el.type === "radio") el.checked = given.indexOf(el.value) >= 0;
    else if (el.multiple && el.options) Array.prototype.forEach.call(el.options, function (o) { o.selected = given.indexOf(o.value) >= 0; });
    else { var i = used[el.name] || 0; if (i >= given.length) return; el.value = given[i]; used[el.name] = i + 1; }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  for (var box = best.parentElement; box; box = box.parentElement) if (box.tagName === "DETAILS") box.open = true;
  if (!best.closest("dialog[open]")) best.scrollIntoView({ block: "center" });
})();

// The names in the footer fit the width (base.html, .footer-owner[data-fit-names]): from "Dr. Philipp Brauner · Dr. Ralf
// Philipsen" through shorter forms to "Brauner&Philipsen". The sentence on the surveys gives way first (CSS), then the
// affiliation, then the names, one step at a time, as far as the room needs; the full names stay as the tooltip. Without
// the script the full names stand there and are cut with "…" (Philipp, 2026-10-15).
(function () {
  var owner = document.querySelector(".footer-owner[data-fit-names]");
  if (!owner) return;
  var levels = Array.prototype.slice.call(owner.querySelectorAll("[data-level]"));
  var affiliation = owner.querySelector(".footer-affiliation");
  function show(n) { levels.forEach(function (el, i) { el.hidden = i !== n; }); }
  // too long: the box is narrower than its text, or (on the phone, where it may wrap) it takes more than one line
  function tight() {
    var line = parseFloat(getComputedStyle(owner).fontSize) || 14;
    return owner.scrollWidth > owner.clientWidth + 1 || owner.getBoundingClientRect().height > line * 2;
  }
  function fit() {
    var n = 0;
    if (affiliation) affiliation.hidden = false;
    show(0);
    if (tight() && affiliation) affiliation.hidden = true;
    while (tight() && n < levels.length - 1) show(++n);
  }
  var later = null;
  window.addEventListener("resize", function () { clearTimeout(later); later = setTimeout(fit, 80); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
  fit();
})();

// Konstruktbox: the full construct box as a dialog (konzept-konstrukt-schnellbox-2026-10-16.md, K4-K9). In the
// editor "+ Einfügen" adds a construct to the block chosen in the dialog's head without leaving it; the card then says
// where it went and takes it back with "Rückgängig" (the editor's undo of that very step). On a page that keeps a
// "Schnellbox" list (Verwaltung, Administration) "+ Aufnehmen" adds it there. The page is loaded anew on closing,
// once something changed.
(function () {
  var box = document.querySelector("[data-construct-box]");
  if (!box) return;
  var content = box.querySelector("[data-box-content]"), qInput = box.querySelector("[data-box-q]");
  var target = box.querySelector("[data-box-block]");
  var csrf = csrfToken(), changed = false, seq = 0, timer = null;
  function params() {
    var p = new URLSearchParams();
    if (box.dataset.survey) p.set("survey", box.dataset.survey);
    if (box.dataset.keep) p.set("keep", box.dataset.keep);
    if (qInput.value.trim()) p.set("q", qInput.value.trim());
    var form = content.querySelector("[data-box-filters]");
    if (form) new FormData(form).forEach(function (v, k) { p.append(k, v); });
    return p;
  }
  function load(now) {
    clearTimeout(timer);
    var mine = ++seq;
    timer = setTimeout(function () {
      fetch(box.dataset.url + "?" + params().toString(), { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          if (mine !== seq) return;
          var list = content.querySelector(".cbox-list"), top = list ? list.scrollTop : 0;
          content.innerHTML = html;
          var fresh = content.querySelector(".cbox-list");
          if (fresh) fresh.scrollTop = top;
        })
        .catch(function () { if (mine === seq) content.innerHTML = '<p class="muted">' + de_en("Die Sammlung ist gerade nicht erreichbar. Bitte später erneut versuchen.", "The collection cannot be reached right now. Please try again later.") + "</p>"; });
    }, now ? 0 : 200);
  }
  function activeBlock() {  // the block marked in the editor, else the last one
    var blocks = document.querySelectorAll(".block[data-block-id]");
    var b = document.querySelector(".block.is-active[data-block-id]") || blocks[blocks.length - 1];
    return b ? b.dataset.blockId : null;
  }
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-box-open]");
    if (!opener) return;
    var side = document.querySelector("[data-palette-search]");  // the word typed in the editor's search goes along
    if (side && side.value.trim()) qInput.value = side.value.trim();
    if (target) {
      var id = activeBlock();
      Array.prototype.forEach.call(target.options, function (o) { if (o.dataset.blockId === id) target.value = o.value; });
    }
    box.showModal();
    load(true);
    qInput.focus();
  });
  box.querySelector("[data-box-close]").addEventListener("click", function () { box.close(); });
  box.addEventListener("close", function () { if (changed) window.location.reload(); });
  qInput.addEventListener("input", function () { load(false); });
  qInput.addEventListener("keydown", function (e) { if (e.key === "Enter") e.preventDefault(); });
  content.addEventListener("change", function (e) { if (e.target.closest("[data-box-filters]")) load(true); });
  function post(url, data) {
    var body = new FormData();
    Object.keys(data).forEach(function (k) { body.append(k, data[k]); });
    body.append("csrf_token", csrf);
    return fetch(url, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok || (r.headers.get("Content-Type") || "").indexOf("json") < 0) throw new Error(r.status);
        return r.json();
      });
  }
  content.addEventListener("click", function (e) {
    var insert = e.target.closest("[data-box-insert]"), keep = e.target.closest("[data-box-keep]");
    var star = e.target.closest("[data-box-star]"), undoBtn = e.target.closest("[data-box-undo]");
    var card = e.target.closest(".cbox-card"), done = card && card.querySelector("[data-box-done]");
    if (insert && target) {
      insert.disabled = true;
      post(target.value, { type: insert.dataset.boxInsert, box: "1" }).then(function (d) {
        changed = true;
        insert.disabled = false;
        done.hidden = false;
        done.innerHTML = "✓ " + esc(de_en("in „" + d.block + "“ eingefügt", "inserted into “" + d.block + "”")) +
          (d.undo_id ? ' · <button type="button" class="link-button" data-box-undo="' + d.undo_id + '">' + de_en("Rückgängig", "Undo") + "</button>" : "");
        if (d.note) done.insertAdjacentHTML("beforeend", '<span class="muted"> ' + esc(d.note) + "</span>");
      }).catch(function () { insert.disabled = false; actionFailed(0); });
    } else if (undoBtn) {
      undoBtn.disabled = true;
      post(box.dataset.undoUrl, { only: undoBtn.dataset.boxUndo }).then(function (d) {
        done.textContent = d.done ? de_en("Zurückgenommen.", "Undone.") : de_en("Nicht mehr der letzte Schritt: bitte im Editor zurücknehmen.", "No longer the last step: please undo it in the editor.");
      }).catch(function () { undoBtn.disabled = false; actionFailed(0); });
    } else if (keep) {
      keep.disabled = true;
      post(box.dataset.keepUrl, { action: "add", key: keep.dataset.boxKeep, json: "1" }).then(function (d) {
        changed = true;
        done.hidden = false;
        done.textContent = d.done ? "✓ " + de_en("in die Liste aufgenommen", "added to the list") : de_en("Nicht aufgenommen: Die Liste ist voll.", "Not added: the list is full.");
        keep.hidden = true;
      }).catch(function () { keep.disabled = false; actionFailed(0); });
    } else if (star) {
      star.disabled = true;
      post(star.dataset.boxStar, { json: "1", survey: box.dataset.survey || "" }).then(function (d) {
        changed = true;
        star.disabled = false;
        star.setAttribute("aria-pressed", d.starred ? "true" : "false");
      }).catch(function () { star.disabled = false; actionFailed(0); });
    }
  });
})();

// The dashboard of Administration > Befragungen (app/admin_stats.py, Ralf 2026-10-17): a period chosen in the switch or
// the field "seit" swaps the dashboard and the list below in place, without reloading the page, and keeps the address;
// both are fetched anew every minute while the page is in view. Without fetch the links and the form work as before.
(function () {
  var dash = document.querySelector("[data-admin-dash]");
  if (!dash || !window.fetch || !window.DOMParser) return;
  var busy = false;
  function load(url, push) {
    if (busy) return Promise.resolve();
    busy = true;
    return fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } }).then(function (r) {
      if (!r.ok || r.redirected) throw new Error(r.status);
      return r.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      ["[data-admin-dash]", "#befragungen"].forEach(function (sel) {
        var fresh = doc.querySelector(sel), here = document.querySelector(sel);
        if (fresh && here) here.replaceWith(fresh);
      });
      if (push) history.replaceState(null, "", url);
    }).catch(function () {
      if (push) window.location.href = url;  // the plain page then
    }).then(function () { busy = false; });
  }
  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest("[data-admin-dash-link]");
    if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
    e.preventDefault();
    load(link.href, true);
  });
  document.addEventListener("submit", function (e) {
    var form = e.target.closest && e.target.closest("[data-admin-dash-form]");
    if (!form) return;
    e.preventDefault();
    load(form.action + "?" + new URLSearchParams(new FormData(form)).toString(), true);
  });
  setInterval(function () {
    var field = document.activeElement;
    // not while someone types in the date or the search of the list
    if (document.hidden || (field && field.closest && field.closest("[data-admin-dash], #befragungen") && field.matches("input"))) return;
    load(window.location.href, false);
  }, 60000);
})();

// A setting another part of the page needs (surveys/_speed.html: the speed index needs "Zeit je Seite erfassen",
// #page-timing in surveys/_fields_quality.html; pre-test 2026-10-20, 27). The link [data-show-setting] opens its
// fold, goes there and marks it; the button [data-switch-setting] ticks it in its own form, whose autosave saves it
// and, as the box is marked data-reload-saved, loads the page anew, where the index can then be chosen.
(function () {
  function setting(id) {
    var box = document.getElementById(id);
    var fold = box && box.closest("details");
    if (fold) fold.open = true;
    return box;
  }
  function point(box) {
    box.classList.add("is-pointed");
    setTimeout(function () { box.classList.remove("is-pointed"); }, 4000);
  }
  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest("[data-show-setting]");
    if (link) {
      var box = setting(link.getAttribute("data-show-setting"));
      if (!box) return;
      e.preventDefault();
      box.scrollIntoView({ block: "center" });
      point(box);
      var input = box.querySelector("input");
      if (input && !input.disabled) input.focus({ preventScroll: true });
      return;
    }
    var button = e.target.closest && e.target.closest("[data-switch-setting]");
    if (!button) return;
    var target = setting(button.getAttribute("data-switch-setting"));
    var check = target && target.querySelector("input[type=checkbox]");
    if (!check || check.disabled) return;
    button.disabled = true;
    if (check.checked) { location.reload(); return; }
    check.checked = true;
    check.dispatchEvent(new Event("change", { bubbles: true }));
  });
})();

// Recordings in a closed fold load nothing until it opens, then their length (app/webm_duration.py names it): a
// long list of interviews does not fetch every recording's head at once (Ralf, 2026-10-23).
document.addEventListener("toggle", function (e) {
  if (!e.target.open || !e.target.querySelectorAll) return;
  e.target.querySelectorAll("audio[data-preload-open][preload=none]").forEach(function (a) { a.preload = "metadata"; a.load(); });
}, true);
