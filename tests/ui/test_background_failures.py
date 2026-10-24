"""Failures in the background are said where they happen and as what they are (UI/UX audit 2026-10-24, B):
the construct box shows a failed step on its card inside the open dialog; a status, live view or preview that did not
arrive says how old the shown state is and offers "Erneut abrufen"; the place search tells a failing service from
"nothing found"; a page of a block that did not reach the server tells no internet from a silent server and says when
the tab could not keep it; the export hint no longer claims the results always leave marked ones out."""
import json
import shutil
from pathlib import Path

import pytest

from tests.helpers.bounded_run import run

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "app" / "static"
NODE = shutil.which("node")


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def node(script, *args):
    out = run([NODE, "-e", script, *args], timeout=60, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_construct_box_failures_stay_in_the_dialog():
    js = read("app.js")
    start = js.index("// Konstruktbox: the full construct box as a dialog")
    box = js[start:js.index("// The dashboard of Administration > Befragungen", start)]
    assert "actionFailed(" not in box  # the page message behind the modal dialog is not reachable
    assert 'note.setAttribute("role", "alert")' in box and "card.appendChild(note)" in box
    for text in ("Das Konstrukt wurde nicht eingefügt.", "Der Stern wurde nicht gespeichert.",
                 "Das Konstrukt wurde nicht in die Liste aufgenommen.", "Das Zurücknehmen hat nicht geklappt."):
        assert text in box
    # without an answer the step may have arrived: the page loads anew on closing instead of offering a second insert
    assert "changed = true;" in box[box.index("function failed"):box.index("content.addEventListener(\"click\"")]


def test_stale_note_for_background_states():
    js = read("app.js")
    assert "function stale(box, text, retry)" in js
    sem = js[js.index('document.querySelectorAll("[data-sem-running]")'):js.index('document.querySelectorAll("[data-power-progress]")')]
    assert "stale(box, " in sem and "failed = 0; poll();" in sem
    power = js[js.index('document.querySelectorAll("[data-power-progress]")'):]
    power = power[:power.index("});\n  });") + 9]
    assert ".catch(function () { box.remove(); })" not in power and "stale(box, " in power
    live = js[js.index("var seen = null;  // the time of the last state"):]
    assert "stale(box, seen ?" in live[:1200] and "r.ok" in live[:1200]
    preview = js[js.index("// Element editor: live preview."):]
    assert "stale(frame, " in preview[:4000]
    assert ".stale-note" in read("app.css")


SEARCH_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const a = src.indexOf("  function el(tag, attrs, text)"), b = src.indexOf("  // --- participants");
let reply = null;
function node(tag) {
  return { tag, children: [], hidden: true, disabled: false, textContent: "", style: {}, attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    addEventListener(t, f) { this["on" + t] = f; }, set innerHTML(v) { this.children = []; },
    get firstChild() { return this.children[0]; }, querySelector(s) { return this.parts[s]; } };
}
const document = { createElement: node, createTextNode: t => ({ textContent: t }), documentElement: { lang: "de" }, body: null };
const window = {};
function uiT(de, en, values) { let t = de; Object.keys(values || {}).forEach(k => { t = t.split("{" + k + "}").join(values[k]); }); return t; }
function setTimeout(fn) { fn(); return 1; }
function clearTimeout() {}
let lastSearch = 0;
function fetch() { return reply(); }
eval(src.slice(a, b) + "; globalThis.setupSearch = setupSearch;");
const flush = () => new Promise(r => setImmediate(r));
async function search(answer) {
  const bar = node("div"), list = node("ul"), input = node("input"), go = node("button"), box = node("div");
  bar.parts = { "[data-map-query]": input, "[data-map-go]": go };
  box.parts = { "[data-map-search]": bar, "[data-map-results]": list };
  setupSearch(box, {}, "/search", null);
  input.value = "Aachen"; reply = answer;
  go.onclick();
  for (let i = 0; i < 6; i++) await flush();
  const texts = list.children.map(c => c.children.length ? "" : c.textContent).filter(Boolean);
  return { list: texts, said: bar.children[0].textContent, disabled: go.disabled };
}
(async () => {
  const down = await search(() => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve([]) }));
  const none = await search(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) }));
  const gone = await search(() => Promise.reject(new TypeError("Failed to fetch")));
  console.log(JSON.stringify({ down, none, gone }));
})();
"""


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_place_search_tells_failure_from_no_hits():
    got = node(SEARCH_HARNESS, str(STATIC / "map_marker.js"))
    unavailable = "Die Ortssuche ist gerade nicht erreichbar. Bitte erneut versuchen oder die Karte verwenden."
    assert got["down"]["list"] == [unavailable] and got["down"]["said"] == unavailable
    assert got["gone"]["list"] == [unavailable]
    assert got["none"]["list"][0] == "Nichts gefunden." and got["none"]["said"] == "Nichts gefunden."
    assert not got["down"]["disabled"] and not got["none"]["disabled"]  # the button always comes back


SEND_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const a = src.indexOf("  function send(body) {"), b = src.indexOf("  // one block POST at a time");
let online = true, reply = null;
const navigator = { get onLine() { return online; } };
const window = { fetch: true, AbortController: null };
function setTimeout() { return 1; }
function clearTimeout() {}
const url = "/b", csrf = "";
function keep() {}
function queued() { return []; }
const location = { reload() {} };
function fetch() { return reply(); }
eval(src.slice(a, b) + "; globalThis.send = send;");
async function why(answer, on) {
  online = on; reply = answer;
  try { await send({ page: "1" }); return "sent"; } catch (e) { return e; }
}
(async () => {
  console.log(JSON.stringify({
    offline: await why(() => Promise.resolve({ ok: true }), false),
    server: await why(() => Promise.resolve({ ok: false, status: 503 }), true),
    nojson: await why(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new SyntaxError("x")) }), true),
    network: await why(() => Promise.reject(new TypeError("Failed to fetch")), true),
  }));
})();
"""


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_block_send_says_why():
    got = node(SEND_HARNESS, str(STATIC / "participant.js"))
    assert got == {"offline": "offline", "server": "server", "nojson": "server", "network": "network"}


def test_block_note_texts():
    js = read("participant.js")
    assert 'note.getAttribute(!kept ? "data-nostore" : why === "offline" ? "data-offline" : "data-unsent")' in js
    assert 'note.getAttribute("data-sent")' in js
    keep = js[js.index("  function keep(list) {"):js.index("  function enqueue(body)")]
    assert "return false" in keep and "return true" in keep  # a full or missing sessionStorage is reported
    page = (ROOT / "app" / "templates" / "participant" / "page.html").read_text(encoding="utf-8")
    for attr in ("data-offline=", "data-unsent=", "data-nostore=", "data-sent="):
        assert attr in page


def test_export_hint_on_marked_participations():
    page = (ROOT / "app" / "templates" / "surveys" / "data.html").read_text(encoding="utf-8")
    assert "immer weg" not in page and "always leave marked ones out" not in page
    assert "Markierte einbeziehen" in page
