"""The interviewee's own screen for the tasks of an interview (app/interview_screen.py; concept
konzept-interview-formate.html, option B, stage 2, Ralf 2026-10-20: experimental, E8 changes marked): the link per
interview, one device, only the released task, the answer back into the interview, the marks in the case overview,
and the refusals (feature off, wrong key, other interview, second device, expiry, revoking, rate limit)."""
import re
from datetime import timedelta

from app.extensions import db
from app.models import Item, Response, Survey, utcnow
from tests.interview.test_interview_data import start
from tests.interview.test_interview_tasks import ranking_task


def _setup(app, client, feature=True):
    with app.app_context():  # a second guide of the same test: the helper finds a guide by its title
        for old in db.session.scalars(db.select(Survey).filter(Survey.interview.is_not(None))):
            old.title = f"Leitfaden {old.id}"
        db.session.commit()
    code, sid, q, tid = ranking_task(app, client)
    rid = start(app, client, code)
    return code, sid, tid, rid


def _connect(client, code, rid):
    r = client.post(f"/guide/{code}/interviews/{rid}/screen/connect")
    assert r.status_code == 200, r.data
    m = re.search(rf"/screen/{code}#([A-Za-z0-9_\-]+)$", r.json["link"])
    assert m and r.json["qr"].startswith("data:image/svg+xml;base64,")
    return m.group(1)


def _device(app, code, key, addr=None):
    c = app.test_client()
    if addr:  # the limits count per sender; the loopback address of the test client counts nobody
        c.environ_base["REMOTE_ADDR"] = addr
    r = c.post(f"/screen/{code}/open", data={"key": key})
    return c, r


def _ranking(tid, order=("2", "1", "3")):
    return {f"i{tid}": list(order), f"i{tid}_touched": "1"}


def _release(client, code, rid, tid):
    return client.post(f"/guide/{code}/interviews/{rid}/screen/release", json={"task": str(tid) if tid else ""})


def test_regular_for_everyone_without_a_switch(app, client):
    """Regular since 2026-10-22 (Ralf): no experimental feature needed any more."""
    code, sid, tid, rid = _setup(app, client, feature=False)
    view = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    assert "Teilnehmerbildschirm verbinden" in view and "interview-screen-view.js" in view
    from app import experimental

    assert "interview_screen" not in experimental.KEYS


def test_view_offers_the_screen_with_the_feature(app, client):
    code, sid, tid, rid = _setup(app, client)
    view = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    assert "Teilnehmerbildschirm verbinden" in view and "interview-screen-view.js" in view
    assert f'data-ivs-task="{tid}"' in view and "Auf dem Teilnehmerbildschirm zeigen" in view
    assert 'id="warum-interview_screen"' in client.get("/learn/glossary").data.decode()
    from tests.interview.test_interview import docx_text

    assert "Aufgaben auf Ihrem eigenen Gerät" in docx_text(client.get(f"/guide/{code}/export/einwilligung.docx").data)
    # a trial without saving has no interview to connect
    assert "data-ivs-view" not in client.get(f"/guide/{code}/conversation").data.decode()


def test_link_only_hash_stored_one_device(app, client):
    code, sid, tid, rid = _setup(app, client)
    key = _connect(client, code, rid)
    assert len(key) >= 40
    with app.app_context():
        screen = db.session.get(Response, rid).interview["screen"]
        assert key not in str(screen) and len(screen["h"]) == 64 and screen["d"] == "" and screen["show"] is None
    assert client.get(f"/guide/{code}/interviews/{rid}/screen/state").json["paired"] is False
    phone, r = _device(app, code, key)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/screen/{code}")
    page = phone.get(f"/screen/{code}").data.decode()
    assert "Gleich geht es los" in page and 'data-key="wait"' in page
    assert client.get(f"/guide/{code}/interviews/{rid}/screen/state").json["paired"] is True
    # the same browser again is welcome; a second device with the same link is refused
    assert phone.post(f"/screen/{code}/open", data={"key": key}).headers["Location"].endswith(f"/screen/{code}")
    other, r = _device(app, code, key)
    assert "problem=taken" in r.headers["Location"]
    assert "schon auf einem anderen Gerät" in other.get(r.headers["Location"]).data.decode()
    assert other.get(f"/screen/{code}/state").json["key"] == "ended"
    # the page itself shows nothing of the guide: no notes, no names, no recording
    with app.app_context():
        label = db.session.get(Response, rid).interview["label"]
    for private in ("Philipp", label, "Notiz", "Aufnahme", "data-iv-", "Wichtigkeit"):
        assert private not in page


