"""A transcript page that still shows an older text does not write it over the stored one (user report
2026-11-17: a corrected transcript uploaded as a Word file showed Whisper's draft again; the page saves the whole
text with every change, also from a second tab or after "back", e.g. with the tick "Transkript geprüft")."""
import io
import re

from docx import Document

from app.extensions import db
from app.models import Response
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start

WHISPER = "P1-S1: Wie war das? #00:00:02-0#\n\nP1-S2: Anstrengent. #00:00:05-0#"
OWN = "I: Wie war das?\n\nB: Anstrengend."


def _world(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**row.interview, "transcript": {"state": "done", "text": WHISPER, "model": "small"}}
        db.session.commit()
    return code, rid, f"/guide/{code}/interviews/{rid}/transcript"


def _base(client, url):
    return re.search(r'name="base" value="([0-9a-f]+)"', client.get(url).get_data(as_text=True)).group(1)


def _tr(app, rid):
    with app.app_context():
        return db.session.get(Response, rid).interview["transcript"]


def _word(text):
    doc = Document()
    for line in text.split("\n\n"):
        doc.add_paragraph(line)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _upload(client, url, text=OWN):
    return client.post(f"{url}/upload", data={"file": (io.BytesIO(_word(text)), "korrigiert.docx")},
                       content_type="multipart/form-data", follow_redirects=True)


def _save(client, url, **data):
    return client.post(url, data={"autosave": "1", **data}, headers={"X-Requested-With": "fetch"})


def test_an_old_page_does_not_write_whispers_draft_over_the_upload(app, client):
    code, rid, url = _world(app, client)
    old = _base(client, url)                                             # the page as open before the upload
    page = _upload(client, url).get_data(as_text=True)
    assert "hochgeladen: korrigiert.docx" in page and "B: Anstrengend." in page
    # the old page (a second tab, "back"): its tick "Transkript geprüft" sends Whisper's text along
    r = _save(client, url, base=old, text=WHISPER, checked_shown="1", checked="1")
    assert r.json["status"] == "conflict" and "Upload" in r.json["who"]
    tr = _tr(app, rid)
    assert tr["text"] == OWN and tr["source"] == "upload" and not tr.get("checked")
    # so does its last save when the page is left, and a form sent without script
    assert _save(client, url, base=old, text=WHISPER, leaving="1").json["status"] == "conflict"
    r = client.post(url, data={"base": old, "text": WHISPER}, follow_redirects=True)
    assert "Nicht gespeichert" in r.get_data(as_text=True) and _tr(app, rid)["text"] == OWN
    # "Meine Fassung behalten": the page sends its text again on the stored one, knowingly
    assert _save(client, url, base=_base(client, url), text=WHISPER).json["status"] == "saved"
    assert _tr(app, rid)["text"] == WHISPER


def test_the_page_itself_goes_on_saving(app, client):
    code, rid, url = _world(app, client)
    _upload(client, url)
    first = _base(client, url)
    r = _save(client, url, base=first, text=OWN + " Ja.")
    assert r.json["status"] == "saved" and r.json["base"] != first      # the next save builds on this one
    assert _save(client, url, base=r.json["base"], text=OWN + " Ja. Sehr.").json["status"] == "saved"
    # left while that save was under way: the beacon still carries the base from before it (after_own)
    before = _base(client, url)
    assert _save(client, url, base=before, text=OWN + " Eins.").json["status"] == "saved"
    assert _save(client, url, base=before, text=OWN + " Eins. Zwei.", leaving="1", after_own="1").json["status"] == "saved"
    assert _tr(app, rid)["text"] == OWN + " Eins. Zwei."
    # not after an upload in between: the upload stays
    _upload(client, url, "I: Neu.")
    assert _save(client, url, base=before, text=OWN, leaving="1", after_own="1").json["status"] == "conflict"
    assert _tr(app, rid)["text"] == "I: Neu."
    # a form without the fingerprint (a script, "übergeben") saves as before
    assert _save(client, url, text="I: Ganz neu.").json["status"] == "saved"
