"""Interviews kodieren: coded passages when the transcript is corrected (app/transcript_coding_correction.py, Ralf
2026-10-30): the editor asks before saving a text that loses coded passages (also at an interviewer link, there with
only what the link may see; leaving the page and a form without script save as before), nearly the same wording is
found again close to the old place and the coding follows it, and "Neu zuordnen" gives a lost passage a new place
with its id, category, author, memos and anchor."""
import json
import re

from app import transcript_coding_correction as correction
from app.extensions import db
from app.models import CodingCategory, Response
from app.transcript_coding_view import place
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link
from tests.interview.test_transcript_coding import _guide, _new, _transcript
from tests.interview.test_transcript_coding_review import TEXT, _code, _sub

# --- finding again ------------------------------------------------------------------------------------------------


def test_nearly_the_same_words_close_to_the_old_place():
    quote = "Abends bin ich einfach fertig."
    fixed = "B: Abends bin ich einfach ganz fertig. Manchmal frage ich mich."
    s, e = place(fixed, {"quote": quote, "start": 3, "end": 33})
    assert fixed[s:e] == "Abends bin ich einfach ganz fertig."
    typo = "B: Abends bin ich einfch fertig. Manchmal"
    s, e = place(typo, {"quote": quote, "start": 3, "end": 33})
    assert typo[s:e] == "Abends bin ich einfch fertig."
    # too different, or too short to judge: not placed
    assert place("B: Morgens geht es mir gut. Manchmal", {"quote": quote, "start": 3, "end": 33}) is None
    assert place("B: Abend fertig.", {"quote": "Abends fertig.", "start": 3, "end": 17}) is None
    # a long passage (word by word) with one word corrected; quick also on a long transcript of common words
    long_quote = " ".join(f"wort{i}" for i in range(60))
    long_text = "x " * 50 + long_quote.replace("wort30", "korrigiert") + " y" * 50
    s, e = place(long_text, {"quote": long_quote, "start": 100, "end": 100 + len(long_quote)})
    assert long_text[s:e].startswith("wort0") and long_text[s:e].endswith("wort59")
    import time

    common = " ".join(["ich und du nicht mehr immer dann"] * 1500)
    t = time.process_time()
    place(common, {"quote": " ".join(["ich dann du"] * 120), "start": 5000, "end": 6000})
    assert time.process_time() - t < 2
    # of two alike places the nearer one
    text = "Abends bin ich einfach fertig! " + "x " * 400 + "Abends bin ich einfch fertig."
    s, _e = place(text, {"quote": "Abends bin ich einfach fertig.", "start": len(text) - 28, "end": len(text)})
    assert s > 400


# --- the editor ---------------------------------------------------------------------------------------------------


def _world(app, client):
    code, sid = _guide(app, client)
    iv, _ = open_link(app, code, add_interviewer(client, code, "Anna Neu"))
    rid = begin(iv, code)
    _transcript(app, rid, TEXT)
    pid = _new(client, code, start="empty")
    base = f"/guide/{code}/interviews/coding/{pid}"
    client.post(f"{base}/categories/new", data={"kind": "main", "name": "Belastung"})
    with app.app_context():
        main = db.session.scalar(db.select(CodingCategory.id).filter_by(project_id=pid, kind="main"))
    cat = _sub(client, base, main, "Erschöpfung")
    return code, pid, rid, base, cat, iv


def _save(who, code, rid, text, **extra):
    return who.post(f"/guide/{code}/interviews/{rid}/transcript", data={"text": text, "autosave": "1", **extra},
                    headers={"X-Requested-With": "fetch"})


def _stored(app, rid):
    with app.app_context():
        meta = db.session.get(Response, rid).interview
        return meta["transcript"]["text"], meta.get("codings") or []


def test_the_editor_asks_before_passages_are_lost(app, client):
    code, pid, rid, base, cat, iv = _world(app, client)
    _code(client, base, rid, "Abends", cat, 29)
    gone = TEXT.replace("Abends bin ich einfach fertig.", "Am Ende des Tages ist nichts mehr übrig.")
    r = _save(client, code, rid, gone)
    assert r.json["status"] == "ask" and r.json["field"] == "lost"
    assert "1 kodierte Stelle" in r.json["title"] and "Seminar · Erschöpfung" in r.json["note"]
    assert "Abends bin ich einfach fertig" in r.json["note"] and r.json["ok"] == "Trotzdem speichern"
    assert _stored(app, rid)[0] == TEXT                                              # nothing saved yet
    # a change that loses nothing saves at once
    assert _save(client, code, rid, TEXT + " Ja.").json["status"] == "saved"
    # "Trotzdem speichern": saved, the passage is "nicht mehr zuzuordnen"
    assert _save(client, code, rid, gone, asked="lost").json["status"] == "saved"
    assert _stored(app, rid)[0] == gone
    page = client.get(f"{base}/i/{rid}").data.decode()
    assert "Nicht mehr zuzuordnen" in page and 'data-tc-reassign="' in page


def test_leaving_the_page_and_a_form_without_script_save_as_before(app, client):
    code, pid, rid, base, cat, iv = _world(app, client)
    _code(client, base, rid, "Abends", cat, 29)
    gone = TEXT.replace("Abends bin ich einfach fertig.", "Ganz anders.")
    assert _save(client, code, rid, gone, leaving="1").json["status"] == "saved"     # the beacon of a page left
    assert _stored(app, rid)[0] == gone
    client.post(f"/guide/{code}/interviews/{rid}/transcript", data={"text": TEXT})
    _code(client, base, rid, "Manchmal", cat)
    r = client.post(f"/guide/{code}/interviews/{rid}/transcript", data={"text": "B: Nichts davon."}, follow_redirects=True)
    assert _stored(app, rid)[0] == "B: Nichts davon." and "lassen sich danach nicht mehr zuordnen" in r.data.decode()


