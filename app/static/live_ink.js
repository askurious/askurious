// Pen notes on presentation slides of a live vote (app/live/ink.py; concept stiftnotizen-konzept-2026-10-23.md, Ralf
// 2026-10-23: all as recommended). A "sheet" is a canvas laid exactly over a slide's picture; it draws the strokes and,
// where the notes may be changed, takes the pen: a pen always, a finger or the mouse only in drawing mode. Changes go
// to the server while they are drawn, one request at a time per sheet, the changes in between gathered. A stroke keeps
// the pen's pressure per point (harder is broader); coordinates run from 0 to 1 over the picture, so the notes sit on
// the same spot on every screen.
// Two pages use it: the presenter view and the audience view (live/screen.html: one sheet for the slide on the screen;
// live.js sends every state as the event "live:state"; the audience view fetches the strokes only when their revision
// changed, every half second while someone draws) and the page "Alle Folien" (live/slides.html: a sheet per
// presentation slide of the run shown; "Notizen bearbeiten" lets the notes of any run be changed afterwards, E9).
(function () {
  var screen = document.querySelector("[data-live-screen]");
  var slidesPage = document.querySelector("[data-live-slides]");
  var root = screen || slidesPage;
  if (!root || !(root.dataset.inkSave || root.dataset.inkJson)) return;
  var UI_LANG = root.dataset.uiLang || document.documentElement.lang;
  function de_en(de, en) { return UI_LANG === "en" ? en : de; }
  var SEND_EVERY = 200, FAST = 500, LASER_EVERY = 100;
  var WIDTH = { pen: 0.004, marker: 0.022 };  // as a share of the slide's width (app/live/ink.py, stroke_width)

  // --- drawing: the same formula as the PDF (ink.stroke_width) ---------------------------------------------------
  function strokeWidth(s, pressure, w) { return s.m ? s.w * w : s.w * w * (0.4 + 1.2 * pressure); }
  function drawStroke(ctx, s, w, h) {
    var p = s.p;
    if (!p || !p.length) return;
    ctx.strokeStyle = s.c;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (s.m) {  // the highlighter: one path, see-through, so crossings within a stroke stay even
      ctx.globalAlpha = 0.4;
      ctx.lineWidth = strokeWidth(s, 0.5, w);
      ctx.beginPath();
      ctx.moveTo(p[0][0] * w, p[0][1] * h);
      for (var i = 1; i < p.length; i++) ctx.lineTo(p[i][0] * w, p[i][1] * h);
      if (p.length === 1) ctx.lineTo(p[0][0] * w + 0.01, p[0][1] * h);
      ctx.stroke();
      ctx.globalAlpha = 1;
      return;
    }
    if (p.length === 1) p = [p[0], p[0]];
    for (var j = 1; j < p.length; j++) {  // the pen: segment by segment, as broad as the pressure at its ends
      ctx.lineWidth = strokeWidth(s, (p[j - 1][2] + p[j][2]) / 2, w);
      ctx.beginPath();
      ctx.moveTo(p[j - 1][0] * w, p[j - 1][1] * h);
      ctx.lineTo(p[j][0] * w + (p[j] === p[j - 1] ? 0.01 : 0), p[j][1] * h);
      ctx.stroke();
    }
  }
  function newId() { return "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }

  // --- the tools: one toolbar per page, shared by its sheets ----------------------------------------------------
  var tools = root.querySelector("[data-ink-tools]") || document.querySelector("[data-ink-tools]");
  var T = { tool: "pen", colour: "#1a1a1a", draw: false, on: !!screen };  // on: the sheets take the pointer
  var undo = [], redo = [];  // steps {sheet, add: stroke | del: [strokes]}
  var status = tools && tools.querySelector("[data-ink-status]");
  var undoBtn = tools && tools.querySelector("[data-ink-undo]"), redoBtn = tools && tools.querySelector("[data-ink-redo]");
  var sayTimer = null;
  function say(text, error) {
    if (!status) return;
    status.textContent = text; status.hidden = !text; status.classList.toggle("is-error", !!error);
    clearTimeout(sayTimer);
    if (text) sayTimer = setTimeout(function () { status.hidden = true; }, 8000);
  }
  function buttons() { if (undoBtn) { undoBtn.disabled = !undo.length; redoBtn.disabled = !redo.length; } }
  function record(step) { undo.push(step); if (undo.length > 200) undo.shift(); redo = []; buttons(); }

  // --- a sheet: the canvas over one slide's picture ---------------------------------------------------------------
  function Sheet(img, opts) {
    var self = this;
    this.img = img; this.item = opts.item; this.run = opts.run || ""; this.editable = !!opts.editable;
    this.laserOn = !!opts.laser; this.strokes = opts.strokes || []; this.rev = -1;
    this.drawing = null; this.laser = null; this.laserAt = 0; this.hidden = false;
    this.pending = []; this.laserSend = undefined; this.sending = false; this.box = { w: 0, h: 0 };
    var layer = this.layer = document.createElement("canvas");
    layer.className = "live-ink-layer" + (this.editable ? " is-editable" : "");
    layer.setAttribute("aria-hidden", "true");
    img.parentNode.classList.add("has-ink");
    img.parentNode.appendChild(layer);
    this.ctx = layer.getContext("2d");
    if (this.editable) this.bind();
    if (img.complete) this.place(); else img.addEventListener("load", function () { self.place(); }, { once: true });
  }
  Sheet.prototype.place = function () {  // exactly over the drawn picture (object-fit: contain leaves bands beside it)
    var img = this.img, layer = this.layer;
    if (!img.naturalWidth || !layer.isConnected) return;
    var wrap = layer.parentNode.getBoundingClientRect(), r = img.getBoundingClientRect();
    if (!r.width || !r.height) return;
    var zoom = wrap.width / (layer.parentNode.offsetWidth || wrap.width) || 1;  // CSS zoom of the printable slides
    var scale = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
    var w = img.naturalWidth * scale, h = img.naturalHeight * scale;
    layer.style.left = (r.left - wrap.left + (r.width - w) / 2) / zoom + "px";
    layer.style.top = (r.top - wrap.top + (r.height - h) / 2) / zoom + "px";
    layer.style.width = w / zoom + "px";
    layer.style.height = h / zoom + "px";
    var dpr = window.devicePixelRatio || 1;
    var pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (layer.width !== pw || layer.height !== ph) { layer.width = pw; layer.height = ph; }
    this.box = { w: w, h: h, dpr: dpr };
    this.paint();
  };
  Sheet.prototype.paint = function () {
    var ctx = this.ctx, b = this.box;
    if (!b.w) return;
    ctx.setTransform(b.dpr, 0, 0, b.dpr, 0, 0);
    ctx.clearRect(0, 0, b.w, b.h);
    if (this.hidden) return;
    this.strokes.forEach(function (s) { drawStroke(ctx, s, b.w, b.h); });
    if (this.drawing) drawStroke(ctx, this.drawing, b.w, b.h);
    if (this.laser && Date.now() - this.laserAt < 2000) {  // the laser dot: red with a glow
      var x = this.laser[0] * b.w, y = this.laser[1] * b.h, r = Math.max(6, b.w * 0.007);
      var g = ctx.createRadialGradient(x, y, 0, x, y, r * 2.6);
      g.addColorStop(0, "rgba(255,40,40,1)"); g.addColorStop(0.35, "rgba(255,40,40,0.9)"); g.addColorStop(1, "rgba(255,40,40,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(x, y, r * 2.6, 0, Math.PI * 2); ctx.fill();
    }
  };
  Sheet.prototype.busy = function () { return this.sending || this.pending.length > 0 || !!this.drawing; };
  Sheet.prototype.fetch = function (force) {  // the strokes from the server (screen pages)
    var self = this;
    var url = root.dataset.inkJson + "?item=" + encodeURIComponent(this.item) + (force ? "" : "&rev=" + this.rev);
    return fetch(url, { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !self.layer.isConnected) return d;
        if (!self.editable || !self.busy()) {  // the presenter's own strokes are newer than the server's while it sends
          if (!d.same) { self.strokes = d.strokes || []; self.rev = d.rev; }
        }
        if (!self.editable) { self.laser = d.laser; self.laserAt = d.laser ? Date.now() : 0; }
        self.paint();
        return d;
      }).catch(function () { return null; });
  };
  // --- sending: one request at a time, the changes in between gathered -----------------------------------------
  Sheet.prototype.queue = function (op) {
    if (op.put) this.pending = this.pending.filter(function (o) { return !(o.put && o.put.id === op.put.id); });  // only the last state of a stroke
    this.pending.push(op);
    this.flush();
  };
  Sheet.prototype.flush = function () {
    var self = this;
    if (this.sending || (!this.pending.length && this.laserSend === undefined)) return;
    var ops = this.pending, l = this.laserSend;
    this.pending = []; this.laserSend = undefined; this.sending = true;
    var body = new FormData();
    body.append("csrf_token", root.dataset.csrf);
    body.append("item", this.item);
    if (this.run) body.append("durchgang", this.run);
    body.append("ops", JSON.stringify(ops));
    if (l !== undefined) body.append("laser", JSON.stringify(l));
    fetch(root.dataset.inkSave, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { r: r, d: d }; }); })
      .then(function (x) {
        self.sending = false;
        if (x.r.ok && x.d.ok) self.rev = x.d.rev;
        else {
          say(x.d.error || de_en("Nicht gespeichert (Fehler {s}). Bitte die Seite neu laden.", "Not saved (error {s}). Please reload the page.").replace("{s}", x.r.status), true);
          if (root.dataset.inkJson) self.fetch(true);  // back to what the server holds
        }
        self.flush();
      })
      .catch(function () {
        self.sending = false;
        say(de_en("Keine Verbindung zum Server: Die Striche werden gesendet, sobald sie wieder da ist.", "No connection to the server: the strokes are sent once it is back."), true);
        self.pending = ops.concat(self.pending);
        setTimeout(function () { self.flush(); }, 2000);
      });
  };
  Sheet.prototype.apply = function (step, back) {  // a step of undo or redo: {add: stroke} | {del: [strokes]}
    var self = this;
    var adds = back ? (step.del || []) : (step.add ? [step.add] : []);
    var dels = back ? (step.add ? [step.add] : []) : (step.del || []);
    if (dels.length) {
      var ids = dels.map(function (s) { return s.id; });
      this.strokes = this.strokes.filter(function (s) { return ids.indexOf(s.id) < 0; });
      this.queue({ del: ids });
    }
    adds.forEach(function (s) { self.strokes.push(s); self.queue({ put: s }); });
    this.paint();
  };
  Sheet.prototype.clear = function () {
    if (!this.strokes.length) return;
    var gone = this.strokes.slice();
    this.strokes = [];
    this.queue({ clear: 1 });
    record({ sheet: this, del: gone });
    this.paint();
  };
  Sheet.prototype.near = function (s, x, y, reach) {  // the eraser touches a stroke: a segment of it within reach
    var ratio = this.box.h / (this.box.w || 1), r = reach + s.w / 2, p = s.p;
    y *= ratio;
    for (var i = 0; i < p.length; i++) {
      var ax = p[i][0], ay = p[i][1] * ratio, b = p[i + 1] || p[i], bx = b[0], by = b[1] * ratio;
      var dx = bx - ax, dy = by - ay, len = dx * dx + dy * dy;
      var t = len ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / len)) : 0;
      var cx = ax + t * dx - x, cy = ay + t * dy - y;
      if (cx * cx + cy * cy < r * r) return true;
    }
    return false;
  };
  Sheet.prototype.bind = function () {  // the pointer: pen, highlighter, eraser, laser
    var self = this, el = this.layer, active = null, mode = null, erased = [], lastSend = 0, penDown = false;
    function at(e) {
      var r = el.getBoundingClientRect();
      return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))];
    }
    function pressure(e) { return e.pointerType === "pen" && e.pressure > 0 ? Math.round(e.pressure * 100) / 100 : 0.5; }
    function erase(p) {
      var hit = self.strokes.filter(function (s) { return self.near(s, p[0], p[1], 0.012); });
      if (!hit.length) return;
      var ids = hit.map(function (s) { return s.id; });
      self.strokes = self.strokes.filter(function (s) { return ids.indexOf(s.id) < 0; });
      erased = erased.concat(hit);
      self.queue({ del: ids });
      self.paint();
    }
    el.addEventListener("pointerdown", function (e) {
      if (active !== null || !T.on) return;
      var pen = e.pointerType === "pen";
      if (!pen && (!T.draw || penDown)) return;  // a finger or the mouse only in drawing mode; the palm never beside a pen
      if (!pen && e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      active = e.pointerId;
      penDown = pen;
      el.setPointerCapture(e.pointerId);
      mode = (pen && (e.button === 5 || (e.buttons & 32))) ? "eraser" : T.tool;  // the eraser end of the pen
      if (mode === "laser" && !self.laserOn) mode = "pen";
      var p = at(e);
      if (mode === "laser") { self.laser = p; self.laserAt = Date.now(); self.laserSend = p; self.flush(); self.paint(); return; }
      if (mode === "eraser") { erased = []; erase(p); return; }
      self.drawing = { id: newId(), c: T.colour, w: WIDTH[mode === "marker" ? "marker" : "pen"], m: mode === "marker" ? 1 : 0,
                       p: [[+p[0].toFixed(4), +p[1].toFixed(4), pressure(e)]] };
      lastSend = Date.now();
      self.paint();
    });
    el.addEventListener("pointermove", function (e) {
      if (e.pointerId !== active) return;
      var events = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
      if (!events.length) events = [e];
      if (mode === "laser") {
        self.laser = at(e); self.laserAt = Date.now(); self.paint();
        if (Date.now() - lastSend > LASER_EVERY) { lastSend = Date.now(); self.laserSend = self.laser; self.flush(); }
        return;
      }
      if (mode === "eraser") { events.forEach(function (ev) { erase(at(ev)); }); return; }
      var d = self.drawing;
      if (!d) return;
      events.forEach(function (ev) {
        var p = at(ev), last = d.p[d.p.length - 1];
        if (Math.abs(p[0] - last[0]) + Math.abs(p[1] - last[1]) < 0.0008) return;
        if (d.p.length < 4000) d.p.push([+p[0].toFixed(4), +p[1].toFixed(4), pressure(ev)]);
      });
      self.paint();
      if (Date.now() - lastSend > SEND_EVERY) { lastSend = Date.now(); self.queue({ put: JSON.parse(JSON.stringify(d)) }); }
    });
    function end(e) {
      if (e.pointerId !== active) return;
      active = null; penDown = false;
      if (mode === "laser") { self.laser = null; self.laserSend = null; self.flush(); self.paint(); return; }
      if (mode === "eraser") { if (erased.length) record({ sheet: self, del: erased }); erased = []; return; }
      var done = self.drawing;
      if (!done) return;
      self.drawing = null;
      self.strokes.push(done);
      self.queue({ put: done });
      record({ sheet: self, add: done });
      self.paint();
    }
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  };

  var sheets = [];
  function refresh() { sheets.forEach(function (s) { s.layer.classList.toggle("is-drawing", T.on && T.draw); s.place(); }); }
  window.addEventListener("resize", refresh);
  window.addEventListener("beforeprint", refresh);

  // --- the toolbar -----------------------------------------------------------------------------------------------
  var current = null;  // the sheet "Folie leeren" acts on: the slide on the screen, or on "Alle Folien" the last drawn on
  if (tools) {
    var drawBox = tools.querySelector("[data-ink-draw]");
    if (drawBox) {
      try { drawBox.checked = localStorage.getItem("askurious-ink-draw") === "1"; } catch (e) { /* private window */ }
      T.draw = drawBox.checked;
      drawBox.addEventListener("change", function () {
        T.draw = drawBox.checked;
        try { localStorage.setItem("askurious-ink-draw", drawBox.checked ? "1" : "0"); } catch (e) { /* private window */ }
        refresh();
      });
    }
    var nowDot = document.querySelector("[data-ink-now]");  // the presenter view's button shows the tool picked
    tools.querySelectorAll("[data-ink-tool]").forEach(function (b) {
      b.addEventListener("click", function () {
        T.tool = b.dataset.inkTool;
        if (b.dataset.inkColour) T.colour = b.dataset.inkColour;
        tools.querySelectorAll("[data-ink-tool]").forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
        var dot = b.querySelector(".live-ink-dot");
        if (nowDot) nowDot.className = "live-ink-dot " + (dot ? dot.className.replace("live-ink-dot", "").trim() : "is-" + T.tool);
      });
    });
    // the presenter view: the tools are a popup at the button "Stift" (Ralf, 2026-10-23: always shown they took too
    // much room); placed fixed beside the button, so the scrolling left column does not cut it; Esc, the button or a
    // click elsewhere closes it, a stroke on the slide too
    var opener = document.querySelector("[data-ink-open]");
    var popOpen = function (open) {
      if (!opener) return;
      tools.hidden = !open;
      opener.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) return;
      var r = opener.getBoundingClientRect(), w = tools.offsetWidth, h = tools.offsetHeight;
      var left = Math.min(r.left, innerWidth - w - 8), top = r.bottom + 6;
      if (top + h > innerHeight - 8) top = Math.max(8, r.top - h - 6);
      tools.style.left = Math.max(8, left) + "px";
      tools.style.top = top + "px";
    };
    if (opener) {
      opener.addEventListener("click", function () { popOpen(tools.hidden); });
      document.addEventListener("pointerdown", function (e) {
        if (!tools.hidden && !tools.contains(e.target) && !opener.contains(e.target)) popOpen(false);
      }, true);
      document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !tools.hidden) { popOpen(false); opener.focus(); } });
      window.addEventListener("resize", function () { if (!tools.hidden) popOpen(true); });
    }
    if (undoBtn) undoBtn.addEventListener("click", function () { var s = undo.pop(); if (s) { s.sheet.apply(s, true); redo.push(s); buttons(); } });
    if (redoBtn) redoBtn.addEventListener("click", function () { var s = redo.pop(); if (s) { s.sheet.apply(s, false); undo.push(s); buttons(); } });
    document.addEventListener("keydown", function (e) {
      if (!T.on || !(e.ctrlKey || e.metaKey) || e.altKey || !sheets.length) return;
      if (e.target && e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
      var k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); if (undoBtn) undoBtn.click(); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); if (redoBtn) redoBtn.click(); }
    });
    var clearBtn = tools.querySelector("[data-ink-clear]"), armed = null;  // the first click arms it, a second within 4 s clears
    if (clearBtn) clearBtn.addEventListener("click", function () {
      if (!armed) {
        clearBtn.classList.add("is-armed");
        say(de_en("Nochmal klicken: alle Striche dieser Folie löschen.", "Click again: delete all strokes on this slide."));
        armed = setTimeout(function () { armed = null; clearBtn.classList.remove("is-armed"); say(""); }, 4000);
        return;
      }
      clearTimeout(armed); armed = null; clearBtn.classList.remove("is-armed"); say("");
      if (current) current.clear();
    });
  }

  // --- the presenter view and the audience view: one sheet for the slide on the screen ----------------------------
  if (screen) {
    var out = screen.querySelector("[data-result]");
    var editable = !!screen.querySelector(".live-screen.is-presenter") && !!tools && !screen.hasAttribute("data-readonly");
    var screenBox = tools && tools.querySelector("[data-ink-screen]");
    var fastTimer = null;
    var fast = function () {  // while someone draws, the audience view asks every half second until nothing changed for a while
      if (fastTimer || editable) return;
      fastTimer = setTimeout(function tick() {
        var sheet = current;
        if (!sheet) { fastTimer = null; return; }
        sheet.fetch(false).then(function (d) {
          if (d && (d.active || d.laser) && sheet === current) fastTimer = setTimeout(tick, FAST);
          else { fastTimer = null; if (sheet.laser) { sheet.laser = null; sheet.paint(); } }
        });
      }, FAST);
    };
    if (screenBox) screenBox.addEventListener("change", function () {
      var body = new FormData();
      body.append("csrf_token", screen.dataset.csrf);
      body.append("action", screenBox.checked ? "ink_on" : "ink_off");
      fetch(screen.dataset.control, { method: "POST", body: body, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok || r.redirected) say(de_en("Nicht übernommen. Bitte die Seite neu laden.", "Not applied. Please reload the page."), true); })
        .catch(function () { say(de_en("Keine Verbindung zum Server.", "No connection to the server."), true); });
    });
    if (window.ResizeObserver && out) new ResizeObserver(refresh).observe(out);
    screen.addEventListener("live:state", function (e) {
      var s = e.detail || {}, c = s.current;
      var ink = c && c.kind === "slide" && c.image ? c.ink : null;
      var img = ink && out ? out.querySelector(".live-slide-full img") : null;
      var inkWrap = screen.querySelector("[data-ink-wrap]");
      if (inkWrap) { inkWrap.hidden = !img; if (!img && !tools.hidden) popOpen(false); }
      else if (tools) tools.hidden = !img;
      if (!img) { current = null; sheets = []; undo = []; redo = []; buttons(); return; }
      if (!current || current.img !== img || !current.layer.isConnected) {
        if (!current || current.item !== c.id) { undo = []; redo = []; buttons(); }
        current = new Sheet(img, { item: c.id, editable: editable, laser: true });
        sheets = [current];
        refresh();
      }
      current.hidden = !editable && !ink.screen;
      if (screenBox) screenBox.checked = !!ink.screen;
      if (current.rev === -1) current.fetch(true);
      else if (ink.rev !== current.rev && !(editable && current.busy())) current.fetch(false);
      if (ink.active && !current.hidden) fast();
      current.paint();
    });
  }

  // --- "Alle Folien": a sheet per presentation slide; "Notizen bearbeiten" for any run (E9) -----------------------
  if (slidesPage) {
    document.addEventListener("DOMContentLoaded", function () {  // live.js draws the slides first
      var mayEdit = !!tools && !!slidesPage.dataset.inkSave;
      slidesPage.querySelectorAll("[data-ink-item]").forEach(function (wrap) {
        var img = wrap.querySelector("img");
        if (!img) return;
        var strokes = [];
        try { strokes = JSON.parse(wrap.dataset.inkStrokes || "[]"); } catch (e) { strokes = []; }
        var sheet = new Sheet(img, { item: wrap.dataset.inkItem, run: slidesPage.dataset.inkRun, editable: mayEdit, strokes: strokes });
        sheet.layer.addEventListener("pointerdown", function () { if (T.on) current = sheet; }, true);
        sheets.push(sheet);
      });
      var toggle = document.querySelector("[data-ink-edit]");
      if (toggle && tools && sheets.length) {
        toggle.hidden = false;
        T.draw = true;  // on this page the mouse draws as soon as editing is on
        toggle.addEventListener("click", function () {
          T.on = !T.on;
          toggle.setAttribute("aria-pressed", T.on ? "true" : "false");
          toggle.textContent = T.on ? de_en("Bearbeiten beenden", "Stop editing") : de_en("Notizen bearbeiten", "Edit notes");
          tools.hidden = !T.on;
          current = current || sheets[0];
          refresh();
        });
      }
      refresh();
      setTimeout(refresh, 300);  // after the slides were scaled to the window (live.js, fitSlides)
    });
  }
})();
