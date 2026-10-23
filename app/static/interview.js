// The interview view of an interview guide (interview/conduct.html, app/interview.py). Nothing goes to the server:
// the page shown, the ticks, the state of each Leitfrage and the clock live in sessionStorage, so an accidental
// reload keeps them and closing the tab forgets them (concept interviewleitfaden-konzept.md 4.3, decision I19).
// A stored interview sends every change; what has not reached the server yet also waits in localStorage (see below).
// Order "free": the list on the left jumps; "linear": Weiter and Zurück, the list only shows the progress.
(function () {
  // the frame speaks the page language of the person conducting (data-ui-lang on <body>), not the survey's (<html lang>)
  function de_en(de, en) { return (document.body.getAttribute("data-ui-lang") || document.documentElement.lang) === "en" ? en : de; }
  var root = document.querySelector("[data-interview]");
  if (!root) return;
  // Offline mode (the field kit, interview-offline.js, app/interview_offline.py): the view waits until the kit is
  // unlocked and an interview chosen; then its state lives (encrypted) with the kit, not in the tab or on a server.
  var OM = root.hasAttribute("data-offline");
  if (OM) { if (window.AskuriousOffline) AskuriousOffline.ready.then(main); return; }
  main(null);
  function main(offlineStart) {
  var key = OM ? offlineStart.key : root.getAttribute("data-key");
  var steps = Array.prototype.slice.call(root.querySelectorAll("[data-iv-step]"));
  var clock = root.querySelector("[data-iv-clock]");
  var pauseButton = root.querySelector("[data-iv-pause]");
  var resetButton = root.querySelector("[data-iv-reset]");
  var prev = root.querySelector("[data-iv-prev]"), next = root.querySelector("[data-iv-next]");
  var pos = root.querySelector("[data-iv-pos]");

  // --- the text size of this view only (Ralf 2026-10-22): A− / A+ in steps, kept on this device (theme-init.js puts it
  // on <html> before the first paint); without a choice the size of "Konto" holds
  var SIZES = [90, 100, 112, 125, 140, 160], html = document.documentElement;
  function sizeNow() { return parseInt(html.getAttribute("data-iv-size") || html.getAttribute("data-size") || "100", 10); }
  function showSize() {
    var now = sizeNow(), out = root.querySelector("[data-iv-size-value]");
    if (out) out.textContent = now + de_en(" %", "%");
    root.querySelectorAll("[data-iv-size]").forEach(function (b) {
      b.disabled = b.getAttribute("data-iv-size") === "1" ? now >= SIZES[SIZES.length - 1] : now <= SIZES[0];
    });
  }
  root.querySelectorAll("[data-iv-size]").forEach(function (b) {
    b.addEventListener("click", function () {
      var now = sizeNow(), up = b.getAttribute("data-iv-size") === "1";
      var nextSize = up ? SIZES.filter(function (v) { return v > now; })[0] : SIZES.filter(function (v) { return v < now; }).pop();
      if (!nextSize) return;
      html.setAttribute("data-iv-size", String(nextSize));
      html.style.setProperty("--iv-bar-zoom", String(parseInt(html.getAttribute("data-size") || "100", 10) / nextSize));
      try { localStorage.setItem("askurious-iv-size", String(nextSize)); } catch (e) {}
      showSize();
    });
  });
  showSize();

  // fits: "trifft zu" of a concrete question with Nachfragen ("<item>:<row>" -> "yes" | "no"); cases: the case features
  // of a trial interview (Kurzfragebogen question id -> [codes]); a stored interview keeps them in its Kurzfragebogen
  // forms: answers of the Kurzfragebogen and Postskript that did not reach the server yet (form action -> [[name, value]])
  function fresh() { return { cur: 0, ticks: {}, states: {}, fits: {}, cases: {}, elapsed: 0, since: null, blocks: {}, notes: {}, marks: [], forms: {} }; }
  var st = fresh();
  // A stored interview ("Interviews erheben", app/interview_data.py): the server's state is the start,
  // unless this tab still holds changes that did not reach it ("dirty"); every change is sent, a failed send again.
  // Offline (Ralf 2026-10-22): what has not reached the server yet is also kept in localStorage, so a closed tab, a
  // crashed browser or an empty battery loses no note; the copy goes away as soon as the server has everything.
  var saveUrl = root.getAttribute("data-save"), csrf = root.getAttribute("data-csrf");
  function local() { try { return saveUrl ? window.localStorage : null; } catch (e) { return null; } }
  // offline G: the page came from the service worker's copy (interview-sw.js), so the state this device kept is newer
  // than the page's own; swOn: the worker runs, the device keeps the state for the whole interview (at most 12 hours)
  var offlineCopy = root.hasAttribute("data-offline-copy"), swOn = false, KEEP_MS = 12 * 3600 * 1000;
  try {
    var kept = OM ? null : sessionStorage.getItem(key) || sessionStorage.getItem(key.replace(/^askurious-/, "queryous-"));
    if (!kept && local()) {
      kept = local().getItem(key);
      var old = kept ? JSON.parse(kept) : null;  // an old copy with nothing unsent: gone, as the worker's copy of the page
      if (old && !old.dirty && !Object.keys(old.forms || {}).length && !(old.keptAt > Date.now() - KEEP_MS)) { local().removeItem(key); kept = null; }
    }
    st = Object.assign(fresh(), OM ? offlineStart.st || {} : JSON.parse(kept || "{}"));
  } catch (e) { /* no storage: in memory only */ }
  var recordEl = root.querySelector("[data-iv-record]"), savedNote = root.querySelector("[data-iv-saved]");
  if (recordEl && !st.dirty && !(offlineCopy && st.keptAt)) {
    try {
      var rec = JSON.parse(recordEl.textContent);
      st.states = rec.states || {}; st.notes = rec.notes || {}; st.marks = rec.marks || []; st.fits = rec.fits || {};
      st.ticks = {}; (rec.ticks || []).forEach(function (t) { st.ticks[t] = true; });
      st.elapsed = rec.elapsed || 0; st.blocks = rec.blocks || {}; st.since = null;
    } catch (e) { /* the page's own data: keep the tab's copy */ }
  }
  // F10 (code review 2026-10-07): a change while a save is under way is sent again once it answers ("again"), and
  // only an answer for the latest change clears "dirty"; a finish waiting meanwhile goes along with that next save
  var pushTimer = null, pushing = false, again = false, waiting = null, edits = 0, failing = false;
  // netDown: the last request did not reach the server at all (no connection, not an error answer); endFailed: what
  // "Interview beenden" does when its last save cannot go out (set below: wait for the connection)
  // signedOut (offline I): the server answered 401, the sign-in (interviewer link or account) ran out while the device
  // was offline; everything waits until someone signs in again in another tab, then the retries get through
  var netDown = false, endFailed = null, signedOut = false;
  function offline() { return netDown || (typeof navigator !== "undefined" && navigator.onLine === false); }
  function stuck() { return offline() || signedOut; }  // waiting for something outside the page, not an error
  function reached(r) { netDown = !r; if (r) signedOut = r.status === 401; }  // r: the answer, false when none came
  function offlineSay() {
    if (signedOut && !netDown) return de_en("Anmeldung abgelaufen: den Interviewlink oder die Anmeldung in einem neuen Tab öffnen; danach wird alles nachgeschickt",
                                            "Sign-in expired: open the interviewer link or sign in in a new tab; then everything is sent");
    return de_en("offline: Alles bleibt auf diesem Gerät und wird nachgeschickt, sobald wieder Verbindung da ist",
                                       "offline: everything stays on this device and is sent once the connection is back"); }
  function say(text, kind) { if (savedNote) { savedNote.textContent = text; savedNote.className = "small iv-saved is-" + kind; } }
  function current() {  // the clock as it stands now, without changing the state
    var extra = st.since !== null ? Date.now() - st.since : 0, blocks = Object.assign({}, st.blocks), b = blockOf(st.cur);
    if (b && extra) blocks[b] = (blocks[b] || 0) + extra;
    return { elapsed: st.elapsed + extra, blocks: blocks };
  }
  function body() {
    var now_ = current();
    return JSON.stringify({ states: st.states, ticks: Object.keys(st.ticks), fits: st.fits, notes: st.notes, marks: st.marks,
                            elapsed: now_.elapsed, blocks: now_.blocks, running: st.since !== null });
  }
  var jsonHeaders = { "Content-Type": "application/json", "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" };
  function push(done) {
    pushTimer = null;
    if (!saveUrl) return;
    if (pushing) { again = true; if (done) waiting = done; return; }
    pushing = true; again = false;
    var sent = edits;
    say(de_en("speichert …", "saving …"), "busy");
    fetch(saveUrl, { method: "POST", credentials: "same-origin", headers: jsonHeaders, body: body() })
      .then(function (r) { reached(r); if (!r.ok) throw new Error(r.status); return r.json(); }, function (e) { reached(false); throw e; })
      .then(function (d) {
        pushing = false; failing = false;
        if (edits === sent) st.dirty = false;
        keep();
        say(de_en("gespeichert ", "saved ") + d.saved, "ok");
        var next = waiting || done; waiting = null;
        if (again) { push(next); return; }  // changed meanwhile: the newer state goes out now
        if (next) next();
      })
      .catch(function () {
        pushing = false; failing = true;
        var next = waiting || done; waiting = null; again = false;
        if (stuck()) say(offlineSay(), "error");
        else say(de_en("nicht gespeichert, wird wiederholt", "not saved, retrying"), "error");
        if (next && endFailed && stuck()) { endFailed(); return; }  // the end waits for the connection and comes by itself
        if (next) { say(de_en("nicht gespeichert: keine Verbindung; bitte gleich noch einmal „Interview beenden“", "not saved: no connection; please choose “End interview” again in a moment"), "error"); return; }
        if (!pushTimer) pushTimer = setTimeout(push, 5000);
      });
  }
  function keep() {
    st.keptAt = Date.now();
    if (OM) { AskuriousOffline.keep(st); return; }  // encrypted with the field kit, never plain in the tab
    var data = JSON.stringify(st);
    try { sessionStorage.setItem(key, data); } catch (e) { /* no storage */ }
    try {  // the device's copy only while something has not reached the server
      var l = local();
      if (l) { if (st.dirty || Object.keys(st.forms || {}).length || swOn) l.setItem(key, data); else l.removeItem(key); }
    } catch (e) { /* full or blocked: the tab's copy holds */ }
  }
  function save() {
    if (saveUrl) {
      st.dirty = true; edits += 1;
      if (pushTimer) clearTimeout(pushTimer);
      pushTimer = setTimeout(push, 700);
    }
    keep();
  }
  if (saveUrl) setInterval(function () { if (st.since !== null) save(); }, 30000);  // the clock, now and then
  // I5 (code review 2026-10-15): a state this tab kept from before a reload that never reached the server goes out
  // at once; leaving the page sends what still waits (keepalive outlives the page), and a state that could not be
  // sent asks before leaving
  if (saveUrl && st.dirty) push();
  if (saveUrl) {
    window.addEventListener("pagehide", function () {
      if (!st.dirty) return;
      var data = body();
      fetch(saveUrl, { method: "POST", credentials: "same-origin", keepalive: data.length < 60000, headers: jsonHeaders, body: data })
        .catch(function () { /* kept in sessionStorage: a reload sends it */ });
    });
    window.addEventListener("beforeunload", function (e) {
      if (st.dirty && (failing || pushing)) { e.preventDefault(); e.returnValue = ""; }
    });
  }
  // the connection: lost, the status line says so at once (no banner, Ralf); back, everything waiting goes out now
  // instead of at the next retry (the recording, the forms and a waiting "Interview beenden" hang on onBack)
  var onBack = [];
  if (saveUrl) {
    window.addEventListener("offline", function () { say(offlineSay(), "error"); });
    window.addEventListener("online", function () {
      netDown = false;
      if (st.dirty && !pushing) { if (pushTimer) clearTimeout(pushTimer); push(); }
      else if (!st.dirty) say(de_en("wieder verbunden", "connected again"), "ok");
      onBack.forEach(function (fn) { fn(); });
    });
  }

  // --- the clock: runs from the first start until a pause; the time of each Themenblock while one of its pages shows
  function now() { return Date.now(); }
  function running() { return st.since !== null; }
  function blockOf(n) { var s = steps[n]; return s ? s.getAttribute("data-iv-block-of") : null; }
  function settle() {  // the time since the last look goes to the whole and to the block on screen
    if (!running()) return;
    var t = now(), spent = t - st.since, block = blockOf(st.cur);
    st.elapsed += spent;
    if (block) st.blocks[block] = (st.blocks[block] || 0) + spent;
    st.since = t;
  }
  function fmt(ms, seconds) {
    var s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    function two(x) { return (x < 10 ? "0" : "") + x; }
    return seconds ? two(h) + ":" + two(m) + ":" + two(s % 60) : (h ? h + ":" + two(m) : m) + de_en(" Min.", " min");
  }
  function tick() {
    var extra = running() ? now() - st.since : 0, block = blockOf(st.cur);
    clock.textContent = fmt(st.elapsed + extra, true);
    root.querySelectorAll("[data-iv-block-time]").forEach(function (el) {
      var id = el.getAttribute("data-iv-block-time");
      var ms = (st.blocks[id] || 0) + (id === block ? extra : 0);
      if (!el.hasAttribute("data-planned")) el.setAttribute("data-planned", el.textContent.trim());
      el.textContent = ms >= 1000 ? fmt(ms) + (el.getAttribute("data-planned") ? " / " + el.getAttribute("data-planned").replace(/^(ca\.|approx\.) /, "") : "")
                                  : el.getAttribute("data-planned");
    });
    pauseButton.querySelector("span").textContent = running() ? de_en("Pause", "Pause") : (st.elapsed ? de_en("Fortsetzen", "Continue") : de_en("Start", "Start"));
    pauseButton.classList.toggle("is-paused", !running() && st.elapsed > 0);
  }
  function start() { if (!running()) { st.since = now(); save(); tick(); } }
  pauseButton.addEventListener("click", function () {
    if (running()) { settle(); st.since = null; } else st.since = now();
    save(); tick();
  });
  setInterval(tick, 1000);

  // --- pages
  function questionState(id) { return st.states[id] || ""; }
  function marks() {
    var core = 0, met = 0, missing = [];
    steps.forEach(function (s) {
      var id = s.getAttribute("data-iv-question");
      if (!id) return;
      var value = questionState(id);
      var mark = root.querySelector('[data-iv-mark="' + s.getAttribute("data-iv-step") + '"]');
      if (mark) {
        mark.classList.toggle("is-done", value === "asked" || value === "spontaneous");
        mark.classList.toggle("is-skipped", value === "skipped");
      }
      if (s.hasAttribute("data-iv-core")) {
        core += 1;
        if (value === "asked" || value === "spontaneous") met += 1; else missing.push(s.getAttribute("data-iv-label"));
      }
    });
    // the Unterfragen under their Leitfrage in the list on the left (interview/conduct.html, data-iv-sub-mark)
    root.querySelectorAll("[data-iv-sub-mark]").forEach(function (li) {
      var value = questionState(li.getAttribute("data-iv-sub-mark"));
      li.classList.toggle("is-done", value === "asked" || value === "spontaneous");
      li.classList.toggle("is-skipped", value === "skipped");
    });
    var count = root.querySelector("[data-iv-core-count]");
    if (count) count.textContent = core ? de_en("Kernfragen: " + met + " von " + core + " vorgekommen", "Core questions: " + met + " of " + core + " came up") : "";
    var list = root.querySelector("[data-iv-missing-list]");
    if (list) {
      list.innerHTML = "";
      missing.forEach(function (label) { var li = document.createElement("li"); li.textContent = label; list.appendChild(li); });
      root.querySelector("[data-iv-missing]").hidden = !missing.length;
      root.querySelector("[data-iv-missing-none]").hidden = missing.length > 0 || !core;
    }
  }
  function show(n, focus) {
    n = Math.max(0, Math.min(steps.length - 1, n));
    if (n !== st.cur) { settle(); st.cur = n; }
    steps.forEach(function (s, i) { s.hidden = i !== n; });
    root.querySelectorAll("[data-iv-mark], [data-iv-go]").forEach(function (el) { el.removeAttribute("aria-current"); });
    var mark = root.querySelector('[data-iv-mark="' + n + '"]');
    if (mark) mark.setAttribute("aria-current", "step");
    var block = blockOf(n);
    root.querySelectorAll("[data-iv-block]").forEach(function (el) { el.classList.toggle("is-current", el.getAttribute("data-iv-block") === block); });
    prev.disabled = n === 0;
    next.disabled = n === steps.length - 1;
    pos.textContent = (n + 1) + " / " + steps.length;
    if (focus) steps[n].scrollIntoView({ block: "start" });
    marks();
    save();
    tick();
  }
  prev.addEventListener("click", function () { start(); show(st.cur - 1, true); });
  // forward from a form only once its required questions are answered (a jump forward in the list too)
  function forward(n) {
    var from = st.cur;
    if (n <= from) { show(n, true); return; }
    next.disabled = true;
    openOn(from).then(function (open) { next.disabled = false; if (!open.length && st.cur === from) show(n, true); });
  }
  next.addEventListener("click", function () { start(); forward(st.cur + 1); });
  root.addEventListener("click", function (e) {
    var go = e.target.closest && e.target.closest("[data-iv-go]");
    if (!go) return;
    start();
    forward(parseInt(go.getAttribute("data-iv-go"), 10));
    var jump = go.closest("details");
    if (jump) jump.open = false;
  });
  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey || /^(INPUT|TEXTAREA|SELECT)$/.test((e.target || {}).tagName || "")) return;
    if (document.body.classList.contains("iv-showing")) return;  // a task is shown (Zum Zeigen): no other page
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); next.click(); }
    if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); prev.click(); }
  });

  // --- ticks and states: kept in the tab only
  root.querySelectorAll("[data-iv-tick]").forEach(function (box) {
    box.checked = !!st.ticks[box.getAttribute("data-iv-tick")];
    box.addEventListener("change", function () {
      start();
      if (box.checked) st.ticks[box.getAttribute("data-iv-tick")] = true; else delete st.ticks[box.getAttribute("data-iv-tick")];
      save();
      conditions();
    });
  });

  // --- Nachfragen and conditions of the concrete questions (concept bedingungen-leitfaden): a row is paled, never
  // hidden, when "trifft nicht zu" of its concrete question is chosen (B), when its aspect came up or did not (C), or
  // when the Kurzfragebogen's answer is another one (D). An answer not given yet decides nothing.
  function caseAnswer(id) {  // the codes chosen in the Kurzfragebogen of a stored interview, else the tab's own
    var form = root.querySelector("[data-iv-form] [name='i" + id + "']");
    if (form) {
      var chosen = [];
      root.querySelectorAll("[data-iv-form] [name='i" + id + "']").forEach(function (el) {
        if (el.tagName === "SELECT") { if (el.value) chosen.push(el.value); }
        else if (el.checked) chosen.push(el.value);
      });
      return chosen;
    }
    return (st.cases || {})[id] || [];
  }
  function off(el) {  // why a row is paled now, or ""
    var item = (el.getAttribute("data-iv-probe") || "").split(":")[0];
    var parent = el.getAttribute("data-iv-parent");
    if (parent && (st.fits || {})[parent] === "no") {
      return de_en("entfällt: K{n} trifft nicht zu", "not needed: Q{n} does not apply").replace("{n}", el.getAttribute("data-iv-parent-n") || "");
    }
    var raw = el.getAttribute("data-iv-if");
    if (!raw) return "";
    var cond;
    try { cond = JSON.parse(raw); } catch (e) { return ""; }
    if (cond.probe) {  // an earlier concrete question and its switch (E); while it is open nothing is paled
      var fit = (st.fits || {})[(cond.of || item) + ":" + cond.probe];
      if (cond.fits && fit === "no") return de_en("entfällt: K{n} trifft nicht zu", "not needed: Q{n} does not apply").replace("{n}", cond.n);
      if (!cond.fits && fit === "yes") return de_en("entfällt: K{n} trifft zu", "not needed: Q{n} applies").replace("{n}", cond.n);
      return "";
    }
    if (cond.aspect) {
      var came = !!st.ticks[item + ":aspects:" + cond.aspect];
      if (cond.mode === "present" && !came) return de_en("Aspekt noch nicht angesprochen", "aspect has not come up yet");
      if (cond.mode !== "present" && came) return de_en("kam schon von selbst", "came up by itself");
      return "";
    }
    var given = caseAnswer(cond.item);
    if (given.length && given.indexOf(String(cond.code)) < 0) return de_en("entfällt: anderes Fallmerkmal", "not needed: other case feature");
    return "";
  }
  function met(el) {  // the condition of a row that is not paled: known to hold (true) or still open (false)
    var item = (el.getAttribute("data-iv-probe") || "").split(":")[0];
    var parent = el.getAttribute("data-iv-parent"), raw = el.getAttribute("data-iv-if"), cond = null;
    if (raw) { try { cond = JSON.parse(raw); } catch (e) { return false; } }
    if (!cond) return !!parent && (st.fits || {})[parent] === "yes";
    if (parent && (st.fits || {})[parent] !== "yes") return false;
    if (cond.probe) return (st.fits || {})[(cond.of || item) + ":" + cond.probe] === (cond.fits ? "yes" : "no");
    if (cond.aspect) return cond.mode === "present";  // came up; "did not come up" stays open until the end
    return caseAnswer(cond.item).indexOf(String(cond.code)) >= 0;
  }
  function conditions() {
    // the line over a row with a condition says its state (Ralf 2026-10-22): met, still open, or why it is paled
    root.querySelectorAll("[data-iv-probe], [data-iv-sub][data-iv-if]").forEach(function (el) {
      var why = off(el), note = el.querySelector(":scope > .iv-cond [data-iv-off]");
      el.classList.toggle("is-off", !!why);
      el.classList.toggle("is-met", !why && met(el));
      if (note) note.textContent = "· " + (why || (met(el) ? de_en("erfüllt", "met") : de_en("noch offen", "still open")));
    });
    root.querySelectorAll("[data-iv-fit]").forEach(function (radio) {
      radio.checked = (st.fits || {})[radio.getAttribute("data-iv-fit")] === radio.value;
    });
    root.querySelectorAll("[data-iv-case]").forEach(function (chip) {
      var on = caseAnswer(chip.getAttribute("data-iv-case")).indexOf(chip.getAttribute("data-code")) >= 0;
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  root.querySelectorAll("[data-iv-fit]").forEach(function (radio) {
    radio.addEventListener("change", function () {
      start();
      st.fits = st.fits || {};
      st.fits[radio.getAttribute("data-iv-fit")] = radio.value;
      save();
      conditions();
    });
  });
  // a case feature tapped during the talk: into the Kurzfragebogen of a stored interview (it saves itself, D), else
  // kept in the tab; tapping the chosen one again takes it back
  root.addEventListener("click", function (e) {
    var chip = e.target.closest && e.target.closest("[data-iv-case]");
    if (!chip) return;
    var id = chip.getAttribute("data-iv-case"), code = chip.getAttribute("data-code"), multi = chip.getAttribute("data-multi") === "1";
    var fields = root.querySelectorAll("[data-iv-form] [name='i" + id + "']");
    var chosen = caseAnswer(id), was = chosen.indexOf(code) >= 0;
    var next_ = multi ? (was ? chosen.filter(function (c) { return c !== code; }) : chosen.concat([code])) : (was ? [] : [code]);
    if (fields.length) {
      fields.forEach(function (el) {
        if (el.tagName === "SELECT") el.value = next_.length ? next_[0] : "";
        else el.checked = next_.indexOf(el.value) >= 0;
      });
      fields[0].dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      st.cases = st.cases || {};
      st.cases[id] = next_;
      save();
    }
    conditions();
  });
  root.querySelectorAll("[data-iv-form]").forEach(function (form) { form.addEventListener("change", conditions); });
  root.querySelectorAll("[data-iv-state]").forEach(function (radio) {
    radio.checked = questionState(radio.getAttribute("data-iv-state")) === radio.value;
    radio.addEventListener("change", function () {
      start();
      st.states[radio.getAttribute("data-iv-state")] = radio.value;
      save();
      marks();
    });
  });

  // --- a new interview: two clicks (the first asks), then everything back to the start
  // --- a stored interview: notes and time marks per Leitfrage, the forms of Kurzfragebogen and Postskript
  root.querySelectorAll("[data-iv-note]").forEach(function (area) {
    var id = area.getAttribute("data-iv-note");
    area.value = (st.notes || {})[id] || "";
    area.addEventListener("input", function () { st.notes[id] = area.value; save(); });
  });
  function two(x) { return (x < 10 ? "0" : "") + x; }
  function drawMarks() {
    root.querySelectorAll("[data-iv-marks]").forEach(function (list) {
      var id = list.getAttribute("data-iv-marks");
      list.innerHTML = "";
      st.marks.forEach(function (m, n) {
        if (String(m.item) !== id) return;
        var li = document.createElement("li"), time = document.createElement("b"), text = document.createElement("input");
        var gone = document.createElement("button");
        time.textContent = two(Math.floor(m.t / 3600)) + ":" + two(Math.floor(m.t / 60) % 60) + ":" + two(m.t % 60);
        text.value = m.text || ""; text.maxLength = 300; text.placeholder = de_en("Stichwort", "Keyword"); text.setAttribute("aria-label", de_en("Stichwort zur Zeitmarke", "Keyword for the time mark"));
        text.addEventListener("input", function () { m.text = text.value; save(); });
        gone.type = "button"; gone.className = "icon-btn"; gone.textContent = "×"; gone.title = de_en("Zeitmarke entfernen", "Remove time mark");
        gone.addEventListener("click", function () { st.marks.splice(n, 1); save(); drawMarks(); });
        li.appendChild(time); li.appendChild(text); li.appendChild(gone);
        list.appendChild(li);
      });
    });
  }
  root.querySelectorAll("[data-iv-mark-add]").forEach(function (b) {
    b.addEventListener("click", function () {
      start();
      st.marks.push({ t: Math.floor(current().elapsed / 1000), item: b.getAttribute("data-iv-mark-add"), text: "" });
      st.marks.sort(function (x, y) { return x.t - y.t; });
      save(); drawMarks();
      var list = root.querySelector('[data-iv-marks="' + b.getAttribute("data-iv-mark-add") + '"]');
      var inputs = list ? list.querySelectorAll("input") : [];
      if (inputs.length) inputs[inputs.length - 1].focus();
    });
  });
  drawMarks();
  // Kurzfragebogen and Postskript: each form saves a moment after a change. A value the server does not take (an
  // age above the maximum) stands at its question and the line says "not saved" (F14); "Interview beenden" sends
  // what still waits and waits for the answers (G2).
  // Required questions (pretest 2026-10-22): the form saves what is given, the server names the required questions
  // still open ("missing", interview_data.missing_required); they are marked once "Weiter" or "Interview beenden"
  // was tried, and both hold on this form until they are answered, as on a page of a questionnaire.
  var forms = [], formChecks = [];
  function draftKey(form) {
    function v(name) { var el = form.querySelector("[name=" + name + "]"); return el ? el.value : ""; }
    return form.getAttribute("action") + "|" + v("part") + "|" + v("task");
  }
  // offline mode: the required questions of a form without an answer (E7 a; the server checks again at the sync),
  // and a form filled in again with what the kit kept when an interview is continued
  function localMissing(form) {
    var out = {};
    form.querySelectorAll(".p-item").forEach(function (item) {
      if (!item.querySelector(".p-required") || item.closest("[hidden]")) return;
      var given = Array.prototype.some.call(item.querySelectorAll("input, select, textarea"), function (el) {
        if (!el.name || el.disabled) return false;
        if (el.type === "checkbox" || el.type === "radio") return el.checked;
        return String(el.value || "").trim() !== "";
      });
      if (!given) out[item.id.replace(/^i/, "")] = de_en("Pflichtfrage: bitte beantworten.", "Required question: please answer.");
    });
    return out;
  }
  function restoreForm(form, list) {
    var byName = {};
    (list || []).forEach(function (p) { (byName[p[0]] = byName[p[0]] || []).push(p[1]); });
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || ["file", "submit", "button"].indexOf(el.type) >= 0 || ["csrf_token", "part", "task", "t"].indexOf(el.name) >= 0) return;
      var values = byName[el.name] || [];
      if (el.type === "checkbox" || el.type === "radio") el.checked = values.indexOf(el.value) >= 0;
      else if (el.tagName === "SELECT" && el.multiple) Array.prototype.forEach.call(el.options, function (o) { o.selected = values.indexOf(o.value) >= 0; });
      else if (values.length) el.value = values.shift();
    });
  }
  function draft(form) {  // what the form sends, without the token of this page and without files
    var out = [];
    new FormData(form).forEach(function (value, name) { if (name !== "csrf_token" && typeof value === "string") out.push([name, value]); });
    return out;
  }
  // Answers kept from a tab that was closed before they reached the server: sent as they were, then the page is
  // loaded once more, so every form and task shows them as the server has them now. Offline they wait for onBack.
  var replaying = false;
  function replayDrafts() {
    var keys = Object.keys(st.forms || {});
    if (!saveUrl || replaying || !keys.length) return;
    replaying = true;
    say(de_en("Antworten von vorhin werden nachgeschickt …", "Sending answers from before …"), "busy");
    var sent = 0;
    keys.reduce(function (chain, k) {
      return chain.then(function () {
        var body = new FormData();
        body.append("csrf_token", csrf || "");
        (st.forms[k] || []).forEach(function (p) { body.append(p[0], p[1]); });
        return fetch(k.split("|")[0], { method: "POST", body: body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
          .then(function (r) { reached(r); if (r.ok) { delete st.forms[k]; keep(); sent += 1; } }, function () { reached(false); });
      });
    }, Promise.resolve()).then(function () {
      replaying = false;
      if (sent && !Object.keys(st.forms).length) { if (st.dirty) push(function () { location.reload(); }); else location.reload(); return; }
      if (Object.keys(st.forms).length) say(stuck() ? offlineSay() : de_en("nicht gespeichert, wird wiederholt", "not saved, retrying"), "error");
    });
  }
  onBack.push(replayDrafts);
  root.querySelectorAll("[data-iv-form]").forEach(function (form) {
    // one request at a time: a change while one is under way is sent once it answers, with the form as it is then,
    // so an older answer never comes last and wins (code review 2026-10-15, I2)
    // nudges: questions set to "Nachfragen", marked once on "Weiter"; a second "Weiter" goes on without them
    var timer = null, running = null, again = false, missing = {}, nudges = {}, checked = false, nudged = false, invalid = {};
    function marks() { return Object.assign({}, nudged ? nudges : {}, checked ? missing : {}, invalid); }
    function showErrors(errors) {
      form.querySelectorAll("[data-iv-form-error]").forEach(function (el) { el.remove(); });
      form.querySelectorAll(".p-item.has-error").forEach(function (el) { el.classList.remove("has-error"); });
      Object.keys(errors || {}).forEach(function (id) {
        var item = form.querySelector("#i" + id);
        if (!item) return;
        var p = document.createElement("p");
        p.className = "p-error"; p.setAttribute("role", "alert"); p.setAttribute("data-iv-form-error", "");
        p.textContent = errors[id];
        item.classList.add("has-error"); item.appendChild(p);
      });
    }
    function send() {
      if (timer) { clearTimeout(timer); timer = null; }
      if (running) { again = true; return running; }
      again = false;
      // a task (interview/_task.html): the clock goes along; the server keeps the time it was first given
      var taskTime = form.querySelector("[data-iv-task-t]");
      if (taskTime) taskTime.value = String(Math.floor(current().elapsed / 1000));
      if (OM) {  // E7 a: kept with the kit; the required questions checked here, again by the server at the sync
        if (id) { st.forms = st.forms || {}; st.forms[id] = draft(form); keep(); }
        missing = localMissing(form); invalid = {}; nudges = {};
        showErrors(marks());
        say(de_en("auf dem Gerät gespeichert", "saved on the device"), "ok");
        return Promise.resolve(true);
      }
      say(de_en("speichert …", "saving …"), "busy");
      running = fetch(form.getAttribute("action"), { method: "POST", body: new FormData(form), credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { reached(r); if (!r.ok) throw new Error(r.status); return r.json(); }, function (e) { reached(false); throw e; })
        .then(function (d) {
          running = null;
          if (again) return send();  // changed meanwhile: the newer state goes out now
          var errors = (d && d.errors) || {}, bad = Object.keys(errors).length;
          invalid = errors; missing = (d && d.missing) || {}; nudges = (d && d.nudges) || {};
          showErrors(marks());
          if (bad) { say(de_en("nicht gespeichert: Eine Angabe ist ungültig, siehe die markierte Frage", "not saved: an entry is not valid, see the marked question"), "error"); return false; }
          if (st.forms && st.forms[id]) { delete st.forms[id]; keep(); }  // the server has it: no copy on the device
          say(de_en("gespeichert", "saved"), "ok");
          return true;
        })
        .catch(function () {
          running = null; again = false;
          say(stuck() ? offlineSay() : de_en("nicht gespeichert, wird wiederholt", "not saved, retrying"), "error");
          timer = setTimeout(send, 5000); return false;
        });
      return running;
    }
    // the answers as typed, kept on the device until the server has them (E, Ralf 2026-10-22): every field the form
    // sends, the hidden ones of a ranking, card sort or image choice too, so a closed tab loses no task result either.
    // One key per form: the forms of one interview share their address and differ in "part" and "task".
    var id = draftKey(form);
    function later() {
      if ((saveUrl || OM) && id) { st.forms = st.forms || {}; st.forms[id] = draft(form); keep(); }
      if (timer) clearTimeout(timer); timer = setTimeout(send, 700);
    }
    onBack.push(function () { if (timer) { clearTimeout(timer); send(); } });
    if (OM && st.forms && st.forms[id]) restoreForm(form, st.forms[id]);  // a continued offline interview
    form.addEventListener("change", later);
    form.addEventListener("input", later);
    form.addEventListener("submit", function (e) { e.preventDefault(); if (timer) clearTimeout(timer); send(); });
    forms.push(function flush() {  // what waits goes out now; resolves true once the latest state is stored
      if (running) return running.then(flush);
      if (timer) { clearTimeout(timer); return send(); }
      return Promise.resolve(true);
    });
    // sends the form as it is and resolves with the ids of the required questions still open (marked from now on);
    // without a connection the last answer of the server counts, so a lost network never locks the interview
    function point(open, soft) {
      say(soft ? de_en("Frage noch offen, „Weiter“ noch einmal geht ohne Antwort weiter", "question still open, “Next” again continues without an answer")
               : de_en("Pflichtfrage noch offen, siehe die markierte Frage", "required question still open, see the marked question"), "error");
      var first = form.querySelector("#i" + open[0]);
      if (first) { first.scrollIntoView({ block: "center" }); var field = first.querySelector("input, select, textarea"); if (field) field.focus(); }
    }
    // soft: "Interview beenden", where only the required questions hold
    formChecks.push({ form: form, point: point, check: function (soft) {
      var wait = running ? running.then(function () { return send(); }) : send();
      return wait.then(function () {
        checked = true;
        var open = Object.keys(missing), asked = soft || nudged ? [] : Object.keys(nudges);
        if (!open.length && asked.length) nudged = true;
        showErrors(marks());
        if (open.length) point(open);
        else if (asked.length) point(asked, true);
        return open.length ? open : asked;
      });
    } });
  });
  function openOn(n) {  // resolves with the required questions still open on step n (none when it holds no form)
    var on = formChecks.filter(function (f) { return steps[n] && steps[n].contains(f.form); });
    return Promise.all(on.map(function (f) { return f.check(); })).then(function (all) { return [].concat.apply([], all); });
  }
  // "Zum Zeigen" (interview/_task.html, app/interview_tasks.py): the task alone, full screen, without guide, notes
  // and clock, to hand the device over. "Zurück" ends it only when held down for a moment (HOLD_MS), so that a tap
  // by the person holding the device does not bring back the interviewer's notes; Esc leaves the browser's full
  // screen, not the show mode.
  var HOLD_MS = 800;
  function endShow(card) {
    card.classList.remove("is-shown", "is-holding");
    document.body.classList.remove("iv-showing");
    var back = card.querySelector("[data-iv-show-back]");
    if (back) back.hidden = true;
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () { /* already left */ });
    var show = card.querySelector("[data-iv-show]");
    if (show) show.focus();
  }
  root.querySelectorAll("[data-iv-task]").forEach(function (card) {
    var show = card.querySelector("[data-iv-show]"), back = card.querySelector("[data-iv-show-back]"), timer = null;
    if (!show || !back) return;
    show.addEventListener("click", function () {
      card.classList.add("is-shown");
      document.body.classList.add("iv-showing");
      back.hidden = false;
      if (card.requestFullscreen) card.requestFullscreen().catch(function () { /* not allowed: the page's own full view stays */ });
      var first = card.querySelector("[data-iv-show-area] input, [data-iv-show-area] button, [data-iv-show-area] [tabindex]");
      if (first) first.focus();
    });
    function hold(e) {
      if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
      if (e.type === "keydown") { e.preventDefault(); if (e.repeat || timer) return; }
      card.classList.add("is-holding");
      timer = setTimeout(function () { timer = null; endShow(card); }, HOLD_MS);
    }
    function release() { if (timer) { clearTimeout(timer); timer = null; } card.classList.remove("is-holding"); }
    back.addEventListener("pointerdown", hold);
    back.addEventListener("keydown", hold);
    ["pointerup", "pointerleave", "pointercancel", "keyup", "blur"].forEach(function (name) { back.addEventListener(name, release); });
    back.addEventListener("click", function (e) { e.preventDefault(); });
  });

  function formsSent() { return Promise.all(forms.map(function (f) { return f(); })).then(function (all) { return all.every(Boolean); }); }
  var audioPending = function () { return 0; };  // pieces of the recording not sent yet (set below)
  var finishButton = root.querySelector("[data-iv-finish]"), forced = false, formsForced = false;
  // Without a connection "Interview beenden" is kept in mind: the line says so, and the end comes by itself once the
  // connection is back (the "online" event, or a try every ten seconds), with every piece and every answer first.
  var endLater = false, endTimer = null;
  function waitForConnection() {
    endLater = true;
    if (signedOut && !netDown) say(de_en("Anmeldung abgelaufen: „Interview beenden“ ist vorgemerkt und geschieht von selbst, sobald Sie sich in einem neuen Tab wieder angemeldet haben (Interviewlink oder Anmeldung). Bitte diese Seite offen lassen.",
                                         "Sign-in expired: “End interview” is noted and happens by itself once you have signed in again in a new tab (interviewer link or sign-in). Please keep this page open."), "error");
    else say(de_en("Keine Verbindung: „Interview beenden“ ist vorgemerkt und geschieht von selbst, sobald die Verbindung wieder da ist. Bitte diese Seite offen lassen.",
                   "No connection: “End interview” is noted and happens by itself once the connection is back. Please keep this page open."), "error");
    if (!endTimer) endTimer = setTimeout(function () { endTimer = null; finish(); }, 10000);
  }
  endFailed = waitForConnection;
  onBack.push(function () { if (endLater) { if (endTimer) { clearTimeout(endTimer); endTimer = null; } finish(); } });
  if (finishButton) finishButton.addEventListener("click", function (e) { e.preventDefault(); if (OM) finishOffline(); else finish(); });
  function finishOffline() {  // offline mode: the recording ends, the required questions of the Kurzfragebogen hold,
    // then the kit keeps the interview as ended and shows its deletion code
    if (st.since !== null) { settle(); st.since = null; }
    var stopRec = root.querySelector("[data-iv-rec-stop]"), stopped = stopRec && !stopRec.hidden;
    if (stopped) stopRec.click();
    setTimeout(function () {
      var short = formChecks.filter(function (f) { var p = f.form.querySelector("[name=part]"); return p && p.value === "short"; })[0];
      (short ? short.check(true) : Promise.resolve([])).then(function (open) {
        if (open.length) {
          var step = short.form.closest("[data-iv-step]");
          if (step) show(steps.indexOf(step), false);
          short.point(open);
          return;
        }
        forms.forEach(function (f) { f(); });
        AskuriousOffline.finish(st);
      });
    }, stopped ? 800 : 0);
  }
  function finish() {  // the last state first, then the end
    if (st.since !== null) { settle(); st.since = null; }
    if (pushTimer) clearTimeout(pushTimer);
    var stopRec = root.querySelector("[data-iv-rec-stop]"), stopped = stopRec && !stopRec.hidden;
    if (stopped) stopRec.click();  // the recording ends with the interview; its last piece arrives a moment later
    var waited = 0;
    setTimeout(function whenSent() {  // the last pieces of the recording first (at most half a minute), then the state, then the end
      var left = forced ? 0 : audioPending();
      if (left && stuck()) { waitForConnection(); return; }  // offline the pieces wait in the browser, the end waits for them
      if (left && waited < 30000) { waited += 500; say(de_en("Aufnahme wird übertragen …", "Sending the recording …"), "busy"); setTimeout(whenSent, 500); return; }
      if (left) {  // not silently: the pieces still missing are named, a second click ends anyway (data audit I-26)
        forced = true;
        say(left === 1 ? de_en("Noch nicht übertragen: " + left + " Stück der Aufnahme. Die Seite des Aufnahmegeräts offen lassen; „Interview beenden“ noch einmal beendet trotzdem.",
                               "Not sent yet: " + left + " piece of the recording. Keep the page of the recording device open; “End interview” again ends anyway.")
            : de_en("Noch nicht übertragen: " + left + " Stücke der Aufnahme. Die Seite des Aufnahmegeräts offen lassen; „Interview beenden“ noch einmal beendet trotzdem.",
                    "Not sent yet: " + left + " pieces of the recording. Keep the page of the recording device open; “End interview” again ends anyway."), "error");
        return;
      }
      formsSent().then(function (ok) {  // Kurzfragebogen and Postskript first (G2)
        if (!ok && stuck()) { waitForConnection(); return; }
        if (!ok && !formsForced) {
          formsForced = true;
          say(de_en("Kurzfragebogen oder Postskript nicht gespeichert (siehe die Meldung dort). „Interview beenden“ noch einmal beendet trotzdem.",
                    "Short questionnaire or postscript not saved (see the message there). “End interview” again ends anyway."), "error");
          return;
        }
        // the Kurzfragebogen's required questions first; the Postskript may still be written afterwards
        var short = formChecks.filter(function (f) { var p = f.form.querySelector("[name=part]"); return p && p.value === "short"; })[0];
        (short ? short.check(true) : Promise.resolve([])).then(function (open) {
          if (open.length) {
            var step = short.form.closest("[data-iv-step]");
            if (step) show(steps.indexOf(step), false);
            short.point(open);
            return;
          }
          push(function () { endLater = false; forget(); finishButton.form.submit(); });
        });
      });
    }, stopped ? 800 : 0);
  }

  // --- the recording (app/interview_audio.py): MediaRecorder, a piece every five seconds, sent in order; a piece that
  // did not get through is sent again, so a short loss of the connection costs nothing. The pieces wait in the
  // browser's storage until the server has them (audiostore.js), so a reload or a tab the system ends loses none.
  // A pause ends the part, "Fortsetzen" begins a new one with its own start on the interview clock (I-11); an
  // error of the recorder or a microphone that goes away (a call, a Bluetooth headset) is shown at once (I-20).
  var recBox = root.querySelector("[data-iv-rec]");
  if (recBox && window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    var recToggle = recBox.querySelector("[data-iv-rec-toggle]"), recStop = recBox.querySelector("[data-iv-rec-stop]");
    var recState = recBox.querySelector("[data-iv-rec-state]"), recUrl = recBox.getAttribute("data-audio-url");
    var part = parseInt(recBox.getAttribute("data-audio-part"), 10) || 1, queue = [], sending = false, refused = "", trouble = "", full = false;
    var recorder = null, stream = null, recMs = 0, recSince = null, lastData = 0, lowSpace = false;
    var store = { put: function () {}, drop: function () {} };
    var mic = window.AskuriousMic ? AskuriousMic.attach(recBox) : { open: function () { return navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } }); }, show: function () {}, hide: function () {}, lock: function () {} };
    var mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].filter(function (m) {
      return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m); })[0] || "";
    function recTime() { return recMs + (recSince ? Date.now() - recSince : 0); }
    var saveButton = recBox.querySelector("[data-iv-rec-save]"), netNote = "", saved = "", savedUntil = 0;
    function drawRec() {
      // offline L: the pieces can be saved as a file whenever they do not get through (no connection, sign-in, refused)
      if (saveButton) saveButton.hidden = !(queue.length >= 2 && (stuck() || refused || full));
      if (Date.now() < savedUntil) { recState.textContent = saved; return; }
      if (refused) { recState.textContent = refused; return; }
      var s = Math.floor(recTime() / 1000), quiet = recSince && lastData ? Math.floor((Date.now() - lastData) / 1000) : 0;
      var text = (stream ? (recSince ? "● " : "Ⅱ ") + two(Math.floor(s / 60)) + ":" + two(s % 60) : "")
        + (queue.length > 1 ? " · " + de_en(queue.length + " Stücke noch nicht übertragen", queue.length + " pieces not sent yet") : "")
        + (full ? " · " + de_en("Server-Speicher voll, die Stücke bleiben im Browser", "server storage full, the pieces stay in the browser") : "")
        + (queue.length && offline() ? " · " + de_en("offline, die Stücke bleiben im Browser und werden nachgeschickt", "offline, the pieces stay in the browser and are sent later")
           : queue.length && signedOut ? " · " + de_en("Anmeldung abgelaufen, die Stücke bleiben im Browser", "sign-in expired, the pieces stay in the browser") : "")
        + (lowSpace ? " · " + de_en("Speicher auf diesem Gerät fast voll", "storage on this device almost full") : "");
      if (!stream && !queue.length && netNote) text = netNote;  // offline K: the check before the interview
      var warn = trouble || (quiet >= 15 ? de_en("seit " + quiet + " s kein Ton vom Mikrofon, Aufnahme prüfen", "no sound from the microphone for " + quiet + " s, check the recording") : "");
      recState.textContent = warn ? "⚠ " + warn + (text ? " · " + text : "") : text;
      recBox.classList.toggle("is-trouble", !!warn);
    }
    // the piece sent: pieces of an earlier visit may have been put before it meanwhile (queue = old.concat(queue)),
    // so it is taken out where it stands, not from the front (code review 2026-10-15, I8)
    function unqueue(piece) { var at = queue.indexOf(piece); if (at >= 0) queue.splice(at, 1); }
    var batchOff = 0;  // after a refused batch: piece by piece for a minute, the single answers say why
    function sendPiece() {
      if (sending || !queue.length || refused || !recUrl) return;  // offline mode: the pieces stay with the kit
      sending = true;
      var list = Date.now() > batchOff && window.AskuriousAudioStore && AskuriousAudioStore.front ? AskuriousAudioStore.front(queue) : null;
      if (list) {  // offline J: what waited goes out a minute per request
        AskuriousAudioStore.sendBatch(recUrl + "/batch", csrf, list, function (p) { return { part: p.rec.part, seq: p.seq, rec: p.rec.id, start: p.start }; })
          .then(function (r) { reached(r); if (!r.ok) throw r.status; return r.json(); }, function () { reached(false); throw 0; })
          .then(function () { full = false; list.forEach(function (p) { store.drop(p); unqueue(p); }); sending = false; drawRec(); sendPiece(); })
          .catch(function (status) {
            sending = false; drawRec();
            if (status) { batchOff = Date.now() + 60000; sendPiece(); } else setTimeout(sendPiece, 3000);
          });
        return;
      }
      var piece = queue[0];
      fetch(recUrl + "?part=" + piece.rec.part + "&seq=" + piece.seq + "&start=" + piece.start + "&rec=" + piece.rec.id, { method: "POST", body: piece.blob,
            credentials: "same-origin", headers: { "Content-Type": piece.type, "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
        .then(function (r) {
          reached(r);
          if (r.status === 409) return r.json().catch(function () { return {}; }).then(function (d) {
            // the part is taken by another recording (the other device): this recording moves to the next free part
            if (d && d.part) {
              piece.rec.part = d.part; part = Math.max(part, d.part + 1);
              queue.forEach(function (q) { if (q.rec === piece.rec) store.put(q); });
              throw 409;
            }
            // no other part to move to: more parts than allowed or a piece missing before this one; sending it again
            // cannot help, so the queue stops with the reason, the pieces stay in the browser (F15)
            throw { final: d && d.error === "parts" ? de_en("zu viele Aufnahmeteile für dieses Interview", "too many recording parts for this interview")
                                                    : de_en("ein Stück davor fehlt", "a piece before it is missing") };
          });
          if (r.status === 507) { full = true; throw 507; }  // the server's disk is full: kept here, sent later
          if (!r.ok) throw r.status; return r.json(); }, function () { reached(false); throw 0; })
        .then(function () { full = false; store.drop(piece); unqueue(piece); sending = false; drawRec(); sendPiece(); })
        .catch(function (status) {
          sending = false;
          if (status === 403 || status === 413 || status === 415) { refused = de_en("Aufnahme abgelehnt (" + status + ")", "Recording refused (" + status + ")"); drawRec(); return; }
          if (status && status.final) { refused = de_en("Aufnahme abgelehnt: ", "Recording refused: ") + status.final; drawRec(); return; }
          drawRec();
          setTimeout(sendPiece, status === 507 ? 30000 : status === 401 ? 10000 : 3000);
        });
    }
    function record() {  // a new part on the open microphone, starting at the interview clock now
      var options = { audioBitsPerSecond: 32000 };  // Opus mono at 32 kbit/s is plenty for speech and Whisper (I-23)
      if (mimeType) options.mimeType = mimeType;
      var rec;
      try { rec = new MediaRecorder(stream, options); } catch (e) { rec = mimeType ? new MediaRecorder(stream, { mimeType: mimeType }) : new MediaRecorder(stream); }
      var type = (rec.mimeType || mimeType || "audio/webm").split(";")[0], startT = Math.floor(current().elapsed / 1000), seq = 0;
      var thisRec = { part: part, id: Math.random().toString(36).slice(2, 12), began: Date.now() };  // one recording; its part may still move
      part += 1;
      rec.ondataavailable = function (e) {
        if (!e.data || !e.data.size) return;
        lastData = Date.now();
        var piece = { blob: e.data, rec: thisRec, seq: seq++, start: startT, type: type, at: Date.now() };
        queue.push(piece); store.put(piece); sendPiece();
      };
      rec.onerror = function () { lost(de_en("Aufnahme unterbrochen (Fehler im Browser), bitte neu starten", "Recording interrupted (error in the browser), please start again")); };
      rec.start(5000);
      recorder = rec;
    }
    function watch(s) {  // the microphone itself: taken away (a call, a disconnected headset) or muted for a while
      s.getAudioTracks().forEach(function (t) {
        t.onended = function () { lost(de_en("Mikrofon getrennt, Aufnahme unterbrochen; bitte neu starten", "Microphone disconnected, recording interrupted; please start again")); };
        t.onmute = function () { trouble = de_en("Mikrofon stumm geschaltet (z. B. durch einen Anruf)", "Microphone muted (e.g. by a call)"); drawRec(); };
        t.onunmute = function () { trouble = ""; drawRec(); };
      });
    }
    function lost(reason) { if (stream) end(); trouble = reason; drawRec(); }  // what was recorded is kept and sent
    // The sound of the video conference (concept konzept-online-interviews-2026-10-15.md, B):
    // the browser shares a tab or the screen with its sound (getDisplayMedia, Chrome and Edge), and an AudioContext
    // mixes it with the microphone into the one stream the recorder takes. The picture is never recorded.
    var conferenceBox = recBox.querySelector("[data-iv-conference]"), shared = null, micStream = null, mixer = null;
    function share() {
      if (!conferenceBox || !conferenceBox.checked) return Promise.resolve(null);
      if (!navigator.mediaDevices.getDisplayMedia || !window.AudioContext)
        return Promise.reject({ said: de_en("Dieser Browser kann den Ton der Videokonferenz nicht mitnehmen (nur Chrome und Edge).", "This browser cannot take the sound of the video conference (Chrome and Edge only).") });
      // called straight from the click: the browser asks only within the user's gesture
      return navigator.mediaDevices.getDisplayMedia({ video: true, audio: { echoCancellation: false, noiseSuppression: false, suppressLocalAudioPlayback: false },
                                                      systemAudio: "include", windowAudio: "system", selfBrowserSurface: "exclude", surfaceSwitching: "include" })
        .catch(function () { throw { said: de_en("Kein Bildschirm oder Tab geteilt.", "No screen or tab shared.") }; })
        .then(function (d) {
          if (!d.getAudioTracks().length) {
            d.getTracks().forEach(function (t) { t.stop(); });
            throw { said: de_en("Beim Teilen wurde kein Ton freigegeben: den Tab der Videokonferenz wählen und „Tab-Audio teilen“ ankreuzen, oder den ganzen Bildschirm mit „Systemaudio teilen“.",
                                "No sound was shared: choose the tab of the video conference and tick “Share tab audio”, or the entire screen with “Share system audio”.") };
          }
          d.getVideoTracks().forEach(function (t) { t.enabled = false; });  // kept open (Chrome ends the sharing without it), never recorded
          d.getAudioTracks().forEach(function (t) {
            t.onended = function () { trouble = de_en("Ton der Videokonferenz nicht mehr geteilt; es wird nur noch das Mikrofon aufgenommen", "Sound of the video conference no longer shared; only the microphone is recorded"); drawRec(); };
          });
          return d;
        });
    }
    function mix(m, d) {
      mixer = new AudioContext();
      var out = mixer.createMediaStreamDestination();
      out.channelCount = 1;
      mixer.createMediaStreamSource(m).connect(out);
      mixer.createMediaStreamSource(new MediaStream(d.getAudioTracks())).connect(out);
      return out.stream;
    }
    function unshare() {
      if (shared) shared.getTracks().forEach(function (t) { t.onended = null; t.stop(); });
      if (micStream && micStream !== stream) micStream.getTracks().forEach(function (t) { t.stop(); });
      if (mixer) mixer.close();
      shared = micStream = mixer = null;
    }
    function begin() {
      share().then(function (d) {
        shared = d;
        return mic.open().catch(function (e) { unshare(); throw e; });
      }).then(function (s) {
        micStream = s; trouble = "";
        stream = shared ? mix(s, shared) : s;
        watch(s);
        record();
        mic.show(s); mic.lock(true);
        start();
        recMs = 0; recSince = lastData = Date.now();
        recBox.classList.add("is-recording");
        recToggle.textContent = "Ⅱ " + de_en("Pause", "Pause");
        recStop.hidden = false;
        drawRec();
        // D (Ralf 2026-10-22): the browser is asked to keep the waiting pieces for good (audiostore.js)
        if (window.AskuriousAudioStore && AskuriousAudioStore.persist) AskuriousAudioStore.persist();
      }).catch(function (e) {
        if (e && e.said) { trouble = e.said; drawRec(); return; }  // the conference sound: the reason, the button stays
        refused = de_en("kein Zugriff auf das Mikrofon", "no access to the microphone"); drawRec();
      });
    }
    function end() {
      if (recorder && recorder.state !== "inactive") recorder.stop();
      stream.getTracks().forEach(function (t) { t.stop(); });
      unshare();
      mic.hide(); mic.lock(false);
      recorder = null; stream = null; recSince = null;
      recBox.classList.remove("is-recording");
      recToggle.textContent = "● " + de_en("Neue Aufnahme", "New recording");
      recStop.hidden = true;
    }
    recToggle.addEventListener("click", function () {
      if (!stream) { begin(); return; }
      if (recSince) {  // pause: the part ends here
        if (recorder && recorder.state !== "inactive") recorder.stop();
        recorder = null; recMs += Date.now() - recSince; recSince = null;
        recBox.classList.remove("is-recording"); recToggle.textContent = "● " + de_en("Fortsetzen", "Continue");
      } else {  // a new part with its own start
        record(); recSince = lastData = Date.now();
        recBox.classList.add("is-recording"); recToggle.textContent = "Ⅱ " + de_en("Pause", "Pause");
      }
      drawRec();
    });
    recStop.addEventListener("click", function () {
      if (!stream) return;
      end();
      drawRec();
    });
    setInterval(drawRec, 1000);
    setInterval(function () {  // the device's storage, now and then while pieces are made or wait
      if (!(stream || queue.length) || !window.AskuriousAudioStore || !AskuriousAudioStore.low) return;
      AskuriousAudioStore.low().then(function (l) { lowSpace = l; drawRec(); });
    }, 60000);
    if (window.AskuriousAudioStore) AskuriousAudioStore.open(recBox.getAttribute("data-audio-store"), OM ? AskuriousOffline.cipher : null).then(function (s) {
      store = s;
      s.left().then(function (old) {  // pieces of an earlier visit that never reached the server: sent first
        old = old.filter(function (p) { return !queue.some(function (q) { return q.rec.id === p.rec.id; }); });
        if (!old.length) return;
        old.forEach(function (p) { part = Math.max(part, p.rec.part + 1); });
        queue = old.concat(queue);
        drawRec(); sendPiece();
      });
    });
    audioPending = function () { return queue.length + (recorder ? 1 : 0); };
    onBack.push(function () { drawRec(); sendPiece(); check(); });
    if (saveButton) saveButton.addEventListener("click", function () {  // offline L: the waiting pieces as a file
      var label = (root.querySelector(".iv-label") || {}).textContent || "interview";
      var stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
      AskuriousAudioStore.save(queue.slice(), parseInt(recBox.getAttribute("data-interview-id"), 10) || null,
                               label.replace(/[^\w-]+/g, "_") + "-tonstuecke-" + stamp + ".askurious-audio.json",
                               function (p) { return { part: p.rec.part, seq: p.seq, rec: p.rec.id, start: p.start }; })
        .then(function (n) { savedUntil = Date.now() + 30000; saved = de_en(n + " Stücke als Datei gesichert; auf der Seite „Aufnahme hochladen“ des Interviews wieder einlesen",
                                                                       n + " pieces saved as a file; read them in on the interview's page “Upload recording”"); drawRec(); })
        .catch(function () { savedUntil = Date.now() + 30000; saved = de_en("Sichern nicht möglich", "Saving not possible"); drawRec(); });
    });
    // offline K: the connection before the interview; some 100 KB up, timed. Weak: the pieces wait and are sent later,
    // the person is told so before the talk begins, not during it
    var checkUrl = recBox.getAttribute("data-netcheck");
    function check() {
      if (!checkUrl || stream || queue.length) return;
      var size = 96 * 1024, began = Date.now();
      fetch(checkUrl, { method: "POST", body: new Blob([new Uint8Array(size)], { type: "application/octet-stream" }), credentials: "same-origin",
                        headers: { "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
        .then(function (r) {
          reached(r);
          if (!r.ok) { netNote = r.status === 401 ? offlineSay() : ""; drawRec(); return; }
          var kbit = Math.round(size * 8 / Math.max(1, Date.now() - began));  // bits per millisecond = kbit/s
          var speed = kbit >= 1000 ? (kbit / 1000).toFixed(1).replace(".", de_en(",", ".")) + " Mbit/s" : kbit + " kbit/s";
          netNote = kbit >= 256 ? de_en("Verbindung gut (" + speed + " hoch)", "Connection good (" + speed + " up)")
            : de_en("Verbindung schwach (" + speed + " hoch): Die Aufnahme wird nachgeschickt, die Seite offen lassen; sicherer mit dem Handy als Aufnahmegerät",
                    "Weak connection (" + speed + " up): the recording is sent later, keep the page open; safer with the phone as recorder");
          drawRec();
        }, function () { reached(false); netNote = offlineSay(); drawRec(); });
      if (window.AskuriousAudioStore && AskuriousAudioStore.low) AskuriousAudioStore.low().then(function (l) { lowSpace = l; drawRec(); });
    }
    check();
    window.addEventListener("beforeunload", function (e) {  // a running recording or pieces not sent yet: ask before leaving
      if (stream || queue.length) { e.preventDefault(); e.returnValue = ""; }
    });
  } else if (recBox) {
    recBox.querySelector("[data-iv-rec-state]").textContent = de_en("Dieser Browser kann nicht aufnehmen.", "This browser cannot record.");
    recBox.querySelector("[data-iv-rec-toggle]").disabled = true;
  }

  // --- a second device records (app/interview_remote.py): its state every few seconds; the clock starts with its
  // recording if it has not run yet, and "Interview beenden" asks it to stop (again until it confirms) and waits
  // for its last piece; a recording device that sends nothing for 15 seconds or reports a problem is shown
  var remoteBox = root.querySelector("[data-iv-remote]"), remoteState = root.querySelector("[data-iv-remote-state]");
  var remote = { state: "", alive: false }, finishing = false;
  if (remoteBox && remoteState) {
    var remoteUrl = remoteBox.getAttribute("data-state-url"), remoteStop = remoteBox.getAttribute("data-stop-url");
    var asked = false;
    function drawRemote() {
      var s = Math.floor((remote.ms || 0) / 1000), busy = remote.alive && (remote.state === "recording" || remote.state === "paused");
      var quiet = remote.alive && remote.state === "recording" && remote.ms > 20000 && remote.piece !== null && remote.piece >= 15;
      var warn = remote.problem ? remote.problem
        : quiet ? de_en("seit " + remote.piece + " s kein Tonstück angekommen", "no audio piece received for " + remote.piece + " s")
                  + (remote.pending ? " (" + de_en(remote.pending + " warten auf dem Handy", remote.pending + " waiting on the phone") + ")" : "") : "";
      remoteState.hidden = !remote.state;
      remoteState.classList.toggle("is-recording", busy && remote.state === "recording" && !warn);
      remoteState.classList.toggle("is-trouble", !!warn);
      remoteState.textContent = !remote.state ? "" : !remote.alive && remote.state !== "stopped" ? de_en("Handy meldet sich nicht", "Phone not responding")
        : (warn ? "⚠ " + de_en("Handy: ", "Phone: ") + warn + " · " : "")
          + (remote.state === "recording" ? "● " + de_en("Handy nimmt auf ", "Phone recording ") : remote.state === "paused" ? "Ⅱ " + de_en("Handy pausiert ", "Phone paused ")
             : de_en("Handy: Aufnahme beendet ", "Phone: recording ended "))
          + two(Math.floor(s / 60)) + ":" + two(s % 60);
      if (busy) remoteBox.open = false;
      var ownToggle = root.querySelector("[data-iv-rec-toggle]"), ownStop = root.querySelector("[data-iv-rec-stop]");
      if (ownToggle && ownStop) ownToggle.hidden = busy && ownStop.hidden;  // one recording at a time: the phone's
    }
    function poll() {
      if (document.visibilityState === "hidden" && !finishing) return;
      fetch(remoteUrl, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { reached(r); return r.ok ? r.json() : null; }, function () { reached(false); return null; })
        .then(function (d) {
          if (!d) return;
          remote = d;
          if (d.alive && d.state === "recording" && !running() && !st.elapsed) {  // the clock starts with the recording
            st.since = now() - (d.ms || 0); save(); tick();
          }
          drawRemote();
        })
        .catch(function () { /* the next round */ });
    }
    poll();
    setInterval(poll, 3000);
    document.addEventListener("visibilitychange", poll);
    var remotePending = function () {
      var busy = remote.alive && (remote.state === "recording" || remote.state === "paused");
      if (busy && !asked) {  // asked again until the server has the request (a refused one is no request)
        asked = true;
        fetch(remoteStop, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf || "", "X-Requested-With": "fetch" } })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) { if (!d || !d.stop) asked = false; })
          .catch(function () { asked = false; });
      }
      if (busy) setTimeout(poll, 0);
      // pieces the phone still holds; a phone that went quiet with pieces left counts too
      return busy ? Math.max(1, remote.pending || 0) : (remote.state && remote.state !== "stopped" ? remote.pending || 0 : 0);
    };
    var ownPending = audioPending;
    audioPending = function () { finishing = true; return ownPending() + remotePending(); };
  }

  var armed = null;
  if (resetButton) resetButton.addEventListener("click", function () {
    if (!armed) {
      resetButton.classList.add("is-armed");
      resetButton.lastChild.textContent = de_en(" Wirklich zurücksetzen?", " Really reset?");
      armed = setTimeout(function () { armed = null; resetButton.classList.remove("is-armed"); resetButton.lastChild.textContent = de_en(" Neues Interview", " New interview"); }, 4000);
      return;
    }
    clearTimeout(armed);
    armed = null;
    st = fresh();
    save();
    root.querySelectorAll("[data-iv-tick], [data-iv-state], [data-iv-fit]").forEach(function (box) { box.checked = false; });
    conditions();
    resetButton.classList.remove("is-armed");
    resetButton.lastChild.textContent = de_en(" Neues Interview", " New interview");
    show(0, true);
  });

  // --- offline G: the service worker (app/static/interview-sw.js) keeps this page and its files, so it opens again
  // without a network; only for a stored interview. The first visit names the page and its files to it.
  var swUrl = root.getAttribute("data-sw"), swWorker = null;
  if ((saveUrl || OM) && swUrl && navigator.serviceWorker) {
    navigator.serviceWorker.register(swUrl, { scope: location.pathname }).then(function () {
      if (!OM) { swOn = true; keep(); }
      return navigator.serviceWorker.ready;
    }).then(function (reg) {
      var files = Array.prototype.map.call(document.querySelectorAll("script[src], link[rel=stylesheet][href], link[rel~=icon][href]"),
                                           function (el) { return el.src || el.href; });
      swWorker = reg && reg.active;
      if (swWorker) swWorker.postMessage({ keep: location.href, files: files });
    }).catch(function () { /* no worker (private window, old browser): online only, as before */ });
  }
  if (offlineCopy) say(de_en("ohne Verbindung geöffnet: Stand dieses Geräts; alles wird nachgeschickt, sobald wieder Verbindung da ist",
                             "opened without a connection: this device's state; everything is sent once the connection is back"), "error");
  function forget() {  // the interview ends: no copy of it stays on this device
    swOn = false;
    try { if (local()) local().removeItem(key); } catch (e) { /* gone */ }
    var worker = swWorker || (navigator.serviceWorker && navigator.serviceWorker.controller);
    if (worker) worker.postMessage({ drop: location.href });
  }

  conditions();
  show(st.cur || 0, false);
  replayDrafts();
  }
})();
