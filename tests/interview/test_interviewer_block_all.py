"""All interviewer links of a guide blocked or unblocked at once (app/interviewer.py, change_all; Ralf 2026-11-27),
and blocking as a step for "Rückgängig" (part blocked:<id> of app/undo.py), for one link and for all. Nothing is
deleted: key, rights, expiry and interviews stay, and a link of before keeps working after unblocking."""
from datetime import timedelta

from flask import url_for

from app.extensions import db
from app.models import InterviewerAccess, utcnow
from tests.interview.test_interview import make
from tests.interview.test_interviewer import add, begin, open_link


def _all(app):
    with app.app_context():
        return {a.name: a for a in db.session.scalars(db.select(InterviewerAccess))}


def _blocked(app):
    return {name: a.blocked for name, a in _all(app).items()}


def _undo_urls(app, sid):
    with app.test_request_context():
        return url_for("editor.undo", survey_id=sid), url_for("editor.redo", survey_id=sid)


def test_all_links_blocked_and_unblocked_at_once(app, client):
    code, sid = make(app, client)
    keys = {name: add(client, code, name) for name in ("Anna", "Bert", "Cem")}
    anna, _ = open_link(app, code, keys["Anna"])
    rid = begin(anna, code)
    before = _all(app)
    r = client.post(f"/guide/{code}/interviewers/all/block")
    assert r.status_code == 302 and r.headers["Location"].endswith("#interviewende")
    after = _all(app)
    assert all(a.blocked for a in after.values()) and len(after) == 3            # nothing deleted
    assert anna.get(f"/i/{code}/interviews").status_code == 302                    # the open session ended
    assert after["Anna"].devices == [] and after["Anna"].revision != before["Anna"].revision
    for name, key in keys.items():
        assert open_link(app, code, key)[1].status_code == 403
        # the link itself is the one of before: key, rights and expiry
        assert (after[name].token_hash, after[name].expires_at, after[name].may_record, after[name].may_code) == (
            before[name].token_hash, before[name].expires_at, before[name].may_record, before[name].may_code)
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert "Interviewlinks gesperrt: 3" in page and page.count("gesperrt</span>") == 3
    assert 'class="flash-undo"' in page                                            # the message carries "Rückgängig"
    client.post(f"/guide/{code}/interviewers/all/unblock")
    assert not any(_blocked(app).values())
    anna, r = open_link(app, code, keys["Anna"])                                    # the link of before opens again
    assert r.status_code == 302
    assert f"interview={rid}" in anna.get(f"/i/{code}/interviews").data.decode()    # with its interview


def test_blocking_all_takes_only_the_open_links_and_undo_restores_the_mix(app, client):
    code, sid = make(app, client)
    keys = {name: add(client, code, name) for name in ("Anna", "Bert", "Cem")}
    ids = {name: a.id for name, a in _all(app).items()}
    undo_url, redo_url = _undo_urls(app, sid)
    client.post(f"/guide/{code}/interviewers/{ids['Bert']}/block")
    bert_revision = _all(app)["Bert"].revision
    client.post(f"/guide/{code}/interviewers/all/block")
    assert _blocked(app) == {"Anna": True, "Bert": True, "Cem": True}
    assert _all(app)["Bert"].revision == bert_revision                             # already blocked: left as it was
    client.post(undo_url)                                                          # "Alle … gesperrt" back
    assert _blocked(app) == {"Anna": False, "Bert": True, "Cem": False}
    assert open_link(app, code, keys["Anna"])[1].status_code == 302
    assert open_link(app, code, keys["Bert"])[1].status_code == 403
    anna, _ = open_link(app, code, keys["Anna"])
    client.post(redo_url)                                                          # and again: sessions end again
    assert _blocked(app) == {"Anna": True, "Bert": True, "Cem": True}
    assert anna.get(f"/i/{code}/interviews").status_code == 302
    client.post(undo_url)
    client.post(undo_url)                                                          # Bert's own block back
    assert _blocked(app) == {"Anna": False, "Bert": False, "Cem": False}
    # unblocking all, with a mix, and back
    client.post(f"/guide/{code}/interviewers/{ids['Cem']}/block")
    client.post(f"/guide/{code}/interviewers/all/unblock")
    assert not any(_blocked(app).values())
    client.post(undo_url)
    assert _blocked(app) == {"Anna": False, "Bert": False, "Cem": True}


