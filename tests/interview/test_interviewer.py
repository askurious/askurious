"""Interviewers without an account (app/interviewer.py, concept konzept-interviewer-zugang-2026-10-15.md): personal
links, the interviewers' own area, the separation between interviewers, ending an access, handing over (decision E)."""
import io
import json
import re
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import InterviewerAccess, Response, Survey, utcnow
from tests.interview.test_interview import make
from tests.interview.test_interview_data import WEBM, start as researcher_start

HINTS = "2026-10-15"


@pytest.fixture
def links(app):
    return app


def add(client, code, name, **extra):
    """The researcher adds an interviewer; returns the key shown once on the page."""
    data = {"name": name, "days": "90", "devices": "3", "may_record": "1", "may_transcribe": "1", **extra}
    client.post(f"/guide/{code}/interviewers/new", data=data)
    page = client.get(f"/guide/{code}/interviews").data.decode()
    m = re.search(rf"/i/{code}#([A-Za-z0-9_\-]+)", page)
    return m.group(1) if m else None


def open_link(app, code, key, client=None, hints=True):
    c = client or app.test_client()
    r = c.post(f"/i/{code}", data={"key": key, **({"hints": HINTS} if hints else {})})
    return c, r


def begin(c, code, **extra):
    data = {"label": "", "date": "2026-10-15", "mode": "praesenz", "consent_form": "schriftlich", "consent": "1", "audio": "1", **extra}
    r = c.post(f"/i/{code}/interviews/new", data=data)
    m = re.search(r"interview=(\d+)", r.headers.get("Location", ""))
    return int(m.group(1)) if m else None


def access_of(app, name):
    with app.app_context():
        return db.session.scalar(db.select(InterviewerAccess).filter_by(name=name)).id


def test_link_shown_once_and_only_its_hash_stored(links, client):
    app = links
    code, sid = make(app, client)
    key = add(client, code, "Anna Becker")
    assert key and len(key) >= 40
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert key not in page and "Anna Becker" in page and "noch nicht geöffnet" in page   # shown once
    with app.app_context():
        access = db.session.scalar(db.select(InterviewerAccess))
        assert access.token_hash != key and len(access.token_hash) == 64 and access.created_by_name == "Philipp"
        assert access.max_devices == 3 and (access.expires_at - utcnow()).days in (89, 90)
    assert add(client, code, "anna becker") is None                                   # the same name twice


def test_interviewer_conducts_own_interview(links, client):
    app = links
    code, sid = make(app, client)
    key = add(client, code, "Anna Becker")
    c = app.test_client()
    assert "Hinweise für Interviewende" in c.get(f"/i/{code}").data.decode()
    _, r = open_link(app, code, key, client=c, hints=False)
    assert r.status_code == 400                                                        # hints not confirmed
    _, r = open_link(app, code, key, client=c)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/i/{code}/interviews")
    home = c.get(f"/i/{code}/interviews").data.decode()
    assert 'value="AB-01"' in home and "Interviewende: Anna Becker" in home and "Mit ORCID anmelden" not in home
    rid = begin(c, code)
    assert rid
    view = c.get(f"/guide/{code}/conversation?interview={rid}")
    assert view.status_code == 200 and f"/guide/{code}/interviews/{rid}/save" in view.data.decode()
    assert f'href="/i/{code}/interviews"' in view.data.decode()                       # back to the own list
    r = c.post(f"/guide/{code}/interviews/{rid}/save", data=json.dumps({"elapsed": 60_000}), content_type="application/json")
    assert r.json["ok"]
    end = c.post(f"/guide/{code}/interviews/{rid}/end").data.decode()
    assert "Löschcode" in end and f"/i/{code}/interviews" in end and "an die Forschenden" in end
    assert c.post(f"/guide/{code}/interviews/{rid}/resume").status_code == 302
    with app.app_context():
        row = db.session.get(Response, rid)
        aid = access_of(app, "Anna Becker")
        assert row.interview["access"] == aid and row.interview["by_name"] == "Anna Becker" and row.interview["by"] is None
        assert row.interview["label"] == "AB-01" and row.interview["elapsed"] == 60
    # the researchers see it, marked as conducted with a link
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert "AB-01" in page and "Anna Becker" in page
    assert "AB-01" in client.get(f"/guide/{code}/interviews?by={aid}").data.decode()
    assert "AB-01" not in client.get(f"/guide/{code}/interviews?by=team").data.decode()


