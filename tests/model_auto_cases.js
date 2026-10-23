// The automatic layout of "Modell zeichnen" (app/static/model.js, auto) against the layout before audit 2026-10-11
// (N15): the same result for models whose columns hold up to five variables, and a bound on the time for large ones.
// Run by tests/test_model_auto.py: node tests/model_auto_cases.js <model.js>; prints JSON.
"use strict";
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const W = 200, H = 64, GRID = 8, layout = {};
function snap(v) { return Math.round(v / GRID) * GRID; }
let EDGES = [];
const budget = src.match(/var BUDGET = ([0-9.e]+);/);
const now = new Function("BUDGET", "W", "H", "GRID", "layout", "snap", "links",
  '"use strict";' + src.slice(src.indexOf("  function auto(codes, all) {"), src.indexOf("  function snap(v)")) + "; return auto;")(
  budget ? +budget[1] : Infinity, W, H, GRID, layout, snap, () => EDGES);
// auto() as it was before audit 2026-10-11 (N15), unchanged
function reference(W, H, GRID, layout, snap, links) {
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
    function crosses(p1, p2, p3, p4) {
      function side(a, b, c) { return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]); }
      var d1 = side(p3, p4, p1), d2 = side(p3, p4, p2), d3 = side(p1, p2, p3), d4 = side(p1, p2, p4);
      return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0)) && d1 && d2 && d3 && d4;
    }
    function cost(cols) {
      var pos = places(cols), n = 0, length = 0;
      edges.forEach(function (e, i) {
        var a = pos[e[0]], b = pos[e[1]];
        length += Math.abs(a[1] - b[1]);
        edges.slice(i + 1).forEach(function (f) {
          if (f[0] === e[0] || f[0] === e[1] || f[1] === e[0] || f[1] === e[1]) return;  // lines that meet in a variable
          if (crosses(a, b, pos[f[0]], pos[f[1]])) n += 1;
        });
        codes.forEach(function (c) {  // through a variable on the way
          if (c === e[0] || c === e[1]) return;
          var p = pos[c];
          for (var s = 1; s < 10; s++) {
            var x = a[0] + (b[0] - a[0]) * s / 10, y = a[1] + (b[1] - a[1]) * s / 10;
            if (Math.abs(x - p[0]) < W / 2 && Math.abs(y - p[1]) < H / 2) { n += 3; break; }
          }
        });
      });
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
    for (var round = 0; round < 6; round++) {
      var better = false;
      columns.forEach(function (col, d) {
        if (col.length < 2) return;
        var orders = col.length <= 6 ? permutations(col) : col.slice(0, -1).map(function (_, i) {
          var o = col.slice(); o[i] = col[i + 1]; o[i + 1] = col[i]; return o;
        });
        orders.forEach(function (order) {
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
  return auto;
}
const before = reference(W, H, GRID, layout, snap, function () { return EDGES; });
let seed = 7;
function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; }
function tallest(codes, edges) {  // the longest column: x positions of the old result
  EDGES = edges;
  const out = before(codes, true), count = {};
  Object.values(out).forEach(function (p) { count[p[0]] = (count[p[0]] || 0) + 1; });
  return Math.max.apply(null, Object.values(count));
}
let compared = 0, differ = 0;
for (let t = 0; t < 400; t++) {
  const nv = 3 + Math.floor(rnd() * 14), codes = [...Array(nv).keys()].map(function (i) { return "v" + i; }), edges = [];
  const ne = 1 + Math.floor(rnd() * 20);
  for (let k = 0; k < ne; k++) {
    let a = Math.floor(rnd() * nv), b = Math.floor(rnd() * nv);
    if (a === b) continue;
    if (a > b && rnd() < 0.8) [a, b] = [b, a];
    edges.push(["v" + a, "v" + b, rnd() < 0.85]);
  }
  if (tallest(codes, edges) > 5) continue;
  compared += 1;
  EDGES = edges;
  if (JSON.stringify(now(codes, true)) !== JSON.stringify(before(codes, true))) differ += 1;
}
// large: 20 mediations over 6/6/6 variables, and ten columns of six
const heavy = [];
{
  const codes = [], edges = [];
  for (let i = 0; i < 6; i++) codes.push("x" + i, "m" + i, "y" + i);
  for (let i = 0; i < 20; i++) {
    const x = "x" + (i % 6), m = "m" + ((i * 5 + 1) % 6), y = "y" + ((i * 7 + 2) % 6);
    edges.push([x, m, true], [m, y, true], [x, y, true]);
  }
  heavy.push([codes, edges]);
}
{
  const codes = [], edges = [];
  for (let d = 0; d < 10; d++) for (let i = 0; i < 6; i++) codes.push("c" + d + "_" + i);
  for (let k = 0; k < 60; k++) { const d = k % 9; edges.push(["c" + d + "_" + (k % 6), "c" + (d + 1) + "_" + ((k * 5 + 1) % 6), true]); }
  heavy.push([codes, edges]);
}
const ms = heavy.map(function (h) {
  EDGES = h[1];
  now(h[0], true);  // warm
  const t0 = process.hrtime.bigint();
  now(h[0], true);
  return Number(process.hrtime.bigint() - t0) / 1e6;
});
console.log(JSON.stringify({ compared: compared, differ: differ, heavy_ms: ms }));
