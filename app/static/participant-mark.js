// Hot spot (items/image_mark.py, modes "areas" and "rate"): every area of the list below the image becomes a button
// over the image at its place (data-box: x, y, w, h relative to the image). A tap ticks or unticks the area's box, in
// mode "rate" it goes round like (1), dislike (-1) and neutral (0). The list keeps the fields that are sent and stays in
// view as the overview of what was chosen, by name, and as a second way to choose (S20, X42); a tap shows the area's
// name on the image for a moment. The buttons carry the area's name for screen readers and say their state
// (aria-pressed, or the rating).
document.querySelectorAll("[data-hot]").forEach(function (box) {
  var stage = box.querySelector("[data-hot-stage]"), list = box.querySelector("[data-hot-list]");
  var status = box.querySelector("[data-mark-status]");
  var rate = box.hasAttribute("data-hot-rate"), max = parseInt(box.dataset.max || "0", 10) || Infinity;
  if (!stage || !list) return;
  var ROUND = { "0": "1", "1": "-1", "-1": "0" };
  function inputs(area) { return Array.prototype.slice.call(area.querySelectorAll("input")); }
  function value(area) {
    var on = inputs(area).filter(function (i) { return i.checked; })[0];
    return rate ? (on ? on.value : "0") : (on ? "1" : "0");
  }
  function word(area, v) {  // the label of a rating as the page wrote it, in the language of the page
    var i = inputs(area).filter(function (x) { return x.value === v; })[0];
    return i ? i.parentNode.textContent.trim() : "";
  }
  function count() {
    return Array.prototype.filter.call(list.querySelectorAll("[data-hot-area]"), function (a) { return value(a) !== "0"; }).length;
  }
  function paint(area, button) {
    var v = value(area);
    button.classList.toggle("is-on", v === "1");
    button.classList.toggle("is-off", v === "-1");
    if (rate) button.setAttribute("aria-label", area.dataset.label + ": " + word(area, v));
    else button.setAttribute("aria-pressed", v === "1" ? "true" : "false");
  }
  Array.prototype.forEach.call(list.querySelectorAll("[data-hot-area]"), function (area) {
    var b = (area.dataset.box || "").split(",").map(parseFloat);
    if (b.length !== 4 || b.some(isNaN)) return;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "p-hot-area";
    button.style.left = b[0] * 100 + "%"; button.style.top = b[1] * 100 + "%";
    button.style.width = b[2] * 100 + "%"; button.style.height = b[3] * 100 + "%";
    if (!rate) button.setAttribute("aria-label", area.dataset.label);
    button.addEventListener("click", function () {
      var v = value(area), next = rate ? ROUND[v] : (v === "1" ? "0" : "1");
      if (v === "0" && next !== "0" && count() >= max) {
        if (status) status.textContent = uiT("Maximum erreicht", "maximum reached");
        return;
      }
      inputs(area).forEach(function (i) { i.checked = rate ? i.value === next : next === "1"; });
      var changed = inputs(area)[0];
      if (changed) changed.dispatchEvent(new Event("change", { bubbles: true }));
      paint(area, button);
      if (status) status.textContent = area.dataset.label + ": " + (rate ? word(area, next) : (next === "1" ? "✓" : "–"));
      if (area.dataset.label) {  // the name on the image for a moment: areas have no text of their own on the picture
        button.setAttribute("data-name", area.dataset.label);
        button.classList.add("is-said");
        clearTimeout(button.saidTimer);
        button.saidTimer = setTimeout(function () { button.classList.remove("is-said"); }, 1600);
      }
    });
    // a choice in the list below the image shows on the image as well
    area.addEventListener("change", function () { paint(area, button); });
    stage.appendChild(button);
    paint(area, button);
  });
  box.classList.add("is-live");
});