def test_no_way_into_the_researchers_pages(links, client):
    app = links
    code, sid = make(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    for url in (f"/guide/{code}", f"/guide/{code}/interviews", f"/guide/{code}/settings", f"/guide/{code}/export",
                f"/guide/{code}/results", f"/guide/{code}/conversation", "/dashboard/"):
        r = c.get(url)
        assert r.status_code in (302, 404) and "/i/" not in r.headers.get("Location", ""), url
    assert c.post(f"/guide/{code}/interviewers/new", data={"name": "Mallory"}).status_code == 302  # to the sign-in
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(InterviewerAccess.id))) == 1


def test_interviewers_do_not_see_each_others_interviews(links, client):
    app = links
    code, sid = make(app, client)
    a, _ = open_link(app, code, add(client, code, "Anna"))
    b, _ = open_link(app, code, add(client, code, "Bert"))
    theirs = begin(b, code, trial="1")
    team = researcher_start(app, client, code)
    for rid in (theirs, team):
        base = f"/guide/{code}/interviews/{rid}"
        gets = [f"/guide/{code}/conversation?interview={rid}", f"{base}/audio/1", f"{base}/recorder", f"{base}/recorder/state",
                f"{base}/transcript", f"{base}/transcript.txt", f"{base}/transcribe/progress", f"{base}/recorder/qr.svg"]
        posts = [(f"{base}/save", {}), (f"{base}/form", {}), (f"{base}/end", {}), (f"{base}/resume", {}),
                 (f"{base}/transcribe", {}), (f"{base}/transcribe/cancel", {}), (f"{base}/transcript/draft", {}),
                 (f"{base}/recorder/stop", {}), (f"/i/{code}/interviews/{rid}/delete", {}), (f"{base}/delete", {})]
        for url in gets:
            assert a.get(url).status_code in (404, 302), url
        for url, data in posts:
            assert a.post(url, data=data).status_code in (404, 302), url
        r = a.post(f"{base}/audio?part=1&seq=0&start=0", data=WEBM + b"AA", content_type="audio/webm")
        assert r.status_code == 404
        r = a.post(f"{base}/transcript", data={"text": "fremd"})
        assert r.status_code == 404
        r = a.post(f"{base}/transcript/upload", data={"file": (io.BytesIO(b"x"), "x.txt")}, content_type="multipart/form-data")
        assert r.status_code == 404
    home = a.get(f"/i/{code}/interviews").data.decode()
    assert "BE-01" not in home and "I01" not in home
    with app.app_context():
        assert db.session.get(Response, theirs) is not None and db.session.get(Response, theirs).interview.get("transcript") is None


