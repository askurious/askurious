// The shared parts of writing on presentation slides (window.AskInk): drawing a stroke, placing a canvas over a
// slide's picture, the eraser's hit test and taking a stroke from the pen. Two scripts use them: live_ink.js (the
// presenter's pen notes, app/live/ink.py) and live_notes.js (the audience's notes, app/live/notes.py). A stroke is
// {id, c: colour, w: width as a share of the slide's width, m: 1 for the highlighter, p: [[x, y, pressure]]} with
// coordinates from 0 to 1 over the picture, so it sits on the same spot on every screen and in the PDF.
(function () {
  var WIDTH = { pen: 0.004, marker: 0.022 };  // as a share of the slide's width (app/live/ink.py, stroke_width)
  var MAX_POINTS = 4000;  // points of one stroke (ink.MAX_POINTS)

  // the same formula as the PDF (ink.stroke_width): the highlighter keeps its width, the pen grows with the pressure
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

  // the canvas exactly over the drawn picture (object-fit: contain leaves bands beside it); its size in CSS pixels
  // and the device's pixel ratio, or null while the picture is not drawn
  function fit(img, layer) {
    if (!img.naturalWidth || !layer.isConnected) return null;
    var wrap = layer.parentNode.getBoundingClientRect(), r = img.getBoundingClientRect();
    if (!r.width || !r.height) return null;
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
    return { w: w, h: h, dpr: dpr };
  }
  // all strokes of the layers (lists of strokes) on a canvas placed by fit()
  function paint(ctx, box, layers) {
    if (!box || !box.w) return;
    ctx.setTransform(box.dpr, 0, 0, box.dpr, 0, 0);
    ctx.clearRect(0, 0, box.w, box.h);
    layers.forEach(function (strokes) { (strokes || []).forEach(function (s) { drawStroke(ctx, s, box.w, box.h); }); });
  }

  // the eraser touches a stroke: a segment of it within reach (ratio: the slide's height to its width)
  function near(s, x, y, reach, ratio) {
    var r = reach + s.w / 2, p = s.p;
    y *= ratio;
    for (var i = 0; i < p.length; i++) {
      var ax = p[i][0], ay = p[i][1] * ratio, b = p[i + 1] || p[i], bx = b[0], by = b[1] * ratio;
      var dx = bx - ax, dy = by - ay, len = dx * dx + dy * dy;
      var t = len ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / len)) : 0;
      var cx = ax + t * dx - x, cy = ay + t * dy - y;
      if (cx * cx + cy * cy < r * r) return true;
    }
    return false;
  }

  // Taking strokes from the pointer on a canvas. o.start(e) says what the pointer does ("pen", "marker", "eraser",
  // "laser") or null to leave it (a finger that scrolls); o.colour() the colour of a new stroke; o.draw(stroke,
  // phase) gets the stroke as it grows ("down", "move") and when it is done ("up"); o.erase(point) the eraser's
  // place, o.erased() its end; o.laser(point or null, phase) the laser dot; o.end() after every pointer.
  function pen(el, o) {
    var active = null, mode = null, stroke = null;
    function at(e) {
      var r = el.getBoundingClientRect();
      return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))];
    }
    function pressure(e) { return e.pointerType === "pen" && e.pressure > 0 ? Math.round(e.pressure * 100) / 100 : 0.5; }
    function point(e) { var p = at(e); return [+p[0].toFixed(4), +p[1].toFixed(4), pressure(e)]; }
    el.addEventListener("pointerdown", function (e) {
      if (active !== null) return;
      var m = o.start(e);
      if (!m) return;
      e.preventDefault();
      active = e.pointerId;
      mode = m;
      el.setPointerCapture(e.pointerId);
      if (mode === "laser") { if (o.laser) o.laser(at(e), "down"); return; }
      if (mode === "eraser") { o.erase(at(e)); return; }
      stroke = { id: newId(), c: o.colour(), w: WIDTH[mode === "marker" ? "marker" : "pen"], m: mode === "marker" ? 1 : 0, p: [point(e)] };
      o.draw(stroke, "down");
    });
    el.addEventListener("pointermove", function (e) {
      if (e.pointerId !== active) return;
      var events = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
      if (!events.length) events = [e];
      if (mode === "laser") { if (o.laser) o.laser(at(e), "move"); return; }
      if (mode === "eraser") { events.forEach(function (ev) { o.erase(at(ev)); }); return; }
      if (!stroke) return;
      events.forEach(function (ev) {
        var p = point(ev), last = stroke.p[stroke.p.length - 1];
        if (Math.abs(p[0] - last[0]) + Math.abs(p[1] - last[1]) < 0.0008) return;
        if (stroke.p.length < MAX_POINTS) stroke.p.push(p);
      });
      o.draw(stroke, "move");
    });
    function end(e) {
      if (e.pointerId !== active) return;
      active = null;
      if (mode === "laser") { if (o.laser) o.laser(null, "up"); }
      else if (mode === "eraser") { if (o.erased) o.erased(); }
      else if (stroke) { var done = stroke; stroke = null; o.draw(done, "up"); }
      if (o.end) o.end();
    }
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  }

  window.AskInk = { WIDTH: WIDTH, strokeWidth: strokeWidth, drawStroke: drawStroke, newId: newId, fit: fit, paint: paint,
                    near: near, pen: pen };
})();
