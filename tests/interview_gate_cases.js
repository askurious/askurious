// The rules of the filters "Nur stellen, wenn …" in the interview view (app/static/interview-gate.js), run under Node
// by tests/interview/test_guide_filter.py. Prints "ok <n>" after n cases.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const ctx = { window: {} };
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const Gate = ctx.window.AskuriousGate;
let count = 0;

// Ralf's example: F3 has K1 ("Pflegen Sie jemanden?"); F4 to F12 only if K1 applies; F13 always; the closing page last.
// Pages: 0 = F3, 1..9 = F4..F12, 10 = F13, 11 = closing page; F5 has an Unterfrage U5.
const K1 = { of: 3, probe: 'k1', fits: true, n: 1, why: 'weil K1 nicht zutrifft', gone: 'weil F3 entfällt', wait: 'Bitte zuerst K1' };
function guide(linear) {
  const fits = {}, answers = {};
  const pages = [{ gates: [], question: '3', subs: [] }];
  for (let q = 4; q <= 12; q++) pages.push({ gates: [K1], question: String(q), subs: q === 5 ? ['55'] : [] });
  pages.push({ gates: [], question: '13', subs: [] }, { gates: [], question: null, subs: [] });
  const gate = Gate.make(pages, linear, () => fits, (id) => answers[id] || []);
  return { gate, fits, answers, pages };
}

// while K1 is open nothing is left out, and "Weiter" waits on F3 (E3)
{
  const { gate } = guide(true);
  assert.deepEqual(JSON.parse(JSON.stringify(gate.forward(1))), { to: 1, skipped: [] });
  assert.equal(gate.waiting(0, ['3:k1']).wait, 'Bitte zuerst K1');
  assert.equal(gate.waiting(0, []), null);                 // no switch of a later filter on this page
  assert.equal(gate.waiting(1, ['4:k1']), null);
  count++;
}
// K1 does not apply: "Weiter" on F3 goes straight to F13, F4 to F12 and U5 are "nicht gestellt" by the filter (E1, E5)
{
  const { gate, fits } = guide(true);
  fits['3:k1'] = 'no';
  assert.equal(gate.waiting(0, ['3:k1']), null);
  const to = gate.forward(1);
  assert.equal(to.to, 10);
  assert.deepEqual(JSON.parse(JSON.stringify(gate.count(to.skipped))), { questions: 9, pages: 9 });
  assert.equal(gate.why(gate.failing(to.skipped[0])), 'weil K1 nicht zutrifft');
  const states = { 7: 'asked' }, by = {};                   // F7 set by hand before
  assert.equal(gate.settle(states, by), true);
  for (const q of ['4', '5', '55', '6', '8', '9', '10', '11', '12']) { assert.equal(states[q], 'skipped'); assert.equal(by[q], 'filter'); }
  assert.equal(states['7'], 'asked'); assert.equal(by['7'], undefined);   // a state by hand stays
  assert.equal(states['3'], undefined); assert.equal(states['13'], undefined);
  assert.equal(gate.back(10), 0);                           // "Zurück" back over the pages left out
  assert.equal(gate.jumpBack(1, 10), 10);                   // a jump into the left-out block lands after it
  // K1 applies after all: the filter's states are taken back, the one by hand stays
  fits['3:k1'] = 'yes';
  assert.equal(gate.settle(states, by), true);
  assert.deepEqual(JSON.parse(JSON.stringify(states)), { 7: 'asked' });
  assert.deepEqual(JSON.parse(JSON.stringify(by)), {});
  assert.equal(gate.forward(1).to, 1);
  count++;
}
// a state the interviewer set on a page the filter left out stays when the filter holds again
{
  const { gate, fits } = guide(true);
  fits['3:k1'] = 'no';
  const states = {}, by = {};
  gate.settle(states, by);
  states['6'] = 'spontaneous'; delete by['6'];              // changed by hand (interview.js deletes "by")
  fits['3:k1'] = 'yes';
  gate.settle(states, by);
  assert.deepEqual(JSON.parse(JSON.stringify(states)), { 6: 'spontaneous' });
  count++;
}
// "Frei springen": nothing is left out or set, the pages are only known as filtered (paled)
{
  const { gate, fits } = guide(false);
  fits['3:k1'] = 'no';
  assert.equal(gate.forward(1).to, 1);
  assert.equal(gate.back(5), 4);
  assert.equal(gate.waiting(0, ['3:k1']), null);
  const states = {}, by = {};
  assert.equal(gate.settle(states, by), false);
  assert.deepEqual(JSON.parse(JSON.stringify(states)), {});
  assert.equal(gate.filtered(3), true);
  count++;
}
// a filter on a Leitfrage that is left out itself does not hold (E7); both filters of a page must hold (E2)
{
  const fits = { '3:k1': 'no', '5:k2': 'yes' };
  const on5 = { of: 5, probe: 'k2', fits: true, n: 2, why: 'w', gone: 'weil F5 entfällt' };
  const pages = [{ gates: [], question: '3' }, { gates: [K1], question: '5' }, { gates: [on5], question: '6' },
                 { gates: [on5, { item: 9, code: '1', why: 'x' }], question: '7' }, { gates: [], question: null }];
  const answers = { 9: ['2'] };
  const gate = Gate.make(pages, true, () => fits, (id) => answers[id] || []);
  assert.equal(gate.filtered(2), true);
  assert.equal(gate.why(gate.failing(2)), 'weil F5 entfällt');
  fits['3:k1'] = 'yes';
  assert.equal(gate.filtered(2), false);
  assert.equal(gate.filtered(3), true);                     // the Kurzfragebogen answer is another one
  answers[9] = ['1'];
  assert.equal(gate.filtered(3), false);
  answers[9] = [];
  assert.equal(gate.holds(pages[3].gates[1], {}), null);    // not answered yet: open, nothing left out
  assert.equal(gate.filtered(3), false);
  count++;
}
// a Themenblock of hints only: the pages count, no Leitfrage; the closing page is never left out
{
  const fits = { '3:k1': 'no' };
  const pages = [{ gates: [], question: '3' }, { gates: [K1], question: null }, { gates: [K1], question: null }, { gates: [K1], question: null }];
  const gate = Gate.make(pages, true, () => fits, () => []);
  const to = gate.forward(1);
  assert.equal(to.to, 3);
  assert.deepEqual(JSON.parse(JSON.stringify(gate.count(to.skipped))), { questions: 0, pages: 2 });
  count++;
}
console.log('ok ' + count);
