// Moving the variables of the path diagram on the results page (surveys/_sem.html, Ralf 2026-10-12): people with
// "Bearbeiten" drag a construct or question, or move it with the arrow keys (Ctrl: further), and the place goes into
// the drawing of the study plan (static/model.js), the one arrangement both pages show (modell-zeichnen-konzept.md,
// F5). Saved by itself after each move (surveys.results_sem_layout), one step for ↶ (Ctrl+Z), the page is not
// loaded anew. The lines are drawn here as app/sem_model.diagram draws them for a drawing: semGeometry is the same
// computation (tests/test_sem_drag.py compares both in Node).
// Needs app.js (pointerDrag, de_en, csrfToken, saveState, saveClock, undoArrows, flash).

var SEM_W = 200, SEM_H = 64;

// the point on the outline of a node (ellipse or box around cx, cy) towards tx, ty (sem_model._rim)
function semRim(cx, cy, tx, ty, latent) {
  var dx = tx - cx, dy = ty - cy, rx = SEM_W / 2, ry = SEM_H / 2, t;
  if (dx === 0 && dy === 0) return [cx, cy];
  if (latent) t = 1 / Math.sqrt((dx / rx) * (dx / rx) + (dy / ry) * (dy / ry));
  else t = Math.min(dx ? rx / Math.abs(dx) : Infinity, dy ? ry / Math.abs(dy) : Infinity);
  return [cx + t * dx, cy + t * dy];
}

function semShape(pos, latent, a, b, gap, bow) {
  var ca = [pos[a][0] + SEM_W / 2, pos[a][1] + SEM_H / 2], cb = [pos[b][0] + SEM_W / 2, pos[b][1] + SEM_H / 2];
  var dist = Math.hypot(cb[0] - ca[0], cb[1] - ca[1]) || 1;
  var nx = -(cb[1] - ca[1]) / dist, ny = (cb[0] - ca[0]) / dist;
  var mx = (ca[0] + cb[0]) / 2 + nx * bow, my = (ca[1] + cb[1]) / 2 + ny * bow;
  var p1 = semRim(ca[0], ca[1], mx, my, latent[a]), p2 = semRim(cb[0], cb[1], mx, my, latent[b]);
  var x1 = p1[0], y1 = p1[1], x2 = p2[0], y2 = p2[1];
  var ux = bow ? x2 - mx : x2 - x1, uy = bow ? y2 - my : y2 - y1, norm = Math.hypot(ux, uy) || 1;
  x2 -= gap * ux / norm; y2 -= gap * uy / norm;
  function f(v) { return v.toFixed(1); }
  if (bow) {
    var vx = x1 - mx, vy = y1 - my;
    norm = Math.hypot(vx, vy) || 1;
    x1 -= gap * vx / norm; y1 -= gap * vy / norm;
    var cx = 2 * mx - (x1 + x2) / 2, cy = 2 * my - (y1 + y2) / 2;
    return { d: "M" + f(x1) + " " + f(y1) + " Q" + f(cx) + " " + f(cy) + " " + f(x2) + " " + f(y2), lx: mx, ly: my - 6,
             q: [x1, y1, cx, cy, x2, y2] };
  }
  return { d: "M" + f(x1) + " " + f(y1) + " L" + f(x2) + " " + f(y2), lx: (x1 + x2) / 2, ly: (y1 + y2) / 2 - 6,
           q: [x1, y1, (x1 + x2) / 2, (y1 + y2) / 2, x2, y2] };
}

// does the (quadratic) line pass through a node other than a and b? (sem_model._crosses)
function semCrosses(pos, a, b, q) {
  return Object.keys(pos).some(function (v) {
    if (v === a || v === b) return false;
    var x = pos[v][0], y = pos[v][1];
    for (var i = 1; i < 20; i++) {
      var t = i / 20, px = (1 - t) * (1 - t) * q[0] + 2 * (1 - t) * t * q[2] + t * t * q[4];
      var py = (1 - t) * (1 - t) * q[1] + 2 * (1 - t) * t * q[3] + t * t * q[5];
      if (x - 6 < px && px < x + SEM_W + 6 && y - 6 < py && py < y + SEM_H + 6) return true;
    }
    return false;
  });
}

