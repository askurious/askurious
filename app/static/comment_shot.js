// The image of a comment from a test run (participant/_comments.html, used by comments.js): take a picture of the
// page itself, and mark on the image what is meant.
//
// capture(box, itemId): the browser's own screen capture (getDisplayMedia), with this tab offered first. One frame is
// taken, then the capture ends at once. The comment box is hidden for that frame; in the mobile view of the editor
// the picture is cut to the phone's screen. Not on phones and tablets (they cannot), there an own screenshot is
// chosen. The element the comment is about gets a frame already (removable with "Rückgängig").
//
// edit(dialog, base, marks): the dialog to mark the image: frame, arrow, cover (black, for what nobody should see),
// undo. Marks are kept in the pixels of the image, so the image can be marked again later. Resolves with the marks
// or null (cancelled). render(base, marks) draws both into a new canvas.
(function () {
  var RED = "#e0442b";

  function supported() {
    var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    return !coarse && !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);
  }

  function width(base) { return Math.max(3, Math.round(Math.max(base.width, base.height) / 320)); }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawMark(ctx, m, lw) {
    ctx.save();
    if (m.t === "cover") {
      ctx.fillStyle = "#111";
      ctx.fillRect(m.x, m.y, m.w, m.h);
    } else {
      ctx.strokeStyle = RED;
      ctx.lineWidth = lw;
      ctx.lineJoin = ctx.lineCap = "round";
      if (m.t === "rect") { roundRect(ctx, m.x, m.y, m.w, m.h, lw * 2); ctx.stroke(); }
      if (m.t === "arrow") {
        var a = Math.atan2(m.y2 - m.y1, m.x2 - m.x1), head = lw * 5;
        ctx.beginPath();
        ctx.moveTo(m.x1, m.y1);
        ctx.lineTo(m.x2, m.y2);
        ctx.moveTo(m.x2 - head * Math.cos(a - 0.45), m.y2 - head * Math.sin(a - 0.45));
        ctx.lineTo(m.x2, m.y2);
        ctx.lineTo(m.x2 - head * Math.cos(a + 0.45), m.y2 - head * Math.sin(a + 0.45));
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  function paint(target, base, marks, extra) {
    var ctx = target.getContext("2d"), lw = width(base);
    ctx.drawImage(base, 0, 0);
    marks.forEach(function (m) { drawMark(ctx, m, lw); });
    if (extra) drawMark(ctx, extra, lw);
  }

  function render(base, marks) {
    var out = document.createElement("canvas");
    out.width = base.width;
    out.height = base.height;
    paint(out, base, marks || []);
    return out;
  }

  function frames(n) {
    return new Promise(function (resolve) {
      (function step(left) { if (left <= 0) resolve(); else requestAnimationFrame(function () { step(left - 1); }); })(n);
    });
  }

  // --- the picture of the page ------------------------------------------------------------------------------------
  function capture(box, itemId, maxSide) {
    var frame = box.classList.contains("is-stage") ? document.querySelector("[data-device-frame]") : null;
    var options = { video: { displaySurface: "browser" }, audio: false, preferCurrentTab: true,
                    selfBrowserSurface: "include", surfaceSwitching: "exclude", monitorTypeSurfaces: "exclude" };
    return navigator.mediaDevices.getDisplayMedia(options).then(function (stream) {
      var video = document.createElement("video"), track = stream.getVideoTracks()[0];
      video.muted = true;
      video.playsInline = true;
      video.srcObject = stream;
      box.style.visibility = "hidden";
      function stop() { stream.getTracks().forEach(function (t) { t.stop(); }); box.style.visibility = ""; }
      return video.play()
        .then(function () { return frames(3); })
        .then(function () { return new Promise(function (r) { setTimeout(r, 350); }); })  // the box is gone from the picture
        .then(function () {
          var vw = video.videoWidth, vh = video.videoHeight;
          var full = document.createElement("canvas");
          full.width = vw;
          full.height = vh;
          full.getContext("2d").drawImage(video, 0, 0, vw, vh);
          var surface = track && track.getSettings ? track.getSettings().displaySurface : "";
          stop();
          // this tab, as far as the size tells: then the page's coordinates can be used (cut, the element's frame)
          var scale = vw / window.innerWidth;
          var thisTab = (!surface || surface === "browser") && Math.abs(vh / scale - window.innerHeight) < 4;
          var cut = { x: 0, y: 0, w: vw, h: vh }, offset = { x: 0, y: 0 }, doc = document;
          if (thisTab && frame) {
            var r = frame.getBoundingClientRect();
            cut = { x: r.left * scale, y: r.top * scale, w: r.width * scale, h: r.height * scale };
            offset = { x: r.left, y: r.top };
            try { doc = frame.contentDocument || null; } catch (e) { doc = null; }
          }
          var s = Math.min(1, maxSide / Math.max(cut.w, cut.h));
          var base = document.createElement("canvas");
          base.width = Math.max(1, Math.round(cut.w * s));
          base.height = Math.max(1, Math.round(cut.h * s));
          base.getContext("2d").drawImage(full, cut.x, cut.y, cut.w, cut.h, 0, 0, base.width, base.height);
          var marks = [];
          var el = thisTab && itemId && doc ? doc.getElementById("i" + itemId) : null;
          if (el) {
            var e = el.getBoundingClientRect(), k = scale * s, pad = 8;
            var m = { t: "rect", x: (e.left + offset.x) * k - cut.x * s - pad, y: (e.top + offset.y) * k - cut.y * s - pad,
                      w: e.width * k + 2 * pad, h: e.height * k + 2 * pad };
            m.x = Math.max(2, m.x); m.y = Math.max(2, m.y);
            m.w = Math.min(base.width - m.x - 2, m.w); m.h = Math.min(base.height - m.y - 2, m.h);
            if (m.w > 10 && m.h > 10) marks.push(m);
          }
          return { base: base, marks: marks };
        }, function (err) { stop(); throw err; });
    });
  }

  // --- the dialog to mark ----------------------------------------------------------------------------------------
  function edit(dialog, base, marks) {
    marks = (marks || []).slice();
    var canvas = dialog.querySelector("[data-shot-canvas]"), tool = "rect", drag = null;
    canvas.width = base.width;
    canvas.height = base.height;
    var tools = dialog.querySelectorAll("[data-tool]");
    function redraw() { paint(canvas, base, marks, drag && drag.m); }
    function pick(name) {
      tool = name;
      tools.forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.tool === name ? "true" : "false"); });
    }
    pick("rect");
    redraw();
    function pos(e) {
      var r = canvas.getBoundingClientRect();
      return { x: (e.clientX - r.left) * canvas.width / r.width, y: (e.clientY - r.top) * canvas.height / r.height };
    }
    function down(e) {
      e.preventDefault();
      try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* a pointer the browser no longer knows */ }
      drag = { p: pos(e), m: null };
    }
    function move(e) {
      if (!drag) return;
      var p = pos(e), s = drag.p;
      drag.m = tool === "arrow" ? { t: "arrow", x1: s.x, y1: s.y, x2: p.x, y2: p.y }
        : { t: tool, x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) };
      redraw();
    }
    function up() {
      var m = drag && drag.m, min = width(base) * 2;
      if (m && (m.t === "arrow" ? Math.hypot(m.x2 - m.x1, m.y2 - m.y1) > min * 2 : m.w > min && m.h > min)) marks.push(m);
      drag = null;
      redraw();
    }
    function onTool(e) { pick(e.currentTarget.dataset.tool); }
    function undo() { marks.pop(); redraw(); }
    var undoButton = dialog.querySelector("[data-shot-undo]");
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    tools.forEach(function (b) { b.addEventListener("click", onTool); });
    undoButton.addEventListener("click", undo);
    return new Promise(function (resolve) {
      var ok = dialog.querySelector("[data-shot-ok]"), cancel = dialog.querySelector("[data-shot-cancel]"), done = false;
      // answered at once (the close event comes later, and in a page in the background much later); Esc closes too
      function accept() { finish(marks); }
      function abort() { finish(null); }
      function closed() { finish(null); }
      function finish(result) {
        if (done) return;
        done = true;
        if (dialog.open) dialog.close();
        canvas.removeEventListener("pointerdown", down);
        canvas.removeEventListener("pointermove", move);
        canvas.removeEventListener("pointerup", up);
        canvas.removeEventListener("pointercancel", up);
        tools.forEach(function (b) { b.removeEventListener("click", onTool); });
        undoButton.removeEventListener("click", undo);
        ok.removeEventListener("click", accept);
        cancel.removeEventListener("click", abort);
        dialog.removeEventListener("close", closed);
        resolve(result);
      }
      ok.addEventListener("click", accept);
      cancel.addEventListener("click", abort);
      dialog.addEventListener("close", closed);
      dialog.showModal();
      ok.focus();
    });
  }

  window.CommentShot = { supported: supported, capture: capture, edit: edit, render: render };
})();
