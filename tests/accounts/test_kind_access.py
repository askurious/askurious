"""Rights per survey type (app/kind_access.py, konzept-rechte-befragungstypen-2026-10-15.md, Ralf 2026-10-15 "alle wie
empfohlen"): Aus / Ansehen / Bearbeiten per account and type as a cap on the role, set by site admins, with a
default for new accounts, a choice per iD in "Person hinzufügen", a group preset and invitations that raise the type
of their survey. Accounts without an entry keep all four types."""
import pytest

from app.extensions import db
from app.models import SignInOrcid, Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
from tests.conftest import allow

OTHER = "0000-0002-1825-0097"
NEW = "0000-0000-0000-0124"
ALL_CREATE = {"survey": "create", "live": "create", "interview": "create", "diary": "create"}


def _as(client, orcid, name="Kollegin"):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})
    return client


def _uid(app, orcid=OTHER):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=orcid))


def _live(client):
    client.post("/presenter/new", data={"title": "Vorlesung"})
    with client.application.app_context():
        return db.session.scalar(db.select(Survey.id).filter(Survey.live.is_not(None)))


def _share(app, survey_id, role, orcid=OTHER):
    with app.app_context():
        s = db.session.get(Survey, survey_id)
        s.members.append(SurveyMember(orcid=orcid, user_id=_uid(app, orcid), name="Kollegin", role=role,
                                      position=len(s.members)))
        db.session.commit()


def _set(client, uid, kind, level):
    return client.post(f"/admin/accounts/{uid}", data={"action": "kind", "kind": kind, "level": level})


def _kinds(app, orcid=OTHER):
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).join(User, User.id == UserLogin.user_id).filter(User.orcid == orcid))
        return row.kinds if row is not None else None


def test_existing_accounts_keep_all_four_types(app, client, survey):
    allow(app, OTHER)
    guest = _as(app.test_client(), OTHER)
    page = guest.get("/dashboard/").data.decode()
    for target in ("/surveys/new", "/guide/new", "/presenter/new"):
        assert target in page
    assert _kinds(app) == ALL_CREATE                                     # let in by themselves: "create" for all four


def test_off_hides_the_type_everywhere(app, client, survey):
    allow(app, OTHER)
    live_id = _live(client)
    _share(app, live_id, "editor")
    guest = _as(app.test_client(), OTHER)
    assert "Vorlesung" in guest.get("/dashboard/").data.decode()
    _set(client, _uid(app), "live", "off")
    assert _kinds(app) == {**ALL_CREATE, "live": "off"}
    page = guest.get("/dashboard/").data.decode()
    assert "Vorlesung" not in page and "/presenter/new" not in page
    assert guest.get("/presenter/new").status_code == 404
    with app.app_context():
        code = db.session.get(Survey, live_id).code
    assert guest.get(f"/surveys/{code}", follow_redirects=True).status_code == 404
    docs = guest.get("/docs/kinds").data.decode()
    assert "19. Live-Abstimmung" not in docs and "18. Interviewleitfaden" in docs and "{kind:" not in docs
    public = app.test_client().get("/docs/kinds").data.decode()          # without an account: everything
    assert "19. Live-Abstimmung" in public and "{kind:" not in public


def test_view_caps_the_role_of_a_shared_survey(app, client, survey):
    allow(app, OTHER)
    _share(app, 1, "editor")
    guest = _as(app.test_client(), OTHER)
    _set(client, _uid(app), "survey", "view")
    assert guest.get("/surveys/1").status_code == 200
    guest.post("/surveys/1/editor/blocks/2/items", data={"type": "text"})  # "Bearbeiten" is needed: refused
    with app.app_context():
        assert not db.session.get(Survey, 1).blocks[1].items
    assert guest.get("/surveys/new").status_code == 404
    page = guest.get("/dashboard/").data.decode()
    assert "/surveys/new" not in page and "/presenter/new" in page
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.role_of(db.session.get(User, _uid(app))) == "viewer"


def test_off_for_questionnaires_takes_constructs_and_sample_size_out_of_the_header(app, client):
    allow(app, OTHER)
    guest = _as(app.test_client(), OTHER)
    assert "/constructs/" in guest.get("/dashboard/").data.decode()
    _set(client, _uid(app), "survey", "off")
    page = guest.get("/dashboard/").data.decode()
    assert "/constructs/" not in page and "/samplesize/" not in page


def test_site_admins_are_never_capped(app, client, survey):
    from app import kind_access

    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=app.config["ADMIN_ORCIDS"][0]))
        login = db.session.get(UserLogin, me.id) or UserLogin(user_id=me.id)
        login.kinds = {"survey": "off"}
        db.session.add(login)
        db.session.commit()
    assert client.get("/surveys/1/editor/").status_code == 200
    with app.test_request_context():
        assert kind_access.levels_of_orcid(app.config["ADMIN_ORCIDS"][0])["survey"] == "create"


