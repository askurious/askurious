"""Names hidden within an organisation (app/names.py; concept konzept-organisation-sichtbarkeit-einladungen-2026-10-23.md,
E15-E23): members see each other as "Mitglied 12" without ORCID iD, also in the search, in groups, surveys, comments
and mails; the person, site admins, admins of the organisation and leads of the person's groups see the name;
participants see the people responsible by name. Without the setting nothing changes."""
from app import names, orgs
from app.extensions import db
from app.models import Organisation, OrgAlias, Survey, User, WorkGroup
from tests.conftest import allow

ME = "0000-0000-0000-001X"        # site admin
ANNA = "0000-0002-1825-0097"      # member, creates surveys
BERT = "0000-0000-0000-0124"      # member
ORG_ADMIN = "0000-0000-0000-0239"  # admin of the organisation


def _as(app, orcid, name):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


def _user(orcid):
    return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _main():
    return db.session.scalar(db.select(Organisation).filter_by(key=orgs.MAIN))


def _setup(app, mode="hidden"):
    allow(app, ANNA, BERT, ORG_ADMIN, keep=True)
    clients = {o: _as(app, o, n) for o, n in ((ANNA, "Anna Alpha"), (BERT, "Bert Beta"), (ORG_ADMIN, "Olga Org"))}
    with app.app_context():
        main = _main()
        orgs.add(main, _user(ORG_ADMIN), role="admin")
        main.names_mode = mode
        db.session.commit()
        orgs.forget()
    return clients


def _shared_survey(app, owner, other_orcid):
    owner.post("/surveys/new", data={"title": "Gemeinsam", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        sid = db.session.scalar(db.select(Survey.id).filter_by(title="Gemeinsam"))
        name = _user(other_orcid).name
    owner.post(f"/surveys/{sid}/members", data={"orcid": other_orcid, "name": name, "role": "editor"})
    with app.app_context():
        assert any(m.user_id == _user(other_orcid).id for m in db.session.get(Survey, sid).members)
    return sid


def test_nothing_changes_while_names_are_shown(app, client):
    clients = _setup(app, mode="shown")
    sid = _shared_survey(app, clients[ANNA], BERT)
    page = clients[BERT].get(f"/surveys/{sid}").data.decode()
    assert "Anna Alpha" in page and "Mitglied 1" not in page
    with app.app_context():
        assert db.session.scalar(db.select(OrgAlias.id)) is None


def test_members_see_each_other_as_numbers_without_orcid(app, client):
    clients = _setup(app)
    sid = _shared_survey(app, clients[ANNA], BERT)
    page = clients[BERT].get(f"/surveys/{sid}").data.decode()
    assert "Anna Alpha" not in page and f"orcid.org/{ANNA}" not in page   # neither name nor iD as a link
    assert "Mitglied " in page
    clients[ANNA].get(f"/surveys/{sid}")  # takes the message of adding, which repeats the name Anna typed
    own = clients[ANNA].get(f"/surveys/{sid}").data.decode()
    assert "Anna Alpha" in own                       # oneself by name (E18)
    assert "Bert Beta" not in own
    account = clients[BERT].get("/account/").data.decode()
    assert "sehen Sie als „Mitglied" in account
    with app.app_context():
        first = db.session.scalar(db.select(OrgAlias.number).filter_by(user_id=_user(ANNA).id))
    again = clients[BERT].get(f"/surveys/{sid}").data.decode()
    assert f"Mitglied {first}" in again               # the number stays


def test_admins_site_admins_and_leads_see_names(app, client):
    clients = _setup(app)
    with app.test_request_context("/dashboard/"):
        anna, bert, admin, me = _user(ANNA), _user(BERT), _user(ORG_ADMIN), _user(ME)
        assert names.is_hidden(anna, viewer=bert)
        assert not names.is_hidden(anna, viewer=admin)
        assert not names.is_hidden(anna, viewer=me)
        assert not names.is_hidden(anna, viewer=anna)
    # Bert leads a group with Anna: he sees her name
    bert = clients[BERT]
    bert.post("/groups/new", data={"name": "Kurs"})
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).filter_by(name="Kurs"))
    bert.post(f"/groups/{gid}/members", data={"orcid": ANNA, "name": ""})
    with app.test_request_context("/dashboard/"):
        assert not names.is_hidden(_user(ANNA), viewer=_user(BERT))
        assert names.is_hidden(_user(BERT), viewer=_user(ANNA))   # not the other way round


def test_the_search_finds_no_one_by_name(app, client):
    clients = _setup(app)
    sid = _shared_survey(app, clients[BERT], ORG_ADMIN)
    page = clients[BERT].get(f"/surveys/{sid}?q=Anna").data.decode()
    assert "Anna Alpha" not in page
    page = clients[BERT].get(f"/surveys/{sid}?q={ANNA}").data.decode()
    assert "Anna Alpha" not in page


def test_comments_show_the_alias_and_participants_the_name(app, client):
    clients = _setup(app)
    sid = _shared_survey(app, clients[ANNA], BERT)
    from flask_login import login_user
    from werkzeug.datastructures import MultiDict

    from app import comments
    from app.models import Block, PreviewComment

    with app.test_request_context():
        login_user(_user(ANNA))
        survey = db.session.get(Survey, sid)
        block = db.session.scalar(db.select(Block).filter_by(survey_id=sid).order_by(Block.id))
        comment, error = comments.add(survey, MultiDict({"text": "Frage 2 unklar", "block_id": str(block.id)}),
                                      _user(ANNA).display_name, "editor", author_id=_user(ANNA).id)
        assert not error and comment.author == "Anna Alpha"
        db.session.commit()
        cid = comment.id
    with app.test_request_context("/dashboard/"):   # a page of researchers ("/" is the public start page)
        login_user(_user(BERT))
        assert comments.as_dict(db.session.get(PreviewComment, cid))["author"].startswith("Mitglied")
        assert "Anna Alpha" not in comments.to_csv(db.session.get(Survey, sid))
    with app.test_request_context("/s/x"):
        assert _user(ANNA).display_name == "Anna Alpha"   # no signed-in viewer (a participant): the name


def test_organisation_admins_decide_when_the_platform_leaves_it_to_them(app, client):
    clients = _setup(app, mode="admins")
    with app.app_context():
        main_id = _main().id
        assert not names.hides(_main())
    admin = clients[ORG_ADMIN]
    assert "Namen der Mitglieder" in admin.get(f"/org/{main_id}/people").data.decode()
    admin.post(f"/org/{main_id}/people/names", data={"hidden": "1"})
    with app.app_context():
        assert names.hides(_main())
        main = _main()
        main.names_mode = "shown"
        db.session.commit()
    assert admin.post(f"/org/{main_id}/people/names", data={"hidden": "1"}).status_code == 403
    client.post(f"/admin/orgs/{main_id}", data={"action": "caps", "invite_scope": "create", "invite_in": "1",
                                                "names_mode": "hidden"})
    with app.app_context():
        assert _main().names_mode == "hidden"
