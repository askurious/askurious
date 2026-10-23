"""The interview view without a connection (Ralf 2026-10-22, options B, D, E, F): what has not reached the server waits in
localStorage as well (a closed tab loses nothing) and leaves it once the server has it, the status line says
"offline", and "Interview beenden" without a connection is kept in mind instead of asking for a second click."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
STATIC = Path(__file__).parents[2] / "app" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const a = src.indexOf("  var pushTimer = null"), b = src.indexOf("  if (saveUrl) setInterval");
if (a < 0 || b < 0) throw new Error("markers not found");
let timers = [], clock = 0, server = [], up = false;
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
function fetch(url, opts) {
  if (!up) return Promise.reject(new TypeError("Failed to fetch"));
  const body = JSON.parse(opts.body);
  server.push(body.notes.a);
  return Promise.resolve({ ok: true, json: () => ({ saved: String(clock) }) });
}
const navigator = { get onLine() { return up; } };
const store = {}, localStorage = { setItem(k, v) { store[k] = v; }, removeItem(k) { delete store[k]; } };
const sessionStorage = { setItem() {} }, key = "k", saveUrl = "/x", csrf = "";
const OM = false;  // not the offline mode (interview-offline.js)
const savedNote = { textContent: "", className: "" };
function local() { return localStorage; }
let swOn = false;
const st = { states: {}, ticks: {}, notes: {}, marks: [], elapsed: 0, since: null, blocks: {}, dirty: false, forms: {} };
function de_en(de) { return de; }
function blockOf() { return null; }
eval(src.slice(a, b) + "; globalThis.api = { save, push, setEnd(fn) { endFailed = fn; } };");
(async () => {
  let ended = 0, finished = false;
  api.setEnd(() => { ended += 1; });
  st.notes.a = "ohne Netz"; api.save();
  await run(1000);
  const keptOffline = store.k ? JSON.parse(store.k).notes.a : null;
  const line = savedNote.textContent;
  api.push(() => { finished = true; });           // "Interview beenden" while offline
  await run(2000);
  up = true; api.push();
  await run(3000);
  console.log(JSON.stringify({ keptOffline, line, ended, finished, server, keptAfter: "k" in store, dirty: st.dirty }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs Node to run the save logic of app/static/interview.js")
def test_offline_notes_wait_on_the_device_and_the_end_waits(tmp_path):
    harness = tmp_path / "offline.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    done = subprocess.run([NODE, str(harness), str(STATIC / "interview.js")], capture_output=True, check=True, timeout=60)
    result = json.loads(done.stdout.decode("utf-8"))
    assert result["keptOffline"] == "ohne Netz"                 # survives a closed tab
    assert result["line"].startswith("offline")                 # the status line, no banner
    assert result["ended"] == 1 and result["finished"] is False  # the end is kept in mind, not refused
    assert result["server"] == ["ohne Netz"] and result["dirty"] is False
    assert result["keptAfter"] is False                         # the device's copy goes once the server has it


def test_offline_wiring_in_the_interview_view():
    js = read("interview.js")
    assert 'window.addEventListener("online"' in js and "onBack.forEach" in js
    assert "endFailed = waitForConnection" in js
    assert "if (left && stuck()) { waitForConnection(); return; }" in js
    assert "if (!ok && stuck()) { waitForConnection(); return; }" in js
    forms = js[js.index('root.querySelectorAll("[data-iv-form]")'):]
    assert "st.forms[id] = draft(form)" in forms


def test_task_results_and_forms_are_kept_per_form_and_sent_again():
    """E: one key per form (they share their address), hidden task fields included, sent again on the next visit."""
    js = read("interview.js")
    assert 'form.getAttribute("action") + "|" + v("part") + "|" + v("task")' in js
    assert 'name !== "csrf_token"' in js and "replayDrafts();" in js and "onBack.push(replayDrafts)" in js


def test_storage_is_kept_and_low_space_is_shown():
    """D: the browser is asked to keep the waiting pieces, and little space left is named in the recording line."""
    store = read("audiostore.js")
    assert "navigator.storage.persist()" in store and "navigator.storage.estimate()" in store
    for name in ("interview.js", "recorder.js"):
        js = read(name)
        assert "AskuriousAudioStore.persist()" in js and "Speicher auf diesem Gerät fast voll" in js, name


def test_the_phone_recorder_says_offline_and_sends_when_back():
    """F: the recording page of the second device."""
    js = read("recorder.js")
    assert 'window.addEventListener("online", function () { netDown = false; draw(); sendPiece(); report(); });' in js
    assert "offline, die Stücke bleiben auf diesem Gerät und werden nachgeschickt" in js


# --- I, J, K, L, G (Ralf 2026-10-22, "alles bis auf h") ------------------------------------------------------------

WEBM = b"\x1a\x45\xdf\xa3"  # the first bytes of a WebM recording (app/items/voice.py sniff)


def _audio(app, code, rid):
    from app import interview_audio
    from app.extensions import db
    from app.models import Response, Survey

    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(code=code))
        row = db.session.get(Response, rid)
        full, _ = interview_audio.path(survey, row, 1)
        with open(full, "rb") as f:
            return f.read(), interview_audio.parts(row)[0]["pieces"]


def _batch(client, code, rid, pieces):
    import io

    data = {"meta": json.dumps([{"part": 1, "seq": seq, "rec": "tab", "start": 0} for seq, _ in pieces])}
    for n, (_, blob) in enumerate(pieces):
        data[f"p{n}"] = (io.BytesIO(blob), f"p{n}", "audio/webm")
    return client.post(f"/guide/{code}/interviews/{rid}/audio/batch", data=data, content_type="multipart/form-data")


def test_j_a_batch_of_pieces_is_stored_in_order_once(app, client):
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, _ = make(app, client)
    rid = start(app, client, code)
    assert client.post(f"/guide/{code}/interviews/{rid}/audio?part=1&seq=0&rec=tab&start=0", data=WEBM + b"A",
                       content_type="audio/webm").status_code == 200
    r = _batch(client, code, rid, [(1, b"BB"), (2, b"CC"), (3, b"DD")])
    assert r.status_code == 200 and r.json["next"] == 4
    assert _batch(client, code, rid, [(2, b"CC"), (3, b"DD")]).status_code == 200   # sent twice: already there
    assert _audio(app, code, rid) == (WEBM + b"ABBCCDD", 4)
    assert _batch(client, code, rid, [(6, b"EE"), (7, b"FF")]).status_code == 409   # a gap: the page goes piece by piece
    assert _audio(app, code, rid) == (WEBM + b"ABBCCDD", 4)


def test_k_the_connection_check_answers_and_keeps_nothing(app, client):
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, _ = make(app, client)
    rid = start(app, client, code)
    assert client.post(f"/guide/{code}/interviews/{rid}/netcheck", data=b"\0" * 98304,
                       content_type="application/octet-stream").json == {"ok": True}
    page = client.get(f"/guide/{code}/conversation?interview={rid}").get_data(as_text=True)
    assert f"/guide/{code}/interviews/{rid}/netcheck" in page and "data-iv-rec-save" in page


def test_i_a_script_without_sign_in_gets_401_not_the_sign_in_page(app, client):
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, _ = make(app, client)
    rid = start(app, client, code)
    other = app.test_client()
    r = other.post(f"/guide/{code}/interviews/{rid}/save", json={}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 401 and r.json["error"] == "signin"
    assert other.post(f"/guide/{code}/interviews/{rid}/save", json={}).status_code in (302, 401)
    js = read("interview.js")
    assert "signedOut = r.status === 401" in js and "Anmeldung abgelaufen" in js
    assert "seen(r)" in read("recorder.js")


def test_g_the_service_worker_is_served_for_the_interview_pages(app, client):
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, _ = make(app, client)
    rid = start(app, client, code)
    r = client.get("/guide/interview-sw.js")
    assert r.status_code == 200 and "javascript" in r.mimetype and "no-" in r.headers["Cache-Control"]
    sw = r.get_data(as_text=True)
    assert "X-Kept-At" in sw and "12 * 3600 * 1000" in sw and "data-offline-copy" in sw
    page = client.get(f"/guide/{code}/conversation?interview={rid}").get_data(as_text=True)
    assert 'data-sw="/guide/interview-sw.js"' in page
    js = read("interview.js")
    assert "!(offlineCopy && st.keptAt)" in js and "worker.postMessage({ drop: location.href })" in js


def test_l_saved_pieces_are_read_in_on_the_upload_page(app, client):
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, _ = make(app, client)
    rid = start(app, client, code)
    page = client.get(f"/guide/{code}/interviews/{rid}/audio/upload").get_data(as_text=True)
    assert "data-audio-rescue" in page and "audio-rescue.js" in page
    rescue = read("audio-rescue.js")
    assert '"askurious-audio-pieces"' in rescue and "moved[p.rec" in rescue
    assert "askurious-audio-pieces" in read("audiostore.js")


_FRONT = r"""
const fs = require("fs");
const window = {};
global.indexedDB = undefined;
eval(fs.readFileSync(process.argv[2], "utf8"));
const S = window.AskuriousAudioStore;
const a = { id: "a" }, b = { id: "b" };
const p = (rec, seq, size) => ({ rec, seq, type: "audio/webm", blob: { size } });
const out = {
  one: S.front([p(a, 0, 10)]),
  run: S.front([p(a, 3, 10), p(a, 4, 10), p(a, 5, 10), p(b, 0, 10)]).map(x => x.seq),
  gap: S.front([p(a, 3, 10), p(a, 5, 10)]),
  big: S.front([p(a, 0, 1500000), p(a, 1, 100000)]),
  many: S.front(Array.from({ length: 20 }, (_, n) => p(a, n, 10))).length,
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="needs Node to run app/static/audiostore.js")
def test_j_the_batch_takes_one_recording_without_gaps(tmp_path):
    harness = tmp_path / "front.js"
    harness.write_text(_FRONT, encoding="utf-8")
    done = subprocess.run([NODE, str(harness), str(STATIC / "audiostore.js")], capture_output=True, check=True, timeout=60)
    out = json.loads(done.stdout.decode("utf-8"))
    assert out == {"one": None, "run": [3, 4, 5], "gap": None, "big": None, "many": 12}
