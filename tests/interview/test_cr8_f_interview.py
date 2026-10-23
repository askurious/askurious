"""Code review 2026-10-07, package F "Interview": the interview view, the recording device, the case summaries, the
AI assistant of the guide, Whisper and the automatic upload to Coscine (F10, F11, F14, F15, F18, G2, G6, I4, I11,
I12, I13 of code-review-2026-10-07.md)."""
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from app import ai, coscine
from app.extensions import db
from app.models import Response, Survey
from tests.exports.test_coscine import _basis_form, BASIS, connect, fake, on  # noqa: F401 - fake is a fixture
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start

WEBM = b"\x1a\x45\xdf\xa3"  # the first bytes of a WebM recording (app/items/voice.py sniff)

NODE = shutil.which("node")
STATIC = Path(__file__).parents[2] / "app" / "static"
TEMPLATES = Path(__file__).parents[2] / "app" / "templates"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


# --- F10: a note changed while a save is under way reaches the server ---------------------------------------------

_PUSH_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const a = src.indexOf("  var pushTimer = null"), b = src.indexOf("  if (saveUrl) setInterval");
if (a < 0 || b < 0) throw new Error("markers not found");
let timers = [], clock = 0, server = [], LATENCY = 1500;
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
  const body = JSON.parse(opts.body);
  return new Promise(ok => setTimeout(() => { server.push(body.notes.a); ok({ ok: true, json: () => ({ saved: String(clock) }) }); }, LATENCY));
}
const sessionStorage = { setItem() {} }, key = "k", saveUrl = "/x", csrf = "", savedNote = null;
const OM = false;  // not the offline mode (interview-offline.js)
const st = { states: {}, ticks: {}, notes: {}, marks: [], elapsed: 0, since: null, blocks: {}, dirty: false };
function de_en(de) { return de; }
function blockOf() { return null; }
eval(src.slice(a, b) + "; globalThis.api = { save, push };");
(async () => {
  st.notes.a = "erste Notiz"; api.save();          // t=0: the save leaves at 700, its answer comes at 2200
  await run(800);
  st.notes.a = "erste Notiz, ergänzt"; api.save();  // t=800: its timer fires at 1500, while the first is under way
  await run(1800);
  const dirtyBetween = st.dirty;                   // the first answer is not there yet
  await run(2300);
  const dirtyAfterOld = st.dirty;                  // the answer for the older state must not clear "dirty"
  await run(60000);
  let finished = false;
  st.notes.a = "dritte"; api.save();
  await run(60700);                               // the save of "dritte" is under way
  st.notes.a = "vierte"; api.push(() => { finished = server[server.length - 1]; });  // "Interview beenden" meanwhile
  await run(120000);
  console.log(JSON.stringify({ server, dirtyBetween, dirtyAfterOld, dirty: st.dirty, finished }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="needs Node to run the save logic of app/static/interview.js")
def test_f10_a_change_during_a_save_is_sent_afterwards(tmp_path):
    harness = tmp_path / "push.js"
    harness.write_text(_PUSH_HARNESS, encoding="utf-8")
    done = subprocess.run([NODE, str(harness), str(STATIC / "interview.js")], capture_output=True, check=True, timeout=60)
    result = json.loads(done.stdout.decode("utf-8"))
    assert result["server"][:2] == ["erste Notiz", "erste Notiz, ergänzt"]   # the second change is sent after the first
    assert result["dirtyBetween"] is True and result["dirtyAfterOld"] is True
    assert result["server"][-1] == "vierte" and result["finished"] == "vierte"  # the end waits for the newest state
    assert result["dirty"] is False


# --- F14, G2: Kurzfragebogen and Postskript ---------------------------------------------------------------------


def test_f14_g2_forms_show_refused_values_and_the_end_waits_for_them():
    js = read("interview.js")
    assert "d.errors" in js and "data-iv-form-error" in js and "has-error" in js
    assert "nicht gespeichert: Eine Angabe ist ungültig" in js
    finish = js[js.index('finishButton.addEventListener("click"'):]
    assert finish.index("formsSent()") < finish.index("finishButton.form.submit()")       # G2
    assert "clearTimeout(timer); return send();" in js                                     # a waiting form goes out now


def test_f14_the_server_names_the_refused_value(app, client):
    """The answer the script reads: errors per item id; the old value stays."""
    code, sid = make(app, client)
    rid = start(app, client, code)
    with app.app_context():
        from app import interview

        age = next(i for i in interview.shown_form(db.session.get(Survey, sid), "short").items if i.type == "text_input")
    assert not client.post(f"/guide/{code}/interviews/{rid}/form", data={f"i{age.id}": "57"}).json["errors"]
    r = client.post(f"/guide/{code}/interviews/{rid}/form", data={f"i{age.id}": "450"})
    assert r.status_code == 200 and r.json["errors"][str(age.id)]                # named at its question by interview.js
    with app.app_context():
        assert db.session.get(Response, rid).answers[str(age.id)] == 57          # the old value stays


# --- F11, F15, F18: the recording device ------------------------------------------------------------------------


def test_f11_pieces_kept_on_the_phone_are_sent_from_an_ended_interview():
    js = read("recorder.js")
    head = js[:js.index("function sendPiece")]
    assert "if (!toggle) return;" not in js and "canRecord" in head
    assert "if (canRecord) toggle.addEventListener" in js and "AskuriousAudioStore.open" in js
    page = (TEMPLATES / "interview" / "recorder.html").read_text(encoding="utf-8")
    ended = page[page.index("row.status == row.COMPLETED"):page.index("{% else %}")]
    assert ended.count("data-rec-state") == 2                    # the ended page and the one without consent


def test_f18_the_device_stops_reporting_once_stopped_is_confirmed():
    js = read("recorder.js")
    assert 'said === "stopped"' in js and "clearInterval(reporting)" in js
    assert "setInterval(report, 3000)" in js and js.count("reportEvery()") >= 3   # again with a new recording


def test_f15_a_409_without_a_part_is_final_and_named(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    url = f"/guide/{code}/interviews/{rid}/audio"
    send = lambda part, seq: client.post(f"{url}?part={part}&seq={seq}&start=0&rec=tab", data=WEBM + b"AAAA", content_type="audio/webm")  # noqa: E731
    assert send(1, 0).status_code == 200
    r = send(1, 2)
    assert r.status_code == 409 and r.json == {"ok": False, "error": "gap"} and "part" not in r.json
    r = send(2, 1)
    assert r.status_code == 409 and r.json["error"] == "gap"
    from app import interview_audio

    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**row.interview, "audio": [{"part": n, "file": f"teil-{n}.webm", "mime": "audio/webm", "bytes": 1,
                                                       "pieces": 1, "start": 0, "rec": "x"} for n in range(1, interview_audio.MAX_PARTS + 1)]}
        db.session.commit()
    r = send(interview_audio.MAX_PARTS + 1, 0)
    assert r.status_code == 409 and r.json["error"] == "parts"
    for name in ("interview.js", "recorder.js"):
        js = read(name)
        assert "status && status.final" in js and 'd.error === "parts"' in js


# --- G6: case summaries ---------------------------------------------------------------------------------------


def test_g6_summaries_retry_only_the_network_and_5xx():
    js = read("summaries.js")
    assert "refused = true" in js and "if (refused) return;" in js
    assert "r.status >= 400 && r.status < 500" in js


# --- I4: an AI error in a later step of the assistant keeps that step --------------------------------------------


def _boom(monkeypatch):
    def boom(*a, **k):
        raise ai.AIError("Der KI-Dienst ist gerade nicht erreichbar.")

    monkeypatch.setattr(ai, "generate_text", boom)


def test_i4_an_ai_error_in_subsume_shows_sort_again(app, client, monkeypatch):
    code, sid = make(app, client)
    _boom(monkeypatch)
    data = {"action": "to_subsume", "data": json.dumps({"questions": [{"text": "Frage A", "src": "own"}, {"text": "Frage B", "src": "own"}]}),
            "title": ["Block Eins", "Block Zwei"], "g0": "0", "g1": "1"}
    page = client.post(f"/guide/{code}/ai/develop", data=data).get_data(as_text=True)
    assert "nicht erreichbar" in page and "Block Eins" in page and "Block Zwei" in page and "Frage B" in page
    assert 'value="to_subsume"' in page                                   # the page "Sortieren" again


def test_i4_an_ai_error_in_sort_shows_check_again_with_the_choices(app, client, monkeypatch):
    code, sid = make(app, client)
    _boom(monkeypatch)
    state = {"questions": [{"text": "Frage A", "src": "own"}, {"text": "Frage B", "src": "ai"}],
             "checks": [{"issues": ["closed"], "suggestion": "Erzählen Sie von A", "why": "geschlossen"},
                        {"issues": [], "suggestion": "", "why": ""}]}
    data = {"action": "to_sort", "data": json.dumps(state), "c0": "suggest", "s0": "Erzählen Sie mehr von A", "c1": "drop"}
    page = client.post(f"/guide/{code}/ai/develop", data=data).get_data(as_text=True)
    assert 'value="to_sort"' in page and "Erzählen Sie mehr von A" in page and "geschlossen" in page
    assert 'name="c1" value="drop" checked' in page and 'name="c0" value="suggest" checked' in page


def test_i4_an_ai_error_in_check_keeps_the_questions(app, client, monkeypatch):
    code, sid = make(app, client)
    _boom(monkeypatch)
    page = client.post(f"/guide/{code}/ai/develop", data={"action": "to_check", "own": "Frage A\nFrage B",
                                                                "data": json.dumps({"questions": []})}).get_data(as_text=True)
    assert 'value="to_check"' in page and "Frage A\nFrage B" in page


# --- I11: no new start while the cancelled request to Whisper runs ------------------------------------------------


def test_i11_no_restart_while_the_earlier_whisper_request_lives(app, client, monkeypatch):
    from app import interview_transcribe
    from tests.interview.test_interview_robust import _whisper

    code, sid, rid, calls = _whisper(app, client, monkeypatch)
    release, inside = threading.Event(), threading.Event()

    def slow():
        inside.set()
        release.wait(10)
        return []

    with app.app_context():  # a request of a cancelled run, still waiting for Whisper in its thread
        left = threading.Thread(target=interview_transcribe._waiting, args=(app, rid, lambda tr: True, slow), daemon=True)
        left.start()
        assert inside.wait(5)
    try:
        assert interview_transcribe.busy(rid) and not interview_transcribe.busy(rid + 100)
        r = client.post(f"/guide/{code}/interviews/{rid}/transcribe", follow_redirects=True)
        assert "läuft noch" in r.get_data(as_text=True) and calls == []
    finally:
        release.set()
        left.join(5)
    assert not interview_transcribe.busy(rid)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    assert len(calls) == 1


# --- I12, I13: the automatic upload to Coscine ---------------------------------------------------------------------


def _auto_on(app, client, fake):
    on(client)
    code, sid = make(app, client)
    client.post(f"/guide/{code}/versions", data={"note": "ethik"})
    connect(client, fake)
    fake.profile = BASIS
    client.post(f"/coscine/survey/{sid}", data={**_basis_form(), "fassung:fassung-1.zip": "1"})
    client.post(f"/coscine/survey/{sid}/automatic", data={"auto": "1"})
    fake.files.clear()
    return code, sid


def test_i12_a_broken_off_automatic_upload_is_noted(app, client, fake, monkeypatch):
    code, sid = _auto_on(app, client, fake)

    def flaky(method, url, **kw):
        if method == "POST" and "checksums" in url:
            raise coscine.requests.ConnectionError("weg")
        return fake(method, url, **kw)

    monkeypatch.setattr(coscine.requests, "request", flaky)
    r = client.post(f"/guide/{code}/versions", data={"note": "zwei"}, follow_redirects=True)
    assert "Automatische Ablage in Coscine fehlgeschlagen" in r.get_data(as_text=True)
    with app.app_context():
        c = db.session.get(Survey, sid).coscine
    entry = c["uploads"][0]
    assert entry["partial"] is True and entry["auto"] is True and entry["failed"] == "checksums.sha256"
    assert "unvollständig" in c["auto"]["error"]
    assert "Abgebrochen: nur 1 von 2 Dateien" in client.get(f"/guide/{code}/export").get_data(as_text=True)


def test_i13_the_automatic_upload_runs_after_the_request(app, client, fake, monkeypatch):
    code, sid = _auto_on(app, client, fake)
    started = []

    class Later:
        def __init__(self, target, args, daemon, name):
            started.append((target, args, name))

        def start(self):
            pass

    monkeypatch.setattr(coscine.threading, "Thread", Later)
    monkeypatch.setattr(coscine, "auto_store", lambda survey, key, background=True, _real=coscine.auto_store: _real(survey, key, background))
    r = client.post(f"/guide/{code}/versions", data={"note": "zwei"}, follow_redirects=True)
    assert "läuft im Hintergrund" in r.get_data(as_text=True) and fake.files == {}   # nothing of Coscine in the request
    with app.app_context():
        assert db.session.get(Survey, sid).coscine["auto"]["running"]
    assert "Automatische Ablage läuft" in client.get(f"/guide/{code}/export").get_data(as_text=True)
    (target, args, name), = started
    assert name == f"coscine-auto-{sid}"
    target(*args)                                                         # the thread
    assert sorted(n.split("/", 3)[3] for n in fake.files) == ["checksums.sha256", "fassungen/fassung-2.zip"]
    with app.app_context():
        c = db.session.get(Survey, sid).coscine
    assert "running" not in c["auto"] and c["auto"]["on"] is True and c["uploads"][0]["auto"] is True
    assert "Automatische Ablage läuft" not in client.get(f"/guide/{code}/export").get_data(as_text=True)
