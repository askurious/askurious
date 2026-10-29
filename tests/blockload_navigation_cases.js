// "Schneller blättern" (participant.js, block loading): an answer to an older "Next" never undoes a later "Back",
// and the note on pages still waiting stays while the queue holds one (UI/UX audit 2026-10-29, A11Y-06, A11Y-07).
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const js = fs.readFileSync('app/static/participant.js', 'utf8');
const block = js.slice(js.indexOf('var blockPages = null'), js.indexOf('// One page, one submit'));
const tick = () => new Promise(setImmediate);

function el(attrs) {
  const a = Object.assign({}, attrs);
  return {
    hidden: false, textContent: '', getAttribute(k) { return k in a ? a[k] : null; }, setAttribute(k, v) { a[k] = String(v); },
    removeAttribute(k) { delete a[k]; }, hasAttribute(k) { return k in a; }, querySelector() { return null; },
    querySelectorAll() { return []; }, focus() {}, addEventListener() {}, appendChild() {},
  };
}

function setUp(network) {
  const heading = el({});
  const pages = [1, 2, 3].map(n => {
    const p = el({ 'data-bpage': 'p' + n, 'data-page': String(n), 'data-page-count': '3', 'data-announce': 'Seite ' + n });
    p.hidden = n !== 2;
    p.querySelector = s => (s === 'h1' ? heading : null);
    return p;
  });
  const note = el({ 'data-offline': 'OFFLINE', 'data-unsent': 'UNSENT', 'data-nostore': 'NOSTORE', 'data-sent': 'SENT' });
  const handlers = {};
  const form = el({ 'data-block-url': '/b', 'data-block-start': 's' });
  form.querySelectorAll = s => (s === '.p-bpage' ? pages : []);
  form.querySelector = s => (s === '[data-block-note]' ? note : null);
  form.addEventListener = (k, fn) => { (handlers[k] = handlers[k] || []).push(fn); };
  const storage = {};
  const ctx = {
    document: { querySelector: s => (s === '.p-container form[data-block]' ? form : null), title: '', addEventListener() {},
                dispatchEvent() {}, createElement: () => el({}) },
    window: { sessionStorage: { getItem: k => (k in storage ? storage[k] : null), setItem(k, v) { storage[k] = v; },
                                removeItem(k) { delete storage[k]; } },
              addEventListener() {}, scrollTo() {}, AbortController, fetch: true },
    AbortController, navigator: { onLine: true }, CustomEvent: class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } },
    setTimeout: (fn, ms) => (ms === 50 ? fn() : 0), clearTimeout() {}, setInterval() {}, Promise, JSON, Date, Math,
    messageSummary: () => null, focusGap() {}, location: { reload() {} },
    fetch: () => network(),
  };
  ctx.window.fetch = ctx.fetch;
  vm.runInNewContext(block, ctx);
  const submit = by => handlers.submit[0]({ submitter: by, defaultPrevented: false, preventDefault() {} });
  return { pages, note, storage, submit, shown: () => pages.findIndex(p => !p.hidden) + 1 };
}

const ok = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ errors: {}, nudges: {} }) });

async function staleForward() {
  let release;
  const calls = [];
  const t = setUp(() => { calls.push(1); return calls.length === 1 ? new Promise(r => { release = () => r(ok()); }) : ok(); });
  t.submit({ name: 'nav', value: 'next' });  // on page 2: "Next", the answer is late
  await tick();
  t.submit({ name: 'nav', value: 'back' });  // meanwhile "Back"
  assert.equal(t.shown(), 1);
  release();
  for (let k = 0; k < 6; k++) await tick();
  assert.equal(t.shown(), 1, 'the late answer to Next must not move on from the page gone back to');
}

async function pendingNoteStays() {
  const t = setUp(() => Promise.reject(new TypeError('network')));
  t.submit({ name: 'nav', value: 'next' });  // page 2 cannot be sent: page 3, the note says so
  for (let k = 0; k < 6; k++) await tick();
  assert.equal(t.shown(), 3);
  assert.equal(t.note.textContent, 'UNSENT');
  t.submit({ name: 'nav', value: 'back' });  // back to 2: the page still waits, and the back page joins it
  for (let k = 0; k < 6; k++) await tick();
  assert.equal(t.shown(), 2);
  assert.equal(t.note.textContent, 'UNSENT', 'the note stays while pages wait');
  assert.equal(JSON.parse(Object.values(t.storage)[0]).length, 2);
}

(async () => {
  await staleForward();
  await pendingNoteStays();
  console.log('2 block navigation cases passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