def test_single_block_and_unblock_are_steps(app, client):
    code, sid = make(app, client)
    key = add(client, code, "Anna")
    aid = _all(app)["Anna"].id
    undo_url, _ = _undo_urls(app, sid)
    client.post(f"/guide/{code}/interviewers/{aid}/block")
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert 'class="flash-undo"' in page and undo_url in page                       # "Rückgängig" right in the message
    with app.app_context():
        from app.models import EditStep

        assert db.session.scalar(db.select(EditStep).order_by(EditStep.id.desc())).label == "Interviewlink von „Anna“ gesperrt"
    r = client.post(undo_url)
    assert r.headers["Location"].endswith(f"/guide/{code}/interviews#interviewende")   # back on the page of the links
    assert not _all(app)["Anna"].blocked and open_link(app, code, key)[1].status_code == 302
    client.post(f"/guide/{code}/interviewers/{aid}/block")
    client.post(f"/guide/{code}/interviewers/{aid}/unblock")
    c, _ = open_link(app, code, key)
    client.post(undo_url)                                                          # the unblocking back: blocked, signed out
    assert _all(app)["Anna"].blocked and c.get(f"/i/{code}/interviews").status_code == 302


def test_undoing_rights_never_opens_a_link_blocked_since(app, client):
    """The block is a part of its own: a step of the rights from before does not carry it."""
    code, sid = make(app, client)
    add(client, code, "Anna")
    add(client, code, "Bert")
    anna = _all(app)["Anna"]
    undo_url, _ = _undo_urls(app, sid)
    client.post(f"/guide/{code}/interviewers/{anna.id}/rights", data={"has_interview": "1", "may_interview": "1"})
    with app.app_context():  # blocked on another way than a step of this person (an account's password change)
        from app import account_access

        account_access.block_interviewers(anna.created_by_id)
        db.session.commit()
    client.post(undo_url)                                                          # the rights back
    after = _all(app)["Anna"]
    assert after.may_record and after.blocked
    # and a step of blocking that no longer fits what is there is refused instead of opening anything
    client.post(f"/guide/{code}/interviewers/all/unblock")
    with app.app_context():
        account_access.block_interviewers(anna.created_by_id)
        db.session.commit()
    client.post(undo_url)
    assert _blocked(app) == {"Anna": True, "Bert": True}


def test_expired_links_stay_expired_and_other_guides_untouched(app, client):
    code, sid = make(app, client)
    other, _ = make(app, client, title="Zweiter Leitfaden")
    key = add(client, code, "Anna")
    add(client, code, "Bert")
    add(client, other, "Dora")
    with app.app_context():
        access = db.session.scalar(db.select(InterviewerAccess).filter_by(name="Anna"))
        access.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    client.post(f"/guide/{code}/interviewers/all/block")
    assert _blocked(app) == {"Anna": True, "Bert": True, "Dora": False}
    client.post(f"/guide/{code}/interviewers/all/unblock")
    assert not any(_blocked(app).values())
    assert open_link(app, code, key)[1].status_code == 403                         # still expired
    assert client.post(f"/guide/{code}/interviewers/all/delete").status_code == 404   # only these two actions
    assert len(_all(app)) == 3


def test_only_editors_block_all(app, client):
    code, sid = make(app, client)
    add(client, code, "Anna")
    add(client, code, "Bert")
    stranger = app.test_client()
    stranger.get("/auth/dev-login?orcid=0000-0000-0000-0028&name=Fremd")
    stranger.post("/terms/", data={"accept": "1"})
    assert stranger.post(f"/guide/{code}/interviewers/all/block").status_code in (403, 404)
    assert app.test_client().post(f"/guide/{code}/interviewers/all/block").status_code in (302, 401)   # not signed in
    assert not any(_blocked(app).values())


def test_the_dialog_names_the_numbers(app, client):
    code, sid = make(app, client)
    add(client, code, "Anna")
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert "iv-block-all" not in page                                              # one link: its own button is enough
    add(client, code, "Bert")
    add(client, code, "Cem")
    client.post(f"/guide/{code}/interviewers/{_all(app)['Cem'].id}/block")
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert 'data-dialog-open="iv-block-all"' in page
    assert "Von 3 Interviewlinks dieses Leitfadens sind 2 nicht gesperrt und 1 gesperrt." in page
    assert "Alle sperren (2)" in page and "Alle entsperren (1)" in page
    client.post(f"/guide/{code}/interviewers/all/block")
    page = client.get(f"/guide/{code}/interviews").data.decode()
    assert "Alle sperren (0)" in page and "Alle entsperren (3)" in page