def test_device_sees_only_the_released_task_and_its_answer_lands(app, client):
    code, sid, tid, rid = _setup(app, client)
    key = _connect(client, code, rid)
    phone, _ = _device(app, code, key)
    # nothing released: no task, an answer is refused
    assert "Wichtigkeit" not in phone.get(f"/screen/{code}").data.decode()
    assert phone.post(f"/screen/{code}/answer", data={"task": str(tid), "rev": "0", **_ranking(tid)}).status_code == 409
    client.post(f"/guide/{code}/interviews/{rid}/save", json={"elapsed": 600_000, "running": True})
    assert _release(client, code, rid, tid).json["show"] == tid
    state = phone.get(f"/screen/{code}/state").json["key"]
    assert state == f"task-{tid}-1"
    page = phone.get(f"/screen/{code}").data.decode()
    assert "Wichtigkeit" in page and "Preis" in page and 'name="rev" value="1"' in page
    # an answer to an old release or to another item is refused
    assert phone.post(f"/screen/{code}/answer", data={"task": str(tid), "rev": "0", **_ranking(tid)}).status_code == 409
    with app.app_context():
        other = next(i.id for i in db.session.get(Survey, sid).blocks[0].items if i.id != tid)
    assert phone.post(f"/screen/{code}/answer", data={"task": str(other), "rev": "1"}).status_code == 409
    r = phone.post(f"/screen/{code}/answer", data={"task": str(tid), "rev": "1", **_ranking(tid)})
    assert r.status_code == 200 and r.json["ok"], r.data
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.answers[str(tid)] == ["2", "1", "3"]
        mark = row.interview["tasks"][str(tid)]
        assert mark["by"] == "screen" and 600 <= mark["t"] < 700 and isinstance(mark["s"], int)
        assert "changed" not in mark
    st = client.get(f"/guide/{code}/interviews/{rid}/screen/state").json
    assert st["results"][str(tid)] == {"text": "1 Qualität, 2 Preis, 3 Marke", "by": "screen", "changed": False}
    # the person changes it again on the screen: replaced, still from the screen
    phone.post(f"/screen/{code}/answer", data={"task": str(tid), "rev": "1", **_ranking(tid, ("3", "2", "1"))})
    with app.app_context():
        assert db.session.get(Response, rid).answers[str(tid)] == ["3", "2", "1"]
    # taken back: the device waits again, its answers are refused
    _release(client, code, rid, None)
    assert phone.get(f"/screen/{code}/state").json["key"] == "wait"
    assert phone.post(f"/screen/{code}/answer", data={"task": str(tid), "rev": "2", **_ranking(tid)}).status_code == 409


def test_interviewer_change_is_marked(app, client):
    from app import interview_results

    code, sid, tid, rid = _setup(app, client)
    phone, _ = _device(app, code, _connect(client, code, rid))
    _release(client, code, rid, tid)
    phone.post(f"/screen/{code}/answer", data={"task": str(tid), "rev": "1", **_ranking(tid)})
    with app.app_context():
        task_code = db.session.get(Item, tid).code
        text = interview_results.overview_csv(db.session.get(Survey, sid), trials=True)
    values = dict(zip(*[line.split(";") for line in text.lstrip("﻿").strip().splitlines()[:2]]))
    assert values[f"{task_code}_quelle"] == "Teilnehmerbildschirm" and values[f"{task_code}_dauer_s"] != ""
    # the frame to change it holds the stored result; saving it from the view marks it
    edit = client.get(f"/guide/{code}/interviews/{rid}/screen/task/{tid}").data.decode()
    assert "data-ivs-edit-page" in edit and edit.index(">Qualität<") < edit.index(">Preis<")
    r = client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "task", "task": str(tid), "t": "", "by": "screen",
                                                                  **_ranking(tid, ("1", "2", "3"))})
    assert r.json["ok"]
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.answers[str(tid)] == ["1", "2", "3"] and row.interview["tasks"][str(tid)]["changed"] is True
        text = interview_results.overview_csv(db.session.get(Survey, sid), trials=True)
        notes = interview_results.notes_text(db.session.get(Survey, sid), row, "de")
    values = dict(zip(*[line.split(";") for line in text.lstrip("﻿").strip().splitlines()[:2]]))
    assert values[f"{task_code}_quelle"] == "Teilnehmerbildschirm, vom Interviewenden geändert"
    assert "(Teilnehmerbildschirm, vom Interviewenden geändert)" in notes
    assert client.get(f"/guide/{code}/interviews/{rid}/screen/state").json["results"][str(tid)]["changed"] is True
    page = client.get(f"/guide/{code}/interviews/analysis?probe=1").data.decode()
    assert "vom Interviewenden geändert" in page
    # a result entered in the view alone stays unmarked ("by" from the form is never read)
    code2, sid2, tid2, rid2 = _setup(app, client)
    client.post(f"/guide/{code2}/interviews/{rid2}/form", data={"part": "task", "task": str(tid2), "t": "5", "by": "screen",
                                                                **_ranking(tid2)})
    with app.app_context():
        assert db.session.get(Response, rid2).interview["tasks"][str(tid2)] == {"t": 5}


