"""The bridge between the participant pages and the Android app of diary studies (architecture concept 2026-10-12,
K2): participant.js tells the app through window.AskuriousApp that an entry ended (done) and hands it the filled form
of a page kept for answering without a connection (offlineSubmit). The app's EntryActivity.Bridge offers exactly
these two functions; a renamed function or a changed form would lose answers without any error.

The tests open the real pages in a headless Chromium browser (Edge or Chrome, whichever is installed) with a stand-in
for the app's bridge that writes what it gets into the page, and send the form it got to the server as the app does
(AnswerQueue). Without such a browser they are skipped; they belong to the slow tests (run with --full)."""
import html as html_lib
import json
import os
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Enrolment, Response, Survey, utcnow
from tests.diary.test_diary import API, _active_from_today, _bearer, _enrol, _new, _prompt

pytestmark = pytest.mark.slow

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"


def _browser():
    """A Chromium-based browser for --headless --dump-dom: BROWSER_BIN, Edge, Chrome or Chromium."""
    for path in (os.environ.get("BROWSER_BIN"),
                 r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome"),
                 "/opt/pw-browsers/chromium"):
        if path and Path(path).is_file():
            return path
    return None


BROWSER = _browser()
needs_browser = pytest.mark.skipif(BROWSER is None, reason="needs a Chromium-based browser (Edge, Chrome)")
PROBE = '<html><body><script>var p = document.createElement("pre"); p.id = "probe"; p.textContent = "ran";'         ' document.body.appendChild(p);</script></body></html>'
_works = {}


def _dump(tmp_path, page):
    return subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                           f"--user-data-dir={tmp_path / 'profile'}", "--allow-file-access-from-files",
                           "--dump-dom", page.as_uri()],
                          capture_output=True, text=True, encoding="utf-8", timeout=90)


def _browser_runs(tmp_path):
    """Does the browser run a script on a page at all? Edge on one development machine ended --dump-dom with an empty
    page and exit code 0 (test suite concept 2026-10-18, B); then the tests skip, naming why, instead of failing on
    something no code here changes. A browser that runs this page but not the participant page still fails."""
    if BROWSER not in _works:
        page = tmp_path / "probe.html"
        page.write_text(PROBE, encoding="utf-8")
        try:
            done = _dump(tmp_path / "probe", page)
            _works[BROWSER] = ('<pre id="probe">ran</pre>' in done.stdout, done.stderr[-500:])
        except (OSError, subprocess.SubprocessError) as error:
            _works[BROWSER] = (False, str(error))
    return _works[BROWSER]

# the app's bridge as the WebView offers it (askurious-android EntryActivity.Bridge: done(), offlineSubmit(json)),
# set before participant.js runs; what it gets lands in <pre id="bridge-out"> for --dump-dom
STAND_IN = """<script>
window.__bridge = [];
window.AskuriousApp = {
  done: function () { window.__bridge.push({call: "done"}); },
  offlineSubmit: function (json) { window.__bridge.push({call: "offlineSubmit", json: json}); }
};
document.addEventListener("DOMContentLoaded", function () {
  %s
  var out = document.createElement("pre");
  out.id = "bridge-out";
  out.textContent = JSON.stringify(window.__bridge);
  document.body.appendChild(out);
});
</script>"""


def _run(tmp_path, html, action=""):
    """The page in the browser with the stand-in bridge; [{"call", "json"?}] as the bridge got them."""
    html = html.replace('"/static/', '"' + STATIC.as_uri() + "/")
    html = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + STAND_IN % action, html, count=1)
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")
    runs, why = _browser_runs(tmp_path)
    if not runs:
        pytest.skip(f"{BROWSER} --headless does not run even an empty test page here: {why or 'no output'}")
    done = _dump(tmp_path, page)
    found = re.search(r'<pre id="bridge-out">(.*?)</pre>', done.stdout, re.S)
    assert found, f"the page did not run to the end: {done.stderr[-2000:]}"
    return json.loads(html_lib.unescape(found.group(1)))


@needs_browser
def test_a_page_kept_offline_hands_its_form_to_the_app_and_the_server_takes_it(app, client, tmp_path):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=10)
    kept = phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).json
    assert kept["offline"] is True
    with app.app_context():
        field = f"i{next(i for b in db.session.get(Survey, 1).blocks for i in b.items if i.type == 'rating').id}"
        # the phone kept the page before the prompt came (an answer cannot begin before it)
        person = db.session.scalar(db.select(Enrolment))
        person.settings = {**(person.settings or {}), "offline_at": (utcnow() - timedelta(hours=1)).isoformat()}
        from app.models import BrowserReceipt
        for receipt in db.session.scalars(db.select(BrowserReceipt).filter_by(survey_id=person.survey_id,
                                                                              scope=f"offline:{person.id}")):
            receipt.data = {**receipt.data, "at": person.settings["offline_at"]}
        db.session.commit()
    # the participant picks the fourth answer and presses "Next", as on the phone
    calls = _run(tmp_path, kept["html"], f"""
      var form = document.querySelector("form[data-app-offline]");
      var pick = form.querySelector('input[name="{field}"][value="4"]');
      pick.checked = true;
      var next = form.querySelector('button[name="nav"][value="next"]') || form.querySelector('button[type="submit"], button:not([type])');
      form.requestSubmit(next);
    """)
    assert [c["call"] for c in calls] == ["offlineSubmit"], calls
    form = json.loads(calls[0]["json"])
    assert form[field] == ["4"] and form.get("nav") == ["next"]
    # the app sends it later just so (AnswerQueue.Item.body)
    sent = {"module": "m1", "due": due, "notified": due, "started": due + 60_000, "completed": due + 90_000,
            "version": kept["version"], "form": form}
    assert phone.post(f"{API}/offline", json=sent, headers=_bearer(secret)).json["status"] == "stored"
    with app.app_context():
        entry = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
        assert entry.status == Response.COMPLETED and entry.occasion["offline"] is True


@needs_browser
def test_the_end_of_an_entry_in_the_app_tells_the_app(app, client, tmp_path):
    from flask import render_template

    _new(app, client)
    with app.test_request_context("/"):
        survey = db.session.get(Survey, 1)
        in_app = render_template("participant/end.html", survey=survey, lang="de", in_app=True, status="completed",
                                 text="Danke!")
        in_browser = render_template("participant/end.html", survey=survey, lang="de", in_app=False,
                                     status="completed", text="Danke!")
    assert [c["call"] for c in _run(tmp_path, in_app)] == ["done"]
    # an entry in the browser has no app to tell
    assert _run(tmp_path, in_browser) == []