// Mark an image: spots (click/tap) or free-hand lines (highlighter). Coordinates relative to the
// image (0..1); "↶" takes back the last mark, "Neu" removes all. The layer is an SVG in image pixels over the image.
// Spots also with the keyboard (WCAG 2.1.1, accessibility concept D2/E3): the image takes the focus and shows a
// crosshair, the arrow keys move it by 2 % (with Shift 10 %), Enter or Space marks the spot, Delete or Backspace takes
// back the last one. The spots stand in a list below the image, each with "entfernen"; a status line says what
// happened ("Stelle 2 gesetzt, 40 % von links, 60 % von oben").
(function () {
  var NS = "http://www.w3.org/2000/svg";
  document.querySelectorAll("[data-mark]").forEach(function (box) {
    var img = box.querySelector(".p-mark-stage img");
    var svg = box.querySelector(".p-mark-layer");
    var input = box.querySelector("[data-mark-input]");
    var undo = box.querySelector("[data-mark-undo]");
    var clear = box.querySelector("[data-mark-clear]");
    var count = box.querySelector("[data-mark-count]");
    var none = box.querySelector("[data-mark-none]");  // The option to answer without marking anything (R54)
    var list = box.querySelector("[data-mark-list]"), status = box.querySelector("[data-mark-status]");
    var stage = box.querySelector(".p-mark-stage"), cross = box.querySelector(".p-mark-cross");
    var lines = box.dataset.mode === "lines";
    var max = parseInt(box.dataset.max, 10) || Infinity;
    var marks = [];
    try { marks = JSON.parse(input.value || "[]") || []; } catch (e) { marks = []; }
    if (none) none.addEventListener("change", function () {  // ticking it takes the marks away, a new mark the tick
      if (none.checked) { marks = []; sync(); var item = box.closest(".p-item"); if (item) item.classList.remove("has-error", "has-nudge"); }
    });
    if (!img || !svg) return;
    var W = 1000, H = 1000, drawing = null, full = false;

    function el(name, attrs) {
      var e = document.createElementNS(NS, name);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      return e;
    }
    function pts(line) { return line.map(function (p) { return (p[0] * W).toFixed(1) + "," + (p[1] * H).toFixed(1); }).join(" "); }
    function shape(m) {
      if (lines) return el("polyline", { points: pts(m), class: "p-mark-line", "stroke-width": (W * 0.03).toFixed(1) });
      return el("circle", { cx: m[0] * W, cy: m[1] * H, r: (W * 0.018).toFixed(1), class: "p-mark-dot", "stroke-width": (W * 0.004).toFixed(1) });
    }
    function pct(v) { return Math.round(v * 100); }
    function where(m) {
      return uiT("{x} % von links, {y} % von oben", "{x}% from the left, {y}% from the top", { x: pct(m[0]), y: pct(m[1]) });
    }
    function tell(text) { if (status) status.textContent = text; }
    function bump() { box.classList.remove("bump"); void box.offsetWidth; box.classList.add("bump"); }
    function takeBack(k) {  // k: 0-based; says so in the status line
      marks.splice(k, 1);
      sync();
      tell(uiT("Stelle {n} entfernt.", "Spot {n} removed.", { n: k + 1 }));
    }
    function pick(k) {  // the spot of the focused "entfernen" stands out on the image
      Array.prototype.forEach.call(svg.childNodes, function (node, n) { node.classList.toggle("is-picked", n === k); });
    }
    function listSpots() {
      if (!list) return;
      list.innerHTML = "";
      marks.forEach(function (m, k) {
        var li = document.createElement("li"), text = document.createElement("span"), button = document.createElement("button");
        text.textContent = uiT("Stelle {n}: {where}", "Spot {n}: {where}", { n: k + 1, where: where(m) });
        button.type = "button"; button.className = "p-mark-undo";
        button.textContent = uiT("entfernen", "remove");
        button.setAttribute("aria-label", uiT("Stelle {n} entfernen", "Remove spot {n}", { n: k + 1 }));
        button.addEventListener("click", function () {
          takeBack(k);
          // the focus to the next "entfernen", else back to the image
          var buttons = list.querySelectorAll("button");
          if (buttons.length) buttons[Math.min(k, buttons.length - 1)].focus(); else if (stage) stage.focus();
        });
        ["focus", "mouseenter"].forEach(function (on) { button.addEventListener(on, function () { pick(k); }); });
        ["blur", "mouseleave"].forEach(function (on) { button.addEventListener(on, function () { pick(-1); }); });
        li.appendChild(text); li.appendChild(button);
        list.appendChild(li);
      });
      list.hidden = !marks.length;
    }
    function add(p) {  // a spot by tap or key
      if (marks.length >= max) { bump(); tell(uiT("Maximum erreicht", "maximum reached")); return; }
      marks.push(p);
      sync();
      tell(uiT("Stelle {n} gesetzt, {where}.", "Spot {n} marked, {where}.", { n: marks.length, where: where(p) }));
    }
    function sync() {
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      marks.forEach(function (m) { svg.appendChild(shape(m)); });
      if (!lines) listSpots();
      input.value = marks.length ? JSON.stringify(marks) : "";
      undo.disabled = clear.disabled = !marks.length;
      var n = marks.length, word = lines ? (n === 1 ? uiT("Linie", "line") : uiT("Linien", "lines"))
        : (n === 1 ? uiT("Stelle", "spot") : uiT("Stellen", "spots"));
      count.textContent = n === 0 ? "" : (isFinite(max) ? n + " / " + max + " " : n + " ") + word
        + (n >= max ? " · " + uiT("Maximum erreicht", "maximum reached") : "");
      box.classList.toggle("is-full", n >= max);
      if (n && none) none.checked = false;
      if (n) { var item = box.closest(".p-item"); if (item) item.classList.remove("has-error", "has-nudge"); }
    }
    function layout() {
      W = img.naturalWidth || 1000; H = img.naturalHeight || 1000;
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      sync();
    }
    function at(e) {
      var r = svg.getBoundingClientRect();
      return [Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1)]
        .map(function (v) { return Math.round(v * 10000) / 10000; });
    }
    // Spots: a mark needs a tap, i.e. down and up at (nearly) the same place. A finger that starts on the
    // image and moves is scrolling the page (the browser then cancels the pointer) and leaves no mark.
    var TAP = 10, tap = null;  // px
    svg.addEventListener("pointerdown", function (e) {
      if (e.button !== undefined && e.button > 0) return;
      if (marks.length >= max) { bump(); return; }
      if (!lines) {
        if (e.pointerType === "mouse") e.preventDefault();  // no text selection or image drag
        tap = { id: e.pointerId, x: e.clientX, y: e.clientY };
        return;
      }
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      drawing = { line: [at(e)], node: null };
      drawing.node = shape(drawing.line);
      svg.appendChild(drawing.node);
    });
    svg.addEventListener("pointermove", function (e) {
      if (!drawing) return;
      var p = at(e), last = drawing.line[drawing.line.length - 1];
      if (Math.abs(p[0] - last[0]) + Math.abs(p[1] - last[1]) < 0.004 || drawing.line.length >= 400) return;
      drawing.line.push(p);
      drawing.node.setAttribute("points", pts(drawing.line));
    });
    function end() {
      if (!drawing) return;
      var line = drawing.line;
      drawing = null;
      if (line.length === 1) line.push([line[0][0] + 0.001, line[0][1]]);  // a tap leaves a short dash
      marks.push(line);
      sync();
    }
    svg.addEventListener("pointerup", function (e) {
      if (tap && tap.id === e.pointerId && Math.abs(e.clientX - tap.x) <= TAP && Math.abs(e.clientY - tap.y) <= TAP) {
        var p = at(e);
        cx = p[0]; cy = p[1]; aim();  // the crosshair follows, for a switch to the keyboard
        add(p);
      }
      tap = null;
      end();
    });
    svg.addEventListener("pointercancel", function () { tap = null; end(); });
    svg.addEventListener("pointerleave", function () { tap = null; });
    undo.addEventListener("click", function () { if (marks.length) takeBack(marks.length - 1); });
    clear.addEventListener("click", function () { marks = []; sync(); });
    // the keyboard: a crosshair over the image (spots only; free-hand lines are exempt from WCAG 2.1.1)
    var cx = 0.5, cy = 0.5;
    function aim() { if (cross) { cross.style.left = (cx * 100) + "%"; cross.style.top = (cy * 100) + "%"; } }
    if (!lines && stage && cross) {
      stage.tabIndex = 0;
      stage.setAttribute("role", "application");  // the arrow keys belong to the crosshair, not to reading
      stage.setAttribute("aria-label", uiT("Bild zum Markieren", "Image to mark") + (img.alt ? ": " + img.alt : ""));
      var keys = box.querySelector(".p-mark-keys");
      if (keys && keys.id) stage.setAttribute("aria-describedby", keys.id);
      aim();
      // the crosshair shows while the keys are in use (CSS), not after a click on the image
      stage.addEventListener("pointerdown", function () { stage.classList.remove("is-keys"); });
      stage.addEventListener("keydown", function (e) {
        if (e.key !== "Tab") stage.classList.add("is-keys");
        var step = e.shiftKey ? 0.1 : 0.02, moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        var move = moves[e.key];
        if (move) {
          e.preventDefault();
          cx = Math.round(Math.min(Math.max(cx + move[0] * step, 0), 1) * 10000) / 10000;
          cy = Math.round(Math.min(Math.max(cy + move[1] * step, 0), 1) * 10000) / 10000;
          aim();
          tell(where([cx, cy]));
        } else if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();  // Enter would send the page
          add([cx, cy]);
        } else if (e.key === "Delete" || e.key === "Backspace") {
          e.preventDefault();
          if (marks.length) takeBack(marks.length - 1);
        }
      });
    }
    box.classList.add("is-live");
    if (img.complete && img.naturalWidth) layout(); else img.addEventListener("load", layout);
  });
})();
