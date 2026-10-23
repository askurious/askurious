// "Modell als Text" of the drawing in the study plan (app/static/model.js, drawText) with a small stand-in for the
// page: an empty model says so, placed variables without a relation and hypotheses not in the picture are named
// (pre-test 2026-10-20, 15). Run by tests/analysis/test_plan_model_text.py: node tests/model_text_cases.js <model.js>.
"use strict";
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function part(from, to) {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error("not found: " + from);
  return src.slice(a, b);
}
function de_en(de, en) { return de; }
function html(tag, attrs, text) { return { tag: tag, attrs: attrs || {}, textContent: text == null ? "" : text }; }
function run(hyps, codes, extra, editable) {
  const textList = { items: [], set innerHTML(v) { this.items = []; }, appendChild(n) { this.items.push(n); } };
  const outside = { hidden: true, textContent: "" };
  const vars = { x: { name: "Skala x" }, y: { name: "Skala y" }, m1: { name: "Wie fühlen Sie sich gerade?" } };
  const make = new Function("textList", "outside", "vars", "extra", "editable", "de_en", "html",
    '"use strict";' + part("  function name(code) {", "  function textFor(h) {")
    + part("  function edgesOf(h) {", "  function nodes() {")
    + part("  function drawText(", "  function drawPalette(") + "; return drawText;");
  make(textList, outside, vars, extra, editable, de_en, html)(hyps, codes);
  return { lines: textList.items.map((n) => n.textContent), outside: outside.hidden ? null : outside.textContent };
}
const missing = [{ n: 1, id: "a", kind: "correlation", iv: "", dv: "" }, { n: 2, id: "b", kind: "effect", iv: "x", dv: "" }];
const drawn = [{ n: 1, id: "a", kind: "correlation", dir: "pos", iv: "x", dv: "y" }, { n: 2, id: "b", kind: "effect", iv: "", dv: "" }];
process.stdout.write(JSON.stringify({
  empty: run(missing, [], [], true),
  empty_readonly: run([], [], [], false),
  placed: run(drawn, ["x", "y", "m1"], ["m1"], true),
}));