def test_an_interviewer_link_learns_only_what_it_may_see(app, client):
    code, pid, rid, base, cat, iv = _world(app, client)
    _code(client, base, rid, "Abends", cat, 29)
    gone = TEXT.replace("Abends bin ich einfach fertig.", "Ganz anders.")
    r = _save(iv, code, rid, gone)
    assert r.json["status"] == "ask" and "1 kodierte Stelle" in r.json["title"]
    assert "Abends" not in r.json["note"] and "Erschöpfung" not in r.json["note"]    # not in the analysis: the number
    assert "Auswertungen, die Sie nicht sehen" in r.json["note"]
    assert _save(iv, code, rid, gone, asked="lost").json["status"] == "saved"


def test_a_corrected_passage_follows_with_its_anchor(app, client):
    code, pid, rid, base, cat, iv = _world(app, client)
    cid = _code(client, base, rid, "Abends", cat, 29)
    client.post(f"{base}/i/{rid}/codings/{cid}/anchor")
    fixed = TEXT.replace("einfach fertig", "einfach total fertig")
    assert _save(client, code, rid, fixed).json["status"] == "saved"                 # found again: no question
    _text, codings = _stored(app, rid)
    c = next(c for c in codings if c["id"] == cid)
    assert c["quote"] == "Abends bin ich einfach total fertig." and c["adjusted"] and fixed[c["start"]:c["end"]] == c["quote"]
    with app.app_context():
        assert db.session.get(CodingCategory, cat).anchor["quote"] == "Abends bin ich einfach total fertig."
    page = client.get(f"{base}/i/{rid}").data.decode()
    data = json.loads(re.search(r'id="tc-data">(.*?)</script>', page, re.S).group(1))
    assert data["codings"][0]["adjusted"] and "Nicht mehr zuzuordnen" not in page
    # a pure shift (text in front) moves the passage; "adjusted" stays from before
    _save(client, code, rid, "I: Wie war das?\n" + fixed)
    c = next(c for c in _stored(app, rid)[1] if c["id"] == cid)
    assert c["start"] == len("I: Wie war das?\n") + 3 and c["adjusted"]


def test_place_anew_keeps_the_coding(app, client):
    code, pid, rid, base, cat, iv = _world(app, client)
    from tests.interview.test_transcript_coding import _person
    from tests.interview.test_transcript_coding_review import BEA

    bea, _bid = _person(app)
    client.post(f"{base}/members", data={"kind": "person", "person": BEA})
    cid = _code(bea, base, rid, "Abends", cat, 29)
    client.post(f"{base}/i/{rid}/codings/{cid}/anchor")
    client.post(f"{base}/memos", data={"kind": "passage", "rid": rid, "coding": cid, "text": "wichtig"})
    new = TEXT.replace("Abends bin ich einfach fertig.", "Nachts kann ich nicht schlafen.")
    _save(client, code, rid, new, asked="lost")
    start = new.index("Nachts")
    url = f"{base}/i/{rid}/codings/{cid}/place"
    carl, _cid = _person(app, "0000-0001-5109-3700", "Carl")
    client.post(f"{base}/members", data={"kind": "person", "person": "0000-0001-5109-3700"})
    assert carl.post(url, json={"start": start, "end": start + 6}).status_code == 403   # not the author, not the team
    r = bea.post(url, json={"start": start, "end": start + 31})
    assert r.json["ok"] and r.json["coding"]["id"] == cid and r.json["coding"]["quote"] == "Nachts kann ich nicht schlafen."
    c = next(c for c in _stored(app, rid)[1] if c["id"] == cid)
    assert c["cat"] == cat and c["by"].startswith("u:") and c["reassigned"]
    with app.app_context():
        assert db.session.get(CodingCategory, cat).anchor["quote"] == "Nachts kann ich nicht schlafen."
    page = bea.get(f"{base}/i/{rid}").data.decode()
    assert "Nicht mehr zuzuordnen" not in page
    assert bea.post(url, json={"start": 0, "end": 0}).status_code == 400            # nothing marked
    assert bea.post(f"{base}/i/{rid}/codings/nope/place", json={"start": 0, "end": 5}).status_code == 404


def test_place_anew_stays_blind(app, client):
    from app.models import User
    from tests.interview.test_transcript_coding import _person
    from tests.interview.test_transcript_coding_double import _assign
    from tests.interview.test_transcript_coding_review import BEA

    code, pid, rid, base, cat, iv = _world(app, client)
    bea, _b = _person(app)
    carl, _c = _person(app, "0000-0001-5109-3700", "Carl")
    for orcid in (BEA, "0000-0001-5109-3700"):
        client.post(f"{base}/members", data={"kind": "person", "person": orcid})
    with app.app_context():
        ids = [db.session.scalar(db.select(User.id).filter_by(orcid=o)) for o in (BEA, "0000-0001-5109-3700")]
    _assign(app, client, base, rid, [f"u:{i}" for i in ids])
    cid = _code(carl, base, rid, "Abends", cat, 29)
    assert bea.post(f"{base}/i/{rid}/codings/{cid}/place", json={"start": 0, "end": 5}).status_code == 404


def test_lost_after_counts_only_placed_passages(app, client):
    code, pid, rid, base, cat, iv = _world(app, client)
    _code(client, base, rid, "Abends", cat, 29)
    with app.test_request_context():
        from app.models import Survey

        row = db.session.get(Response, rid)
        survey = db.session.get(Survey, row.survey_id)
        assert correction.lost_after(survey, row, TEXT)[0] == 0
        assert correction.lost_after(survey, row, "B: anders")[0] == 1