def test_wrong_key_other_interview_expiry_and_revoking(app, client):
    code, sid, tid, rid = _setup(app, client)
    key = _connect(client, code, rid)
    # a wrong key, a key on another guide's address
    c, r = _device(app, code, "x" * 43)
    assert "problem=unknown" in r.headers["Location"]
    code2, sid2, tid2, rid2 = _setup(app, client)
    c, r = _device(app, code2, key)
    assert "problem=unknown" in r.headers["Location"]
    # bound to one interview: the session of the device never opens another one
    phone, _ = _device(app, code, key)
    key2 = _connect(client, code2, rid2)
    _release(client, code2, rid2, tid2)
    assert phone.get(f"/screen/{code2}/state").json["key"] == "ended"
    assert phone.post(f"/screen/{code2}/answer", data={"task": str(tid2), "rev": "1", **_ranking(tid2)}).status_code == 409
    with phone.session_transaction() as s:  # a session forged to name the other interview
        s["ivs"] = {**s["ivs"], str(sid2): {**s["ivs"][str(sid)], "r": rid2}}
    assert phone.get(f"/screen/{code2}/state").json["key"] == "ended"
    # a new link ends the old device
    _connect(client, code, rid)
    assert phone.get(f"/screen/{code}/state").json["key"] == "ended"
    # "Bildschirm trennen"
    key = _connect(client, code, rid)
    phone, _ = _device(app, code, key)
    assert phone.get(f"/screen/{code}/state").json["key"] == "wait"
    assert client.post(f"/guide/{code}/interviews/{rid}/screen/disconnect").json["on"] is False
    assert phone.get(f"/screen/{code}/state").json["key"] == "ended"
    with app.app_context():
        assert "screen" not in db.session.get(Response, rid).interview
    # an unopened link holds OPEN_MINUTES, a connection HOURS
    from app import interview_screen

    key = _connect(client, code, rid)
    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**row.interview, "screen": {**row.interview["screen"], "made": (utcnow() - timedelta(minutes=interview_screen.OPEN_MINUTES + 1)).isoformat(timespec="seconds")}}
        db.session.commit()
    c, r = _device(app, code, key)
    assert "problem=ended" in r.headers["Location"]
    key = _connect(client, code, rid)
    phone, _ = _device(app, code, key)
    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**row.interview, "screen": {**row.interview["screen"], "made": (utcnow() - timedelta(hours=interview_screen.HOURS, minutes=1)).isoformat(timespec="seconds")}}
        db.session.commit()
    assert phone.get(f"/screen/{code}/state").json["key"] == "ended"
    # ending the interview ends the screen
    key = _connect(client, code, rid)
    phone, _ = _device(app, code, key)
    client.post(f"/guide/{code}/interviews/{rid}/end")
    assert phone.get(f"/screen/{code}/state").json["key"] == "ended"
    assert client.post(f"/guide/{code}/interviews/{rid}/screen/connect").status_code == 409


def test_foreign_researcher_and_interviewer_link(app, client):
    from tests.interview.test_interviewer import add, begin, open_link

    code, sid, tid, rid = _setup(app, client)
    # someone without a role on the guide
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0000-0000-002X&name=Fremd")
    other.post("/terms/", data={"accept": "1"})
    assert other.post(f"/guide/{code}/interviews/{rid}/screen/connect").status_code in (403, 404)
    # an interviewer without an account: offered as to the researchers
    key = add(client, code, "Anna Becker")
    c, _ = open_link(app, code, key)
    own = begin(c, code)
    view = c.get(f"/guide/{code}/conversation?interview={own}").data.decode()
    assert "Teilnehmerbildschirm verbinden" in view
    assert c.post(f"/guide/{code}/interviews/{own}/screen/connect").status_code == 200
    assert c.post(f"/guide/{code}/interviews/{rid}/screen/connect").status_code == 404   # not their interview


