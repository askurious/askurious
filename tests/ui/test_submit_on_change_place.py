"""A field that sends its form at once (data-submit-on-change) still applies at once, without an "Anwenden" button,
but the page loaded anew opens at the same place with the focus on the field just changed (UI/UX audit 2026-10-24,
D: Ergebnisse, Meine Befragungen). Not on another page, not after half a minute, not when the address names a place."""
import json
import shutil
from pathlib import Path

import pytest

from tests.helpers.bounded_run import run

NODE = shutil.which("node")
APP_JS = Path(__file__).parents[2] / "app" / "static" / "app.js"

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const a = src.indexOf("(function () {\n  var KEY = \"submit-on-change\";");
const b = src.indexOf("// Small things without inline code", a);
if (a < 0 || b < 0) throw new Error("markers not found");
const code = src.slice(a, b);
function page(path, hash, store, now) {
  const listeners = {}, focused = [];
  const field = { name: "group", value: "", form: { id: "view-form" }, type: "select-one",
                  matches: () => true, focus() { focused.push("group"); } };
  const form = { querySelectorAll: () => [field] };
  const env = {
    location: { pathname: path, hash },
    sessionStorage: { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } },
    window: { scrollY: 0, scrollTo(x, y) { env.window.scrollY = y; }, addEventListener() {} },
    document: { addEventListener(t, f) { listeners[t] = f; }, getElementById: id => (id === "view-form" ? form : null),
                querySelectorAll: () => [field] },
    Date: { now: () => now },
  };
  new Function("location", "sessionStorage", "window", "document", "Date", code)(
    env.location, env.sessionStorage, env.window, env.document, env.Date);
  return { change(y) { env.window.scrollY = y; listeners.change({ target: field }); }, y: () => env.window.scrollY, focused };
}
const out = {};
let store = {};
page("/surveys/abc/results", "", store, 1000).change(1800);
const back = page("/surveys/abc/results", "", store, 2000);
out.same = { y: back.y(), focused: back.focused };
store = {};
page("/surveys/abc/results", "", store, 1000).change(1800);
out.other = page("/surveys/abc/data", "", store, 2000).y();
store = {};
page("/surveys/abc/results", "", store, 1000).change(1800);
out.late = page("/surveys/abc/results", "", store, 40000).y();
store = {};
page("/surveys/abc/results", "", store, 1000).change(1800);
out.hash = page("/surveys/abc/results", "#sem", store, 2000).y();
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_place_and_focus_kept_after_instant_apply():
    res = run([NODE, "-e", HARNESS, str(APP_JS)], timeout=60, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout.strip().splitlines()[-1])
    assert got["same"] == {"y": 1800, "focused": ["group"]}
    assert got["other"] == 0 and got["late"] == 0 and got["hash"] == 0


def test_still_applies_at_once():
    js = APP_JS.read_text(encoding="utf-8")
    assert "form.requestSubmit" in js and 'el.matches("select[data-submit-on-change], input[data-submit-on-change]")' in js
