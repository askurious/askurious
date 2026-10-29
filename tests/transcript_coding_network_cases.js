// The coding view without a connection (app/static/transcript_coding_code.js, post): a broken-off request answers
// like the server with ok false and a message, so every caller says it and frees its buttons (UI/UX audit
// 2026-10-29, WQ-10).
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const src = fs.readFileSync('app/static/transcript_coding_code.js', 'utf8').replace(/\r\n/g, '\n');  // also in a CRLF checkout
const start = src.indexOf('  function post(url, body) {');
const post = src.slice(start, src.indexOf('\n  }\n', start) + 4);
const ctx = { csrf: 'x', JSON, de_en: (de, en) => en, fetch: () => Promise.reject(new TypeError('Failed to fetch')) };
vm.runInNewContext(post + '\nthis.post = post;', ctx);
ctx.post('/add', { cat: 1 }).then(d => {
  assert.equal(d.ok, false);
  assert.equal(d.network, true);
  assert.match(d.error, /connection broke off/);
  ctx.fetch = () => Promise.resolve({ json: () => Promise.reject(new Error('html')) });
  return ctx.post('/add', {});
}).then(d => {
  assert.equal(d.ok, false); assert.equal(d.network, undefined);
  console.log('2 coding network cases passed');
}).catch(e => { console.error(e); process.exitCode = 1; });
