// The drawing of the model in the study plan (concept modell-zeichnen-konzept.md, Ralf
// 2026-10-11: F1 to F10 as recommended). A second view of the hypotheses beside the list: every arrow is a row of
// the list (templates/surveys/_fields_plan.html), and what the drawing changes is written into those rows, which the
// form saves as before (app/study_plan.from_form, one step for "Rückgängig" per gesture). Only the places of the
// variables are the drawing's own (hidden field plan_layout, study_plan.layout_of).
//   pointer   drag a variable from the list onto the area; drag a variable to move it; drag from its handle (the dot
//             at its edge) to another one for a relation; drag it onto another one and hold for a moment (the target
//             turns blue) for a relation; onto an arrow for a mediator. Every gesture also works by clicking.
//   keyboard  one stop for the area, the arrow keys go from item to item, Shift+arrows move, Enter opens, V
//             connects, M puts a mediator or moderator onto an arrow, Delete removes; "?" lists the keys.
//   stage 2   a variable dropped onto an arrow can moderate it (F2): a new hypothesis "Moderation", drawn as an
//             arrow from the moderator onto the middle of the path it changes.
// Needs app.js (pointerDrag, de_en, askConfirm, renumberHypotheses).
(function () {
  var root = document.querySelector("[data-plan-model]");
  if (!root) return;
  var form = root.closest("form"), set = root.closest("[data-rows]");
  var data = JSON.parse(root.querySelector("[data-model-data]").textContent || "{}");
  var layoutInput = form.querySelector("[data-plan-layout]"), saveButton = form.querySelector("[data-model-save]");
  var NS = "http://www.w3.org/2000/svg", W = 200, H = 64, GRID = 8, DWELL = 400, INNER = 0.7, GAP = 16;
  var editable = !!data.editable;
  var vars = {};
  (data.vars || []).forEach(function (v) { vars[v.code] = v; });
  var layout = {};
  try { layout = (JSON.parse(layoutInput.value || "{}").nodes) || {}; } catch (e) { layout = {}; }
  var extra = [];        // placed without a relation yet: not saved (F6)
  var selected = [];     // codes, for "Ausrichten"
  var focusKey = null;   // the item that holds the roving focus
  var connect = null;    // {from: code} while a relation is being made by click or key
  var zoom = 1, pop = null, counter = 0;
  // the measurement models (the statements of each construct) shown or not: a view only, kept in this browser
  var MEASURE_KEY = "askurious-plan-measure", measure = false;
  try { measure = localStorage.getItem(MEASURE_KEY) === "1"; } catch (e) { measure = false; }
  var dragging = null;   // the variable being dragged: the arrows do not bend away from it
  var dragGroup = [];    // the other selected variables moved along with it (Ralf, 2026-10-17)

  var area = root.querySelector("[data-pm-area]"), svg = null, say = root.querySelector("[data-pm-say]");
  var panel = root.querySelector("[data-pm-panel]"), textList = root.querySelector("[data-pm-text]");
  var outside = root.querySelector("[data-pm-outside]"), tools = root.querySelector("[data-pm-tools]");
  var hint = root.querySelector("[data-pm-hint]");

  function announce(text) { if (say) { say.textContent = ""; setTimeout(function () { say.textContent = text; }, 30); } }
  function el(name, attrs, parent) {
    var node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (parent) parent.appendChild(node);
    return node;
  }
  function html(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = text;
    return node;
  }

  // --- the rows of the list --------------------------------------------------------------------------------------

  function rows() { return Array.prototype.slice.call(set.querySelectorAll(":scope > .plan-hyp[data-row]")); }
  function field(row, name) { return row.querySelector('[name="' + name + '"]'); }
  function val(row, name) { var f = field(row, name); return f ? f.value : ""; }
  function hyps() {
    return rows().map(function (row, i) {
      return { row: row, n: i + 1, id: val(row, "h_id"), text: val(row, "h_text"), kind: val(row, "h_kind") || "correlation",
               dir: val(row, "h_dir"), iv: val(row, "h_iv"), dv: val(row, "h_dv"), mv: val(row, "h_mv"), wv: val(row, "h_wv"),
               analysis: val(row, "h_analysis") };
    });
  }
  function name(code) { return (vars[code] && vars[code].name) || code; }
  function textFor(h) {
    var a = name(h.iv), b = name(h.dv), m = name(h.mv), w = name(h.wv);
    var pos = h.dir === "pos", neg = h.dir === "neg";
    switch (h.kind) {
      case "effect":
        return pos ? de_en(a + " wirkt positiv auf " + b + ".", a + " has a positive effect on " + b + ".")
             : neg ? de_en(a + " wirkt negativ auf " + b + ".", a + " has a negative effect on " + b + ".")
             : de_en(a + " wirkt auf " + b + ".", a + " affects " + b + ".");
      case "mediation":
        return de_en(a + " wirkt über " + m + " auf " + b + ".", a + " affects " + b + " through " + m + ".");
      case "moderation":
        // the sign of the interaction, not "stronger": with a negative path a positive interaction weakens it (SEM-11)
        return pos ? de_en("Je höher " + w + ", desto größer der Pfad von " + a + " auf " + b + " (positive Wechselwirkung).", "The higher " + w + ", the larger the path from " + a + " to " + b + " (positive interaction).")
             : neg ? de_en("Je höher " + w + ", desto kleiner der Pfad von " + a + " auf " + b + " (negative Wechselwirkung).", "The higher " + w + ", the smaller the path from " + a + " to " + b + " (negative interaction).")
             : de_en(w + " verändert den Einfluss von " + a + " auf " + b + ".", w + " changes the effect of " + a + " on " + b + ".");
      case "difference":
        return de_en(b + " unterscheidet sich zwischen den Gruppen von " + a + ".", b + " differs between the groups of " + a + ".");
      case "causal":
        return de_en("Die Bedingung " + a + " wirkt auf " + b + ".", "The condition " + a + " affects " + b + ".");
      default:
        return pos ? de_en(a + " und " + b + " hängen positiv zusammen.", a + " and " + b + " are positively related.")
             : neg ? de_en(a + " und " + b + " hängen negativ zusammen.", a + " and " + b + " are negatively related.")
             : de_en(a + " und " + b + " hängen zusammen.", a + " and " + b + " are related.");
    }
  }
  // write a hypothesis into its row; a text that was the drawing's own suggestion follows, one typed by hand stays
  function writeRow(row, before, h) {
    var text = field(row, "h_text");
    var auto = !text.value.trim() || (before && text.value.trim() === textFor(before));
    [["h_kind", h.kind], ["h_dir", h.dir || ""], ["h_iv", h.iv], ["h_dv", h.dv], ["h_mv", h.mv || ""], ["h_wv", h.wv || ""],
     ["h_analysis", h.analysis || ""]].forEach(function (pair) {
      var f = field(row, pair[0]);
      if (!f) return;
      if (f.tagName === "SELECT" && pair[1] && !Array.prototype.some.call(f.options, function (o) { return o.value === pair[1]; }))
        f.appendChild(new Option(pair[1], pair[1]));
      f.value = pair[1];
    });
    if (h.text != null) text.value = h.text;
    else if (auto) text.value = textFor(h);
    row.dataset.modelChanged = "1";
    return auto;
  }
  function newRow() {
    var template = set.querySelector("template[data-row-template]");
    var holder = document.createElement("div");
    holder.innerHTML = template.innerHTML.replace(/__N__/g, String((Date.now() + (counter++)) % 100000000));
    var row = holder.firstElementChild;
    template.parentNode.insertBefore(row, template);
    if (window.renumberHypotheses) window.renumberHypotheses(set);
    return row;
  }
  function removeRow(row) {
    row.remove();
    if (window.renumberHypotheses) window.renumberHypotheses(set);
  }
  // one gesture, one save and one step for "Rückgängig" (the form saves on a click of a button: app.js)
  function commit(what) {
    var used = {};
    nodes().forEach(function (code) { if (extra.indexOf(code) < 0 && layout[code]) used[code] = layout[code]; });
    layoutInput.value = JSON.stringify({ v: 1, nodes: used });
    saveButton.setAttribute("data-undo-what", what);
    saveButton.click();
  }

  // --- what is drawn ---------------------------------------------------------------------------------------------

  // edges of one hypothesis: [{key, a, b, type: "path" | "cov" | "direct", leg}], or a reason it is not drawn
  function edgesOf(h) {
    if (h.kind === "open") return { why: de_en("offene Forschungsfrage", "open research question") };
    if (h.kind === "descriptive") return { why: de_en("beschreibende Annahme ohne Test", "descriptive assumption without a test") };
    if (!h.iv || !h.dv) return { why: de_en("eine Variable fehlt noch", "a variable is still missing") };
    if (h.iv === h.dv) return { why: de_en("zweimal dieselbe Frage", "the same question twice") };
    if (h.kind === "mediation") {
      if (!h.mv || h.mv === h.iv || h.mv === h.dv) return { why: de_en("der Vermittler fehlt noch", "the mediator is still missing") };
      return { edges: [{ key: "e:" + h.id + ":a", a: h.iv, b: h.mv, type: "path", leg: "a" },
                       { key: "e:" + h.id + ":b", a: h.mv, b: h.dv, type: "path", leg: "b" },
                       { key: "e:" + h.id + ":d", a: h.iv, b: h.dv, type: "direct" }] };
    }
    if (h.kind === "moderation") {
      if (!h.wv || h.wv === h.iv || h.wv === h.dv) return { why: de_en("der Moderator fehlt noch", "the moderator is still missing") };
      return { edges: [{ key: "e:" + h.id, a: h.wv, b: h.iv, c: h.dv, type: "mod" }] };
    }
    return { edges: [{ key: "e:" + h.id, a: h.iv, b: h.dv, type: h.kind === "correlation" ? "cov" : "path" }] };
  }
  function nodes() {
    var out = [];
    hyps().forEach(function (h) {
      (edgesOf(h).edges || []).forEach(function (e) { [e.a, e.b, e.c].forEach(function (c) { if (c && out.indexOf(c) < 0) out.push(c); }); });
    });
    extra = extra.filter(function (c) { return out.indexOf(c) < 0 && (vars[c] || layout[c]); });
    return out.concat(extra);
  }
  function directed(skip) {  // the arrows with a direction, but those of hypothesis `skip`
    var out = [];
    hyps().forEach(function (h) {
      if (h.id === skip) return;
      (edgesOf(h).edges || []).forEach(function (e) {
        if (e.type === "path") out.push([e.a, e.b]);
        if (e.type === "mod") { out.push([e.b, e.c]); out.push([e.a, e.c]); }  // the path and the moderator's own one
      });
    });
    return out;
  }
  function reaches(from, to, arrows) {  // is there a way along the arrows from `from` to `to`?
    var seen = {}, todo = [from];
    while (todo.length) {
      var v = todo.pop();
      if (v === to) return true;
      if (seen[v]) continue;
      seen[v] = true;
      arrows.forEach(function (p) { if (p[0] === v) todo.push(p[1]); });
    }
    return false;
  }
  function circle(a, b, skip) { return reaches(b, a, directed(skip)); }

  // automatic places (Ralf, 2026-10-11: as few crossing lines as possible): a column per step along the arrows, a
  // variable moved right up to the column before its nearest target; a variable linked only without a direction
  // stands in the column of its partner. Within the columns the order with the fewest crossings: first by the mean
  // row of the neighbours (barycentre, in both directions), then every order of a column up to six variables, swaps
  // of neighbours beyond, counted on the drawn straight lines (a line through a variable counts three times). All
  // this within BUDGET steps of counting, so the page never stands still for long (audit 2026-10-11, N15: before,
  // columns of six with many arrows held the browser for seconds); a column of six then gets swaps only.
  function links() {  // [a, b, directed] of everything drawn between two variables
    var out = [];
    hyps().forEach(function (h) {
      (edgesOf(h).edges || []).forEach(function (e) {
        if (e.type === "path" || e.type === "direct") out.push([e.a, e.b, true]);
        else if (e.type === "cov") out.push([e.a, e.b, false]);
        else if (e.type === "mod") { out.push([e.b, e.c, true]); out.push([e.a, e.c, true]); }
      });
    });
    return out;
  }
  var BUDGET = 4e6;  // steps of cost() in auto(): about 30 ms on a desktop, about 0.2 s on a slow tablet
  function auto(codes, all) {
    var edges = links().filter(function (l) { return codes.indexOf(l[0]) >= 0 && codes.indexOf(l[1]) >= 0 && l[0] !== l[1]; });
    var arrows = edges.filter(function (l) { return l[2]; }), depth = {};
    codes.forEach(function (c) { depth[c] = 0; });
    for (var k = 0; k < codes.length; k++) {
      arrows.forEach(function (p) { depth[p[1]] = Math.max(depth[p[1]], depth[p[0]] + 1); });
    }
    for (k = 0; k < codes.length; k++) {  // a source next to its nearest target: a short straight line, no arc
      codes.forEach(function (v) {
        var targets = arrows.filter(function (p) { return p[0] === v; }).map(function (p) { return depth[p[1]]; });
        if (targets.length) depth[v] = Math.max(depth[v], Math.min.apply(null, targets) - 1);
      });
    }
    codes.forEach(function (v) {  // only relations without a direction: beside its partner
      if (arrows.some(function (p) { return p[0] === v || p[1] === v; })) return;
      var partner = edges.filter(function (l) { return l[0] === v || l[1] === v; })
                         .map(function (l) { return l[0] === v ? l[1] : l[0]; })[0];
      if (partner) depth[v] = depth[partner];
    });
    var columns = [];
    codes.forEach(function (c) { (columns[depth[c]] = columns[depth[c]] || []).push(c); });
    columns = columns.filter(Boolean);
    var tall = Math.max.apply(null, columns.map(function (col) { return col.length; }));
    function places(cols) {
      var out = {};
      cols.forEach(function (col, d) {
        var top = (tall - col.length) * (H + 88) / 2;
        col.forEach(function (c, i) { out[c] = [W / 2 + 32 + d * (W + 104), H / 2 + 72 + top + i * (H + 88)]; });
      });
      return out;
    }
    // cost() runs thousands of times: everything that does not depend on the order is prepared once, the places go
    // into two arrays, and a variable can only lie on a line inside the rectangle of its two ends (audit 2026-10-11,
    // N15: the same result as before, several times faster)
    var index = {}, nCodes = codes.length;
    codes.forEach(function (c, i) { index[c] = i; });
    var ea = edges.map(function (e) { return index[e[0]]; }), eb = edges.map(function (e) { return index[e[1]]; });
    var pairs = [];  // edges that do not meet in a variable: they may cross
    for (var i0 = 0; i0 < edges.length; i0++) {
      for (var j0 = i0 + 1; j0 < edges.length; j0++) {
        if (ea[j0] === ea[i0] || ea[j0] === eb[i0] || eb[j0] === ea[i0] || eb[j0] === eb[i0]) continue;
        pairs.push(i0, j0);
      }
    }
    var px = new Float64Array(nCodes), py = new Float64Array(nCodes);
    // what one cost() costs, in steps: every pair of lines and every variable beside every line
    var steps = pairs.length / 2 + edges.length * nCodes + nCodes, spent = 0;
    function side(ax, ay, bx, by, cx, cy) { return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax); }
    function cost(cols) {
      spent += steps;
      for (var d = 0; d < cols.length; d++) {
        var col = cols[d], top = (tall - col.length) * (H + 88) / 2;
        for (var i = 0; i < col.length; i++) {
          var k = index[col[i]];
          px[k] = W / 2 + 32 + d * (W + 104);
          py[k] = H / 2 + 72 + top + i * (H + 88);
        }
      }
      var n = 0, length = 0, e, c;
      for (e = 0; e < edges.length; e++) length += Math.abs(py[ea[e]] - py[eb[e]]);
      for (var q = 0; q < pairs.length; q += 2) {
        var a = ea[pairs[q]], b = eb[pairs[q]], f = ea[pairs[q + 1]], g = eb[pairs[q + 1]];
        var d1 = side(px[f], py[f], px[g], py[g], px[a], py[a]), d2 = side(px[f], py[f], px[g], py[g], px[b], py[b]);
        var d3 = side(px[a], py[a], px[b], py[b], px[f], py[f]), d4 = side(px[a], py[a], px[b], py[b], px[g], py[g]);
        if (((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0)) && d1 && d2 && d3 && d4) n += 1;
      }
      for (e = 0; e < edges.length; e++) {  // through a variable on the way
        var ax = px[ea[e]], ay = py[ea[e]], bx = px[eb[e]], by = py[eb[e]];
        var x0 = Math.min(ax, bx) - W / 2, x1 = Math.max(ax, bx) + W / 2;
        var y0 = Math.min(ay, by) - H / 2, y1 = Math.max(ay, by) + H / 2;
        for (c = 0; c < nCodes; c++) {
          if (c === ea[e] || c === eb[e]) continue;
          var cx = px[c], cy = py[c];
          if (cx < x0 || cx > x1 || cy < y0 || cy > y1) continue;
          for (var s = 1; s < 10; s++) {
            var x = ax + (bx - ax) * s / 10, y = ay + (by - ay) * s / 10;
            if (Math.abs(x - cx) < W / 2 && Math.abs(y - cy) < H / 2) { n += 3; break; }
          }
        }
      }
      return n + length * 1e-5;
    }
    function rowOf(cols) {
      var out = {};
      cols.forEach(function (col) { col.forEach(function (c, i) { out[c] = i - (col.length - 1) / 2; }); });
      return out;
    }
    // barycentre sweeps: left to right by the variables before, right to left by those after
    for (var sweep = 0; sweep < 4; sweep++) {
      [1, -1].forEach(function (dir) {
        var order = dir > 0 ? columns.map(function (_, i) { return i; }) : columns.map(function (_, i) { return columns.length - 1 - i; });
        order.forEach(function (d) {
          if (spent >= BUDGET) return;
          var row = rowOf(columns);
          var mean = {};
          columns[d].forEach(function (c, i) {
            var near = edges.filter(function (l) { return l[0] === c || l[1] === c; }).map(function (l) { return l[0] === c ? l[1] : l[0]; })
                            .filter(function (o) { return dir > 0 ? depth[o] < depth[c] : depth[o] > depth[c]; });
            mean[c] = near.length ? near.reduce(function (s, o) { return s + row[o]; }, 0) / near.length : row[c] + i * 1e-3;
          });
          var before = cost(columns), trial = columns.slice();
          trial[d] = columns[d].slice().sort(function (a, b) { return mean[a] - mean[b]; });
          if (cost(trial) <= before) columns = trial;
        });
      });
    }
    function permutations(list) {
      if (list.length <= 1) return [list];
      var out = [];
      list.forEach(function (x, i) {
        permutations(list.slice(0, i).concat(list.slice(i + 1))).forEach(function (rest) { out.push([x].concat(rest)); });
      });
      return out;
    }
    var best = cost(columns);
    for (var round = 0; round < 6 && spent < BUDGET; round++) {
      var better = false;
      columns.forEach(function (col, d) {
        if (col.length < 2 || spent >= BUDGET) return;
        // every order up to five variables; six (720 orders) only while the budget holds them, else as beyond six
        // only swaps of neighbours
        var every = col.length <= 5 || (col.length === 6 && spent + 720 * steps <= BUDGET);
        var orders = every ? permutations(col) : col.slice(0, -1).map(function (_, i) {
          var o = col.slice(); o[i] = col[i + 1]; o[i + 1] = col[i]; return o;
        });
        orders.forEach(function (order) {
          if (spent >= BUDGET) return;
          var trial = columns.slice();
          trial[d] = order;
          var c = cost(trial);
          if (c < best - 1e-9) { best = c; columns = trial; better = true; }
        });
      });
      if (!better) break;
    }
    var found = places(columns), out = {};
    codes.forEach(function (c) { if (all || !layout[c]) out[c] = [snap(found[c][0]), snap(found[c][1])]; });
    return out;
  }
  function snap(v) { return Math.round(v / GRID) * GRID; }
  function place(code) { return layout[code]; }
  function overlaps(code, x, y) {
    return nodes().some(function (o) {
      if (o === code || !layout[o]) return false;
      return Math.abs(layout[o][0] - x) < W + GAP && Math.abs(layout[o][1] - y) < H + GAP;
    });
  }
  function free(code, x, y) {  // the nearest place without overlap, on the grid
    x = Math.max(W / 2 + 8, x); y = Math.max(H / 2 + 8, y);
    if (!overlaps(code, x, y)) return [x, y];
    for (var r = 1; r < 60; r++) {
      var best = null;
      for (var dx = -r; dx <= r; dx++) {
        for (var dy = -r; dy <= r; dy++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
          var nx = snap(x + dx * 16), ny = snap(y + dy * 16);
          if (nx < W / 2 + 8 || ny < H / 2 + 8 || overlaps(code, nx, ny)) continue;
          var dist = dx * dx + dy * dy;
          if (!best || dist < best[2]) best = [nx, ny, dist];
        }
      }
      if (best) return [best[0], best[1]];
    }
    return [x, y];
  }
  function ensurePlaces() {
    var codes = nodes(), missing = codes.filter(function (c) { return !layout[c]; });
    if (!missing.length) return;
    var found = auto(codes, !Object.keys(layout).length);
    Object.keys(found).forEach(function (c) { layout[c] = free(c, found[c][0], found[c][1]); });
  }

  // --- geometry ----------------------------------------------------------------------------------------------------

  function latent(code) { return vars[code] && vars[code].shape === "latent"; }
  function rim(code, tx, ty, at) {  // at: the centre of an ellipse not in layout (the measurement models)
    var c = at || layout[code], dx = tx - c[0], dy = ty - c[1], rx = W / 2, ry = H / 2, t;
    if (!dx && !dy) return [c[0], c[1]];
    if (at || latent(code)) t = 1 / Math.sqrt((dx / rx) * (dx / rx) + (dy / ry) * (dy / ry));
    else t = Math.min(dx ? rx / Math.abs(dx) : Infinity, dy ? ry / Math.abs(dy) : Infinity);
    return [c[0] + t * dx, c[1] + t * dy];
  }
  function blocks(a, b, x1, y1, cx, cy, x2, y2) {  // does the line or curve cross another variable?
    return nodes().some(function (o) {
      if (o === a || o === b || o === dragging || !layout[o]) return false;
      for (var i = 1; i < 20; i++) {
        var t = i / 20, x = (1 - t) * (1 - t) * x1 + 2 * (1 - t) * t * cx + t * t * x2;
        var y = (1 - t) * (1 - t) * y1 + 2 * (1 - t) * t * cy + t * t * y2;
        if (Math.abs(x - layout[o][0]) < W / 2 + 6 && Math.abs(y - layout[o][1]) < H / 2 + 6) return true;
      }
      return false;
    });
  }
  // an edge from a to b: straight where nothing stands in its way, else bent around (one side, then the other);
  // a relation without a direction is always a little bent
  function geometry(a, b, bend, both) {
    var ca = layout[a], cb = layout[b];
    var dx = cb[0] - ca[0], dy = cb[1] - ca[1], dist = Math.sqrt(dx * dx + dy * dy) || 1;
    var nx = -dy / dist, ny = dx / dist, tries = bend ? [bend, bend * 2, -bend, -bend * 2] : [0, 48, -48, 96, -96, 150, -150];
    var out = null;
    tries.some(function (bow) {
      var mx = (ca[0] + cb[0]) / 2 + nx * bow, my = (ca[1] + cb[1]) / 2 + ny * bow;
      var p1 = rim(a, mx, my), p2 = rim(b, mx, my);
      var cx = 2 * mx - (p1[0] + p2[0]) / 2, cy = 2 * my - (p1[1] + p2[1]) / 2;
      out = { x1: p1[0], y1: p1[1], cx: cx, cy: cy, x2: p2[0], y2: p2[1], mx: mx, my: my, bow: bow };
      return !blocks(a, b, p1[0], p1[1], cx, cy, p2[0], p2[1]);
    });
    // leave room for the arrow heads
    function back(x, y, fx, fy, by) { var vx = x - fx, vy = y - fy, n = Math.sqrt(vx * vx + vy * vy) || 1; return [x - by * vx / n, y - by * vy / n]; }
    var end = back(out.x2, out.y2, out.bow ? out.cx : out.x1, out.bow ? out.cy : out.y1, 6);
    var start = back(out.x1, out.y1, out.bow ? out.cx : out.x2, out.bow ? out.cy : out.y2, both ? 6 : 0);
    out.d = out.bow ? "M" + f(start[0]) + " " + f(start[1]) + " Q" + f(out.cx) + " " + f(out.cy) + " " + f(end[0]) + " " + f(end[1])
                    : "M" + f(start[0]) + " " + f(start[1]) + " L" + f(end[0]) + " " + f(end[1]);
    out.lx = out.bow ? out.mx : (out.x1 + out.x2) / 2;
    out.ly = out.bow ? out.my : (out.y1 + out.y2) / 2;
    return out;
  }
  function f(v) { return Math.round(v * 10) / 10; }
  function near(g, x, y) {  // the distance of a point to an edge
    var best = Infinity;
    for (var i = 0; i <= 30; i++) {
      var t = i / 30, px = g.bow ? (1 - t) * (1 - t) * g.x1 + 2 * (1 - t) * t * g.cx + t * t * g.x2 : g.x1 + t * (g.x2 - g.x1);
      var py = g.bow ? (1 - t) * (1 - t) * g.y1 + 2 * (1 - t) * t * g.cy + t * t * g.y2 : g.y1 + t * (g.y2 - g.y1);
      best = Math.min(best, Math.sqrt((px - x) * (px - x) + (py - y) * (py - y)));
    }
    return best;
  }
  function inside(code, x, y, share) {  // in the inner part (share) of a variable's shape?
    var c = layout[code];
    if (!c) return false;
    var rx = W / 2 * share, ry = H / 2 * share, dx = x - c[0], dy = y - c[1];
    return latent(code) ? (dx / rx) * (dx / rx) + (dy / ry) * (dy / ry) <= 1 : Math.abs(dx) <= rx && Math.abs(dy) <= ry;
  }

  // --- drawing -----------------------------------------------------------------------------------------------------

  var drawn = {};  // key -> {kind: "node" | "edge", code | h, edge, x, y}
  function sign(h) { return h.dir === "pos" ? " +" : h.dir === "neg" ? " −" : ""; }
  function edgeLabel(h, e) {
    var a = name(e.a), b = name(e.b);
    if (e.type === "cov") return de_en("H" + h.n + ": " + a + " und " + b + " hängen zusammen, ohne Richtung", "H" + h.n + ": " + a + " and " + b + " are related, without a direction");
    if (e.type === "mod") return de_en("H" + h.n + ": " + a + " moderiert den Pfad " + b + " → " + name(e.c), "H" + h.n + ": " + a + " moderates the path " + b + " → " + name(e.c));
    if (h.kind === "mediation") return de_en("H" + h.n + " " + (e.leg === "a" ? "erster" : "zweiter") + " Pfad der Vermittlung: " + a + " wirkt auf " + b,
                                             "H" + h.n + " " + (e.leg === "a" ? "first" : "second") + " path of the mediation: " + a + " affects " + b);
    return de_en("H" + h.n + ": " + a + " wirkt auf " + b, "H" + h.n + ": " + a + " affects " + b);
  }
  // a construct's statements as small numbered boxes in a row above it (below it at the top of the area), an arrow
  // from the construct to each, the text of a statement in its tooltip; at most MEASURE_MAX boxes, then "+n"
  var MEASURE_MAX = 12, IW = 26, IH = 22, IGAP = 8;
  function drawMeasure(c, statements, layer) {
    var shown = statements.slice(0, MEASURE_MAX), more = statements.length - shown.length;
    var count = shown.length + (more ? 1 : 0), rowW = count * IW + (count - 1) * IGAP;
    var above = c[1] - H / 2 - 40 - IH > 4, y = above ? c[1] - H / 2 - 40 - IH : c[1] + H / 2 + 40;
    var group = el("g", { class: "pm-indicators" }, layer);
    for (var i = 0; i < count; i++) {
      var x = c[0] - rowW / 2 + i * (IW + IGAP), cx = x + IW / 2, end = above ? y + IH : y;
      var from = rim(null, cx, end, c);
      el("path", { d: "M" + f(from[0]) + " " + f(from[1]) + " L" + f(cx) + " " + f(above ? end + 4 : end - 4),
                   class: "pm-indicator-line", "marker-end": "url(#pm-arrow)" }, group);
      var box = el("g", { class: "pm-indicator" }, group);
      el("rect", { x: f(x), y: f(y), width: IW, height: IH, rx: 3 }, box);
      var label = el("text", { x: f(cx), y: f(y + IH / 2 + 4), "text-anchor": "middle" }, box);
      label.textContent = i < shown.length ? String(i + 1) : "+" + more;
      el("title", {}, box).textContent = i < shown.length ? (i + 1) + ": " + shown[i] : more + de_en(" weitere Aussagen", " more statements");
    }
  }
  // a name on two lines instead of cut after 24 characters (Ralf, 2026-10-17): broken at a space where it can be,
  // the second line cut with "…" only when that is still too long
  function nameLines(text, max) {
    if (text.length <= max) return [text];
    var cut = text.lastIndexOf(" ", max);
    if (cut < max / 2) cut = max;
    var rest = text.slice(cut).trim();
    return [text.slice(0, cut).trim(), rest.length > max ? rest.slice(0, max - 1) + "…" : rest];
  }
  // where the label of an arrow goes: its middle, else a little before or after, wherever it covers neither a variable
  // nor another label (Ralf, 2026-10-17: "H5 +" lay on a box)
  var chips = [];
  function chipHits(x, y, w) {
    return nodes().some(function (o) {
      return layout[o] && Math.abs(layout[o][0] - x) < W / 2 + w / 2 + 4 && Math.abs(layout[o][1] - y) < H / 2 + 15;
    }) || chips.some(function (c) { return Math.abs(c[0] - x) < (c[2] + w) / 2 + 4 && Math.abs(c[1] - y) < 26; });
  }
  function chipAt(g, w) {  // on the arrow first; where every place on it is covered, just beside it
    var spot = [g.lx, g.ly], dx = g.x2 - g.x1, dy = g.y2 - g.y1, len = Math.sqrt(dx * dx + dy * dy) || 1;
    [0, 26, -26, 44, -44].some(function (off) {
      return [0.5, 0.4, 0.6, 0.32, 0.68, 0.25, 0.75].some(function (t) {
        var u = 1 - t;
        var x = (g.bow ? u * u * g.x1 + 2 * u * t * g.cx + t * t * g.x2 : g.x1 + t * dx) - off * dy / len;
        var y = (g.bow ? u * u * g.y1 + 2 * u * t * g.cy + t * t * g.y2 : g.y1 + t * dy) + off * dx / len;
        if (chipHits(x, y, w)) return false;
        spot = [x, y];
        return true;
      });
    });
    chips.push([spot[0], spot[1], w]);
    return spot;
  }
  function render() {
    ensurePlaces();
    chips = [];
    var codes = nodes(), list = hyps();
    var maxX = 0, maxY = 0;
    codes.forEach(function (c) { if (layout[c]) { maxX = Math.max(maxX, layout[c][0]); maxY = Math.max(maxY, layout[c][1]); } });
    var width = Math.max(area.clientWidth / zoom - 2, maxX + W / 2 + 64), height = Math.max(440, maxY + H / 2 + 72);
    var hadFocus = svg && svg.contains(document.activeElement);
    if (svg) svg.remove();
    svg = el("svg", { width: Math.round(width * zoom), height: Math.round(height * zoom), viewBox: "0 0 " + f(width) + " " + f(height),
                      class: "pm-svg", role: "group", "aria-label": de_en("Modell: Variablen und Beziehungen", "Model: variables and relations"),
                      "aria-roledescription": de_en("Zeichenfläche", "drawing area") });
    var defs = el("defs", {}, svg);
    var marker = el("marker", { id: "pm-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0 0L10 5L0 10z", class: "sem-arrowhead" }, marker);
    var hot = el("marker", { id: "pm-arrow-hot", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0 0L10 5L0 10z", class: "pm-arrowhead-hot" }, hot);
    el("rect", { x: 0, y: 0, width: f(width), height: f(height), class: "pm-bg" }, svg);
    var guides = el("g", { class: "pm-guides", "aria-hidden": "true" }, svg);
    var edgeLayer = el("g", { class: "pm-edges" }, svg);
    var measureLayer = el("g", { class: "pm-measure", "aria-hidden": "true" }, svg);
    var nodeLayer = el("g", { class: "pm-nodes" }, svg);
    // the variable being dragged in front of everything, arrows and other variables (Ralf, 2026-10-17)
    var dragLayer = el("g", { class: "pm-nodes pm-dragged" }, svg);
    drawn = {};
    var order = [], pairs = {};
    list.forEach(function (h) {
      (edgesOf(h).edges || []).forEach(function (e) {
        if (!layout[e.a] || !layout[e.b] || e.type === "mod") return;
        // two hypotheses over the same two variables (a mediation's leg and a path of its own): side by side
        var pair = [e.a, e.b].sort().join("|"), k = pairs[pair] = (pairs[pair] || 0) + 1;
        var side = e.a < e.b ? 1 : -1, bend = e.type === "cov" ? 40 : 0;
        if (k > 1 && e.type !== "direct") bend = side * (bend || 0) + side * 34 * Math.ceil((k - 1) / 2) * (k % 2 ? -1 : 1);
        var g = geometry(e.a, e.b, bend, e.type === "cov");
        if (e.type === "direct") {  // in the model, but no hypothesis of its own
          el("path", { d: g.d, class: "pm-edge-direct", "marker-end": "url(#pm-arrow)", "aria-hidden": "true" }, edgeLayer);
          return;
        }
        var on = focusKey === e.key || (pop && pop.key === e.key);
        var group = el("g", { class: "pm-edge" + (on ? " is-on" : ""), "data-key": e.key, tabindex: "-1", role: "button",
                              "aria-label": edgeLabel(h, e) }, edgeLayer);
        el("path", { d: g.d, class: "pm-edge-hit" }, group);
        var line = el("path", { d: g.d, class: "pm-edge-line", "marker-end": on ? "url(#pm-arrow-hot)" : "url(#pm-arrow)" }, group);
        if (e.type === "cov") line.setAttribute("marker-start", on ? "url(#pm-arrow-hot)" : "url(#pm-arrow)");
        var label = "H" + h.n + (e.leg ? " " + e.leg : "") + sign(h);
        var chipW = 14 + label.length * 7.4, spot = chipAt(g, chipW);
        el("rect", { x: f(spot[0] - chipW / 2), y: f(spot[1] - 11), width: f(chipW), height: 22, rx: 6, class: "pm-chip" }, group);
        var t = el("text", { x: f(spot[0]), y: f(spot[1] + 4), "text-anchor": "middle", class: "pm-chip-text" }, group);
        t.textContent = label;
        drawn[e.key] = { kind: "edge", h: h, edge: e, geo: g, x: spot[0], y: spot[1] };
        order.push(e.key);
      });
    });
    // moderations: from the moderator onto the middle of the path x -> y; without a hypothesis of its own on that
    // path a thin line stands for it
    list.forEach(function (h) {
      (edgesOf(h).edges || []).forEach(function (e) {
        if (e.type !== "mod" || !layout[e.a] || !layout[e.b] || !layout[e.c]) return;
        var base = null;
        Object.keys(drawn).forEach(function (k) {
          var it = drawn[k];
          if (!base && it.kind === "edge" && it.edge.type === "path" && it.edge.a === e.b && it.edge.b === e.c) base = it.geo;
        });
        if (!base) {
          base = geometry(e.b, e.c, 0, false);
          el("path", { d: base.d, class: "pm-edge-base", "marker-end": "url(#pm-arrow)", "aria-hidden": "true" }, edgeLayer);
        }
        // a little before the middle of the path, so that its label stays free
        var t = 0.4, u = 1 - t;
        var mx = base.bow ? u * u * base.x1 + 2 * u * t * base.cx + t * t * base.x2 : base.x1 + t * (base.x2 - base.x1);
        var my = base.bow ? u * u * base.y1 + 2 * u * t * base.cy + t * t * base.y2 : base.y1 + t * (base.y2 - base.y1);
        var p1 = rim(e.a, mx, my), dx = mx - p1[0], dy = my - p1[1], len = Math.sqrt(dx * dx + dy * dy) || 1;
        var g = { x1: p1[0], y1: p1[1], x2: mx - 5 * dx / len, y2: my - 5 * dy / len, bow: 0 };
        g.d = "M" + f(g.x1) + " " + f(g.y1) + " L" + f(g.x2) + " " + f(g.y2);
        g.lx = g.x1 + 0.5 * (g.x2 - g.x1); g.ly = g.y1 + 0.5 * (g.y2 - g.y1);
        var on = focusKey === e.key || (pop && pop.key === e.key);
        var group = el("g", { class: "pm-edge pm-edge-mod" + (on ? " is-on" : ""), "data-key": e.key, tabindex: "-1", role: "button",
                              "aria-label": edgeLabel(h, e) }, edgeLayer);
        el("path", { d: g.d, class: "pm-edge-hit" }, group);
        el("path", { d: g.d, class: "pm-edge-line", "marker-end": on ? "url(#pm-arrow-hot)" : "url(#pm-arrow)" }, group);
        var label = "H" + h.n + sign(h), chipW = 14 + label.length * 7.4, spot = chipAt(g, chipW);
        el("rect", { x: f(spot[0] - chipW / 2), y: f(spot[1] - 11), width: f(chipW), height: 22, rx: 6, class: "pm-chip" }, group);
        var t = el("text", { x: f(spot[0]), y: f(spot[1] + 4), "text-anchor": "middle", class: "pm-chip-text" }, group);
        t.textContent = label;
        drawn[e.key] = { kind: "edge", h: h, edge: e, geo: g, x: spot[0], y: spot[1] };
        order.push(e.key);
      });
    });
    codes.forEach(function (code) {
      var c = layout[code];
      if (!c) return;
      var v = vars[code], key = "n:" + code, isSel = selected.indexOf(code) >= 0;
      var cls = "pm-node sem-node" + (latent(code) ? " is-latent" : "") + (v && v.rand ? " is-condition" : "") + (!v ? " is-missing" : "") +
                (isSel ? " is-selected" : "") + (connect && connect.from === code ? " is-source" : "");
      var info = v ? v.info : de_en("nicht mehr im Fragebogen", "no longer in the questionnaire");
      var group = el("g", { class: cls, "data-key": key, "data-code": code, tabindex: "-1", role: "button",
                            "aria-label": (v && v.shape === "latent" ? de_en("Konstrukt ", "Construct ") : v && v.rand ? "" : de_en("Frage ", "Question ")) +
                                          name(code) + ", " + info + (isSel ? ", " + de_en("ausgewählt", "selected") : "") }, code === dragging || dragGroup.indexOf(code) >= 0 ? dragLayer : nodeLayer);
      if (latent(code)) {
        el("ellipse", { cx: c[0], cy: c[1], rx: W / 2 + 7, ry: H / 2 + 7, class: "pm-ring" }, group);
        el("ellipse", { cx: c[0], cy: c[1], rx: W / 2, ry: H / 2 }, group);
      } else {
        el("rect", { x: c[0] - W / 2 - 7, y: c[1] - H / 2 - 7, width: W + 14, height: H + 14, rx: 9, class: "pm-ring" }, group);
        el("rect", { x: c[0] - W / 2, y: c[1] - H / 2, width: W, height: H, rx: 4 }, group);
      }
      var label = name(code), rows = nameLines(label, 24), top = rows.length > 1 ? -11 : -3;
      rows.forEach(function (row, i) {
        el("text", { x: c[0], y: c[1] + top + 16 * i, "text-anchor": "middle", class: "sem-node-label" }, group).textContent = row;
      });
      var t2 = el("text", { x: c[0], y: c[1] + top + 16 * rows.length + 1, "text-anchor": "middle", class: "sem-node-sub" }, group);
      t2.textContent = (code.length > 14 ? code.slice(0, 13) + "…" : code) + " · " + info;
      var title = el("title", {}, group);
      title.textContent = label + (v && v.question && v.question !== label ? ": " + v.question : "");
      if (editable) {  // the dot to connect from, with a target of 24 px (WCAG 2.5.8)
        el("circle", { cx: c[0] + W / 2, cy: c[1], r: 12, class: "pm-handle pm-handle-hit", "aria-hidden": "true" }, group);
        el("circle", { cx: c[0] + W / 2, cy: c[1], r: 7, class: "pm-handle", "aria-hidden": "true" }, group);
      }
      if (measure && latent(code) && v && v.statements && v.statements.length) drawMeasure(c, v.statements, measureLayer);
      drawn[key] = { kind: "node", code: code, x: c[0], y: c[1] };
      order.push(key);
    });
    area.appendChild(svg);
    svg.guides = guides;
    // the roving focus: one stop for the whole area
    if (!focusKey || !drawn[focusKey]) focusKey = order[0] || null;
    order.forEach(function (key) { svg.querySelector('[data-key="' + cssKey(key) + '"]').setAttribute("tabindex", key === focusKey ? "0" : "-1"); });
    if (hadFocus && focusKey) focusItem(focusKey);
    if (!order.length) {
      var empty = el("text", { x: 24, y: 40, class: "pm-empty" }, svg);
      empty.textContent = editable ? de_en("Variablen aus der Liste links hierher ziehen oder anklicken.", "Drag variables from the list on the left here, or click them.")
                                   : de_en("Noch keine Beziehungen.", "No relations yet.");
    }
    drawText(list, codes);
    drawPalette(codes);
    drawTools();
  }
  function cssKey(key) { return key.replace(/["\\]/g, "\\$&"); }
  function focusItem(key) {
    focusKey = key;
    if (!svg) return;
    svg.querySelectorAll("[data-key]").forEach(function (g) { g.setAttribute("tabindex", g.getAttribute("data-key") === key ? "0" : "-1"); });
    var g = svg.querySelector('[data-key="' + cssKey(key) + '"]');
    if (g) g.focus({ preventScroll: false });
  }

  // the model as text (screen readers find it first) and what is not in the picture; never empty: the variables
  // without a relation and the hypotheses not drawn are named too, an empty model says so (pre-test 2026-10-20, 15)
  function drawText(list, codes) {
    if (!textList) return;
    textList.innerHTML = "";
    var out = [], lines = 0;
    list.forEach(function (h) {
      var found = edgesOf(h);
      if (!found.edges) { out.push("H" + h.n + " (" + found.why + ")"); return; }
      lines += 1;
      var line;
      if (h.kind === "mediation") line = name(h.iv) + " → " + name(h.mv) + " → " + name(h.dv);
      else if (h.kind === "moderation") line = name(h.wv) + " × " + name(h.iv) + " → " + name(h.dv);
      else if (h.kind === "correlation") line = name(h.iv) + " ↔ " + name(h.dv);
      else line = name(h.iv) + " → " + name(h.dv);
      var item = html("li", {}, "H" + h.n + ": " + line + (h.dir === "pos" ? " (+)" : h.dir === "neg" ? " (−)" : ""));
      textList.appendChild(item);
    });
    var alone = (codes || []).filter(function (c) { return extra.indexOf(c) >= 0; });
    if (!lines && !alone.length)
      textList.appendChild(html("li", { class: "muted" }, editable
        ? de_en("Noch keine Variablen im Modell. Ziehen Sie Variablen aus der Liste links auf die Fläche oder klicken Sie sie an; ein Pfeil zwischen zwei Variablen legt eine Hypothese an.",
                "No variables in the model yet. Drag variables from the list on the left onto the area or click them; an arrow between two variables creates a hypothesis.")
        : de_en("Noch keine Variablen im Modell.", "No variables in the model yet.")));
    if (alone.length)
      textList.appendChild(html("li", {}, de_en("Ohne Beziehung: ", "Without a relation: ") + alone.map(name).join(", ")));
    if (out.length)
      textList.appendChild(html("li", {}, de_en("Nicht im Bild: ", "Not in the picture: ") + out.join("; ")));
    if (outside) {
      outside.hidden = !out.length;
      outside.textContent = out.length ? de_en("Nicht im Bild: ", "Not in the picture: ") + out.join("; ") : "";
    }
  }
  function drawPalette(codes) {
    root.querySelectorAll("[data-pm-var]").forEach(function (b) {
      var on = codes.indexOf(b.getAttribute("data-pm-var")) >= 0;
      b.classList.toggle("is-placed", on);
      var mark = b.querySelector("[data-pm-placed]");
      if (mark) mark.hidden = !on;
    });
  }
  function drawTools() {
    if (!tools) return;
    var many = selected.length >= 2;
    tools.querySelectorAll("[data-pm-align]").forEach(function (b) { b.hidden = !many; });
    var mb = tools.querySelector("[data-pm-measure]");
    if (mb) {
      mb.setAttribute("aria-pressed", measure ? "true" : "false");
      mb.textContent = measure ? de_en("Messmodelle ausblenden", "Hide measurement models") : de_en("Messmodelle zeigen", "Show measurement models");
      mb.hidden = !nodes().some(function (c) { return latent(c) && vars[c] && vars[c].statements && vars[c].statements.length; });
    }
    var z = tools.querySelector("[data-pm-zoom-level]");
    if (z) z.textContent = Math.round(zoom * 100) + de_en(" %", "%");
  }

  // --- the field "Beziehung" ----------------------------------------------------------------------------------------

  function typeOf(code) {
    var v = vars[code];
    if (!v) return "num";
    return v.level === "nominal" ? "groups" : "num";
  }
  // what a relation between a and b can be, by what they are (section 3.2): [{kind, iv, dv, label}]
  function choices(a, b) {
    var A = name(a), B = name(b), ta = typeOf(a), tb = typeOf(b), out = [];
    function groupsOf(g, other) {
      var rand = vars[g] && vars[g].rand;
      out.push({ kind: rand ? "causal" : "difference", iv: g, dv: other,
                 label: rand ? de_en("Die Bedingung " + name(g) + " wirkt auf " + name(other), "The condition " + name(g) + " affects " + name(other))
                             : de_en(name(other) + " unterscheidet sich zwischen den Gruppen von " + name(g), name(other) + " differs between the groups of " + name(g)) });
    }
    if (ta === "num" && tb === "num") {
      out.push({ kind: "effect", iv: a, dv: b, label: de_en(A + " wirkt auf " + B, A + " affects " + B) });
      out.push({ kind: "effect", iv: b, dv: a, label: de_en(B + " wirkt auf " + A, B + " affects " + A) });
      out.push({ kind: "correlation", iv: a, dv: b, label: de_en(A + " und " + B + " hängen zusammen, ohne Richtung", A + " and " + B + " are related, without a direction") });
    } else if (ta === "groups" && tb === "num") groupsOf(a, b);
    else if (ta === "num" && tb === "groups") groupsOf(b, a);
    else if (vars[a] && vars[a].rand) groupsOf(a, b);
    else if (vars[b] && vars[b].rand) groupsOf(b, a);
    else out.push({ kind: "correlation", iv: a, dv: b, label: de_en(A + " und " + B + " hängen zusammen", A + " and " + B + " are related") });
    return out;
  }
  function blockedWhy(c, skip) {
    if (c.kind === "correlation") return "";
    if (vars[c.dv] && vars[c.dv].rand) return de_en("Eine Zufallszuteilung kann nichts beeinflussen.", "A random assignment cannot be influenced.");
    if (circle(c.iv, c.dv, skip)) return de_en("würde einen Kreis bilden; das Modell schätzt nur Wirkungen in eine Richtung", "would form a circle; the model only estimates effects in one direction");
    return "";
  }
  function pairOf(a, b, skip) {  // another hypothesis between the same two
    return hyps().filter(function (h) {
      if (h.id === skip || h.kind === "moderation" || !(edgesOf(h).edges)) return false;
      var pair = [h.iv, h.dv];
      return (pair[0] === a && pair[1] === b) || (pair[0] === b && pair[1] === a);
    })[0];
  }
  function closePop(back) {
    if (!pop) return;
    var key = pop.back;
    pop.box.remove();
    pop = null;
    render();
    if (back !== false && key && drawn[key]) focusItem(key);
  }
  function popAt(x, y, box) {
    box.style.left = Math.max(8, Math.min(x * zoom - area.scrollLeft + 12, area.clientWidth - 340)) + "px";
    box.style.top = Math.max(8, y * zoom - area.scrollTop + 12) + "px";
  }
  // the field to make or change a relation; h: the hypothesis (or null for a new one between a and b)
  function relation(a, b, h, back) {
    if (!editable) return;
    closePop(false);
    if (!h) {
      var same = pairOf(a, b, null);
      if (same) { announce(de_en("Zwischen diesen beiden gibt es schon H" + same.n + ".", "There already is H" + same.n + " between these two.")); return relation(same.iv, same.dv, same, back); }
      if (hyps().length >= data.max) { announce(de_en("Höchstens " + data.max + " Hypothesen.", "At most " + data.max + " hypotheses.")); flashNote(de_en("Höchstens " + data.max + " Hypothesen je Studienplan.", "At most " + data.max + " hypotheses per research plan.")); return; }
    }
    var box = html("div", { class: "pm-pop", role: "dialog", "aria-labelledby": "pm-pop-title" });
    var title = html("h3", { id: "pm-pop-title" }, h ? de_en("Beziehung H" + h.n, "Relation H" + h.n) : de_en("Neue Beziehung", "New relation"));
    box.appendChild(title);
    var mediation = h && (h.kind === "mediation" || h.kind === "moderation");  // fixed by their arrows
    var list = mediation ? [] : choices(a, b);
    var current = h ? list.findIndex(function (c) { return c.kind === h.kind && c.iv === h.iv && c.dv === h.dv; }) : -1;
    var fieldset = html("fieldset", { class: "pm-kinds" });
    fieldset.appendChild(html("legend", { class: "visually-hidden" }, de_en("Art der Beziehung", "Type of relation")));
    var firstFree = -1;
    list.forEach(function (c, i) {
      var why = blockedWhy(c, h ? h.id : null);
      var label = html("label", { class: "check" });
      var input = html("input", { type: "radio", name: "pm-kind", value: String(i), form: "pm-none" });
      if (why) { input.disabled = true; label.title = why; }
      if (!why && firstFree < 0) firstFree = i;
      label.appendChild(input);
      label.appendChild(html("span", {}, c.label + (why ? " (" + why + ")" : "")));
      fieldset.appendChild(label);
    });
    if (mediation && h.kind === "moderation") box.appendChild(html("p", { class: "small" }, de_en(name(h.wv) + " verändert die Stärke von " + name(h.iv) + " → " + name(h.dv) + " (Moderation).",
                                                                     name(h.wv) + " changes the strength of " + name(h.iv) + " → " + name(h.dv) + " (moderation).")));
    else if (mediation) box.appendChild(html("p", { class: "small" }, de_en(name(h.iv) + " wirkt über " + name(h.mv) + " auf " + name(h.dv) + " (Vermittlung).",
                                                                     name(h.iv) + " affects " + name(h.dv) + " through " + name(h.mv) + " (mediation).")));
    else box.appendChild(fieldset);
    var pick = current >= 0 ? current : firstFree;
    if (pick >= 0 && fieldset.querySelectorAll("input")[pick]) fieldset.querySelectorAll("input")[pick].checked = true;
    if (!mediation && firstFree < 0 && current < 0) box.appendChild(html("p", { class: "plan-error small" }, de_en("Diese Beziehung lässt sich so nicht anlegen.", "This relation cannot be made like this.")));
    var row = html("div", { class: "pm-pop-row" });
    var dirLabel = html("label", {}, de_en("Erwartung", "Expectation"));
    var dir = html("select", { form: "pm-none", "aria-label": de_en("Erwartete Richtung", "Expected direction") });
    dirLabel.appendChild(dir);
    var methodLabel = html("label", {}, de_en("Verfahren", "Method"));
    var method = html("select", { form: "pm-none", "aria-label": de_en("Verfahren", "Method") });
    methodLabel.appendChild(method);
    row.appendChild(dirLabel); row.appendChild(methodLabel);
    box.appendChild(row);
    var textLabel = html("label", { class: "pm-pop-text" }, de_en("Hypothese in einem Satz", "Hypothesis in one sentence"));
    var text = html("input", { type: "text", maxlength: "600", form: "pm-none" });
    textLabel.appendChild(text);
    box.appendChild(textLabel);
    var note = html("p", { class: "muted small pm-pop-note" });
    box.appendChild(note);
    function chosen() {
      if (mediation) return { kind: h.kind, iv: h.iv, dv: h.dv, mv: h.mv, wv: h.wv };
      var r = fieldset.querySelector("input:checked");
      return r ? list[+r.value] : null;
    }
    function fill() {
      var c = chosen();
      var kind = c ? c.kind : "correlation", keep = dir.value || (h ? h.dir : "");
      dir.innerHTML = "";
      var labels = (data.dirs || {})[kind] || [];
      labels.forEach(function (pair) { dir.appendChild(new Option(pair[1], pair[0])); });
      dir.value = labels.some(function (pair) { return pair[0] === keep; }) ? keep : "";
      var keepM = method.value || (h ? h.analysis : "");
      method.innerHTML = "";
      var methods = data.analyses || [];
      if (kind === "effect" || kind === "mediation" || kind === "moderation") {
        var sem = methods.filter(function (pair) { return pair[0] === "sem"; })[0];
        method.appendChild(new Option(sem ? sem[1] : "SEM", "sem"));
        method.disabled = true;
      } else {
        method.disabled = false;
        method.appendChild(new Option(de_en("automatisch", "automatic"), ""));
        methods.forEach(function (pair) { method.appendChild(new Option(pair[1], pair[0])); });
        method.value = methods.some(function (pair) { return pair[0] === keepM; }) ? keepM : "";
      }
      var parts = [];
      if (kind === "effect" && !(vars[c.iv] && vars[c.iv].rand))
        parts.push(de_en("Die Richtung ist Ihre Annahme; die Daten prüfen sie nicht.", "The direction is your assumption; the data do not test it."));
      if (kind === "correlation" && method.value === "sem")
        parts.push(de_en("Im Modell ein Zusammenhang ohne Richtung (Kovarianz).", "In the model a relationship without a direction (covariance)."));
      var semBad = [c && c.iv, c && c.dv, c && c.mv, c && c.wv].filter(function (code) { return code && vars[code] && !vars[code].sem; });
      if ((kind === "effect" || kind === "mediation" || kind === "moderation" || method.value === "sem") && semBad.length)
        parts.push(de_en("Nicht im Modell: ", "Not in the model: ") + semBad.map(function (code) { return name(code) + " " + vars[code].why; }).join("; ") + ".");
      if (h && h.text.trim() && h.text.trim() !== textFor(h) && c && (c.kind !== h.kind || c.iv !== h.iv || c.dv !== h.dv))
        parts.push(de_en("Ihr Text bleibt; passt er noch zur neuen Beziehung?", "Your text stays; does it still fit the new relation?"));
      note.textContent = parts.join(" ");
      text.placeholder = c ? textFor({ kind: c.kind, iv: c.iv, dv: c.dv, mv: c.mv, wv: c.wv, dir: dir.value }) : "";
    }
    text.value = h ? h.text : "";
    fill();
    fieldset.addEventListener("change", fill);
    dir.addEventListener("change", fill);
    method.addEventListener("change", fill);
    var buttons = html("div", { class: "button-row" });
    var ok = html("button", { type: "button", class: "button button-small" }, h ? de_en("Fertig", "Done") : de_en("Anlegen", "Create"));
    buttons.appendChild(ok);
    if (h && !mediation && chosen() && chosen().kind === "effect") {
      var turn = html("button", { type: "button", class: "button button-secondary button-small" }, de_en("Richtung umkehren", "Reverse direction"));
      turn.addEventListener("click", function () {
        var other = list.findIndex(function (c) { return c.kind === "effect" && c.iv === h.dv && c.dv === h.iv; });
        var input = fieldset.querySelectorAll("input")[other];
        if (input && !input.disabled) { input.checked = true; fill(); } else announce(de_en("Umkehren würde einen Kreis bilden.", "Reversing would form a circle."));
      });
      buttons.appendChild(turn);
    }
    if (mediation && h.kind === "mediation") {
      var undoMed = html("button", { type: "button", class: "button button-secondary button-small" }, de_en("Vermittler entfernen", "Remove mediator"));
      undoMed.addEventListener("click", function () {
        var before = copy(h);
        writeRow(h.row, before, { kind: "effect", iv: h.iv, dv: h.dv, dir: h.dir, analysis: "sem" });
        closePop();
        commit(de_en("Modell: Vermittler aus H" + h.n + " entfernt", "Model: mediator removed from H" + h.n));
        announce(de_en("Vermittler entfernt.", "Mediator removed."));
      });
      buttons.appendChild(undoMed);
    }
    if (h) {
      var del = html("button", { type: "button", class: "button button-secondary button-small pm-danger" }, de_en("Löschen", "Delete"));
      del.addEventListener("click", function () { closePop(false); deleteHyp(h); });
      buttons.appendChild(del);
    }
    var cancel = html("button", { type: "button", class: "button button-secondary button-small" }, de_en("Abbrechen", "Cancel"));
    cancel.addEventListener("click", function () { closePop(); });
    buttons.appendChild(cancel);
    box.appendChild(buttons);
    function apply() {
      var c = chosen();
      if (!c) return;
      var input = fieldset.querySelector("input:checked");
      if (input && input.disabled) return;
      var next = { kind: c.kind, iv: c.iv, dv: c.dv, mv: c.mv || "", wv: c.wv || "", dir: dir.value,
                   analysis: method.disabled ? (c.kind === "effect" || c.kind === "moderation" ? "sem" : "") : method.value,
                   text: text.value.trim() && (!h || text.value.trim() !== h.text.trim()) ? text.value.trim() : null };
      var target = h ? h.row : newRow(), before = h ? copy(h) : null;
      writeRow(target, before, next);
      var n = rows().indexOf(target) + 1;
      closePop(false);
      extra = extra.filter(function (code) { return code !== c.iv && code !== c.dv; });
      render();
      focusItem("e:" + val(target, "h_id") + (c.kind === "mediation" ? ":a" : ""));
      commit(h ? de_en("Modell: H" + n + " geändert", "Model: H" + n + " changed") : de_en("Modell: H" + n + " angelegt", "Model: H" + n + " created"));
      announce(h ? de_en("H" + n + " geändert.", "H" + n + " changed.") : de_en("H" + n + " angelegt: ", "H" + n + " created: ") + textFor(next));
    }
    ok.addEventListener("click", apply);
    box.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePop(); }
      else if (e.key === "Enter" && e.target.tagName !== "BUTTON" && e.target.tagName !== "SELECT") { e.preventDefault(); apply(); }
    });
    root.querySelector("[data-pm-frame]").appendChild(box);
    var at = layout[a] && layout[b] ? [(layout[a][0] + layout[b][0]) / 2, (layout[a][1] + layout[b][1]) / 2] : [40, 40];
    popAt(at[0], at[1], box);
    pop = { box: box, key: h ? "e:" + h.id + (h.kind === "mediation" ? ":a" : "") : null, back: back || (h ? "e:" + h.id : "n:" + a) };
    render();
    var first = box.querySelector("input:checked") || box.querySelector("input, select, button");
    if (first) first.focus();
  }
  function copy(h) { return { kind: h.kind, iv: h.iv, dv: h.dv, mv: h.mv, wv: h.wv, dir: h.dir, analysis: h.analysis, text: h.text }; }
  function flashNote(text) { if (window.flash) window.flash(text, "warning"); }

  // a variable dropped onto an arrow: a mediator (section 3.2; moderation follows with stage 2)
  function onto(code, key, back) {
    if (!editable) return;
    var item = drawn[key];
    if (!item || item.kind !== "edge") return;
    var h = item.h;
    closePop(false);
    function notSem(list) {
      return list.filter(function (c) { return vars[c] && !vars[c].sem; }).map(function (c) {
        return de_en("„" + name(c) + "“ " + vars[c].why + ".", "“" + name(c) + "” " + vars[c].why + ".");
      }).join(" ");
    }
    // a mediator (section 3.2)
    var whyMed = "";
    var many = function (c) { return vars[c] && vars[c].level === "nominal" && vars[c].groups > 2; };
    if (h.kind === "mediation" || h.kind === "moderation") whyMed = de_en("Auf einer Vermittlung oder Moderation kommt kein weiterer Vermittler dazu; ein Moderator auf einem Pfad der Vermittlung geht.", "A mediation or moderation takes no further mediator; a moderator on a path of the mediation works.");
    else if (code === h.iv || code === h.dv) whyMed = de_en("Der Vermittler muss eine dritte Variable sein.", "The mediator has to be a third variable.");
    else if (vars[code] && vars[code].rand) whyMed = de_en("Eine Zufallszuteilung kann nicht vermitteln: Sie lässt sich nicht beeinflussen.", "A random assignment cannot mediate: it cannot be influenced.");
    else if (many(code)) whyMed = de_en("Eine Größe mit mehr als zwei Gruppen kann nicht vermitteln.", "A variable with more than two groups cannot mediate.");
    else if (notSem([h.iv, h.dv, code])) whyMed = notSem([h.iv, h.dv, code]);
    else if (circle(h.iv, code, h.id) || circle(code, h.dv, h.id)) whyMed = de_en("Das würde einen Kreis bilden.", "That would form a circle.");
    // a moderator (stage 2, F2): a hypothesis of its own on the path dropped onto; on a path of a mediation a
    // moderated mediation (stage 3, F9)
    var pa = item.edge.a, pb = item.edge.b, onLeg = h.kind === "mediation";
    var whyMod = "", groupsMod = !!(vars[code] && vars[code].level === "nominal" && !data.pls && !onLeg);
    if (h.kind === "moderation" || (onLeg && item.edge.type !== "path")) whyMod = de_en("Eine Moderation geht auf einen Pfad mit Richtung, nicht auf eine Moderation.", "A moderation goes onto a path with a direction, not onto a moderation.");
    else if (h.kind === "correlation") whyMod = de_en("Eine Moderation braucht einen Pfad mit Richtung (Art „Einfluss“).", "A moderation needs a path with a direction (type “Influence”).");
    else if (code === pa || code === pb) whyMod = de_en("Der Moderator muss eine dritte Variable sein.", "The moderator has to be a third variable.");
    else if (notSem([pa, pb, code])) whyMod = notSem([pa, pb, code]);
    else if (onLeg && many(code)) whyMod = de_en("Auf einem Pfad der Vermittlung moderiert eine Größe mit höchstens zwei Gruppen.", "On a path of the mediation a variable with at most two groups moderates.");
    else if (hyps().some(function (o) { return o.kind === "moderation" && o.iv === pa && o.dv === pb && o.wv === code; })) whyMod = de_en("Diese Moderation gibt es schon.", "This moderation already exists.");
    else if (circle(code, pb, null)) whyMod = de_en("Das würde einen Kreis bilden.", "That would form a circle.");
    else if (hyps().length >= data.max) whyMod = de_en("Höchstens " + data.max + " Hypothesen.", "At most " + data.max + " hypotheses.");
    var box = html("div", { class: "pm-pop", role: "dialog", "aria-labelledby": "pm-pop-title" });
    box.appendChild(html("h3", { id: "pm-pop-title" }, de_en(name(code) + " auf H" + h.n, name(code) + " onto H" + h.n)));
    var buttons = html("div", { class: "pm-onto" });
    var med = html("button", { type: "button", class: "button button-secondary button-small" }, de_en("vermittelt: " + name(h.iv) + " → " + name(code) + " → " + name(h.dv), "mediates: " + name(h.iv) + " → " + name(code) + " → " + name(h.dv)));
    if (whyMed) { med.disabled = true; med.title = whyMed; }
    var mod = html("button", { type: "button", class: "button button-secondary button-small" },
                   groupsMod ? de_en("moderiert: der Pfad " + name(pa) + " → " + name(pb) + " im Vergleich der Gruppen", "moderates: the path " + name(pa) + " → " + name(pb) + " compared between the groups")
                             : de_en("moderiert die Stärke von " + name(pa) + " → " + name(pb) + (onLeg ? " (moderierte Vermittlung)" : ""), "moderates the strength of " + name(pa) + " → " + name(pb) + (onLeg ? " (moderated mediation)" : "")));
    if (whyMod) { mod.disabled = true; mod.title = whyMod; }
    buttons.appendChild(med); buttons.appendChild(mod);
    box.appendChild(buttons);
    if (whyMed) box.appendChild(html("p", { class: "small plan-error" }, de_en("Vermitteln: ", "Mediate: ") + whyMed));
    if (whyMod && whyMod !== whyMed) box.appendChild(html("p", { class: "small plan-error" }, de_en("Moderieren: ", "Moderate: ") + whyMod));
    var cancel = html("button", { type: "button", class: "button button-secondary button-small" }, de_en("Abbrechen", "Cancel"));
    cancel.addEventListener("click", function () { closePop(); });
    var row = html("div", { class: "button-row" }); row.appendChild(cancel); box.appendChild(row);
    function placeNear(dy) {
      extra = extra.filter(function (c) { return c !== code; });
      if (!layout[code] || overlaps(code, layout[code][0], layout[code][1])) {
        var mid = [(layout[h.iv][0] + layout[h.dv][0]) / 2, (layout[h.iv][1] + layout[h.dv][1]) / 2 + dy];
        layout[code] = free(code, snap(mid[0]), snap(mid[1]));
      }
    }
    med.addEventListener("click", function () {
      writeRow(h.row, copy(h), { kind: "mediation", iv: h.iv, dv: h.dv, mv: code, dir: h.dir, analysis: "" });
      placeNear(H + 40);
      closePop(false);
      render();
      focusItem("e:" + h.id + ":a");
      commit(de_en("Modell: Vermittler " + name(code) + " in H" + h.n, "Model: mediator " + name(code) + " in H" + h.n));
      announce(de_en("Vermittler " + name(code) + " eingefügt: H" + h.n + " ist jetzt eine Vermittlung.", "Mediator " + name(code) + " inserted: H" + h.n + " is now a mediation."));
    });
    mod.addEventListener("click", function () {
      var target = newRow();
      writeRow(target, null, { kind: "moderation", iv: pa, dv: pb, wv: code, dir: "", analysis: "sem" });
      var n = rows().indexOf(target) + 1;
      placeNear(-(H + 56));
      closePop(false);
      render();
      focusItem("e:" + val(target, "h_id"));
      commit(de_en("Modell: H" + n + " angelegt (Moderation)", "Model: H" + n + " created (moderation)"));
      announce(de_en("H" + n + " angelegt: ", "H" + n + " created: ") + textFor({ kind: "moderation", iv: pa, dv: pb, wv: code }));
    });
    box.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePop(); } });
    root.querySelector("[data-pm-frame]").appendChild(box);
    popAt(item.x, item.y, box);
    pop = { box: box, key: key, back: back || key };
    render();
    (!med.disabled ? med : !mod.disabled ? mod : cancel).focus();
  }
  // "M" on an arrow: the mediator or moderator chosen from a list instead of dragged
  function pickMediator(key) {
    if (!editable) return;
    var item = drawn[key];
    if (!item || item.kind !== "edge") return;
    closePop(false);
    var box = html("div", { class: "pm-pop", role: "dialog", "aria-labelledby": "pm-pop-title" });
    box.appendChild(html("h3", { id: "pm-pop-title" }, de_en("Vermittler oder Moderator für H" + item.h.n, "Mediator or moderator for H" + item.h.n)));
    var label = html("label", {}, de_en("Variable", "Variable"));
    var select = html("select", { form: "pm-none" });
    (data.vars || []).forEach(function (v) { if (v.code !== item.h.iv && v.code !== item.h.dv) select.appendChild(new Option(v.name + " (" + v.code + ")", v.code)); });
    label.appendChild(select);
    box.appendChild(label);
    var row = html("div", { class: "button-row" });
    var go = html("button", { type: "button", class: "button button-small" }, de_en("Weiter", "Next"));
    var cancel = html("button", { type: "button", class: "button button-secondary button-small" }, de_en("Abbrechen", "Cancel"));
    row.appendChild(go); row.appendChild(cancel); box.appendChild(row);
    go.addEventListener("click", function () { closePop(false); onto(select.value, key, key); });
    cancel.addEventListener("click", function () { closePop(); });
    box.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePop(); } });
    root.querySelector("[data-pm-frame]").appendChild(box);
    popAt(item.x, item.y, box);
    pop = { box: box, key: key, back: key };
    render();
    select.focus();
  }
  function deleteHyp(h) {
    var n = h.n;
    removeRow(h.row);
    render();
    commit(de_en("Modell: H" + n + " gelöscht", "Model: H" + n + " deleted"));
    announce(de_en("H" + n + " gelöscht. Strg+Z nimmt es zurück.", "H" + n + " deleted. Ctrl+Z takes it back."));
  }
  function deleteNode(code) {
    var mine = hyps().filter(function (h) { return [h.iv, h.dv, h.mv, h.wv].indexOf(code) >= 0 && edgesOf(h).edges; });
    function go() {
      mine.forEach(function (h) { removeRow(h.row); });
      extra = extra.filter(function (c) { return c !== code; });
      delete layout[code];
      selected = selected.filter(function (c) { return c !== code; });
      focusKey = null;
      render();
      if (mine.length) commit(de_en("Modell: " + name(code) + " mit " + mine.length + " Hypothesen entfernt", "Model: " + name(code) + " removed with " + mine.length + " hypotheses"));
      announce(de_en(name(code) + " entfernt.", name(code) + " removed."));
    }
    if (!mine.length) return go();
    window.askConfirm({ title: de_en(name(code) + " und " + mine.length + " Hypothesen entfernen?", "Remove " + name(code) + " and " + mine.length + " hypotheses?"),
                        note: mine.map(function (h) { return "H" + h.n + ": " + h.text; }).join("\n"),
                        ok: de_en("Entfernen", "Remove"), danger: true }).then(function (yes) { if (yes) go(); });
  }

  // --- the panel of a variable --------------------------------------------------------------------------------------

  function showPanel(code) {
    if (!panel) return;
    var v = vars[code];
    panel.innerHTML = "";
    panel.hidden = false;
    panel.appendChild(html("h3", {}, name(code) + " "));
    panel.querySelector("h3").appendChild(html("span", { class: "hint" }, code + " · " + (v ? v.info : de_en("nicht mehr im Fragebogen", "no longer in the questionnaire"))));
    if (v && v.question && v.question !== v.name) panel.appendChild(html("p", { class: "small" }, v.question));
    if (v && v.statements && v.statements.length) {
      panel.appendChild(html("p", { class: "small muted" }, de_en("Messmodell aus dem Fragebogen (nur ansehen):", "Measurement model from the questionnaire (view only):")));
      var ul = html("ul", { class: "small pm-statements" });
      v.statements.forEach(function (s) { ul.appendChild(html("li", {}, s)); });
      panel.appendChild(ul);
    }
    if (v && !v.sem && v.why) panel.appendChild(html("p", { class: "small" }, de_en("Im Strukturgleichungsmodell nicht möglich: ", "Not possible in the structural equation model: ") + v.why + "."));
    var mine = hyps().filter(function (h) { return [h.iv, h.dv, h.mv, h.wv].indexOf(code) >= 0 && edgesOf(h).edges; });
    if (mine.length) {
      var list = html("ul", { class: "plain small" });
      mine.forEach(function (h) {
        var li = html("li");
        var b = html("button", { type: "button", class: "link" }, "H" + h.n + ": " + (h.text || textFor(h)));
        b.addEventListener("click", function () { relation(h.iv, h.dv, h, "n:" + code); });
        li.appendChild(b); list.appendChild(li);
      });
      panel.appendChild(list);
    }
    if (editable) {
      var row = html("div", { class: "button-row" });
      var link = html("button", { type: "button", class: "button button-secondary button-small" }, de_en("Verbinden …", "Connect …"));
      link.addEventListener("click", function () { startConnect(code); });
      var gone = html("button", { type: "button", class: "button button-secondary button-small pm-danger" }, de_en("Entfernen", "Remove"));
      gone.addEventListener("click", function () { panel.hidden = true; deleteNode(code); });
      row.appendChild(link); row.appendChild(gone); panel.appendChild(row);
    }
  }

  // --- connecting by click or key -----------------------------------------------------------------------------------

  function startConnect(code) {
    if (!editable) return;
    connect = { from: code };
    if (hint) { hint.hidden = false; hint.textContent = de_en("Verbinden ab " + name(code) + ": Ziel anklicken oder mit den Pfeiltasten wählen und Enter drücken; Esc bricht ab.",
                                                               "Connecting from " + name(code) + ": click the target, or choose it with the arrow keys and press Enter; Esc cancels."); }
    announce(hint ? hint.textContent : "");
    render();
    focusItem("n:" + code);
  }
  function endConnect() {
    connect = null;
    if (hint) hint.hidden = true;
    render();
  }

  // --- placing variables ----------------------------------------------------------------------------------------------

  function addVar(code, x, y) {
    if (!editable) return;
    if (nodes().indexOf(code) >= 0) { focusItem("n:" + code); announce(de_en(name(code) + " steht schon im Bild.", name(code) + " is already in the picture.")); return; }
    if (x == null) {
      var by = selected.length ? layout[selected[selected.length - 1]] : null;
      x = by ? by[0] + W + 64 : W / 2 + 32; y = by ? by[1] : H / 2 + 72;
    }
    layout[code] = free(code, snap(x), snap(y));
    extra.push(code);
    selected = [code];
    render();
    focusItem("n:" + code);
    announce(de_en(name(code) + " eingefügt. Mit V verbinden.", name(code) + " inserted. Press V to connect."));
  }

  // --- pointer: drag and drop ---------------------------------------------------------------------------------------

  function point(d) {
    var box = svg.getBoundingClientRect();
    return [(d.x - box.left) / zoom, (d.y - box.top) / zoom, d.x >= box.left && d.x <= box.right && d.y >= box.top && d.y <= box.bottom];
  }
  function targetAt(x, y, self, share) {  // a variable or an arrow under the pointer
    var found = null;
    nodes().forEach(function (code) { if (code !== self && !found && inside(code, x, y, share)) found = "n:" + code; });
    if (found) return found;
    Object.keys(drawn).forEach(function (key) {
      var item = drawn[key];
      if (!found && item.kind === "edge" && item.edge.a !== self && item.edge.b !== self && near(item.geo, x, y) <= 8) found = key;
    });
    return found;
  }
  function mark(key, text) {
    svg.querySelectorAll(".is-target").forEach(function (g) { g.classList.remove("is-target"); });
    var tip = svg.querySelector(".pm-tip");
    if (tip) tip.remove();
    if (!key) return;
    var g = svg.querySelector('[data-key="' + cssKey(key) + '"]');
    if (!g) return;
    g.classList.add("is-target");
    if (text) {
      var item = drawn[key], tipG = el("g", { class: "pm-tip", "aria-hidden": "true" }, svg);
      var w = 16 + text.length * 7;
      el("rect", { x: f(item.x - w / 2), y: f(item.y + (item.kind === "node" ? H / 2 + 10 : 14)), width: f(w), height: 22, rx: 6 }, tipG);
      var t = el("text", { x: f(item.x), y: f(item.y + (item.kind === "node" ? H / 2 + 25 : 29)), "text-anchor": "middle" }, tipG);
      t.textContent = text;
    }
  }
  // alignment with others (centre lines) and equal spacing between two neighbours: [x, y, line at x, line at y]
  function guide(x, y, code) {
    var others = nodes().filter(function (o) { return o !== code && layout[o]; }), sx = null, sy = null;
    others.forEach(function (o) {
      if (sx == null && Math.abs(layout[o][0] - x) < 6) sx = layout[o][0];
      if (sy == null && Math.abs(layout[o][1] - y) < 6) sy = layout[o][1];
    });
    others.forEach(function (o1, i) {
      others.slice(i + 1).forEach(function (o2) {
        var a = layout[o1], b = layout[o2];
        if (sx == null && Math.abs(a[1] - b[1]) < 6 && Math.abs(y - a[1]) < 6 && Math.abs(x - (a[0] + b[0]) / 2) < 6) { sx = (a[0] + b[0]) / 2; sy = a[1]; }
        if (sy == null && Math.abs(a[0] - b[0]) < 6 && Math.abs(x - a[0]) < 6 && Math.abs(y - (a[1] + b[1]) / 2) < 6) { sy = (a[1] + b[1]) / 2; sx = a[0]; }
      });
    });
    return [sx != null ? sx : x, sy != null ? sy : y, sx, sy];
  }
  function guides(sx, sy) {
    var g = svg.guides;
    if (sx != null) el("line", { x1: sx, y1: 0, x2: sx, y2: svg.viewBox.baseVal.height, class: "pm-guide" }, g);
    if (sy != null) el("line", { x1: 0, y1: sy, x2: svg.viewBox.baseVal.width, y2: sy, class: "pm-guide" }, g);
  }
  function moveNode(code, x, y) {  // live, while dragging: the node and its arrows
    layout[code] = [x, y];
    var keepFocus = focusKey;
    render();
    focusKey = keepFocus;
  }
  if (editable && window.pointerDrag) pointerDrag({
    source: function (target) {
      if (!target.closest || pop) return null;
      var item = target.closest("[data-pm-var]");
      if (item && root.contains(item)) return { type: "new", code: item.getAttribute("data-pm-var") };
      if (!svg || !svg.contains(target)) return null;
      var handle = target.closest(".pm-handle"), node = target.closest(".pm-node");
      if (handle && node) return { type: "link", code: node.getAttribute("data-code") };
      if (node) return { type: "move", code: node.getAttribute("data-code"), from: layout[node.getAttribute("data-code")].slice() };
      // on the empty area: a frame drawn with the mouse selects every variable it touches (Ralf, 2026-10-17)
      if (editable && !target.closest(".pm-edge")) return { type: "box" };
      return null;
    },
    start: function (d) {
      if (d.type === "new") {
        d.ghost = html("div", { class: "pm-ghost" }, name(d.code));
      }
      if (d.type === "move") {
        var p = point(d); d.dx = d.from[0] - p[0]; d.dy = d.from[1] - p[1]; dragging = d.code;
        // a selected variable takes the others of the selection along, as they stand to each other
        dragGroup = selected.length > 1 && selected.indexOf(d.code) >= 0 ? selected.filter(function (c) { return c !== d.code && layout[c]; }) : [];
        d.others = dragGroup.map(function (c) { return [c, layout[c].slice()]; });
      }
      if (d.type === "box") {
        var b0 = point(d); d.x0 = b0[0]; d.y0 = b0[1]; d.add = !!(window.event && window.event.shiftKey);
        d.before = d.add ? selected.slice() : [];
      }
      d.target = null; d.armed = false;
    },
    move: function (d) {
      if (!svg) return;
      var p = point(d), x = p[0], y = p[1];
      if (d.type === "link") {
        var line = svg.querySelector(".pm-rubber");
        if (!line) line = el("line", { class: "pm-rubber", "aria-hidden": "true" }, svg);
        var c = layout[d.code];
        line.setAttribute("x1", c[0] + W / 2); line.setAttribute("y1", c[1]); line.setAttribute("x2", x); line.setAttribute("y2", y);
        var t = targetAt(x, y, d.code, 1);
        d.target = t && t.indexOf("n:") === 0 ? t : null;
        mark(d.target, d.target ? de_en("Beziehung anlegen", "Create relation") : "");
        return;
      }
      if (d.type === "box") {
        var rect = svg.querySelector(".pm-marquee");
        if (!rect) rect = el("rect", { class: "pm-marquee", "aria-hidden": "true" }, svg);
        var bx = Math.min(d.x0, x), by = Math.min(d.y0, y), bw = Math.abs(x - d.x0), bh = Math.abs(y - d.y0);
        rect.setAttribute("x", f(bx)); rect.setAttribute("y", f(by)); rect.setAttribute("width", f(bw)); rect.setAttribute("height", f(bh));
        d.hit = nodes().filter(function (c) {
          var c0 = layout[c];
          return c0 && c0[0] + W / 2 >= bx && c0[0] - W / 2 <= bx + bw && c0[1] + H / 2 >= by && c0[1] - H / 2 <= by + bh;
        });
        svg.querySelectorAll(".pm-node").forEach(function (n) {
          var c = n.getAttribute("data-code");
          n.classList.toggle("is-selected", d.hit.indexOf(c) >= 0 || d.before.indexOf(c) >= 0);
        });
        return;
      }
      if (d.type === "move" && d.others.length) {  // several at once: no relation by dropping, only moving
        var gx = x + d.dx, gy = y + d.dy;
        if (!document.body.dataset.pmAlt) { gx = snap(gx); gy = snap(gy); }
        var mx = gx - d.from[0], my = gy - d.from[1];
        d.others.forEach(function (o) { layout[o[0]] = [Math.max(W / 2 + 8, o[1][0] + mx), Math.max(H / 2 + 8, o[1][1] + my)]; });
        moveNode(d.code, Math.max(W / 2 + 8, gx), Math.max(H / 2 + 8, gy));
        return;
      }
      if (d.type === "move") {
        var nx = x + d.dx, ny = y + d.dy;
        var t2 = targetAt(x, y, d.code, INNER);
        if (t2 !== d.target) {
          d.target = t2; d.armed = false; clearTimeout(d.timer);
          if (t2) d.timer = setTimeout(function () { d.armed = true; mark(d.target, d.target.indexOf("n:") === 0 ? de_en("Beziehung anlegen", "Create relation") : de_en("Vermittler oder Moderator?", "Mediator or moderator?")); }, DWELL);
        }
        if (!d.armed) {
          var g = guide(nx, ny, d.code), free_ = !!document.body.dataset.pmAlt;  // Alt: no grid, no guides
          if (!free_) { nx = g[2] == null ? snap(nx) : g[0]; ny = g[3] == null ? snap(ny) : g[1]; }
          moveNode(d.code, Math.max(W / 2 + 8, nx), Math.max(H / 2 + 8, ny));
          if (!free_) guides(g[2], g[3]);
          if (d.target) mark(d.target, "");
        } else mark(d.target, d.target.indexOf("n:") === 0 ? de_en("Beziehung anlegen", "Create relation") : de_en("Vermittler oder Moderator?", "Mediator or moderator?"));
        return;
      }
      // a new variable from the list
      if (!p[2]) { d.target = null; mark(null); return; }
      var t3 = targetAt(x, y, null, INNER);
      if (t3 !== d.target) {
        d.target = t3; d.armed = false; clearTimeout(d.timer);
        if (t3) d.timer = setTimeout(function () { d.armed = true; mark(d.target, d.target.indexOf("n:") === 0 ? de_en("Beziehung anlegen", "Create relation") : de_en("Vermittler oder Moderator?", "Mediator or moderator?")); }, DWELL);
        else mark(null);
      }
    },
    drop: function (d) {
      d.dropped = true;
      clearTimeout(d.timer);
      var p = point(d);
      if (d.type === "link") {
        if (d.target) relation(d.code, d.target.slice(2), null, "n:" + d.code);
        return;
      }
      if (d.type === "box") {
        var picked = (d.hit || []).concat(d.before.filter(function (c) { return (d.hit || []).indexOf(c) < 0; }));
        selected = picked;
        render();
        if (picked.length) announce(de_en(picked.length + " ausgewählt. Ziehen verschiebt sie gemeinsam.", picked.length + " selected. Dragging moves them together."));
        return;
      }
      if (d.type === "move" && d.others.length) {
        render();
        focusItem("n:" + d.code);
        if (layout[d.code][0] !== d.from[0] || layout[d.code][1] !== d.from[1]) {
          commit(de_en("Modell: " + (d.others.length + 1) + " Variablen verschoben", "Model: " + (d.others.length + 1) + " variables moved"));
        }
        return;
      }
      if (d.type === "move") {
        if (d.armed && d.target) {
          layout[d.code] = d.from;
          if (d.target.indexOf("n:") === 0) relation(d.code, d.target.slice(2), null, "n:" + d.code);
          else onto(d.code, d.target, "n:" + d.code);
          return;
        }
        var spot = free(d.code, layout[d.code][0], layout[d.code][1]);
        layout[d.code] = spot;
        render();
        focusItem("n:" + d.code);
        if (spot[0] !== d.from[0] || spot[1] !== d.from[1]) {
          if (extra.indexOf(d.code) < 0) commit(de_en("Modell: " + name(d.code) + " verschoben", "Model: " + name(d.code) + " moved"));
        }
        return;
      }
      if (!p[2]) return;
      if (nodes().indexOf(d.code) >= 0) { focusItem("n:" + d.code); return; }
      if (d.armed && d.target) {
        var at = drawn[d.target];
        layout[d.code] = free(d.code, snap(at.x), snap(at.y + H + 48));
        extra.push(d.code);
        render();
        if (d.target.indexOf("n:") === 0) relation(d.code, d.target.slice(2), null, "n:" + d.code);
        else onto(d.code, d.target, "n:" + d.code);
        return;
      }
      addVar(d.code, p[0], p[1]);
    },
    end: function (d) {
      clearTimeout(d.timer);
      dragging = null;
      dragGroup = [];
      if (d.type === "move" && !d.dropped) {
        layout[d.code] = d.from;
        d.others.forEach(function (o) { layout[o[0]] = o[1]; });
      }
      var frame = svg && svg.querySelector(".pm-marquee");
      if (frame) frame.remove();
      if (!pop) render();
      else { var line = svg && svg.querySelector(".pm-rubber"); if (line) line.remove(); mark(null); }
    },
    scroller: function () { return null; }
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Alt") document.body.dataset.pmAlt = "1"; });
  document.addEventListener("keyup", function (e) { if (e.key === "Alt") delete document.body.dataset.pmAlt; });

  // clicks: a variable of the list, a node, a handle, an arrow, the empty area
  root.addEventListener("click", function (e) {
    var item = e.target.closest("[data-pm-var]");
    if (item) { addVar(item.getAttribute("data-pm-var")); return; }
    if (!svg || !svg.contains(e.target)) return;
    var handle = e.target.closest(".pm-handle"), node = e.target.closest(".pm-node"), edge = e.target.closest(".pm-edge");
    if (handle && node) { startConnect(node.getAttribute("data-code")); return; }
    if (node) {
      var code = node.getAttribute("data-code");
      if (connect && connect.from !== code) { var from = connect.from; endConnect(); relation(from, code, null, "n:" + from); return; }
      if (e.shiftKey) {
        selected = selected.indexOf(code) >= 0 ? selected.filter(function (c) { return c !== code; }) : selected.concat([code]);
        render(); focusItem("n:" + code); return;
      }
      selected = [code];
      render(); focusItem("n:" + code);
      showPanel(code);
      return;
    }
    if (edge) { var it = drawn[edge.getAttribute("data-key")]; if (it) relation(it.h.iv, it.h.dv, it.h, edge.getAttribute("data-key")); return; }
    if (pop) closePop(false);
    if (connect) endConnect();
    selected = [];
    if (panel) panel.hidden = true;
    render();
  });

  // --- keyboard -------------------------------------------------------------------------------------------------------

  function towards(key, dx, dy) {  // the nearest item in a direction
    var from = drawn[key], best = null, score = Infinity;
    Object.keys(drawn).forEach(function (k) {
      if (k === key) return;
      var it = drawn[k], vx = it.x - from.x, vy = it.y - from.y;
      var along = vx * dx + vy * dy, across = Math.abs(vx * dy - vy * dx);
      if (along <= 0 || across > along * 2) return;
      var s = along + 2 * across;
      if (s < score) { score = s; best = k; }
    });
    return best;
  }
  var nudge = null;
  root.addEventListener("keydown", function (e) {
    var g = e.target.closest && e.target.closest("[data-key]");
    if (!g || !svg || !svg.contains(g)) return;
    var key = g.getAttribute("data-key"), item = drawn[key];
    if (!item) return;
    var arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (arrows[e.key]) {
      e.preventDefault();
      var dir = arrows[e.key];
      if (e.shiftKey && item.kind === "node" && editable) {
        var step = e.ctrlKey || e.metaKey ? 40 : GRID, c = layout[item.code];
        layout[item.code] = [Math.max(W / 2 + 8, c[0] + dir[0] * step), Math.max(H / 2 + 8, c[1] + dir[1] * step)];
        render(); focusItem(key);
        clearTimeout(nudge);
        if (extra.indexOf(item.code) < 0) nudge = setTimeout(function () { commit(de_en("Modell: " + name(item.code) + " verschoben", "Model: " + name(item.code) + " moved")); }, 800);
        return;
      }
      var next = towards(key, dir[0], dir[1]);
      if (next) { focusItem(next); if (connect) announce(de_en("Ziel: ", "Target: ") + (drawn[next].kind === "node" ? name(drawn[next].code) : "")); }
      return;
    }
    if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      var keys = Object.keys(drawn);
      if (keys.length) focusItem(e.key === "Home" ? keys[0] : keys[keys.length - 1]);
      return;
    }
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (item.kind === "node") {
        if (connect && connect.from !== item.code) { var from = connect.from; endConnect(); relation(from, item.code, null, "n:" + from); }
        else showPanel(item.code);
      } else relation(item.h.iv, item.h.dv, item.h, key);
      return;
    }
    if ((e.key === "v" || e.key === "V") && item.kind === "node" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); startConnect(item.code); return; }
    if ((e.key === "m" || e.key === "M") && item.kind === "edge" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); pickMediator(key); return; }
    if ((e.key === "Delete" || e.key === "Backspace") && editable) {
      e.preventDefault();
      if (item.kind === "edge") deleteHyp(item.h); else deleteNode(item.code);
      return;
    }
    if (e.key === "Escape") {
      if (connect) { e.preventDefault(); endConnect(); focusItem(key); }
      else if (selected.length) { selected = []; render(); focusItem(key); }
      return;
    }
    if (e.key === "?") { e.preventDefault(); var help = root.querySelector("[data-pm-keys]"); if (help) { help.open = !help.open; } }
  });

  // --- tools ----------------------------------------------------------------------------------------------------------

  function align(how) {
    var pts = selected.filter(function (c) { return layout[c]; });
    if (pts.length < 2) return;
    var xs = pts.map(function (c) { return layout[c][0]; }), ys = pts.map(function (c) { return layout[c][1]; });
    if (how === "left") { var minX = Math.min.apply(null, xs); pts.forEach(function (c) { layout[c][0] = minX; }); }
    if (how === "middle") { var mx = snap(xs.reduce(function (a, b) { return a + b; }, 0) / xs.length); pts.forEach(function (c) { layout[c][0] = mx; }); }
    if (how === "top") { var minY = Math.min.apply(null, ys); pts.forEach(function (c) { layout[c][1] = minY; }); }
    if (how === "row") { var my = snap(ys.reduce(function (a, b) { return a + b; }, 0) / ys.length); pts.forEach(function (c) { layout[c][1] = my; }); }
    if (how === "spread-x" || how === "spread-y") {
      var axis = how === "spread-x" ? 0 : 1;
      pts.sort(function (a, b) { return layout[a][axis] - layout[b][axis]; });
      var lo = layout[pts[0]][axis], hi = layout[pts[pts.length - 1]][axis], need = (axis ? H : W) + GAP;
      if (hi - lo < need * (pts.length - 1)) hi = lo + need * (pts.length - 1);
      pts.forEach(function (c, i) { layout[c][axis] = snap(lo + (hi - lo) * i / (pts.length - 1)); });
    }
    render();
    commit(de_en("Modell: ausgerichtet", "Model: aligned"));
  }
  if (tools) tools.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    if (b.hasAttribute("data-pm-auto") && editable) {
      var found = auto(nodes(), true);
      Object.keys(found).forEach(function (c) { layout[c] = found[c]; });
      render();
      commit(de_en("Modell: automatisch angeordnet", "Model: arranged automatically"));
      announce(de_en("Automatisch angeordnet. Strg+Z holt die alte Anordnung zurück.", "Arranged automatically. Ctrl+Z brings back the old arrangement."));
    }
    if (b.hasAttribute("data-pm-zoom")) {
      zoom = Math.max(0.5, Math.min(1.6, Math.round((zoom + (+b.getAttribute("data-pm-zoom"))) * 10) / 10));
      render();
    }
    if (b.hasAttribute("data-pm-measure")) {
      measure = !measure;
      try { localStorage.setItem(MEASURE_KEY, measure ? "1" : "0"); } catch (e) { /* a convenience only */ }
      render();
    }
    if (b.hasAttribute("data-pm-fit")) {  // "Alles zeigen": the whole drawing in the width and in the window's height
      var maxX = 0, maxY = 0;
      nodes().forEach(function (c) {
        if (layout[c]) { maxX = Math.max(maxX, layout[c][0] + W / 2 + 32); maxY = Math.max(maxY, layout[c][1] + H / 2 + 40); }
      });
      var room = Math.max(320, window.innerHeight - 160);
      zoom = maxX ? Math.max(0.5, Math.min(1, Math.floor(Math.min(area.clientWidth / maxX, room / maxY) * 10) / 10)) : 1;
      render();
    }
    if (b.hasAttribute("data-pm-align")) align(b.getAttribute("data-pm-align"));
    if (b.hasAttribute("data-pm-image")) {
      picture(b.getAttribute("data-pm-image"));
      var menu = b.closest("details");
      if (menu) menu.open = false;
    }
  });
  // zoom with Ctrl and the mouse wheel (or a pinch on a touchpad), in the same steps as − and +
  area.addEventListener("wheel", function (e) {
    if (!e.ctrlKey || !svg) return;
    e.preventDefault();
    var next = Math.max(0.5, Math.min(1.6, Math.round((zoom + (e.deltaY < 0 ? 0.1 : -0.1)) * 10) / 10));
    if (next !== zoom) { zoom = next; render(); }
  }, { passive: false });
  // "Als Bild": the drawing as an SVG file, with the colours of the page written into it, or as a PNG drawn from it at
  // twice its size on white
  function picture(kind) {
    var clone = svg.cloneNode(true);
    var from = svg.querySelectorAll("*"), to = clone.querySelectorAll("*");
    Array.prototype.forEach.call(from, function (node, i) {
      var s = getComputedStyle(node), out = to[i];
      ["fill", "stroke", "stroke-width", "stroke-dasharray", "font-size", "font-weight", "font-family", "opacity"].forEach(function (p) {
        var v = s.getPropertyValue(p);
        if (v) out.style.setProperty(p, v);
      });
    });
    clone.querySelectorAll(".pm-handle, .pm-ring, .pm-guides, .pm-edge-hit").forEach(function (n) { n.remove(); });
    clone.setAttribute("xmlns", NS);
    function offer(blob, ext) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (data.file || "modell") + "." + ext;
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    }
    if (kind !== "png") {
      offer(new Blob(['<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml" }), "svg");
      return;
    }
    var box = svg.viewBox.baseVal, scale = 2, img = new Image();
    clone.setAttribute("width", box.width); clone.setAttribute("height", box.height);
    img.onload = function () {
      var canvas = document.createElement("canvas");
      canvas.width = Math.round(box.width * scale); canvas.height = Math.round(box.height * scale);
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(function (png) { if (png) offer(png, "png"); }, "image/png");
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(clone));
  }

  // --- the view: list or model ----------------------------------------------------------------------------------------

  var VIEW_KEY = "askurious-plan-view";
  function view(which, remember) {
    var model = which === "model";
    set.classList.toggle("is-model", model);
    root.hidden = !model;
    document.querySelectorAll("[data-plan-view] input").forEach(function (r) { r.checked = r.value === which; });
    if (remember) { try { localStorage.setItem(VIEW_KEY, which); } catch (e) { /* the view is only a convenience */ } }
    if (model) render();
    else {
      // the rows the drawing changed: their names and choices as the list draws them (app.js); nothing new to save
      rows().forEach(function (row) {
        if (!row.dataset.modelChanged) return;
        delete row.dataset.modelChanged;
        var kind = field(row, "h_kind");
        if (kind) kind.dispatchEvent(new Event("change", { bubbles: true }));
      });
    }
  }
  document.querySelectorAll("[data-plan-view] input").forEach(function (r) {
    r.addEventListener("change", function () { if (r.checked) view(r.value, true); });
  });
  var start = "list";
  try { start = localStorage.getItem(VIEW_KEY) || "list"; } catch (e) { start = "list"; }
  view(start === "model" ? "model" : "list", false);
  window.addEventListener("resize", function () { if (!root.hidden && !pop) render(); });
})();
