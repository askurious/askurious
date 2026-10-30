// Case summaries of an interview guide (interview/summaries.html, app/interview_summaries.py): every cell saves by
// itself a moment after typing; a cell that did not get through is sent again.
(function () {
  var box = document.querySelector("[data-summaries]");
  if (!box) return;
  var csrf = box.getAttribute("data-csrf"), status = document.querySelector("[data-summary-status]");
  // the state goes to the head of the survey (S2, saveState in app.js); the line here only shows a failure
  function say(text, kind, area) {
    var head = window.saveState ? window.saveState(area, kind, text, area) : false;
    if (status) { status.textContent = text; status.className = "small autosave-status is-" + kind; status.hidden = head && kind !== "error"; }
  }
  var flushes = [];
  box.querySelectorAll("[data-summary-url]").forEach(function (area) {
    // one request at a time: typing while one is under way is sent once it answers, with the latest text, so an
    // older answer never comes last and wins; leaving the page sends what still waits (code review 2026-10-15, I2)
    var timer = null, refused = false, saving = false, again = false, sent = area.value, inflight = null;
    if (window.saveState) window.saveState(area, "idle", "", area);
    function body() {
      var data = new FormData();
      data.append("key", area.getAttribute("data-summary-key"));
      data.append("text", area.value);
      return data;
    }
    function send() {
      if (timer) { clearTimeout(timer); timer = null; }
      if (refused) return;
      if (saving) { again = true; return; }
      saving = true; again = false;
      var text = inflight = area.value;
      say(de_en("speichert …", "saving …"), "busy", area);
      fetch(area.getAttribute("data-summary-url"), { method: "POST", body: body(), credentials: "same-origin", headers: { "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
        .then(function (r) {
          saving = false;
          if (r.ok && r.clone) r.clone().json().then(function (d) { shown(area, d); }).catch(function () {});
          // a refusal (400, 403, 404: the block deleted, the right taken away) stays one: not sent again; only the
          // network, 5xx, 408 and 429 are tried again (G6)
          if (r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429) { refused = true; return say(de_en("nicht gespeichert: abgelehnt (" + r.status + "); bitte die Seite neu laden", "not saved: refused (" + r.status + "); please reload the page"), "error", area); }
          if (!r.ok) throw new Error(r.status);
          sent = text;
          if (again || area.value !== sent) { again = false; send(); return; }  // typed meanwhile: the newer text now
          say(de_en("Gespeichert", "Saved") + " " + new Date().toLocaleTimeString(de_en("de-DE", "en-GB"), { hour: "2-digit", minute: "2-digit" }), "ok", area); })
        .catch(function () { saving = false; again = false; say(de_en("nicht gespeichert, wird wiederholt", "not saved, trying again"), "error", area); timer = setTimeout(send, 4000); });  // the network or 5xx
    }
    area.addEventListener("input", function () { if (timer) clearTimeout(timer); timer = setTimeout(send, 800); });
    flushes.push(function () {  // leaving the page: a text not stored yet goes along (keepalive outlives the page)
      if (refused || area.value === sent || (saving && area.value === inflight)) return;
      fetch(area.getAttribute("data-summary-url"), { method: "POST", body: body(), credentials: "same-origin", keepalive: area.value.length < 40000, headers: { "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } }).catch(function () {});
    });
  });
  window.addEventListener("pagehide", function () { flushes.forEach(function (f) { f(); }); });

  // who wrote a cell last, and "Rückgängig" of one's own last step in it (app/interview_summaries.py, undo_cell)
  function shown(area, d) {
    var cell = area.closest("td");
    if (!cell || !d) return;
    var by = cell.querySelector("[data-summary-by]");
    if (by) by.textContent = area.value.trim() && d.by ? de_en("von ", "by ") + d.by + (d.at ? ", " + d.at : "") : "";
    var back = cell.querySelector("[data-summary-revert]");
    if (back) back.hidden = !d.undo;
  }
  if (box.addEventListener) box.addEventListener("click", function (e) {
    var back = e.target.closest && e.target.closest("[data-summary-revert]");
    if (!back) return;
    var area = back.closest("td").querySelector("textarea");
    var data = new FormData();
    data.append("key", area.getAttribute("data-summary-key"));
    back.disabled = true;
    fetch(area.getAttribute("data-summary-url") + "/undo", { method: "POST", body: data, credentials: "same-origin",
                                                             headers: { "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        back.disabled = false;
        if (!d.ok) { say(d.error || de_en("Nicht zurückgenommen.", "Not taken back."), "error", area); return; }
        area.value = d.text;
        shown(area, d);
        say(de_en("Zurückgenommen", "Taken back"), "ok", area);
      })
      .catch(function () { back.disabled = false; say(de_en("Nicht zurückgenommen: keine Verbindung.", "Not taken back: no connection."), "error", area); });
  });

  // with an analysis of "Interviews kodieren" as the columns (app/transcript_coding_cases.py): a draft from the
  // passages of a cell, or from the AI, only into an empty field; "Rückgängig" empties it again
  function fill(cell, text) {
    var area = cell.querySelector("textarea");
    if (!area || area.value.trim() || !text) return;
    area.value = text;
    area.dispatchEvent(new Event("input"));
    var undo = cell.querySelector("[data-summary-undo]");
    if (undo) undo.hidden = false;
  }
  if (box.addEventListener) box.addEventListener("click", function (e) {
    var cell = e.target.closest("td");
    if (!cell) return;
    var prefill = e.target.closest("[data-summary-prefill]");
    if (prefill) { fill(cell, prefill.getAttribute("data-summary-prefill")); return; }
    var undo = e.target.closest("[data-summary-undo]");
    if (undo) {
      var area = cell.querySelector("textarea");
      area.value = "";
      area.dispatchEvent(new Event("input"));
      undo.hidden = true;
      return;
    }
    var ai = e.target.closest("[data-summary-ai]");
    if (ai) {
      var field = cell.querySelector("textarea");
      if (field.value.trim()) { say(de_en("Das Feld ist nicht leer.", "The field is not empty."), "error", field); return; }
      var data = new FormData();
      data.append("key", field.getAttribute("data-summary-key"));
      ai.disabled = true;
      say(de_en("KI schreibt einen Entwurf …", "AI is writing a draft …"), "busy", field);
      fetch(ai.getAttribute("data-summary-ai"), { method: "POST", body: data, credentials: "same-origin", headers: { "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json(); })
        .then(function (d) { ai.disabled = false; if (d.ok) fill(cell, d.text); else say(d.error || "", "error", field); })
        .catch(function () { ai.disabled = false; say(de_en("Die KI hat nicht geantwortet.", "The AI did not answer."), "error", field); });
    }
  });
})();

// the table "Fälle × Unterkategorien" (interview/summaries.html): sorting by a column and showing only the cases with
// a subcategory
(function () {
  var body = document.querySelector ? document.querySelector("[data-types-body]") : null;
  if (!body) return;
  var asc = {};
  document.querySelectorAll("[data-types-sort]").forEach(function (b) {
    b.addEventListener("click", function () {
      var i = Number(b.getAttribute("data-types-sort"));
      asc[i] = !asc[i];
      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (x, y) {
        var a = x.cells[i].getAttribute("data-v"), c = y.cells[i].getAttribute("data-v");
        var na = Number(a), nc = Number(c);
        var cmp = i === 0 || isNaN(na) || isNaN(nc) ? String(a).localeCompare(String(c)) : na - nc;
        return asc[i] ? cmp : -cmp;
      });
      rows.forEach(function (r) { body.appendChild(r); });
    });
  });
  var filter = document.querySelector("[data-types-filter]");
  if (filter) filter.addEventListener("change", function () {
    var sub = filter.value;
    Array.prototype.forEach.call(body.rows, function (r) {
      var cell = sub ? r.querySelector('[data-sub="' + sub + '"]') : null;
      r.hidden = !!sub && (!cell || cell.getAttribute("data-v") === "0");
    });
  });
})();