def test_rate_limits(app, client):
    code, sid, tid, rid = _setup(app, client)
    key = _connect(client, code, rid)
    app.config["TOKEN_TRY_LIMIT"] = 2
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = "198.51.100.7"
    from app import limits

    for _ in range(2):
        limits._asked.clear()  # a mere question is kept a few seconds in the process
        assert "problem=unknown" in c.post(f"/screen/{code}/open", data={"key": "nope"}).headers["Location"]
    limits._asked.clear()
    assert "problem=many" in c.post(f"/screen/{code}/open", data={"key": key}).headers["Location"]  # even the right one waits
    app.config["TOKEN_TRY_LIMIT"] = 20
    app.config["LIVE_VOTE_LIMIT"] = 1
    limits._asked.clear()
    phone, r = _device(app, code, key, addr="198.51.100.8")
    _release(client, code, rid, tid)
    assert phone.post(f"/screen/{code}/answer", data={"task": str(tid), "rev": "1", **_ranking(tid)}).status_code == 200
    assert phone.post(f"/screen/{code}/answer", data={"task": str(tid), "rev": "1", **_ranking(tid)}).status_code == 429


def test_device_routes_on_the_participants_side():
    from app import hosts

    assert hosts.side_of("interview_screen_public.answer") == hosts.PARTICIPANT
    assert hosts.side_of("interview_screen.connect_view") == hosts.RESEARCHER


def test_texts_for_the_device_in_every_language():
    from app.ui_i18n import tr

    assert tr("fr", "Gleich geht es los …", "Starting soon …") == "Ça commence bientôt …"
    assert tr("es", "Verbindung wird hergestellt …", "Connecting …") == "Conectando …"
    assert "Du kannst" in tr("de-du", "x", "Received. You can still change the result and send it again.")


def test_short_questionnaire_on_the_screen(app, client):
    """The Kurzfragebogen on the interviewee's own screen (Ralf 2026-10-22, A): all its questions at once, required
    questions checked there (E1), the answers in the interview marked as from the screen, a change in the view marked
    (E2); the Postskript never goes there (E3)."""
    import re as re_

    from app import interview

    code, sid, tid, rid = _setup(app, client)
    with app.app_context():
        short = interview.shown_form(db.session.get(Survey, sid), "short")
        age = next(i for i in short.items if i.type == "text_input")
        gender = next(i for i in short.items if i.type == "single_choice")
        option = gender.config["options"][0]["code"]
        age.answer_mode = "required"
        db.session.commit()
        age_id, gender_id = age.id, gender.id
    view = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    assert 'data-ivs-task="short"' in view and "Kurzfragebogen auf dem Teilnehmerbildschirm zeigen" in view
    key = _connect(client, code, rid)
    phone, _ = _device(app, code, key)
    assert client.post(f"/guide/{code}/interviews/{rid}/screen/release", json={"task": "post"}).status_code == 404
    assert client.post(f"/guide/{code}/interviews/{rid}/screen/release", json={"task": "short"}).json["show"] == "short"
    page = phone.get(f"/screen/{code}").data.decode()
    assert f'id="i{age_id}"' in page and f'id="i{gender_id}"' in page and 'name="task" value="short"' in page
    rev = re_.search(r'name="rev" value="(\d+)"', page).group(1)
    r = phone.post(f"/screen/{code}/answer", data={"task": "short", "rev": rev, f"i{gender_id}": option})
    assert r.status_code == 400 and str(age_id) in r.json["errors"]          # E1: the required question holds
    r = phone.post(f"/screen/{code}/answer", data={"task": "short", "rev": rev, f"i{age_id}": "57", f"i{gender_id}": option})
    assert r.json["ok"]
    with app.app_context():
        row = db.session.get(Response, rid)
        assert row.answers[str(age_id)] == 57 and row.interview["short_screen"]["by"] == "screen"
    state = client.get(f"/guide/{code}/interviews/{rid}/screen/state").json
    assert state["results"]["short"]["by"] == "screen" and not state["results"]["short"]["changed"]
    edit = client.get(f"/guide/{code}/interviews/{rid}/screen/task/short").data.decode()
    assert 'name="part" value="short"' in edit and 'value="57"' in edit
    client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "short", f"i{age_id}": "58", f"i{gender_id}": option})
    assert client.get(f"/guide/{code}/interviews/{rid}/screen/state").json["results"]["short"]["changed"] is True   # E2
