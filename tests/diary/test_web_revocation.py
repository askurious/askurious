"""Fresh audit W2: old links and browser grants cannot submit after withdrawal or credential changes."""
import json
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlsplit

import pytest

from app import diary, diary_access, diary_api, secrets_box
from app.extensions import db
from app.models import Block, BrowserReceipt, Enrolment, Response, Survey, utcnow
from tests.conftest import CONSENT_OK
from tests.diary.test_diary import (
    API,
    _active_from_today,
    _bearer,
    _enrol,
    _fields,
    _new,
    _path,
    _prompt,
)
from tests.diary.test_diary_rejoin import _codes, _personal


def pending(app, client, personal=False):
    code = _new(app, client)
    card = code
    if personal:
        _personal(client)
        client.post("/diary/1/invites", data={"n": "1"})
        card = f"{code}-{_codes(app, code)[0]}"
    phone = app.test_client()
    joined = phone.post(API + "/enrol", json={"code": card}).json
    if personal:  # the researchers release the code, so that it connects a new phone (Abgleich 2026-10-15, NB-1)
        client.post(f"/diary/1/invites/{_codes(app, code)[0]}/release")
    assert phone.get(_path(joined["url"])).status_code == 302
    page = phone.get(f"/s/{code}").get_data(as_text=True)
    return phone, code, card, joined, {**_fields(page), **CONSENT_OK, "i1_c_age": "1", "nav": "next"}


def snapshot(app):
    with app.app_context():
        r = db.session.scalar(db.select(Response).order_by(Response.id.desc()))
        return r.id, r.status, r.position, deepcopy(r.answers), r.completed_at


def test_leave_revokes_old_link_open_browser_and_replayed_cookie(app, client):
    phone, code, _, joined, form = pending(app, client)
    cookie = phone.get_cookie("session").value
    before = snapshot(app)
    assert phone.post(API + "/leave", json={}, headers=_bearer(joined["secret"])).status_code == 200
    assert app.test_client().get(_path(joined["url"])).status_code == 404
    assert phone.get(f"/s/{code}").status_code == 302
    assert phone.post(f"/s/{code}", data=form).status_code == 302
    assert phone.post(f"/s/{code}/crowd/1", data={}).status_code == 409
    replay = app.test_client()
    replay.set_cookie("session", cookie)
    assert replay.post(f"/s/{code}", data=form).status_code == 302
    assert snapshot(app) == before
    with app.app_context():
        assert db.session.scalar(db.select(Enrolment)).status == Enrolment.LEFT
        assert not db.session.scalar(db.select(BrowserReceipt).where(BrowserReceipt.scope.like("diary-web:%")))


def test_rejoining_at_the_same_timestamp_never_revives_old_access(app, client, monkeypatch):
    fixed = utcnow()
    monkeypatch.setattr(diary_api, "utcnow", lambda: fixed)
    phone, code, card, joined, form = pending(app, client, personal=True)
    replacement = app.test_client().post(API + "/enrol", json={"code": card}).json
    assert replacement["url"] != joined["url"]
    assert phone.post(f"/s/{code}", data=form).status_code == 302
    assert app.test_client().get(_path(joined["url"])).status_code == 404
    assert app.test_client().get(_path(replacement["url"])).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Response)).status == Response.ACTIVE


@pytest.mark.parametrize("action", ["pause", "end"])
def test_state_changes_revoke_modules_and_resume_does_not_revive_them(app, client, action):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=5)
    entry = phone.post(API + "/entry", json={"module": "m1", "due": due}, headers=_bearer(secret)).json
    phone.get(_path(entry["url"]))
    page = phone.get(f"/s/{code}").get_data(as_text=True)
    form = {**_fields(page), "nav": "next"}
    before = snapshot(app)
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        assert getattr(diary, action)(person)
        db.session.commit()
    assert phone.post(f"/s/{code}", data=form).status_code == 302
    assert app.test_client().get(_path(entry["url"])).status_code == 404
    if action == "pause":
        with app.app_context():
            assert diary.resume(db.session.scalar(db.select(Enrolment)))
            db.session.commit()
        assert app.test_client().get(_path(entry["url"])).status_code == 404
        assert phone.post(f"/s/{code}", data=form).status_code == 302
    assert snapshot(app) == before


