"""Accounts (app/accounts.py): the page "Konto", deleting an account (by the person, by a site admin) and blocking.
Deleting removes the person and what belonged to nobody else; shared surveys and groups stay with the others."""
import os

import pytest

from app import accounts, zenodo
from app.extensions import db
from app.models import (AiUsage, ConstructPick, Contact, DeletedAccount, Design, DesignEditor, DesignGroupShare, DesignShare,
                        EditMark, Feedback, Response, Survey, SurveyMember, SurveyPin, User, UserLogin,
                        WorkGroup, WorkGroupMember, ZenodoConnection, CoscineConnection)
from app import theme
from tests.conftest import allow

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"


@pytest.fixture

def other(app):
    """A second researcher who may create surveys, signed in on a client of their own."""
    allow(app, OTHER, THIRD)
    c = app.test_client()
    _as(c, OTHER, "Kollegin")
    return c


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _survey(client, title):
    client.post("/surveys/new", data={"title": title, "languages": ["de"], "default_language": "de"})
    with client.application.app_context():
        return db.session.scalar(db.select(Survey.id).filter_by(title=title))


def _answers(app, sid, n=2, status=None):
    """n real participations; optionally a new status of the survey."""
    with app.app_context():
        survey = db.session.get(Survey, sid)
        for _ in range(n):
            survey.responses.append(Response(status=Response.COMPLETED, source="real", answers={}))
        if status:
            survey.status = status
        db.session.commit()


def _user(orcid):
    return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _delete(client, name="Kollegin", **data):
    return client.post("/account/delete", data={"name": name, **data}, follow_redirects=True).data.decode()


def test_account_page(other):
    page = other.get("/account/").data.decode()
    assert "Kollegin" in page and "Konto löschen" in page and "Registriernummer" not in page
    with other.application.app_context():  # only the first twelve accounts show their number, behind the ORCID iD
        number = _user(OTHER).number
        assert number and number <= 12
    assert f"(Kontonummer {number})" in page
    with other.application.app_context():
        _user(OTHER).number = 13
        db.session.commit()
    assert "Kontonummer" not in other.get("/account/").data.decode()
    assert "Angemeldet als" not in other.get("/").data.decode()  # the name is in the menu already
    assert 'href="/account/"' in other.get("/").data.decode()  # in the header
    page = other.get("/account/delete").data.decode()
    assert "Wird gelöscht" in page and "Bleibt bestehen" in page and "Konto endgültig löschen" in page
    assert "Nachfolge" not in page and "Bitte zuerst klären" not in page


def test_delete_own_account(app, client, other):
    client.post("/groups/new", data={"name": "Team"})          # the admin knows the colleague as a contact
    client.post("/groups/1/members", data={"orcid": OTHER, "name": "Kollegin"})
    with app.app_context():
        uid, number = _user(OTHER).id, _user(OTHER).number
        assert db.session.scalar(db.select(Contact).filter_by(orcid=OTHER)) is not None
    other.post("/constructs/bfi10/favourite")
    assert "stimmt nicht" in _delete(other, name="Jemand anders")
    with app.app_context():
        assert _user(OTHER) is not None
        assert db.session.scalar(db.select(ConstructPick).filter_by(user_id=uid)).starred
    assert "Ihr Konto wurde gelöscht" in _delete(other, name="  kollegin ")
    assert other.get("/account/").status_code == 302  # signed out
    with app.app_context():
        assert _user(OTHER) is None
        assert db.session.scalar(db.select(Contact).filter_by(orcid=OTHER)) is None
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(orcid=OTHER)) is None
        gone = db.session.scalar(db.select(DeletedAccount))
        assert (gone.user_id, gone.number, gone.by) == (uid, number, "self")
        assert db.session.scalar(db.select(ConstructPick).filter_by(user_id=uid)) is None  # the starred constructs