def test_recording_and_transcript_of_the_own_interview(links, client):
    app = links
    code, sid = make(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    rid = begin(c, code)
    url = f"/guide/{code}/interviews/{rid}/audio"
    assert c.post(f"{url}?part=1&seq=0&start=0", data=WEBM + b"AAAA", content_type="audio/webm").json["next"] == 1
    r = c.get(f"{url}/1")
    assert r.status_code == 200 and r.data == WEBM + b"AAAA"
    r.close()
    assert c.get(f"/guide/{code}/interviews/{rid}/recorder").status_code == 200
    assert "Als Aufnahmegerät" in c.get(f"/i/{code}/interviews").data.decode()
    up = c.post(f"/guide/{code}/interviews/{rid}/transcript/upload", data={"file": (io.BytesIO(b"I: Wie war das?\nB: Gut."), "t.txt")},
                content_type="multipart/form-data")
    assert up.status_code == 302 and up.headers["Location"].endswith(f"/guide/{code}/interviews/{rid}/transcript")
    page = c.get(f"/guide/{code}/interviews/{rid}/transcript").data.decode()
    assert "Transkript geprüft: übergeben" in page and 'name="checked" value="1" ' not in page
    c.post(f"/guide/{code}/interviews/{rid}/transcript", data={"text": "I: Wie war das?\nB: Gut, danke."})  # autosave
    with app.app_context():
        assert db.session.get(Response, rid).interview["transcript"]["text"] == "I: Wie war das?\nB: Gut, danke."
        assert not db.session.get(Response, rid).interview["transcript"].get("checked")


def test_handing_over_ends_the_interviewers_access_to_it(links, client):
    app = links
    code, sid = make(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    rid = begin(c, code)
    c.post(f"/guide/{code}/interviews/{rid}/transcript/upload", data={"file": (io.BytesIO(b"I: Frage\nB: Antwort"), "t.txt")},
           content_type="multipart/form-data")
    r = c.post(f"/guide/{code}/interviews/{rid}/transcript", data={"checked_shown": "1", "checked": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/i/{code}/interviews")
    with app.app_context():
        tr = db.session.get(Response, rid).interview["transcript"]
        assert tr["checked"] and tr["text"] == "I: Frage\nB: Antwort"                   # the text stays
    for url in (f"/guide/{code}/conversation?interview={rid}", f"/guide/{code}/interviews/{rid}/transcript",
                f"/guide/{code}/interviews/{rid}/audio/1"):
        assert c.get(url).status_code == 404, url
    home = c.get(f"/i/{code}/interviews").data.decode()
    assert "übergeben" in home and f"/interviews/{rid}/transcript" not in home
    assert client.get(f"/guide/{code}/interviews/{rid}/transcript").status_code == 200    # the researchers still do


def test_rights_of_an_access(links, client):
    app = links
    code, sid = make(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna", may_record="", may_transcribe=""))
    rid = begin(c, code)  # "Tonaufnahme erlaubt" ticked, but the access may not record
    with app.app_context():
        assert db.session.get(Response, rid).interview["consent"]["audio"] is False
    assert c.post(f"/guide/{code}/interviews/{rid}/audio?part=1&seq=0&start=0", data=WEBM, content_type="audio/webm").status_code == 404
    assert c.get(f"/guide/{code}/interviews/{rid}/transcript").status_code == 404
    assert "Tonaufnahme erlaubt" not in c.get(f"/i/{code}/interviews").data.decode()


def test_only_own_trial_interviews_are_deleted(links, client):
    app = links
    code, sid = make(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    real, trial = begin(c, code), begin(c, code, trial="1")
    assert c.post(f"/i/{code}/interviews/{real}/delete").status_code == 404
    assert c.post(f"/i/{code}/interviews/{trial}/delete").status_code == 302
    with app.app_context():
        assert db.session.get(Response, real) is not None and db.session.get(Response, trial) is None


def test_wrong_link_devices_and_ending_an_access(links, client):
    app = links
    code, sid = make(app, client)
    key = add(client, code, "Anna", devices="2")
    aid = access_of(app, "Anna")
    _, r = open_link(app, code, "x" * 43)
    assert r.status_code == 404 and "gilt nicht" in r.data.decode()
    one, _ = open_link(app, code, key)
    two, _ = open_link(app, code, key)
    _, r = open_link(app, code, key)
    assert r.status_code == 403 and "2 Geräten" in r.data.decode()                    # decision F: at most the devices set
    assert "2 / 2" in client.get(f"/guide/{code}/interviews").data.decode()
    client.post(f"/guide/{code}/interviewers/{aid}/signout")
    for c in (one, two):
        assert c.get(f"/i/{code}/interviews").headers["Location"].endswith(f"/i/{code}")  # every session ended
    one, _ = open_link(app, code, key)
    client.post(f"/guide/{code}/interviewers/{aid}/block")
    assert one.get(f"/i/{code}/interviews").status_code == 302
    _, r = open_link(app, code, key)
    assert r.status_code == 403
    client.post(f"/guide/{code}/interviewers/{aid}/unblock")
    one, r = open_link(app, code, key)
    assert r.status_code == 302
    client.post(f"/guide/{code}/interviewers/{aid}/link")                              # a new link
    new = re.search(rf"/i/{code}#([A-Za-z0-9_\-]+)", client.get(f"/guide/{code}/interviews").data.decode()).group(1)
    assert open_link(app, code, key)[1].status_code == 404 and one.get(f"/i/{code}/interviews").status_code == 302
    assert open_link(app, code, new)[1].status_code == 302
    with app.app_context():
        access = db.session.get(InterviewerAccess, aid)
        access.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert open_link(app, code, new)[1].status_code == 403
    client.post(f"/guide/{code}/interviewers/{aid}/extend", data={"days": "30"})
    assert open_link(app, code, new)[1].status_code == 302


def test_idle_session_and_archived_guide(links, client):
    app = links
    code, sid = make(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    assert c.get(f"/i/{code}/interviews").status_code == 200
    with c.session_transaction() as s:
        s["iv"][str(sid)]["t"] = (utcnow() - timedelta(minutes=61)).isoformat(timespec="seconds")
    assert c.get(f"/i/{code}/interviews").status_code == 302                           # an hour idle
    c, _ = open_link(app, code, add(client, code, "Bert"))
    with app.app_context():
        db.session.get(Survey, sid).archived = True
        db.session.commit()
    assert c.get(f"/i/{code}/interviews").status_code == 302


def test_idle_or_reopening_device_frees_its_place(links, client):
    """Code review 2026-10-15, K1: every opening after an idle hour added a device, until the tablet was refused."""
    app = links
    code, sid = make(app, client)
    key = add(client, code, "Anna", devices="1")
    aid = access_of(app, "Anna")

    def devices():
        with app.app_context():
            return db.session.get(InterviewerAccess, aid).devices

    c, _ = open_link(app, code, key)
    for day in range(4):                                                               # an idle hour each day
        with c.session_transaction() as s:
            s["iv"][str(sid)]["t"] = (utcnow() - timedelta(minutes=61)).isoformat(timespec="seconds")
        assert c.get(f"/i/{code}/interviews").status_code == 302 and devices() == []
        assert open_link(app, code, key, client=c)[1].status_code == 302
    assert len(devices()) == 1
    assert open_link(app, code, key, client=c)[1].status_code == 302 and len(devices()) == 1  # the same browser again
    assert open_link(app, code, key)[1].status_code == 403                            # another one: the place is taken
    c.post(f"/i/{code}/logout")
    assert devices() == []
    other, r = open_link(app, code, key)
    assert r.status_code == 302
    with app.app_context():                                                            # a device gone without a word
        access = db.session.get(InterviewerAccess, aid)
        access.devices = [{**access.devices[0], "last": (utcnow() - timedelta(minutes=62)).isoformat(timespec="seconds")}]
        db.session.commit()
    assert open_link(app, code, key)[1].status_code == 302 and len(devices()) == 1


def test_deleting_an_access_keeps_its_interviews(links, client):
    app = links
    code, sid = make(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    rid = begin(c, code)
    client.post(f"/guide/{code}/interviewers/{access_of(app, 'Anna')}/delete")
    assert c.get(f"/i/{code}/interviews").status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(InterviewerAccess.id))) == 0
        assert db.session.get(Response, rid).interview["by_name"] == "Anna"           # decision C


def test_accesses_go_with_the_guide(links, client):
    from app import lifecycle

    app = links
    code, sid = make(app, client)
    add(client, code, "Anna")
    with app.app_context():
        lifecycle.delete(db.session.get(Survey, sid), force=True)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(InterviewerAccess.id))) == 0


def test_several_interviewers_at_once(links, client):
    """A seminar at once (Ralf, 2026-10-15): one name per line or only a number; all links once on one page with
    a QR code each, names already there skipped, the same form sent again makes nothing twice."""
    app = links
    code, sid = make(app, client)
    add(client, code, "Anna Becker")
    r = client.post(f"/guide/{code}/interviewers/new", data={"names": "Ben Yilmaz\nanna becker\n\nCara Wolf\nBen Yilmaz",
                                                              "days": "30", "devices": "2", "may_record": "1", "batch": "b1"})
    page = r.data.decode()
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    keys = re.findall(rf"/i/{code}#([A-Za-z0-9_\-]+)", page)
    assert len(set(keys)) == 2 and page.count("<svg") >= 2 and "Ben Yilmaz" in page and "Cara Wolf" in page
    with app.app_context():
        made = db.session.scalars(db.select(InterviewerAccess).order_by(InterviewerAccess.id)).all()
        assert [a.name for a in made] == ["Anna Becker", "Ben Yilmaz", "Cara Wolf"]
        assert made[1].max_devices == 2 and not made[1].may_transcribe and (made[1].expires_at - utcnow()).days in (29, 30)
    assert "nicht noch einmal angelegt: anna becker" in page  # skipped, said so
    c, opened = open_link(app, code, keys[0])
    assert opened.status_code in (200, 302)
    again = client.post(f"/guide/{code}/interviewers/new", data={"names": "Dora", "batch": "b1"})
    assert again.status_code == 302
    r = client.post(f"/guide/{code}/interviewers/new", data={"names": "", "count": "3", "batch": "b2"})
    assert r.status_code == 200 and len(set(re.findall(rf"/i/{code}#([A-Za-z0-9_\-]+)", r.data.decode()))) == 3
    client.post(f"/guide/{code}/interviewers/new", data={"count": "1", "batch": "b3"})
    too_many = client.post(f"/guide/{code}/interviewers/new", data={"count": "51", "batch": "b4"})
    assert too_many.status_code == 302
    with app.app_context():
        names = [a.name for a in db.session.scalars(db.select(InterviewerAccess).order_by(InterviewerAccess.id))]
        assert names[3:] == ["Person 1", "Person 2", "Person 3", "Person 4"] and "Dora" not in names


def test_an_interviewer_session_deletes_no_recording(links, client):
    """Audit 2026-10-22, B-02: deleting a recording needs "Admin" on the guide (S22). An account without that role that
    also held an interviewer's session got its own row from row_for and deleted the recording of a real interview."""
    from tests.conftest import allow

    from app import interview_audio

    app = links
    code, sid = make(app, client)
    key = add(client, code, "Anna Becker")
    other = "0000-0002-1825-0097"
    allow(app, other, keep=True)
    b = app.test_client()
    b.get(f"/auth/dev-login?orcid={other}&name=B")
    b.post("/terms/", data={"accept": "1"})
    open_link(app, code, key, client=b)
    rid = begin(b, code)
    assert b.post(f"/guide/{code}/interviews/{rid}/audio?part=1&seq=0&start=0", data=WEBM + b"AAAA",
                  content_type="audio/webm").status_code == 200
    assert b.post(f"/guide/{code}/interviews/{rid}/audio/delete").status_code in (403, 404)
    with app.app_context():
        assert interview_audio.parts(db.session.get(Response, rid))
    assert client.post(f"/guide/{code}/interviews/{rid}/audio/delete").status_code == 302  # the guide's admin may
    with app.app_context():
        assert not interview_audio.parts(db.session.get(Response, rid))
