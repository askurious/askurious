// The audience's notes on presentation slides of a live vote (app/live/notes.py; concept folien-notizen, Ralf
// 2026-10-29: all as recommended). The phone shows the slide, a note field and a strip of the slides so far; on a
// wide screen (900 px and more) the note field stands beside the slide and a pen writes on it: a pen or the mouse
// draw, a finger scrolls. Notes stay on this device (IndexedDB, per vote and slide; E1). The audience pages back,
// never beyond the presenter's place ("upto" of /state; E3): whoever pages back stops following until "Zur aktuellen
// Folie". "Folien mit Notizen herunterladen" sends the notes once for the PDF; the server keeps none of them (E7).
// live.js asks the state and hands it over as the event "live:poll" on the stage; a new slide on the screen does
// not load the page again while the notes are open.
(function () {
  var cfg = document.querySelector("[data-live-notes-cfg]");
  var stage = document.querySelector("[data-live]");
  var Ink = window.AskInk;
  if (!cfg || !stage || !Ink) return;
  var CODE = cfg.dataset.code;
  var slides = [];
  try { slides = JSON.parse(cfg.dataset.slides || "[]"); } catch (e) { slides = []; }
  var upto = parseInt(cfg.dataset.upto, 10) || 0;  // the slides reached: slides[0 .. upto - 1]
  var MAX_STROKES = 2000, MAX_SLIDE_BYTES = 300000, MAX_BYTES = 6000000;  // app/live/notes.py (E10)
  var COLOURS = { black: "#1a1a1a", red: "#d32f2f", blue: "#1565c0", marker: "#ffd400" };
  function url(base, id) { return base.split("/folie/0").join("/folie/" + id); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }
  function fill(text, n) { return text.split("{n}").join(n); }

  // --- the notes on this device: IndexedDB, else only in memory (a private window, storage blocked) -------------
  var memory = {}, dbp = null, stored = true;
  function openDb() {
    if (dbp) return dbp;
    dbp = new Promise(function (resolve) {
      try {
        var req = indexedDB.open("askurious-notes", 1);
        req.onupgradeneeded = function () { req.result.createObjectStore("notes"); };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { resolve(null); };
        req.onblocked = function () { resolve(null); };
      } catch (e) { resolve(null); }
    }).then(function (db) { if (!db) { stored = false; noStore(); } return db; });
    return dbp;
  }
  function key(id) { return CODE + ":" + id; }
  function load(id) {
    if (memory[id]) return Promise.resolve(memory[id]);
    return openDb().then(function (db) {
      if (!db) return memory[id] || { t: "", s: [] };
      return new Promise(function (resolve) {
        try {
          var req = db.transaction("notes", "readonly").objectStore("notes").get(key(id));
          req.onsuccess = function () { resolve(req.result || { t: "", s: [] }); };
          req.onerror = function () { resolve({ t: "", s: [] }); };
        } catch (e) { resolve({ t: "", s: [] }); }
      });
    }).then(function (n) { memory[id] = memory[id] || { t: n.t || "", s: n.s || [] }; return memory[id]; });
  }
  function save(id) {
    var n = memory[id];
    if (!n) return;
    openDb().then(function (db) {
      if (!db) return;
      try {
        var store = db.transaction("notes", "readwrite").objectStore("notes");
        if (!n.t && !n.s.length) store.delete(key(id)); else store.put({ t: n.t, s: n.s }, key(id));
      } catch (e) { stored = false; noStore(); }
    });
  }
  function noStore() { document.querySelectorAll("[data-notes-saved]").forEach(function (el) { el.textContent = cfg.dataset.noStore; }); }

  // --- the view on the stage (live/_notes.html): only while the phone shows a presentation slide ----------------
  var view = stage.querySelector("[data-live-notes]");
  var V = null;
  if (view) V = build(view);

  function build(view) {
    var img = view.querySelector("[data-notes-img]"), pic = view.querySelector(".live-mynotes-pic");
    var text = view.querySelector("[data-notes-text]"), strip = view.querySelector("[data-notes-strip]");
    var behind = view.querySelector("[data-notes-behind]"), tools = view.querySelector("[data-notes-tools]");
    var error = view.querySelector("[data-notes-error]"), theirsBox = view.querySelector("[data-notes-theirs]");
    var undoBtn = view.querySelector("[data-notes-undo]");
    var layer = document.createElement("canvas");
    layer.className = "live-ink-layer live-mynotes-layer";
    layer.setAttribute("aria-hidden", "true");
    pic.classList.add("has-ink");
    pic.appendChild(layer);
    var ctx = layer.getContext("2d");
    var S = { current: parseInt(view.dataset.current, 10), at: parseInt(view.dataset.current, 10), follow: true,
              box: null, drawing: null, tool: "pen", colour: COLOURS.black, theirs: { strokes: [], rev: -2 }, undo: [] };
    var wide = window.matchMedia ? window.matchMedia("(min-width: 900px)") : { matches: false };

    function mine() { return memory[S.at] || { t: "", s: [] }; }
    function paint() {
      if (!S.box) return;
      var layers = [theirsBox && !theirsBox.checked ? [] : S.theirs.strokes, mine().s];
      if (S.drawing) layers.push([S.drawing]);
      Ink.paint(ctx, S.box, layers);
    }
    function place() { S.box = Ink.fit(img, layer); paint(); }
    img.addEventListener("load", place);
    window.addEventListener("resize", place);
    if (window.ResizeObserver) new ResizeObserver(place).observe(pic);
    function say(msg) { error.textContent = msg || ""; error.hidden = !msg; }

    function theirs(force) {  // the presenter's strokes on the slide shown (E6)
      var id = S.at;
      fetch(url(cfg.dataset.ink, id) + (force ? "" : "?rev=" + S.theirs.rev), { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && id === S.at && !d.same) { S.theirs = { strokes: d.strokes || [], rev: d.rev }; paint(); } })
        .catch(function () { /* offline for a moment */ });
    }
    function drawStrip() {
      strip.innerHTML = slides.map(function (s, n) {
        var open = n < upto, label = fill(cfg.dataset.slideLabel, s[1]);
        var cls = "live-mynotes-thumb" + (s[0] === S.at ? " is-shown" : "") + (s[0] === S.current ? " is-current" : "") +
          (memory[s[0]] && (memory[s[0]].t || memory[s[0]].s.length) ? " has-note" : "");
        return '<button type="button" class="' + cls + '" data-notes-go="' + s[0] + '"' + (open ? "" : " disabled") +
          ' title="' + esc(open ? label : label + " (" + cfg.dataset.locked + ")") + '"' + (s[0] === S.at ? ' aria-current="true"' : "") + ">" +
          (open ? '<img src="' + esc(url(cfg.dataset.img, s[0]) + "?klein=1") + '" alt="" loading="lazy">' : "") +
          '<span class="live-mynotes-num">' + s[1] + "</span></button>";
      }).join("");
      var shown = strip.querySelector(".is-shown");
      // the slide shown in sight within the strip; the page itself does not move
      if (shown) strip.scrollLeft = Math.max(0, shown.offsetLeft - (strip.clientWidth - shown.offsetWidth) / 2);
    }
    function show(id) {
      S.at = id;
      S.undo = [];
      if (undoBtn) undoBtn.disabled = true;
      S.theirs = { strokes: [], rev: -2 };
      img.src = url(cfg.dataset.img, id);
      var n = (slides.find(function (s) { return s[0] === id; }) || [id, ""])[1];
      img.alt = fill(cfg.dataset.slideLabel, n);
      behind.hidden = S.follow || id === S.current;
      text.value = "";
      load(id).then(function (note) { if (S.at === id) { text.value = note.t; paint(); drawStrip(); } });
      theirs(true);
      drawStrip();
      say("");
    }
    strip.addEventListener("click", function (e) {
      var b = e.target.closest("[data-notes-go]");
      if (!b || b.disabled) return;
      var id = parseInt(b.dataset.notesGo, 10);
      S.follow = id === S.current;
      show(id);
    });
    view.querySelector("[data-notes-now]").addEventListener("click", function () { S.follow = true; show(S.current); });

    var typing = null;
    text.addEventListener("input", function () {
      var id = S.at;
      load(id).then(function (note) { note.t = text.value.slice(0, 5000); clearTimeout(typing); typing = setTimeout(function () { save(id); drawStrip(); }, 400); });
    });
    if (theirsBox) theirsBox.addEventListener("change", paint);

    // the pen: only on a wide screen, only a pen or the mouse (a finger scrolls; E5)
    function penOn() { var on = !!wide.matches; if (tools) tools.hidden = !on; view.classList.toggle("is-wide", on); layer.classList.toggle("is-editable", on); place(); }
    if (wide.addEventListener) wide.addEventListener("change", penOn);
    penOn();
    if (tools) tools.querySelectorAll("[data-notes-tool]").forEach(function (b) {
      b.addEventListener("click", function () {
        S.tool = b.dataset.notesTool;
        if (b.dataset.notesColour) S.colour = COLOURS[b.dataset.notesColour];
        tools.querySelectorAll("[data-notes-tool]").forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
      });
    });
    function changed() { save(S.at); paint(); drawStrip(); if (undoBtn) undoBtn.disabled = !S.undo.length; }
    if (undoBtn) undoBtn.addEventListener("click", function () {
      var step = S.undo.pop(), note = mine();
      if (!step) return;
      if (step.add) note.s = note.s.filter(function (s) { return s.id !== step.add.id; });
      if (step.del) note.s = note.s.concat(step.del);
      changed();
    });
    var erased = [];
    Ink.pen(layer, {
      start: function (e) {
        if (!wide.matches || (e.pointerType !== "pen" && e.pointerType !== "mouse")) return null;  // a finger scrolls
        if (e.pointerType === "mouse" && e.button !== 0) return null;
        if (!memory[S.at]) return null;  // the slide's notes are still loading
        var eraser = e.pointerType === "pen" && (e.button === 5 || (e.buttons & 32));
        if (S.tool === "eraser" || eraser) { erased = []; return "eraser"; }
        if (mine().s.length >= MAX_STROKES || JSON.stringify(mine().s).length > MAX_SLIDE_BYTES) { say(cfg.dataset.full); return null; }
        return S.tool === "marker" ? "marker" : "pen";
      },
      colour: function () { return S.tool === "marker" ? COLOURS.marker : S.colour; },
      erase: function (p) {
        var note = mine(), ratio = S.box ? S.box.h / (S.box.w || 1) : 0.5625;
        var hit = note.s.filter(function (s) { return Ink.near(s, p[0], p[1], 0.012, ratio); });
        if (!hit.length) return;
        var ids = hit.map(function (s) { return s.id; });
        note.s = note.s.filter(function (s) { return ids.indexOf(s.id) < 0; });
        erased = erased.concat(hit);
        paint();
      },
      erased: function () { if (erased.length) { S.undo.push({ del: erased }); erased = []; changed(); } },
      draw: function (stroke, phase) {
        if (phase !== "up") { S.drawing = stroke; paint(); return; }
        S.drawing = null;
        mine().s.push(stroke);
        S.undo.push({ add: stroke });
        if (S.undo.length > 200) S.undo.shift();
        changed();
      }
    });

    show(S.current);
    return {
      poll: function (s) {  // the state of /state: the slide on the screen, the slides reached, the presenter's strokes
        var id = /^slide-(\d+)$/.exec(s.key || "");
        if (id) {
          var now = parseInt(id[1], 10);
          if (now !== S.current) { S.current = now; if (S.follow) show(now); else { behind.hidden = S.at === now; drawStrip(); } }
          else if (s.ink != null && S.at === now && s.ink !== S.theirs.rev) theirs(false);
        }
        if (S.at !== S.current && slides.findIndex(function (x) { return x[0] === S.at; }) >= upto) { S.follow = true; show(S.current); }  // locked again (E3)
      },
      strip: drawStrip
    };
  }

  // --- the state from live.js: the slides reached may change (the presenter goes back: later ones are locked) ----
  function buttons() { document.querySelectorAll("[data-notes-download]").forEach(function (b) { b.hidden = !upto; }); }
  stage.addEventListener("live:poll", function (e) {
    var s = e.detail || {};
    if (typeof s.upto === "number" && s.upto !== upto) { upto = s.upto; if (V) V.strip(); }
    buttons();  // also the button under a closing text live.js has just drawn
    if (V && view.isConnected) V.poll(s);
  });
  buttons();

  // --- "Folien mit Notizen herunterladen": the dialog, then the PDF (E7, E8) ----------------------------------------
  var dialog = cfg.querySelector("[data-notes-dialog]");
  var form = dialog && dialog.querySelector("form"), msg = dialog && dialog.querySelector("[data-notes-msg]");
  var busy = false;
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-notes-download]");
    if (!b || !dialog) return;
    msg.hidden = true;
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open", "");
  });
  if (form) form.addEventListener("submit", function (e) {
    if (e.submitter && e.submitter.value === "cancel") return;  // the cancel button closes the dialog
    e.preventDefault();
    if (busy) return;
    var ids = slides.slice(0, upto).map(function (s) { return s[0]; });
    Promise.all(ids.map(load)).then(function (notes) {
      var out = {}, pick = { t: form.mine_text.checked, s: form.mine_ink.checked };
      ids.forEach(function (id, n) {
        var note = notes[n];
        if ((pick.t && note.t) || (pick.s && note.s.length)) out[id] = { t: pick.t ? note.t : "", s: pick.s ? note.s : [] };
      });
      var body = JSON.stringify(out);
      if (body.length > MAX_BYTES) { tell(cfg.dataset.tooLarge, true); return; }
      var data = new FormData();
      data.append("csrf_token", cfg.dataset.csrf);
      data.append("device", stage.dataset.device || "");
      data.append("notes", body);
      data.append("mine_ink", pick.s ? "1" : "");
      data.append("mine_text", pick.t ? "1" : "");
      data.append("presenter", form.presenter.checked ? "1" : "");
      busy = true;
      tell(cfg.dataset.making, false);
      return fetch(cfg.dataset.pdf, { method: "POST", body: data, credentials: "same-origin" }).then(function (r) {
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (d) { tell(d.error || cfg.dataset.fail, true); });
        var name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(r.headers.get("Content-Disposition") || "");
        return r.blob().then(function (blob) {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = name ? decodeURIComponent(name[1]) : "notes.pdf";
          document.body.appendChild(a);
          a.click();
          setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
          dialog.close ? dialog.close() : dialog.removeAttribute("open");
        });
      });
    }).catch(function () { tell(cfg.dataset.fail, true); }).then(function () { busy = false; });
  });
  function tell(text, bad) { msg.textContent = text || ""; msg.hidden = !text; msg.classList.toggle("p-error", !!bad); }
})();