def test_deleting_is_no_ban_and_ids_are_never_given_again(app, other):
    """The deleted account was the newest one. The next person gets neither its registration number nor its
    internal id: a browser that still holds the session of the deleted account must not become the new person."""
    stale = app.test_client()
    _as(stale, OTHER, "Kollegin")            # a second browser of the same person
    with app.app_context():
        uid, number = _user(OTHER).id, _user(OTHER).number
    _delete(other)
    _as(other, THIRD, "Dritte")
    with app.app_context():
        assert _user(THIRD).id > uid and _user(THIRD).number > number
    assert stale.get("/account/").status_code == 302
    _as(other, OTHER, "Kollegin")            # may still sign in: comes back with an empty account
    with app.app_context():
        assert _user(OTHER).id > uid and _user(OTHER).terms_version is not None


def test_delete_needs_a_fresh_sign_in(app, other):
    with other.session_transaction() as s:
        s["signed_in_at"] = s["signed_in_at"] - accounts.FRESH_SECONDS - 5
    page = other.get("/account/delete").data.decode()
    assert "Neu mit ORCID anmelden" in page and "Konto endgültig löschen" not in page
    assert "liegt zu lange zurück" in _delete(other)
    with app.app_context():
        assert _user(OTHER) is not None


def test_terms_not_accepted_still_allows_deleting(app):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    assert c.get("/").status_code == 302 or "Nutzungsbedingungen" in c.get("/", follow_redirects=True).data.decode()
    assert c.get("/account/delete").status_code == 200
    assert "Ihr Konto wurde gelöscht" in _delete(c)


def test_own_surveys_without_real_answers_go(app, other):
    sid = _survey(other, "Entwurf")
    with app.app_context():
        folder = os.path.join(app.config["UPLOAD_FOLDER"], db.session.get(Survey, sid).uid)
    os.makedirs(folder, exist_ok=True)
    page = other.get("/account/delete").data.decode()
    assert "1 Befragung ohne echte Teilnahmen" in page and "„Entwurf“" in page
    _delete(other)
    with app.app_context():
        assert db.session.get(Survey, sid) is None
    assert not os.path.exists(folder)


def test_own_surveys_with_real_answers_need_a_word(app, other):
    sid = _survey(other, "Studie")
    _answers(app, sid, 3, status=Survey.CLOSED)
    page = other.get("/account/delete").data.decode()
    assert "samt allen Antworten" in page and "3 Teilnahmen" in page and 'name="confirm_data"' in page
    assert "Bitte bestätigen Sie" in _delete(other)
    with app.app_context():
        assert _user(OTHER) is not None and db.session.get(Survey, sid) is not None
    assert "Ihr Konto wurde gelöscht" in _delete(other, confirm_data="1")
    with app.app_context():
        assert db.session.get(Survey, sid) is None
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 0


def test_a_running_survey_of_ones_own_blocks(app, other):
    sid = _survey(other, "Feld")
    _answers(app, sid, 1, status=Survey.RUNNING)
    page = other.get("/account/delete").data.decode()
    assert "Bitte zuerst klären" in page and "Konto endgültig löschen" not in page
    assert "Bitte beenden Sie zuerst" in _delete(other, confirm_data="1")
    with app.app_context():
        assert _user(OTHER) is not None and db.session.get(Survey, sid) is not None