def test_the_account_page_names_what_a_lower_level_takes(app, client, survey):
    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    _share(app, 1, "editor")
    page = client.get(f"/admin/accounts/{_uid(app)}").data.decode()
    assert 'id="befragungstypen"' in page
    assert "betrifft 1 Befragungen" in page or "affects 1 surveys" in page
    _set(client, _uid(app), "survey", "view")
    _set(client, _uid(app), "survey", "edit")
    assert _kinds(app) == {"live": "create", "interview": "create", "diary": "create"}  # nothing stored for "edit"


def test_new_accounts_get_the_default_and_an_invitation_raises_its_type(app, client, survey):
    allow(app, "*")
    client.post("/admin/admit", data={"action": "kinds_default", "kind_survey": "view", "kind_live": "off",
                                      "kind_interview": "edit", "kind_diary": "off"})
    _as(app.test_client(), NEW, "Neu")
    assert _kinds(app, NEW) == {"survey": "view", "live": "off", "interview": "create", "diary": "off"}
    # invited to the questionnaire as "Bearbeiten" before the first sign-in: questionnaires are opened that far
    _share(app, 1, "editor", orcid=OTHER)
    _as(app.test_client(), OTHER)
    assert _kinds(app, OTHER) == {"survey": "create", "live": "off", "interview": "create", "diary": "off"}


def test_the_choice_for_a_listed_id_holds_at_its_first_sign_in(app, client):
    client.post("/admin/admit", data={"action": "add", "orcid": NEW, "kind_survey": "off", "kind_live": "edit",
                                      "kind_interview": "off", "kind_diary": "off"})
    with app.app_context():
        assert db.session.get(SignInOrcid, NEW).kinds["live"] == "edit"
    _as(app.test_client(), NEW, "Neu")
    assert _kinds(app, NEW) == {"survey": "off", "interview": "off", "diary": "off"}


def test_a_group_preset_replaces_the_default_for_its_new_members(app, client):
    with app.app_context():
        group = WorkGroup(name="Seminar", kinds_preset={"survey": "edit", "live": "off", "interview": "off",
                                                        "diary": "off"})
        group.members.append(WorkGroupMember(orcid=NEW, name="Neu", role="member"))
        db.session.add(group)
        db.session.commit()
    _as(app.test_client(), NEW, "Neu")
    assert _kinds(app, NEW) == {"live": "off", "interview": "off", "diary": "off"}


def test_duplicating_needs_edit_for_the_type(app, client, survey):
    from app import duplicate

    allow(app, OTHER)
    _share(app, 1, "admin")
    _as(app.test_client(), OTHER)
    _set(client, _uid(app), "survey", "view")
    with app.test_request_context():
        user = db.session.get(User, _uid(app))
        assert not duplicate.may(user, db.session.get(Survey, 1))


def _create_all(app, orcid=OTHER):
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        from app import kind_access

        kind_access.give_create(user)
        db.session.commit()


def test_edit_works_on_shared_surveys_and_only_create_creates(app, client, survey):
    """The fourth level (konzept-stufe-anlegen-2026-10-18.md): "Bearbeiten" edits what is shared and creates nothing,
    "Anlegen" creates surveys of its type; the former right to create is gone."""
    allow(app, OTHER)
    other = _as(app.test_client(), OTHER)
    _share(app, 1, "editor")
    uid = _uid(app)
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "0"})
    assert _kinds(app) is None                                           # all four at "edit"
    assert other.get("/surveys/1/editor/").status_code == 200            # works on the shared questionnaire
    r = other.get("/surveys/new")
    assert r.status_code == 302 and "/dashboard" in r.headers["Location"]
    _set(client, uid, "survey", "create")
    assert _kinds(app) == {"survey": "create"}
    assert other.get("/surveys/new").status_code == 200
    r = other.get("/presenter/new")                                       # live polls still only edited
    assert r.status_code == 302 and "/dashboard" in r.headers["Location"]
    with app.app_context():
        from app import auth

        assert auth.may_create_surveys(db.session.get(User, uid))         # groups and designs: any "create"


def test_the_account_page_shows_four_levels_and_both_shortcuts(app, client, survey):
    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    uid = _uid(app)
    _set(client, uid, "live", "view")
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "1"})
    assert _kinds(app) == {"survey": "create", "live": "view", "interview": "create", "diary": "create"}
    page = client.get(f"/admin/accounts/{uid}").data.decode()
    block = page[page.index('id="befragungstypen"'):]
    block = block[:block.index("</section>")]
    assert block.count('name="level" value="create"') == 1               # only the live poll can still be raised
    assert block.count('name="level" value="edit"') == 4
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "0"})
    assert _kinds(app) == {"live": "view"}                               # "Nirgends anlegen" leaves "Ansehen" alone
    assert client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "groups"}).status_code == 400


