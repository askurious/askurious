"""Saving in the browser on the interview pages (code review 2026-10-15, package I): the case summaries send one
request at a time and the newest text last (I2), the interview view sends a state kept from before a reload and on
leaving (I5), and the queue of recorded pieces takes out the piece that was sent (I8)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
STATIC = Path(__file__).parents[2] / "app" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


_SUMMARIES = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
let timers = [], clock = 0, server = [], LATENCY = [3000, 500], calls = 0, listeners = {};
function setTimeout(fn, ms) { const t = { at: clock + ms, fn }; timers.push(t); return t; }
function clearTimeout(t) { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); }
const flush = () => new Promise(r => setImmediate(r));
async function run(until) {
  for (;;) {
    await flush(); await flush();
    timers.sort((x, y) => x.at - y.at);
    if (!timers.length || timers[0].at > until) { clock = until; await flush(); return; }
    const t = timers.shift(); clock = t.at; t.fn();
  }
}
class FormData { constructor() { this.v = {}; } append(k, v) { this.v[k] = v; } }
function fetch(url, opts) {
  const text = opts.body.v.text;
  const wait = LATENCY[Math.min(calls++, LATENCY.length - 1)];  // the first answer is slow, the next fast
  return new Promise(ok => setTimeout(() => { server.push(text); ok({ ok: true, status: 200 }); }, wait));
}
const area = { value: "", attrs: { "data-summary-url": "/s", "data-summary-key": "k" },
               getAttribute(n) { return this.attrs[n]; }, addEventListener(e, fn) { listeners[e] = fn; } };
const box = { getAttribute() { return ""; }, querySelectorAll() { return [area]; } };
const document = { querySelector(s) { return s === "[data-summaries]" ? box : null; } };
const window = { addEventListener(e, fn) { listeners["window:" + e] = fn; } };
function de_en(de) { return de; }
eval(src);
(async () => {
  area.value = "erste"; listeners.input();        // sent at 800, answered at 3800
  await run(1000);
  area.value = "erste und zweite"; listeners.input();   // its timer fires at 1800, while the first is under way
  await run(20000);
  const ordered = server.slice();
  area.value = "dritte"; listeners.input();       // not sent yet when the page is left
  listeners["window:pagehide"]();
  await run(40000);
  console.log(JSON.stringify({ ordered, server }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs Node to run app/static/summaries.js")
def test_summaries_send_one_at_a_time_and_on_leaving(tmp_path):
    harness = tmp_path / "summaries.js"
    harness.write_text(_SUMMARIES, encoding="utf-8")
    done = subprocess.run([NODE, str(harness), str(STATIC / "summaries.js")], capture_output=True, check=True, timeout=60)
    result = json.loads(done.stdout.decode("utf-8"))
    # the second text waits for the first answer: the server sees them in order and keeps the newest
    assert result["ordered"] == ["erste", "erste und zweite"]
    assert result["server"][-1] == "dritte"                    # leaving the page sends what still waits


def test_interview_view_sends_a_kept_state_and_on_leaving():
    js = read("interview.js")
    assert "if (saveUrl && st.dirty) push();" in js                                 # a state kept from before a reload
    leave = js[js.index('window.addEventListener("pagehide"'):]
    assert "keepalive" in leave[:400] and "st.dirty" in leave[:200]
    assert "if (st.dirty && (failing || pushing))" in js                            # asks before leaving
    forms = js[js.index('root.querySelectorAll("[data-iv-form]")'):]
    assert "if (running) { again = true; return running; }" in forms                # one form request at a time (I2)


def test_the_piece_sent_is_taken_out_where_it_stands():
    for name in ("interview.js", "recorder.js"):
        js = read(name)
        assert "queue.shift()" not in js and "queue.splice(at, 1)" in js, name