function semLine(pos, latent, a, b, bow) {
  if (bow) return semShape(pos, latent, a, b, 6, bow);
  var tries = [0, 48, -48, 96, -96, 150, -150];
  for (var i = 0; i < tries.length; i++) {
    var found = semShape(pos, latent, a, b, 6, tries[i]);
    if (!semCrosses(pos, a, b, found.q)) return found;
  }
  return semShape(pos, latent, a, b, 6, 0);
}

// the values on the arrows off the variables and off each other (sem_model._free_labels, the same computation):
// its own place if free, else along its line from the middle outwards, else just beside it (Ralf, 2026-10-17)
var SEM_LW = 44, SEM_LH = 16;
function semFreeLabels(pos, edges) {
  var taken = [];
  function hits(x, y) {
    var names = Object.keys(pos);
    for (var i = 0; i < names.length; i++) {
      var vx = pos[names[i]][0], vy = pos[names[i]][1];
      if (vx - 4 < x + SEM_LW / 2 && x - SEM_LW / 2 < vx + SEM_W + 4 && vy - 4 < y + 4 && y - 12 < vy + SEM_H + 4) return true;
    }
    return taken.some(function (t) { return Math.abs(t[0] - x) < SEM_LW + 4 && Math.abs(t[1] - y) < SEM_LH + 2; });
  }
  edges.forEach(function (e) {
    if (!e || !e.q) return;
    if (!hits(e.lx, e.ly)) { taken.push([e.lx, e.ly]); return; }
    var q = e.q, dx = q[4] - q[0], dy = q[5] - q[1], norm = Math.sqrt(dx * dx + dy * dy) || 1, found = null;
    [0, 18, -18, 32, -32].some(function (off) {
      return [0.5, 0.4, 0.6, 0.32, 0.68, 0.25, 0.75].some(function (t) {
        var u = 1 - t;
        var x = u * u * q[0] + 2 * u * t * q[2] + t * t * q[4] - off * dy / norm;
        var y = u * u * q[1] + 2 * u * t * q[3] + t * t * q[5] - 6 + off * dx / norm;
        if (hits(x, y)) return false;
        found = [x, y];
        return true;
      });
    });
    if (found) { e.lx = found[0]; e.ly = found[1]; }
    taken.push([e.lx, e.ly]);
  });
}

