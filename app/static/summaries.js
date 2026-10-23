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
})();