def test_exit_remains_available_with_a_new_grant_after_end(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        block = Block(position=len(survey.blocks), titles={"de": "Abschluss"}, settings={"part": "exit"})
        block.items = [diary._mood_item(survey, "de")]
        survey.blocks.append(block)
        assert diary.end(db.session.scalar(db.select(Enrolment)))
        db.session.commit()
    entry = phone.post(API + "/entry", json={"module": "exit"}, headers=_bearer(secret))
    assert entry.status_code == 200
    assert phone.get(_path(entry.json["url"])).status_code == 302
    page = phone.get(f"/s/{code}")
    assert page.status_code == 200
    before = snapshot(app)
    assert phone.post(API + "/leave", json={}, headers=_bearer(secret)).json["status"] == Enrolment.LEFT
    assert app.test_client().get(_path(entry.json["url"])).status_code == 404
    assert phone.post(f"/s/{code}", data={**_fields(page.get_data(as_text=True)), "nav": "next"}).status_code == 302
    assert snapshot(app) == before


def test_grants_are_bounded_expire_and_keep_the_legacy_wire_format(app, client, monkeypatch):
    monkeypatch.setattr(diary_access, "MAX_LINKS", 3)
    phone, code, _, joined, _ = pending(app, client)
    urls = [joined["url"]]
    for _ in range(6):
        urls.append(phone.post(API + "/entry", json={"module": "entry"}, headers=_bearer(joined["secret"])).json["url"])
    assert len(set(urls)) == 7
    assert app.test_client().get(_path(urls[0])).status_code == 404
    assert phone.get(_path(urls[-1])).status_code == 302
    token = urlsplit(urls[-1]).path.rsplit("/", 1)[-1]
    with app.app_context():
        pid, stamp = json.loads(secrets_box.unseal(diary_api.PURPOSE, token))
        assert isinstance(pid, str) and isinstance(stamp, int) and len(token) < 400
        grants = db.session.scalars(db.select(BrowserReceipt).where(BrowserReceipt.scope.like("diary-web:%"))).all()
        assert len(grants) == 3
        assert all(token not in g.key and token not in json.dumps(g.data) for g in grants)
        for grant in grants:
            grant.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert app.test_client().get(_path(urls[-1])).status_code == 404
    with phone.session_transaction() as state:
        state["da1"] = {**state["da1"], "until": "2000-01-01T00:00:00"}
    assert phone.post(f"/s/{code}", data={"nav": "next"}).status_code == 302


def test_erasure_removes_all_web_grants(app, client):
    phone, _, _, joined, _ = pending(app, client)
    assert phone.post(API + "/erase", json={}, headers=_bearer(joined["secret"])).status_code == 200
    with app.app_context():
        assert not db.session.scalar(db.select(Enrolment))
        assert not db.session.scalar(db.select(BrowserReceipt).where(BrowserReceipt.scope.like("diary-web:%")))
    assert app.test_client().get(_path(joined["url"])).status_code == 404


def test_inflight_answer_commits_before_leave_and_cannot_write_afterwards(app, client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app import runtime

    phone, code, _, joined, form = pending(app, client)
    entered, release, leaving = Event(), Event(), Event()
    original_step, original_person = runtime.step, diary_api._person

    def hold_answer(survey, response, fields, lang, method, **kwargs):
        if method == "POST":
            entered.set()
            assert release.wait(10)
        return original_step(survey, response, fields, lang, method, **kwargs)

    def started_leave():
        leaving.set()
        return original_person()

    monkeypatch.setattr(runtime, "step", hold_answer)
    monkeypatch.setattr(diary_api, "_person", started_leave)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answer = pool.submit(phone.post, f"/s/{code}", data=form)
        assert entered.wait(10)
        leave = pool.submit(app.test_client().post, API + "/leave", json={}, headers=_bearer(joined["secret"]))
        try:
            assert leaving.wait(10)
            assert not leave.done()  # withdrawal waits on the answer transaction's participation lock
        finally:
            release.set()
        assert answer.result(timeout=10).status_code in (200, 302)
        assert leave.result(timeout=10).status_code == 200
    before = snapshot(app)
    assert phone.post(f"/s/{code}", data=form).status_code == 302
    assert snapshot(app) == before


@pytest.mark.parametrize("action", ["pause", "resume", "leave", "end"])
def test_state_changes_of_an_erased_person_do_not_restore_them(app, client, action):
    from concurrent.futures import ThreadPoolExecutor

    _, _, _, joined, _ = pending(app, client)
    with app.app_context():
        stale = db.session.scalar(db.select(Enrolment))
        with ThreadPoolExecutor(max_workers=1) as pool:
            erased = pool.submit(app.test_client().post, API + "/erase", json={}, headers=_bearer(joined["secret"]))
            assert erased.result(timeout=10).status_code == 200
        assert not getattr(diary, action)(stale)
        db.session.commit()
        assert not db.session.scalar(db.select(Enrolment))


def test_leave_after_concurrent_erasure_returns_unauthorised_not_a_server_error(app, client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from flask import request

    _, _, _, joined, _ = pending(app, client)
    located, release = Event(), Event()
    original_lock = diary_access.lock_person

    def hold_lookup(person):
        if request.path.endswith("/leave"):
            located.set()
            assert release.wait(10)
        return original_lock(person)

    monkeypatch.setattr(diary_access, "lock_person", hold_lookup)
    with ThreadPoolExecutor(max_workers=1) as pool:
        leave = pool.submit(app.test_client().post, API + "/leave", json={}, headers=_bearer(joined["secret"]))
        try:
            assert located.wait(10)
            assert app.test_client().post(API + "/erase", json={}, headers=_bearer(joined["secret"])).status_code == 200
        finally:
            release.set()
        assert leave.result(timeout=10).status_code == 401


def test_sync_cannot_overwrite_withdrawal_revision(app, client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from flask import request

    phone, code, _, joined, form = pending(app, client)
    synced, release, leaving = Event(), Event(), Event()
    original = diary_api._person

    def hold_sync():
        if request.path.endswith("/leave"):
            leaving.set()
        result = original()
        if request.path.endswith("/study"):
            synced.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(diary_api, "_person", hold_sync)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sync = pool.submit(app.test_client().post, API + "/study", json={"tz": "UTC"}, headers=_bearer(joined["secret"]))
        assert synced.wait(10)
        leave = pool.submit(app.test_client().post, API + "/leave", json={}, headers=_bearer(joined["secret"]))
        try:
            assert leaving.wait(10)
            assert not leave.done()
        finally:
            release.set()
        assert sync.result(timeout=10).status_code == 200
        assert leave.result(timeout=10).status_code == 200
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        assert person.status == Enrolment.LEFT
        assert person.settings["web_revision"] and person.settings["left_at"]
    assert phone.post(f"/s/{code}", data=form).status_code == 302


def test_waiting_old_secret_cannot_sync_after_reconnect(app, client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from flask import request

    _, _, card, joined, _ = pending(app, client, personal=True)
    located, release = Event(), Event()
    original = diary_access.lock_person

    def hold_authentication(person):
        if request.path.endswith("/study"):
            located.set()
            assert release.wait(10)
        return original(person)

    monkeypatch.setattr(diary_access, "lock_person", hold_authentication)
    with ThreadPoolExecutor(max_workers=1) as pool:
        sync = pool.submit(app.test_client().post, API + "/study", json={"tz": "UTC"}, headers=_bearer(joined["secret"]))
        try:
            assert located.wait(10)
            replacement = app.test_client().post(API + "/enrol", json={"code": card})
            assert replacement.status_code == 200
        finally:
            release.set()
        assert sync.result(timeout=10).status_code == 401


def test_known_uuid_cannot_issue_a_questionnaire_link_while_paused(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _active_from_today(app)
    due = _prompt(app, minutes_ago=5)
    body = {"module": "m1", "due": due, "uuid": "00000000-0000-4000-8000-000000000001"}
    assert phone.post(API + "/entry", json=body, headers=_bearer(secret)).status_code == 200
    with app.app_context():
        assert diary.pause(db.session.scalar(db.select(Enrolment)))
        db.session.commit()
    reply = phone.post(API + "/entry", json=body, headers=_bearer(secret))
    assert reply.status_code == 409 and reply.json["error"] == "paused"


def test_an_entry_link_belongs_to_the_browser_that_opened_it_first(app, client, monkeypatch):
    """Abgleich 2026-10-15, KR-12: within its twelve hours the address of an entry no longer opens in any number of
    browsers. The same browser (the app's WebView after the system ended it) opens it again; another one only within
    REOPEN_SECONDS of the first opening (a first reply lost on the way)."""
    phone, code, _, joined, _ = pending(app, client)
    link = _path(joined["url"])
    assert phone.get(link).status_code == 302  # again in the same browser
    assert app.test_client().get(link).status_code == 302  # another one right after the first: still possible
    monkeypatch.setattr(diary_access, "REOPEN_SECONDS", -1)
    assert app.test_client().get(link).status_code == 404
    assert phone.get(link).status_code == 302
    assert phone.get(f"/s/{code}").status_code == 200