// The picture for the centres of the variables (by code): {pos: {name: [x, y]} (top-left corners), edges: [{d, lx,
// ly}] in the order of data.edges (null: not drawn), box: [left, top, width, height]} (sem_model.diagram with a
// drawing: the subscales of one instrument below each other around its centre)
function semGeometry(data, centres) {
  var pos = {}, latent = {}, byCode = {};
  data.nodes.forEach(function (n) { (byCode[n.code] = byCode[n.code] || []).push(n.name); latent[n.name] = n.latent; });
  data.nodes.forEach(function (n) {
    var parts = byCode[n.code], c = centres[n.code];
    var off = (parts.indexOf(n.name) - (parts.length - 1) / 2) * (SEM_H + 14);
    pos[n.name] = [c[0] - SEM_W / 2, c[1] + off - SEM_H / 2];
  });
  var out = [], byPair = {};
  data.edges.forEach(function (e, i) {
    if (e.kind === "path" && pos[e.a] && pos[e.b]) { out[i] = semLine(pos, latent, e.a, e.b, 0); byPair[e.a + "\u0000" + e.b] = out[i]; }
  });
  data.edges.forEach(function (e, i) {
    if (e.kind === "mod") {
      var base = byPair[e.x + "\u0000" + e.b];
      if (!base || !pos[e.a]) { out[i] = null; return; }
      var q = base.q, mx = 0.36 * q[0] + 0.48 * q[2] + 0.16 * q[4], my = 0.36 * q[1] + 0.48 * q[3] + 0.16 * q[5];
      var p = semRim(pos[e.a][0] + SEM_W / 2, pos[e.a][1] + SEM_H / 2, mx, my, latent[e.a]);
      var dist = Math.hypot(mx - p[0], my - p[1]) || 1;
      var x2 = mx - 5 * (mx - p[0]) / dist, y2 = my - 5 * (my - p[1]) / dist;
      out[i] = { d: "M" + p[0].toFixed(1) + " " + p[1].toFixed(1) + " L" + x2.toFixed(1) + " " + y2.toFixed(1),
                 lx: p[0] + 0.55 * (x2 - p[0]) + 14, ly: p[1] + 0.55 * (y2 - p[1]), q: [p[0], p[1], (p[0] + x2) / 2, (p[1] + y2) / 2, x2, y2] };
    } else if (e.kind === "cov") {
      out[i] = pos[e.a] && pos[e.b] ? semLine(pos, latent, e.a, e.b, 36) : null;
    } else if (out[i] === undefined) out[i] = null;
  });
  semFreeLabels(pos, out);
  var names = Object.keys(pos);
  var left = Math.min.apply(null, names.map(function (v) { return pos[v][0]; })) - 30;
  var top = Math.min.apply(null, names.map(function (v) { return pos[v][1]; })) - 30;
  var right = Math.max.apply(null, names.map(function (v) { return pos[v][0]; })) + SEM_W + 30;
  var bottom = Math.max.apply(null, names.map(function (v) { return pos[v][1]; })) + SEM_H + 30;
  var tops = names.map(function (v) { return pos[v][1]; }).concat(out.filter(Boolean).map(function (g) { return g.ly - 14; }));
  top = Math.max(top, Math.min.apply(null, tops) - 20);
  return { pos: pos, edges: out, box: [left, top, right - left, bottom - top] };
}