def test_leaving_a_shared_draft_removes_the_entry(app, client, other):
    sid = _survey(client, "Gemeinsam")
    client.post(f"/surveys/{sid}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    page = other.get("/account/delete").data.decode()
    assert "Gemeinsam" in page and "wird entfernt" in page
    _delete(other)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert [(m.orcid, m.position) for m in survey.members] == [(ME, 0)]


def test_in_a_survey_with_real_answers_the_name_stays(app, client, other):
    """Participants consented under this name: the author entry stays, without account and without access."""
    sid = _survey(other, "Studie")
    other.post(f"/surveys/{sid}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    _answers(app, sid, 2, status=Survey.CLOSED)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert _keys(survey.consent_item.config["researchers"]) == [OTHER]
    assert "Der Name bleibt" in other.get("/account/delete").data.decode()
    assert "Ihr Konto wurde gelöscht" in _delete(other)  # no word needed: the survey stays with the other admin
    with app.app_context():
        survey = db.session.get(Survey, sid)
        former = survey.members[0]
        assert former.is_former and former.user_id is None and former.role == "viewer" and former.display_name == "Kollegin"
        assert [m.role for m in survey.members] == ["viewer", "admin"] and survey.created_by_id is None
        assert _keys(survey.consent_item.config["researchers"]) == ["former"]
        assert [m.display_name for m in survey.consent_item.kind.researchers(survey.consent_item)] == ["Kollegin"]
        creators = zenodo.metadata(survey)["creators"]
        assert "orcid" not in creators[0] and creators[1]["orcid"] == ME
        mid = former.id
    page = client.get(f"/surveys/{sid}").data.decode()
    assert ">ehemalig</span>" in page and f"orcid.org/{OTHER}" not in page
    # the entry gets no role, and the remaining admin can remove it
    assert "kann keine Rolle bekommen" in client.post(f"/surveys/{sid}/members/{mid}/role", data={"role": "admin"},
                                                    follow_redirects=True).data.decode()
    client.post(f"/surveys/{sid}/members/{mid}/remove")
    with app.app_context():
        assert [m.orcid for m in db.session.get(Survey, sid).members] == [ME]


def test_the_last_admin_names_a_successor(app, client, other):
    _as(app.test_client(), THIRD, "Dritte")  # only accounts that signed in can take over (tests/accounts/test_audit_b_accounts.py)
    first, second = _survey(other, "Eins"), _survey(other, "Zwei")
    for sid in (first, second):
        other.post(f"/surveys/{sid}/members", data={"orcid": ME, "name": "Philipp", "role": "viewer"})
        other.post(f"/surveys/{sid}/members", data={"orcid": THIRD, "name": "Dritte", "role": "editor"})
    page = other.get("/account/delete").data.decode()
    assert "Nachfolge" in page and f'name="survey_{first}"' in page and "Dritte (bisher Bearbeiten)" in page
    with app.app_context():
        third = db.session.scalar(db.select(SurveyMember.id).filter_by(survey_id=second, orcid=THIRD))
    _delete(other, **{f"survey_{second}": str(third)})
    with app.app_context():
        # without a choice: the next one in the author order
        assert [(m.orcid, m.role) for m in db.session.get(Survey, first).members] == [(ME, "admin"), (THIRD, "editor")]
        assert [(m.orcid, m.role) for m in db.session.get(Survey, second).members] == [(ME, "viewer"), (THIRD, "admin")]


def test_groups(app, client, other):
    _as(app.test_client(), THIRD, "Dritte")
    with app.app_context():
        for name in ("Allein", "Leitung", "Auflösen", "Mitglied"):
            db.session.add(WorkGroup(name=name))
        db.session.flush()
        alone, lead, dissolve, member = db.session.scalars(db.select(WorkGroup).order_by(WorkGroup.id)).all()
        alone.members.append(WorkGroupMember(orcid=OTHER, role="lead"))
        for g in (lead, dissolve):
            g.members.append(WorkGroupMember(orcid=OTHER, role="lead"))
            g.members.append(WorkGroupMember(orcid=THIRD, name="Dritte"))
            g.members.append(WorkGroupMember(orcid=ME, name="Philipp"))
        member.members.append(WorkGroupMember(orcid=ME, role="lead"))
        member.members.append(WorkGroupMember(orcid=OTHER))
        db.session.commit()
        ids = [g.id for g in (alone, lead, dissolve, member)]
    page = other.get("/account/delete").data.decode()
    assert "1 Gruppe ohne weitere Mitglieder" in page and "„Allein“" in page
    assert f'name="group_{ids[1]}"' in page and "niemand, die Gruppe löschen" in page
    _delete(other, **{f"group_{ids[2]}": "delete"})
    with app.app_context():
        assert db.session.get(WorkGroup, ids[0]) is None and db.session.get(WorkGroup, ids[2]) is None
        # the member who is in the group longest leads it now
        assert [(m.orcid, m.role) for m in db.session.get(WorkGroup, ids[1]).members] == [(THIRD, "lead"), (ME, "member")]
        assert [m.orcid for m in db.session.get(WorkGroup, ids[3]).members] == [ME]


def test_designs_and_everything_else_that_knows_the_person(app, client, other):
    sid = _survey(client, "Fremd")
    with app.app_context():
        user, admin = _user(OTHER), _user(ME)
        uid = user.id
        own = Design(owner_id=uid, name="Hausfarben", data=theme.standard())
        foreign = Design(owner_id=admin.id, name="Vom Admin", data=theme.standard())
        group = WorkGroup(name="Team")
        group.members.append(WorkGroupMember(orcid=ME, role="lead"))
        group.members.append(WorkGroupMember(orcid=OTHER))
        db.session.add_all([own, foreign, group])
        db.session.flush()
        own.shares.append(DesignShare(orcid=ME))
        own.group_shares.append(DesignGroupShare(group_id=group.id))
        foreign.shares.append(DesignShare(orcid=OTHER))
        foreign.editors.append(DesignEditor(orcid=OTHER))
        own.editors.append(DesignEditor(orcid=ME))
        db.session.add_all([
            Contact(owner_id=uid, orcid=THIRD, name="Dritte"),
            ZenodoConnection(user_id=uid, target="sandbox", access_token="x"),
            CoscineConnection(user_id=uid, token="x"),
            AiUsage(user_id=uid, day="2026-10-01", kind="text", count=3),
            SurveyPin(user_id=uid, survey_id=sid),
            EditMark(survey_id=sid, key="item:1", user_id=uid),
            Feedback(user_id=uid, message="Fehler auf der Seite"),
        ])
        db.session.commit()
        own_id, foreign_id = own.id, foreign.id
        folder = os.path.join(app.config["UPLOAD_FOLDER"], f"_design_{own_id}")
        progress = os.path.join(app.config["UPLOAD_FOLDER"], "progress")
    os.makedirs(folder)
    os.makedirs(progress, exist_ok=True)
    for name in (f"{uid}_abcdefgh.json", f"{uid}9_abcdefgh.json"):
        open(os.path.join(progress, name), "w").close()
    page = other.get("/account/delete").data.decode()
    assert "1 Design" in page and "Hausfarben" in page and "1 davon ist geteilt" in page and "Verbindung zu Zenodo" in page and "Das Coscine-Token" in page
    _delete(other)
    with app.app_context():
        assert db.session.get(Design, own_id) is None and db.session.get(Design, foreign_id) is not None
        assert db.session.scalar(db.select(db.func.count(DesignShare.id))) == 0
        assert db.session.scalar(db.select(db.func.count(DesignGroupShare.id))) == 0
        assert db.session.scalar(db.select(db.func.count(DesignEditor.id))) == 0
        for model in (ZenodoConnection, CoscineConnection, AiUsage, SurveyPin, EditMark, UserLogin):
            assert db.session.scalar(db.select(db.func.count()).select_from(model).filter_by(user_id=uid)) == 0, model
        assert db.session.scalar(db.select(db.func.count(Contact.id)).filter_by(owner_id=uid)) == 0
        feedback = db.session.scalar(db.select(Feedback))
        assert feedback.user_id is None and feedback.message == "Fehler auf der Seite"
    assert not os.path.exists(folder)
    assert os.listdir(progress) == [f"{uid}9_abcdefgh.json"]  # the file of another account stays


# --- site admins: block and delete ----------------------------------------------------------------------


def _uid(app, orcid):
    with app.app_context():
        return _user(orcid).id


def test_admin_blocks_and_unblocks(app, client, other):
    uid = _uid(app, OTHER)
    sid = _survey(other, "Bleibt")
    assert other.post(f"/admin/accounts/{uid}/lock", data={"action": "block"}).status_code == 403
    page = client.get("/admin/people").data.decode()
    assert "Sperren" in page and f"/admin/accounts/{uid}/delete" in page and "über die Konfiguration" in page
    r = client.post(f"/admin/accounts/{uid}/lock", data={"action": "block"}, follow_redirects=True).data.decode()
    assert "Kollegin ist gesperrt" in r and "Entsperren" in r and ">gesperrt<" in r  # row actions as buttons (W93)
    assert other.get("/account/").status_code == 302                      # signed out with the next request
    assert other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin").status_code == 403
    with app.app_context():
        assert db.session.get(Survey, sid) is not None                   # nothing else changes
    client.post(f"/admin/accounts/{uid}/lock", data={"action": "unblock"})
    _as(other, OTHER, "Kollegin")
    assert other.get("/account/").status_code == 200


def test_site_admins_are_managed_by_the_configuration(app, client):
    uid = _uid(app, ME)
    r = client.post(f"/admin/accounts/{uid}/lock", data={"action": "block"}, follow_redirects=True).data.decode()
    assert "ADMIN_ORCIDS" in r
    assert "ADMIN_ORCIDS" in client.get(f"/admin/accounts/{uid}/delete", follow_redirects=True).data.decode()
    with app.app_context():
        assert not accounts.is_blocked(_user(ME))
    assert client.get("/account/delete").status_code == 200  # the own account: on the page "Konto", like everyone


def test_admin_right_follows_the_configuration_without_a_new_sign_in(app, client, other):
    assert other.get("/admin/people").status_code == 403
    app.config["ADMIN_ORCIDS"] = [OTHER]                     # the .env was changed and the app restarted
    assert other.get("/admin/people").status_code == 200           # the session that was already signed in
    assert ">Administration</a>" in other.get("/account/").data.decode()   # a word in the menu, not an icon
    assert ">Administration</a>" not in client.get("/account/").data.decode()
    assert client.get("/admin/people").status_code == 403          # and who was taken off the list
    with app.app_context():
        assert _user(OTHER).is_admin and not _user(ME).is_admin


def test_admin_deletes_an_account_and_takes_over_its_data(app, client, other):
    uid = _uid(app, OTHER)
    keep, drop, field, draft = (_survey(other, t) for t in ("Behalten", "Weg", "Feld", "Entwurf"))
    _answers(app, keep, 2, status=Survey.CLOSED)
    _answers(app, drop, 1, status=Survey.CLOSED)
    _answers(app, field, 1, status=Survey.RUNNING)
    assert other.get(f"/admin/accounts/{uid}/delete").status_code == 403
    page = client.get(f"/admin/accounts/{uid}/delete").data.decode()
    assert "Konto von Kollegin löschen" in page and "übernehmen und archivieren" in page
    assert f'name="data_{keep}"' in page and f'name="data_{field}"' not in page and "wird übernommen" in page
    assert "stimmt nicht" in client.post(f"/admin/accounts/{uid}/delete", data={"name": "Philipp"},
                                         follow_redirects=True).data.decode()
    r = client.post(f"/admin/accounts/{uid}/delete", data={"name": "Kollegin", f"data_{drop}": "delete",
                                                           f"data_{field}": "delete"}, follow_redirects=True).data.decode()
    assert "Das Konto von Kollegin wurde gelöscht" in r
    assert other.get("/account/").status_code == 302
    with app.app_context():
        assert _user(OTHER) is None and db.session.scalar(db.select(DeletedAccount)).by == "admin"
        assert db.session.get(Survey, drop) is None and db.session.get(Survey, draft) is None
        kept, running = db.session.get(Survey, keep), db.session.get(Survey, field)
        for survey in (kept, running):  # a running survey is never deleted on the way, whatever the form says
            assert [(m.is_former, m.role) for m in survey.members] == [(True, "viewer"), (False, "admin")]
            assert survey.members[1].orcid == ME and survey.role_of(_user(ME)) == "admin"
        assert kept.archived and not running.archived and running.status == Survey.RUNNING
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=keep)) == 2


def _keys(refs):
    """The iDs a consent's "researchers" names: references by account since 2b ("u<id>"), "former" for the entry
    of a deleted account ("m<id>")."""
    from app.extensions import db
    from app.models import User

    out = []
    for r in refs:
        if r[:1] == "u" and r[1:].isdigit():
            user = db.session.get(User, int(r[1:]))
            out.append(user.orcid if user is not None else None)
        else:
            out.append("former" if r[:1] == "m" and r[1:].isdigit() else r)
    return out
