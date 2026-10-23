// Execute the complete production controller with a small DOM and canvas adapter.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    dataset: {}, value: '', disabled: false, hidden: false, handlers: {},
    classList: {contains: () => false, add() {}, remove() {}},
    addEventListener(name, fn) {this.handlers[name] = fn;},
    querySelector: element, querySelectorAll: () => [], focus() {},
    setAttribute() {}, insertBefore() {}, appendChild() {}
  });
  return elements.get(selector);
}
const box = element('[data-comments]');
box.dataset.url = '/comment';
const fields = element('[data-shot-rights]');
fields.disabled = true;
const creator = {name: 'shot_rights_creator', value: ''};
fields.querySelectorAll = () => [creator];
const panel = element('[data-shot-rights-panel]');
panel.hidden = true;
const file = element('[data-shot-file]');
const editor = element('[data-shot-editor]');
editor.showModal = () => {};
let nextMarks = [{kind: 'cover'}], requests = [], originalReads = 0;
function canvas() {
  return {width: 400, height: 300,
    getContext: () => ({fillRect() {}, drawImage() {}}),
    toBlob: fn => fn({type: 'image/png', size: 100, pixels: 'redacted'})};
}
class ImageStub {
  constructor() {this.naturalWidth = 400; this.naturalHeight = 300;}
  set src(v) {originalReads++; this.onload();}
}
class FormDataStub {
  constructor(form) {this.values = new Map(); if (form && !fields.disabled) this.values.set(creator.name, creator.value);}
  append(k, v) {this.values.set(k, v);}
}
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {
  document: {querySelectorAll: s => s === '[data-comments]' ? [box] : [], querySelector: element,
    addEventListener() {}, createElement: kind => kind === 'canvas' ? canvas() : element('new-' + kind),
    documentElement: {lang: 'de'}, body: null},
  navigator: {userAgent: '', platform: ''},
  window: {innerWidth: 800, innerHeight: 600, matchMedia: () => ({matches: false}),
    CommentShot: {supported: () => false, edit: () => Promise.resolve(nextMarks), render: base => base}},
  URL: {createObjectURL: () => 'blob:preview', revokeObjectURL() {}},
  Image: ImageStub, FormData: FormDataStub,
  fetch: (url, options) => {requests.push(options.body.values); return Promise.resolve({ok: false, json: () => Promise.resolve({})});}
});
const tick = () => new Promise(resolve => setImmediate(resolve));
const event = {preventDefault() {}};
async function pick() {file.files = [{type: 'image/png', pixels: 'UNREDACTED'}]; file.handlers.change(); await tick();}
(async function () {
  await pick();
  assert.equal(originalReads, 1);
  assert.equal(fields.disabled, false);
  assert.equal(panel.hidden, false);
  creator.value = 'Photo credit';
  element('[data-shot-img]').handlers.click();
  await tick();
  assert.equal(creator.value, 'Photo credit');  // re-marking the same source keeps credit
  nextMarks = null;
  await pick();
  assert.equal(creator.value, 'Photo credit');  // cancelled replacement leaves the previous image and credit
  element('[data-comment-form]').handlers.submit(event);
  await tick();
  assert.equal(requests[0].get('shot_rights_creator'), 'Photo credit');
  assert.equal(requests[0].get('shot').pixels, 'redacted');
  assert.equal([...requests[0].values()].some(v => v && v.pixels === 'UNREDACTED'), false);
  assert.equal(creator.value, 'Photo credit');  // failed send can be retried with its credit
  nextMarks = [];
  await pick();
  assert.equal(creator.value, '');  // unrelated picture never inherits prior credit
  creator.value = 'Second image';
  element('[data-shot-remove]').handlers.click();
  assert.equal(creator.value, '');
  assert.equal(fields.disabled, true);
  assert.equal(panel.hidden, true);
})().catch(e => {console.error(e); process.exitCode = 1;});
