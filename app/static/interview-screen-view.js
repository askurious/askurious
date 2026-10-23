// The interviewee's own screen, the interview view's part (app/interview_screen.py; templates/interview/conduct.html,
// _task.html, screen_edit.html). "Teilnehmerbildschirm verbinden" makes a link with its QR code (shown once); the view
// then asks every few seconds whether a device opened it, which task is released and which results there are. Each
// task card gets "Auf dem Teilnehmerbildschirm zeigen" / "Zurücknehmen"; a result from the screen stands in the card
// at once. While a task is out on the screen or its result came from there, the card's own form is hidden (it would
// hold what it held before); "Ergebnis ändern" draws the result fresh in a frame (E8: then marked as changed).
(function () {
  function de_en(de, en) { return (document.body.getAttribute("data-ui-lang") || "de") === "en" ? en : de; }

  // --- the frame "Ergebnis ändern" (screen_edit.html): saves a moment after a change, tells the interview view ---
  var edit = document.querySelector("[data-ivs-edit-page]");
  if (edit) {
    var note = edit.querySelector("[data-ivs-edit-note]"), timer = null, running = false, again = false;
    function tell(kind) {
      try { window.parent.postMessage({ ivs: kind, task: edit.querySelector("[name=task]").value, height: document.documentElement.scrollHeight }, window.location.origin); } catch (e) { /* not in a frame */ }
    }
    function send() {
      if (running) { again = true; return; }
      running = true; again = false;
      note.textContent = edit.getAttribute("data-saving");
      fetch(edit.getAttribute("action"), { method: "POST", body: new FormData(edit), credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          running = false;
          if (again) { send(); return; }
          var bad = Object.keys((d && d.errors) || {});
          note.textContent = bad.length ? d.errors[bad[0]] : edit.getAttribute("data-saved");
          tell("saved");
        })
        .catch(function () { running = false; note.textContent = edit.getAttribute("data-failed"); });
    }
    function later() { if (timer) clearTimeout(timer); timer = setTimeout(send, 700); }
    edit.addEventListener("change", later);
    edit.addEventListener("input", later);
    edit.addEventListener("click", function (e) { if (e.target.closest("button")) later(); });  // ranking: ↑ ↓
    edit.addEventListener("submit", function (e) { e.preventDefault(); send(); });
    window.addEventListener("load", function () { tell("size"); });
    return;
  }

  // --- the interview view ---
  var box = document.querySelector("[data-ivs-view]");
  if (!box) return;
  var csrf = box.getAttribute("data-csrf"), every = (parseInt(box.getAttribute("data-poll"), 10) || 2) * 1000;
  var line = box.querySelector("[data-ivs-line]"), badge = document.querySelector("[data-ivs-badge]");
  var connect = box.querySelector("[data-ivs-connect]"), renew = box.querySelector("[data-ivs-renew]");
  var disconnect = box.querySelector("[data-ivs-disconnect]"), fresh = box.querySelector("[data-ivs-new]");
  var state = { on: false, paired: false, alive: false, show: null, results: {} }, timer = null, editing = {};

  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin", body: JSON.stringify(body || {}),
                        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json().catch(function () { return null; }); });
  }

  function paint() {
    var s = state;
    connect.hidden = s.on;
    renew.hidden = !s.on;
    disconnect.hidden = !s.on;
    if (!s.on) fresh.hidden = true;
    if (s.paired) fresh.hidden = true;  // opened: the link has done its work, it opens no second device
    line.textContent = !s.on ? "" : !s.paired ? de_en("Wartet, bis die interviewte Person den Link öffnet …", "Waiting for the interviewee to open the link …")
      : s.alive ? de_en("Verbunden.", "Connected.") : de_en("Verbunden, das Gerät meldet sich gerade nicht.", "Connected, the device is not responding right now.");
    badge.hidden = !s.on;
    badge.classList.toggle("is-trouble", s.paired && !s.alive);
    badge.textContent = !s.on ? "" : !s.paired ? de_en("Teilnehmerbildschirm: Wartet", "Participant screen: waiting")
      : s.alive ? "● " + de_en("Teilnehmerbildschirm verbunden", "Participant screen connected") : de_en("Teilnehmerbildschirm meldet sich nicht", "Participant screen not responding");
    document.querySelectorAll("[data-ivs-task]").forEach(function (part) {
      var id = part.getAttribute("data-ivs-task"), card = part.closest("[data-iv-task]");
      var result = s.results[id], fromScreen = result && result.by === "screen", out = String(s.show) === id;
      part.hidden = !(s.paired || fromScreen);
      var area = card.querySelector("[data-iv-show-area]"), showBtn = card.querySelector("[data-iv-show]");
      var own = out || fromScreen;  // the card's own form would hold an old state
      if (area) area.hidden = own;
      if (showBtn) showBtn.hidden = own;
      part.querySelector("[data-ivs-task-name]").hidden = !own;  // the task's question stands in the hidden form
      part.querySelector("[data-ivs-show]").hidden = !s.paired || out;
      part.querySelector("[data-ivs-hide]").hidden = !out;
      part.querySelector("[data-ivs-change]").hidden = !fromScreen || !!editing[id];
      part.querySelector("[data-ivs-task-state]").textContent = out
        ? (fromScreen ? de_en("Auf dem Teilnehmerbildschirm; das Ergebnis ist da und kann dort noch geändert werden.", "On the participant screen; the result is in and can still be changed there.")
                      : de_en("Auf dem Teilnehmerbildschirm, wartet auf das Ergebnis …", "On the participant screen, waiting for the result …")) : "";
      var res = part.querySelector("[data-ivs-result]"), by = part.querySelector("[data-ivs-result-by]");
      res.hidden = !result || !(fromScreen || out);
      if (result) {
        part.querySelector("[data-ivs-result-text]").textContent = result.text;
        by.textContent = result.changed ? by.getAttribute("data-changed") : fromScreen ? by.getAttribute("data-screen") : "";
        by.hidden = !by.textContent;
      }
    });
  }

  function ask() {
    if (timer) { clearTimeout(timer); timer = null; }
    fetch(box.getAttribute("data-state"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) { state = d; paint(); } })
      .catch(function () { /* the next round asks again */ })
      .then(function () { if (state.on || Object.keys(editing).length) timer = setTimeout(ask, document.hidden ? every * 3 : every); });
  }

  function made(d) {
    if (!d || !d.ok) { line.textContent = de_en("Nicht möglich. Bitte die Seite neu laden.", "Not possible. Please reload the page."); return; }
    state = d;
    if (d.link) {
      fresh.hidden = false;
      fresh.querySelector("[data-ivs-qr]").src = d.qr;
      var a = fresh.querySelector("[data-ivs-link]");
      a.href = d.link; a.textContent = d.link;
    }
    paint();
    ask();
  }
  connect.addEventListener("click", function () { post(box.getAttribute("data-connect")).then(made); });
  renew.addEventListener("click", function () { post(box.getAttribute("data-connect")).then(made); });
  disconnect.addEventListener("click", function () { post(box.getAttribute("data-disconnect")).then(made); });
  box.querySelector("[data-ivs-copy]").addEventListener("click", function () {
    var a = fresh.querySelector("[data-ivs-link]"), done = box.querySelector("[data-ivs-copied]");
    if (navigator.clipboard) navigator.clipboard.writeText(a.href).then(function () { done.hidden = false; setTimeout(function () { done.hidden = true; }, 2000); });
  });

  document.querySelectorAll("[data-ivs-task]").forEach(function (part) {
    var id = part.getAttribute("data-ivs-task"), frame = part.querySelector("[data-ivs-frame]");
    part.querySelector("[data-ivs-show]").addEventListener("click", function () { post(box.getAttribute("data-release"), { task: id }).then(made); });
    part.querySelector("[data-ivs-hide]").addEventListener("click", function () { post(box.getAttribute("data-release"), { task: "" }).then(made); });
    part.querySelector("[data-ivs-change]").addEventListener("click", function () {
      editing[id] = true;
      frame.src = box.getAttribute("data-edit").replace(/\/0$/, "/" + id);
      frame.hidden = false;
      paint();
      ask();
    });
  });
  window.addEventListener("message", function (e) {
    if (e.origin !== window.location.origin || !e.data || !e.data.ivs) return;
    var part = document.querySelector('[data-ivs-task="' + String(e.data.task).replace(/[^0-9a-z]/g, "") + '"]');
    var frame = part && part.querySelector("[data-ivs-frame]");
    if (frame && e.data.height) frame.style.height = Math.min(Math.max(e.data.height + 8, 160), 1200) + "px";
    if (e.data.ivs === "saved") ask();
  });
  ask();
})();
