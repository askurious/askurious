// Runs the app dialog of app/static/app.js (askConfirm) for tests/ui/test_confirm_dialog.py in a small stand-in DOM.
// The stand-in dialog sends its "close" event only when asked to (FIRE_CLOSE), as a browser that never sends it (the
// test browser of the desktop app, 2026-10-30). Prints the answers as JSON.
const fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("app/static/app.js", "utf8");
const start = source.indexOf("// Questions before a step that cannot be taken back");
const body = source.slice(source.indexOf("(function () {", start), source.indexOf("})();", start) + 5);

function element(tag) {
  const listeners = {}, kids = {};
  return {
    tag, open: false, returnValue: "", hidden: false, disabled: false, textContent: "", value: "", dataset: {}, style: {},
    offsetHeight: 200, classList: { toggle() {} }, setAttribute() {}, focus() {},
    set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html || ""; },
    querySelector(sel) { return kids[sel] || (kids[sel] = element(sel)); },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type, extra) { (listeners[type] || []).forEach(function (fn) { fn(Object.assign({ target: this }, extra || {})); }, this); },
    click() { this.fire("click"); },
    showModal() { this.open = true; },
    close(value) {
      if (!this.open) return;
      this.open = false;
      if (value !== undefined) this.returnValue = value;
      if (ctx.FIRE_CLOSE) { const self = this; setTimeout(function () { self.fire("close"); }, 0); }
    },
  };
}
let made = null;
const ctx = {
  FIRE_CLOSE: false, setTimeout, Promise,
  de_en: function (de, en) { return en; },
  document: { createElement(tag) { made = element(tag); return made; }, body: { appendChild() {} } },
  window: { HTMLDialogElement: function () {}, innerWidth: 1000, visualViewport: null },
};
vm.createContext(ctx);
vm.runInContext(body, ctx);
const ask = ctx.window.askConfirm;
const tick = () => new Promise((r) => setTimeout(r, 5));

async function answer(how, fireClose) {
  ctx.FIRE_CLOSE = fireClose;
  let got = "waiting";
  ask({ title: "Delete?" }).then(function (v) { got = v; });
  await tick();
  const d = made;
  if (how === "yes") d.querySelector("[data-yes]").click();
  if (how === "no") d.querySelector("[data-no]").click();
  if (how === "beside") d.fire("click", { target: d });
  if (how === "escape") { d.fire("cancel"); d.close(); }
  await tick(); await tick();
  return { how, fireClose, got, open: d.open };
}

(async function () {
  const out = [];
  for (const fireClose of [false, true]) {
    for (const how of ["yes", "no", "beside", "escape"]) out.push(await answer(how, fireClose));
  }
  // answered once only: a yes and then a late "close" with "no" stays yes
  ctx.FIRE_CLOSE = false;
  let got = [];
  ask({ title: "Once?" }).then(function (v) { got.push(v); });
  await tick();
  made.querySelector("[data-yes]").click();
  made.returnValue = "no";
  made.fire("close");
  await tick();
  out.push({ how: "once", got });
  // the next question opens after an answered one (the queue does not hang)
  let next = "waiting";
  ask({ title: "Next?" }).then(function (v) { next = v; });
  await tick();
  const opened = made.open;
  made.querySelector("[data-no]").click();
  await tick();
  out.push({ how: "next", opened, got: next });
  process.stdout.write(JSON.stringify(out));
})();