(function () {
  var box = document.querySelector("[data-sem-drag]");
  if (!box || !window.pointerDrag) return;
  var data = JSON.parse(box.querySelector("[data-sem-drag-data]").textContent || "{}");
  var svg = box.querySelector("svg"), url = box.getAttribute("data-url");
  var GRID = 8, mine = {};  // the steps this visit made or took back here: ↶ ↷ take them without loading the page
  var shift = data.shift, centres = null, base = {};
  data.nodes.forEach(function (n) { base[n.name] = [n.x, n.y]; });
  // the picture as the server drew it: back to it when ↶ takes the first move away from the automatic arrangement
  var drawn = { box: svg.getAttribute("viewBox"), width: svg.getAttribute("width"), parts: [] };
  svg.querySelectorAll("[data-edge], [data-edge-label]").forEach(function (el) {
    drawn.parts.push([el, el.getAttribute("d"), el.getAttribute("x"), el.getAttribute("y")]);
  });
  function asDrawn() {
    drawn.parts.forEach(function (p) {
      if (p[1] != null) p[0].setAttribute("d", p[1]);
      if (p[2] != null) { p[0].setAttribute("x", p[2]); p[0].setAttribute("y", p[3]); }
    });
    svg.querySelectorAll("[data-node]").forEach(function (node) { node.removeAttribute("transform"); });
    svg.setAttribute("viewBox", drawn.box);
    svg.setAttribute("width", drawn.width);
    centres = null; shift = data.shift;
  }

  // the centre of each variable (code) on the page, from the positions the server drew
  function fromPage() {
    var sum = {};
    data.nodes.forEach(function (n) {
      var s = sum[n.code] = sum[n.code] || [0, 0, 0];
      s[0] += n.x + SEM_W / 2; s[1] += n.y + SEM_H / 2; s[2] += 1;
    });
    var out = {};
    Object.keys(sum).forEach(function (code) { out[code] = [sum[code][0] / sum[code][2], sum[code][1] / sum[code][2]]; });
    return out;
  }
  // the automatic arrangement becomes a drawing at the first move: its places on the grid of the study plan
  function begin() {
    if (centres) return;
    centres = fromPage();
    if (!shift) {
      var minX = Infinity, minY = Infinity;
      Object.keys(centres).forEach(function (c) { minX = Math.min(minX, centres[c][0]); minY = Math.min(minY, centres[c][1]); });
      shift = [Math.max(0, Math.ceil((SEM_W / 2 + 8 - minX) / GRID) * GRID), Math.max(0, Math.ceil((SEM_H / 2 + 8 - minY) / GRID) * GRID)];
      Object.keys(centres).forEach(function (c) { centres[c] = [snap(centres[c][0], 0), snap(centres[c][1], 1)]; });
    }
  }
  function snap(v, axis) { return Math.round((v + shift[axis]) / GRID) * GRID - shift[axis]; }
  function draw() {
    var g = semGeometry(data, centres);
    data.nodes.forEach(function (n, i) {
      var node = svg.querySelector('[data-node="' + i + '"]'), p = g.pos[n.name];
      if (node) node.setAttribute("transform", "translate(" + (p[0] - base[n.name][0]).toFixed(1) + " " + (p[1] - base[n.name][1]).toFixed(1) + ")");
    });
    g.edges.forEach(function (e, i) {
      var path = svg.querySelector('[data-edge="' + i + '"]'), label = svg.querySelector('[data-edge-label="' + i + '"]');
      if (!e) return;
      if (path) path.setAttribute("d", e.d);
      if (label) { label.setAttribute("x", e.lx.toFixed(1)); label.setAttribute("y", e.ly.toFixed(1)); }
    });
    var view = g.box.slice();
    if (box.classList.contains("is-measure")) view[3] += SEM_MEASURE;
    svg.setAttribute("viewBox", view.map(function (v) { return Math.round(v); }).join(" "));
    svg.setAttribute("width", Math.round(g.box[2]));
  }
  function nameOf(code) {
    var n = data.nodes.filter(function (m) { return m.code === code; })[0];
    return n ? n.label : code;
  }
  function say(kind, text) { if (window.saveState) saveState(box, kind, text, box); }
  // one save at a time: a move while one is under way goes out once it answers, with the latest places, so an
  // older answer can never come last and win (code review 2026-10-15, I2)
  var saving = null, again = null;
  function save(code) {
    if (saving) { again = code; return saving; }
    var nodes = {};
    Object.keys(centres).forEach(function (c) { nodes[c] = [Math.round(centres[c][0] + shift[0]), Math.round(centres[c][1] + shift[1])]; });
    say("busy", "");
    saving = fetch(url, { method: "POST", credentials: "same-origin",
                        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
                        body: JSON.stringify({ nodes: nodes, name: nameOf(code) }) })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        if (d.step) mine[d.step] = true;
        arrows(d);
        say("ok", de_en("Gespeichert ", "Saved ") + (d.at || (window.saveClock ? saveClock() : "")));
      })
      .catch(function () {
        say("error", de_en("Anordnung nicht gespeichert: keine Verbindung zum Server, oder die Anmeldung ist abgelaufen.",
                           "Arrangement not saved: no connection to the server, or the sign-in has expired."));
      })
      .then(function () {
        saving = null;
        if (again !== null) { var next = again; again = null; return save(next); }
      });
    return saving;
  }
  function arrows(d) {
    if (window.undoArrows) undoArrows(d.undo, d.redo);
    var u = document.querySelector("[data-undo]"), r = document.querySelector("[data-redo]");
    if (u) u.setAttribute("data-step", d.undo_id || "");
    if (r) r.setAttribute("data-step", d.redo_id || "");
  }
  function point(e) {
    var m = svg.getScreenCTM(), p = svg.createSVGPoint();
    p.x = e.x; p.y = e.y;
    p = p.matrixTransform(m.inverse());
    return [p.x, p.y];
  }
  function codeOf(node) { return data.nodes[+node.getAttribute("data-node")].code; }

  pointerDrag({
    source: function (target) {
      var node = target.closest && target.closest("[data-node]");
      if (!node || !svg.contains(node)) return null;
      return { code: codeOf(node), node: node };
    },
    start: function (d) {
      begin();
      var p = point(d);
      d.from = centres[d.code].slice();
      d.dx = d.from[0] - p[0]; d.dy = d.from[1] - p[1];
      box.classList.add("is-dragging");
      // in front of the arrows, the values and the other variables while it moves (Ralf, 2026-10-17)
      d.node.parentNode.appendChild(d.node);
    },
    move: function (d) {
      var p = point(d);
      centres[d.code] = [snap(p[0] + d.dx, 0), snap(p[1] + d.dy, 1)];
      draw();
    },
    drop: function (d) {
      var c = centres[d.code];
      if (c[0] !== d.from[0] || c[1] !== d.from[1]) save(d.code);
    },
    end: function (d) {
      box.classList.remove("is-dragging");
      d.node.focus({ preventScroll: true });
    },
    scroller: function () { return null; }
  });

  // the arrow keys move the variable that has the focus, Ctrl further; saved once the keys rest
  var timer = null;
  svg.addEventListener("keydown", function (e) {
    var node = e.target.closest && e.target.closest("[data-node]");
    var step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (!node || !step || e.altKey || e.shiftKey) return;
    e.preventDefault();
    begin();
    var code = codeOf(node), by = e.ctrlKey || e.metaKey ? 40 : GRID;
    centres[code] = [snap(centres[code][0] + step[0] * by, 0), snap(centres[code][1] + step[1] * by, 1)];
    draw();
    clearTimeout(timer);
    timer = setTimeout(function () { save(code); }, 600);
  });

  // ↶ ↷ (Ctrl+Z, Ctrl+Y) on a step made here: taken back without loading the page; any other step the usual way
  document.addEventListener("submit", function (e) {
    var button = e.submitter || (e.target.querySelector && e.target.querySelector("[data-undo], [data-redo]"));
    if (!button || !button.matches || !button.matches("[data-undo], [data-redo]")) return;
    var id = button.getAttribute("data-step");
    if (!id || !mine[id]) return;
    e.preventDefault();
    var form = e.target, body = new FormData(form), answered = false;
    body.append("only", id);
    fetch(form.action, { method: "POST", credentials: "same-origin", body: body })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        answered = true;
        if (d.stale) { form.submit(); return; }
        arrows(d);
        if (!d.done) { if (d.error && window.flash) flash(d.error, "error"); return; }
        if (d.undo_id) mine[d.undo_id] = true;
        if (d.redo_id) mine[d.redo_id] = true;
        return fetch(url + "?places=1", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (p) {
          if (!p.nodes || !centres) return;
          var codes = Object.keys(centres);
          if (codes.some(function (code) { return !p.nodes[code]; })) asDrawn();  // no drawing any more
          else {
            codes.forEach(function (code) { centres[code] = [p.nodes[code][0] - shift[0], p.nodes[code][1] - shift[1]]; });
            draw();
          }
          var again = button.matches("[data-redo]");
          if (window.flash && d.label) flash((again ? de_en("Wiederholt: ", "Redone: ") : de_en("Zurückgenommen: ", "Undone: ")) + d.label, "success");
        });
      })
      // the step is taken back once the server answered: sending the form now would take back a second one, so
      // the page is only loaded anew (code review 2026-10-15, I4)
      .catch(function () { if (answered) location.reload(); else form.submit(); });
  }, true);
})();

// „Messmodell zeigen“ above a path diagram: the statements of each construct with their loadings, below it; the
// picture grows by the room they take (Ralf, 2026-10-17)
var SEM_MEASURE = 64;
document.addEventListener("click", function (e) {
  var button = e.target.closest && e.target.closest("[data-sem-measure]");
  if (!button) return;
  var box = document.getElementById(button.getAttribute("aria-controls"));
  var svg = box && box.querySelector("svg");
  if (!svg) return;
  var on = !box.classList.contains("is-measure"), view = svg.getAttribute("viewBox").split(/\s+/).map(Number);
  box.classList.toggle("is-measure", on);
  view[3] += on ? SEM_MEASURE : -SEM_MEASURE;
  svg.setAttribute("viewBox", view.join(" "));
  button.setAttribute("aria-pressed", on ? "true" : "false");
  button.textContent = on ? button.getAttribute("data-off") : button.getAttribute("data-on");
});