def test_a_group_gives_create_once_on_joining(app, client, survey):
    """E3: a group with "darf Befragungen anlegen" raises "edit" to "create" when someone joins; leaving keeps it,
    and switching the group's tick on raises its members at that moment."""
    from app import groups as groups_mod

    allow(app, OTHER, NEW)
    _as(app.test_client(), OTHER)
    _as(app.test_client(), NEW)
    for orcid in (OTHER, NEW):
        client.post(f"/admin/accounts/{_uid(app, orcid)}", data={"action": "may_create", "value": "0"})
    with app.app_context():
        group = WorkGroup(name="Lehrstuhl", may_create=True)
        db.session.add(group)
        db.session.commit()
        gid = group.id
    with app.app_context():
        from app import kind_access

        group = db.session.get(WorkGroup, gid)
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        group.members.append(WorkGroupMember(orcid=OTHER, user_id=user.id, name="Kollegin", role="member"))
        kind_access.joined(group, user)
        db.session.commit()
    assert _kinds(app) == {k: "create" for k in ("survey", "live", "interview", "diary")}
    with app.app_context():
        groups_mod.remove(db.session.get(WorkGroup, gid))
        db.session.commit()
    assert _kinds(app) == {k: "create" for k in ("survey", "live", "interview", "diary")}  # stays after leaving
    with app.app_context():
        late = WorkGroup(name="Später")
        user = db.session.scalar(db.select(User).filter_by(orcid=NEW))
        late.members.append(WorkGroupMember(orcid=NEW, user_id=user.id, name="Neu", role="member"))
        db.session.add(late)
        db.session.commit()
        lid = late.id
    assert _kinds(app, NEW) is None
    client.post(f"/admin/groups/{lid}", data={"action": "may_create", "value": "1"})
    assert _kinds(app, NEW) == {k: "create" for k in ("survey", "live", "interview", "diary")}


def test_an_organisation_without_creating_caps_create_at_edit(app, client, survey):
    """E5: "Niemand mit Heimat hier legt Befragungen an" caps "create" at "edit"; kinds_cap may stop below "create"."""
    from app import kind_access, orgs
    from app.models import Organisation

    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    _create_all(app)
    with app.app_context():
        org = Organisation(key="lehrstuhl", name="Lehrstuhl", may_create=False)
        db.session.add(org)
        db.session.commit()
        orgs.set_home(OTHER, org)
        db.session.commit()
        orgs.forget()
        kind_access.forget()
        assert set(kind_access.levels_of_orcid(OTHER).values()) == {"edit"}
        org.may_create, org.kinds_cap = None, {"live": "edit"}
        db.session.commit()
        orgs.forget()
        kind_access.forget()
        levels = kind_access.levels_of_orcid(OTHER)
        assert levels["survey"] == "create" and levels["live"] == "edit"


@pytest.mark.sqlite_foreign_keys_off  # goes down and up again: SQLite's batch mode (tests/conftest.py, B8)
def test_migration_0080_keeps_who_created(app):
    """E7: whoever the former right to create let create gets "create" for every type at "edit"; nobody else."""
    from alembic import command
    from sqlalchemy import text

    from app import migrations

    no = "0000-0000-0000-0239"
    allow(app, "*")
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = False                       # the rows below say what each one had
    for orcid, name in ((OTHER, "Mit Häkchen"), (NEW, "Über Gruppe"), (no, "Nein trotz Gruppe")):
        _as(app.test_client(), orcid, name)
    with app.app_context():
        for orcid, own, kinds in ((OTHER, True, {"live": "view"}), (NEW, None, None), (no, False, None)):
            row = db.session.get(UserLogin, _uid(app, orcid)) or UserLogin(user_id=_uid(app, orcid))
            row.may_create, row.kinds = own, kinds
            db.session.add(row)
        db.session.flush()
        db.session.delete(db.session.get(UserLogin, _uid(app, NEW)))     # an account without a row of its own
        group = WorkGroup(name="Lehrstuhl", may_create=True)
        for orcid in (NEW, no):
            group.members.append(WorkGroupMember(orcid=orcid, user_id=_uid(app, orcid), name="x", role="member"))
        db.session.add(group)
        db.session.commit()
        db.session.remove()
        migrations._run(command.downgrade, "0079")
        migrations._run(command.upgrade)
        assert migrations.current() == migrations.head()
    assert _kinds(app) == {"survey": "create", "live": "view", "interview": "create", "diary": "create"}
    assert _kinds(app, NEW) == {k: "create" for k in ("survey", "live", "interview", "diary")}
    assert _kinds(app, no) is None
