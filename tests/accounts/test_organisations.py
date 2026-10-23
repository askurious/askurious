"""Organisations above the groups (app/orgs.py, konzept-abteilungen-2026-10-15.md, Ralf 2026-10-15): one organisation
to start with, in which nothing looks different; a group made into an organisation with a preview, nobody losing
access; and the fence: a group shares only within its organisation, the people search finds only one's own
organisations, a lead adds no account of another organisation directly."""
from flask_login import login_user

from app import access, orgs
from app.extensions import db
from app.models import (AccountType, Organisation, OrganisationMember, Survey, SurveyGroup, SurveyMember, User, UserLogin,
                        WorkGroup, WorkGroupMember)
from tests.conftest import allow

ME = "0000-0000-0000-001X"       # site admin and admin of survey 1 (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"
FOURTH = "0000-0000-0000-0239"
FIFTH = "0000-0000-0000-0458"


def _as(app, orcid, name):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


def _group(client, name="Lehrstuhl", members=((OTHER, "Kollegin"),)):
    client.post("/groups/new", data={"name": name})
    with client.application.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).order_by(WorkGroup.id.desc()))
    for orcid, who in members:
        client.post(f"/groups/{gid}/members", data={"orcid": orcid, "name": who})
    return gid


def _convert(client, gid, **form):
    data = {"name": "Lehrstuhl X", "leads_admin": "1", "keep_group": "1"}
    data.update(form)
    return client.post(f"/admin/groups/{gid}/to-org", data=data, follow_redirects=True)


def _new_org(app):
    with app.app_context():
        return db.session.scalar(db.select(Organisation.id).where(Organisation.key.is_(None)))


def test_one_organisation_to_start_with_and_nothing_shows(app, client, survey):
    with app.app_context():
        main = db.session.scalar(db.select(Organisation).filter_by(key=orgs.MAIN))
        assert main is not None and main.name
        assert not orgs.several()
    page = client.get("/admin/people").data.decode()
    assert "Heimat-Organisation" not in page                     # no badge while there is only one (A24)
    assert client.get("/admin/orgs").status_code == 200
    gid = _group(client)
    r = client.post("/surveys/1/access-groups", data={"group": gid, "role": "viewer"}, follow_redirects=True)
    assert "kann die Befragung jetzt ansehen" in r.data.decode()


def test_a_group_becomes_an_organisation_with_a_preview(app, client, survey):
    _as(app, OTHER, "Kollegin")                                  # has an account before
    gid = _group(client)
    client.post("/surveys/1/access-groups", data={"group": gid, "role": "editor"})
    preview = client.get(f"/admin/groups/{gid}/to-org").data.decode()
    assert "„Lehrstuhl“ zur Organisation machen" in preview and "Test" in preview and "Kollegin" in preview
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Organisation.id))) == 2   # the preview changes nothing (main, guests)
    r = _convert(client, gid)
    assert "Organisation „Lehrstuhl X“ angelegt" in r.data.decode()
    org_id = _new_org(app)
    with app.app_context():
        assert db.session.get(Survey, 1).org_id == org_id       # its only admin is in the group
        assert db.session.get(WorkGroup, gid).org_id == org_id
        assert orgs.home_id_of_orcid(ME) == org_id and orgs.home_id_of_orcid(OTHER) == org_id
        assert orgs.is_admin_of(db.session.scalar(db.select(User).filter_by(orcid=ME)), org_id)  # the lead
        main = orgs.default_id()
        assert {main, org_id} <= orgs.ids_of_orcid(ME)          # stays a member where it was
        assert access.role_of_orcid(OTHER, db.session.get(Survey, 1)) == "editor"   # nothing lost
        orgs.forget()
        assert orgs.several()
    assert "Lehrstuhl X" in client.get("/admin/orgs").data.decode()          # a row of the table (variant A)
    page = client.get(f"/admin/orgs/{org_id}").data.decode()             # its own page
    assert "Lehrstuhl X" in page and "aus der Gruppe „Lehrstuhl“" in page


def test_shares_that_would_cross_the_fence_become_personal(app, client, survey):
    """A survey stays where it was (an admin outside the group), its group moves: the members keep their access
    as personal entries."""
    gid = _group(client)
    client.post("/surveys/1/access-groups", data={"group": gid, "role": "editor"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=THIRD, name="Dritte", role="admin", position=1))
        db.session.commit()
    _convert(client, gid)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.org_id is None                                  # stays in "main"
        assert not s.group_links                                 # the group share is gone ...
        assert {m.orcid: m.role for m in s.members}[OTHER] == "editor"   # ... and personal now
        assert access.role_of_orcid(OTHER, s) == "editor"


def test_a_group_shares_only_within_its_organisation(app, client, survey):
    gid = _group(client)
    _convert(client, gid)
    org_id = _new_org(app)
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        # a group of "main" with me in it, and a survey of the new organisation
        g = WorkGroup(name="Alt", created_by_id=me.id)
        g.members.append(WorkGroupMember(orcid=ME, user_id=me.id, role="lead"))
        g.members.append(WorkGroupMember(orcid=FOURTH, name="Vierte", role="member"))
        db.session.add(g)
        db.session.commit()
        old_gid = g.id
        g.org_id = None  # made by someone at home in the new organisation; put it into "main"
        db.session.commit()
    r = client.post("/surveys/1/access-groups", data={"group": old_gid, "role": "viewer"}, follow_redirects=True)
    assert "gehört zu einer anderen Organisation" in r.data.decode()
    with app.app_context():
        assert not db.session.scalar(db.select(SurveyGroup).filter_by(group_id=old_gid))
        # a link across the fence (made before, or by hand) opens nothing
        db.session.add(SurveyGroup(survey_id=1, group_id=old_gid, role="editor"))
        db.session.commit()
        s = db.session.get(Survey, 1)
        assert access.role_of_orcid(FOURTH, s) is None
        assert 1 not in set(db.session.scalars(access.survey_ids(db.session.scalar(db.select(User).filter_by(orcid=FOURTH)))))


def test_people_search_ends_at_the_fence(app, client, survey):
    gid = _group(client, members=())
    _convert(client, gid)
    org_id = _new_org(app)
    allow(app, THIRD, keep=True)
    _as(app, THIRD, "Dritte Person")                              # home "main"
    _as(app, FOURTH, "Vierte Person")
    with app.app_context():
        orgs.add(db.session.get(Organisation, org_id), FOURTH, home=True)   # only in the new organisation
        db.session.commit()
    from app.surveys import search_people

    with app.test_request_context():
        third = db.session.scalar(db.select(User).filter_by(orcid=THIRD))
        login_user(third)
        orgs.forget()
        found = {r["orcid"] for r in search_people("Person", set()) if r["source"] == "user"}
        assert FOURTH not in found                               # in another organisation only
    with app.test_request_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        login_user(me)
        orgs.forget()
        found = {r["orcid"] for r in search_people("Person", set()) if r["source"] == "user"}
        assert {THIRD, FOURTH} <= found                           # a member of both organisations finds both


def test_a_lead_adds_no_account_of_another_organisation(app, client, survey):
    gid = _group(client, members=())
    _convert(client, gid)
    allow(app, THIRD, keep=True)
    _as(app, THIRD, "Dritte")                                     # an account at home in "main" only
    r = client.post(f"/groups/{gid}/members", data={"orcid": THIRD, "name": "Dritte"}, follow_redirects=True)
    assert "gehört nicht zur Organisation dieser Gruppe" in r.data.decode()
    with app.app_context():
        assert not db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=THIRD))
    # someone without an account yet comes in with the group's organisation as home
    client.post(f"/groups/{gid}/members", data={"orcid": FIFTH, "name": "Fünfte"})
    _as(app, FIFTH, "Fünfte")
    with app.app_context():
        assert orgs.home_id_of_orcid(FIFTH) == _new_org(app)


def test_new_things_go_into_the_home_organisation(app, client, survey):
    gid = _group(client, members=())
    _convert(client, gid)
    org_id = _new_org(app)
    client.post("/groups/new", data={"name": "Projekt"})
    with app.app_context():
        assert db.session.scalar(db.select(WorkGroup).filter_by(name="Projekt")).org_id == org_id


def test_an_organisation_goes_only_empty_and_never_the_first(app, client, survey):
    gid = _group(client, members=())
    _convert(client, gid)
    org_id = _new_org(app)
    with app.app_context():
        main = orgs.default_id()
    r = client.post(f"/admin/orgs/{org_id}", data={"action": "delete"}, follow_redirects=True)
    assert "Nur eine leere Organisation" in r.data.decode()
    r = client.post(f"/admin/orgs/{main}", data={"action": "delete"}, follow_redirects=True)
    assert "lässt sich nicht löschen" in r.data.decode()
    with app.app_context():
        assert db.session.get(Organisation, org_id) is not None and db.session.get(Organisation, main) is not None
        assert db.session.scalar(db.select(db.func.count(OrganisationMember.id))) >= 1


# --- stage 3 and 4 (Ralf 2026-10-16 "mach mit Stufe 3 und 4 weiter"; designs and institutions the same day) ---------

def _org_with_admin(app, client, admin_orcid=OTHER, name="Kollegin"):
    """A group made an organisation whose lead (OTHER) is its admin; ME stays a site admin."""
    _as(app, admin_orcid, name)
    gid = _group(client, members=((admin_orcid, name),))
    with app.app_context():
        db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=admin_orcid)).role = "lead"
        db.session.commit()
    _convert(client, gid)
    return _new_org(app), gid


def test_the_management_is_for_the_organisations_admins(app, client, survey):
    org_id, _ = _org_with_admin(app, client)
    other = _as(app, OTHER, "Kollegin")
    page = other.get(f"/org/{org_id}/people").data.decode()
    assert "Verwaltung" in page and "Personen der Organisation" in page and "Kollegin" in page
    page = other.get("/dashboard/").data.decode()                                     # in the menu at the name,
    assert ">Organisationsverwaltung</p>" in page and "Lehrstuhl X</a>" in page          # under its heading (2026-10-17)
    for tab in ("groups", "surveys", "design"):
        assert other.get(f"/org/{org_id}/{tab}").status_code == 200
    allow(app, THIRD, keep=True)
    third = _as(app, THIRD, "Dritte")
    assert third.get(f"/org/{org_id}/").status_code == 404                               # not an admin of it
    with app.app_context():
        main = orgs.default_id()
    assert other.get(f"/org/{main}/").status_code == 404
    assert client.get(f"/org/{org_id}/people").status_code == 200                              # site admins may look in


def test_an_admin_takes_in_a_person_who_comes_with_home_and_type(app, client, survey):
    org_id, _ = _org_with_admin(app, client)
    other = _as(app, OTHER, "Kollegin")
    from app import account_types

    other.post(f"/org/{org_id}/types", data={"action": "template", "template": "master"})  # its own types
    with app.app_context():
        tid = db.session.scalar(db.select(AccountType.id).filter_by(org_id=org_id, name_de="Masterarbeit"))
    r = other.post(f"/org/{org_id}/people", data={"action": "add", "orcid": FOURTH, "name": "Vierte",
                                                   "account_type": tid, "valid_mode": "type"}, follow_redirects=True)
    assert "kann sich jetzt anmelden" in r.data.decode()
    _as(app, FOURTH, "Vierte")                                                          # may sign in now
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=FOURTH))
        assert orgs.home_id(user) == org_id and account_types.of(user).id == tid
        assert db.session.get(UserLogin, user.id).valid_until is not None
        uid = user.id
    # the account at home here is managed on its own page, within the organisation's limits
    with app.app_context():
        db.session.get(Organisation, org_id).kinds_cap = {"diary": "off"}
        db.session.commit()
    page = other.get(f"/org/{org_id}/people/{uid}").data.decode()
    assert "Befragungstypen" in page and "Nutzertyp und Laufzeit" in page
    r = other.post(f"/org/{org_id}/people/{uid}", data={"action": "kind", "kind": "diary", "level": "edit"},
                   follow_redirects=True)
    assert "Höher, als die Organisation erlaubt" in r.data.decode()
    other.post(f"/org/{org_id}/people/{uid}", data={"action": "block"})
    with app.app_context():
        assert db.session.get(UserLogin, uid).status == UserLogin.BLOCKED
    assert other.get(f"/org/{org_id}/people/{_uid(app, ME)}").status_code == 404        # never a site admin's account


def _uid(app, orcid):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=orcid))


def test_the_caps_of_the_home_organisation_hold(app, client, survey):
    org_id, _ = _org_with_admin(app, client)
    with app.app_context():
        org = db.session.get(Organisation, org_id)
        org.kinds_cap, org.may_create = {"live": "view", "diary": "off"}, False
        db.session.commit()
    _as(app, OTHER, "Kollegin")
    from app import auth, kind_access

    with app.test_request_context():
        other = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        login_user(other)
        assert kind_access.levels(other)["live"] == "view" and kind_access.levels(other)["diary"] == "off"
        assert not auth.may_create_surveys(other)
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        assert auth.may_create_surveys(me) and kind_access.levels(me)["diary"] == "create"   # site admins: never capped
    # the tab of the administration saves the caps
    client.post(f"/admin/orgs/{org_id}", data={"action": "caps", "kind_survey": "create", "kind_live": "create",
                                                 "kind_interview": "off", "kind_diary": "edit", "invite_out": "1",
                                                 "max_accounts": "40"})
    with app.app_context():
        org = db.session.get(Organisation, org_id)
        # "create" is no cap, "edit" stops below it (konzept-stufe-anlegen-2026-10-18.md, E5)
        assert org.kinds_cap == {"interview": "off", "diary": "edit"} and org.may_create is None and org.max_accounts == 40
        assert org.invite_out and not org.invite_in


def test_an_account_invited_only_to_a_survey_is_a_guest(app, client, survey):
    from app import auth

    client.post("/surveys/1/members", data={"orcid": FIFTH, "name": "Fünfte", "role": "viewer"})
    guest = _as(app, FIFTH, "Fünfte")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=FIFTH))
        assert orgs.home_id(user) == orgs.guest_id()
    with app.test_request_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=FIFTH))
        login_user(user)
        orgs.forget()
        assert orgs.is_guest(user) and not auth.may_create_surveys(user)
        from app.surveys import search_people

        found = {r["orcid"] for r in search_people("Phil", set()) if r["source"] == "user"}
        assert found <= {ME}                       # a guest finds only the people of its shared surveys
    assert guest.get("/surveys/1").status_code == 200                                    # the survey it was invited to
    # an admin of an organisation invites the guest; once it accepts, its home moves there (audit 2026-10-18,
    # AUTHZ-01: never before)
    org_id, _ = _org_with_admin(app, client)
    other = _as(app, OTHER, "Kollegin")
    other.post(f"/org/{org_id}/people", data={"action": "add", "orcid": FIFTH})
    with app.app_context():
        orgs.forget()
        assert orgs.home_id_of_orcid(FIFTH) == orgs.guest_id()
    import re

    link = re.search(r"/org/join/[A-Za-z0-9_.-]+", other.get(f"/org/{org_id}/people").data.decode()).group(0)
    guest.post(link, data={"answer": "join"})
    with app.app_context():
        orgs.forget()
        assert orgs.home_id_of_orcid(FIFTH) == org_id


def test_examples_and_designs_of_an_organisation(app, client, survey):
    org_id, gid = _org_with_admin(app, client)
    with app.app_context():
        assert db.session.get(Survey, 1).org_id == org_id                                # came along with its admin
    allow(app, THIRD, keep=True)
    third = _as(app, THIRD, "Dritte")                                                    # "main" only
    client.post("/surveys/1/org-showcase", data={"state": "on"})
    with app.app_context():
        assert db.session.get(Survey, 1).org_showcase
    assert third.get("/surveys/1").status_code == 404                                    # not a member of it
    with app.app_context():
        orgs.add(db.session.get(Organisation, org_id), THIRD)
        db.session.commit()
    assert third.get("/surveys/1").status_code == 200                                    # a member of its organisation
    with app.app_context():
        orgs.forget()
        from app import access

        assert access.showcase_only(db.session.scalar(db.select(User).filter_by(orcid=THIRD)), db.session.get(Survey, 1))
    # a design shared with the whole organisation
    from app.models import Design

    with app.app_context():
        d = Design(owner_id=_uid(app, ME), name="Haus", data={}, org_id=org_id, org_shared=True)
        db.session.add(d)
        db.session.commit()
    assert "Haus" in third.get("/designs/").data.decode()


def test_moving_a_survey_and_the_switches_for_invitations(app, client, survey):
    org_id, gid = _org_with_admin(app, client)
    with app.app_context():
        main = orgs.default_id()
        db.session.get(Survey, 1).org_id = None                                          # back in "main"
        db.session.add(SurveyGroup(survey_id=1, group_id=gid, role="viewer"))           # a group of the new one ...
        db.session.commit()
    r = client.post("/surveys/1/organisation", data={"org": org_id}, follow_redirects=True)
    assert "gehört jetzt zur Organisation „Lehrstuhl X“" in r.data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.org_id == org_id and [link.group_id for link in s.group_links] == [gid]  # ... stays
    client.post("/surveys/1/organisation", data={"org": main})
    with app.app_context():
        assert db.session.get(Survey, 1).org_id is None and not db.session.get(Survey, 1).group_links
        db.session.get(Organisation, org_id).invite_in = False
        db.session.commit()
    # OTHER's home does not let its members be invited out: not into ME's survey of "main"
    with app.app_context():
        db.session.execute(db.delete(OrganisationMember).where(OrganisationMember.orcid == OTHER,
                                                               OrganisationMember.org_id == main))
        db.session.commit()
    r = client.post("/surveys/1/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"},
                    follow_redirects=True)
    assert "Einladungen über die Organisation hinaus sind" in r.data.decode()


def test_where_new_things_go_and_the_default_design(app, client, survey):
    org_id, _ = _org_with_admin(app, client)
    client.post("/groups/new", data={"name": "Im Haus"})                               # new things: the home
    with app.app_context():
        assert db.session.scalar(db.select(WorkGroup).filter_by(name="Im Haus")).org_id == org_id
    assert 'name="org" data-submit-on-change' not in client.get("/dashboard/").data.decode()  # no choice (2026-10-16)
    # the organisation design, light and dark, is the standard for new surveys (Ralf 2026-10-19)
    for mode, source in (("light", "builtin:papier"), ("dark", "builtin:nachtblau")):
        r = client.post(f"/org/{org_id}/design", data={"action": "design_take", "mode": mode, "source": source},
                        follow_redirects=True)
        assert "Organisationsdesign" in r.data.decode() and "gespeichert" in r.data.decode()
    with app.app_context():
        s = Survey(title="Neu", created_by_id=_uid(app, ME), org_id=org_id)
        db.session.add(s)
        db.session.commit()
        from app import theme

        t = theme.clean(s.theme)
        assert t["preset"] == "org" and t["name"] == "Papier" and t["scheme"] == "fixed" and t["font"] == "serif"
        assert "colors_dark" not in t  # the light side only, it stays light (Ralf, 2026-10-23)


def test_no_institutions_any_more(app, client, survey):
    assert client.post("/admin/affiliations").status_code in (404, 405)
    page = client.get("/account/").data.decode()
    assert 'name="affiliation"' not in page and "Organisation" in page
    org_id, _ = _org_with_admin(app, client)
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert orgs.affiliation_of(user) == "Lehrstuhl X"
    # no design of an institution any more (Ralf 2026-10-19): the RWTH templates are offered to nobody, the tick is gone
    from app import theme

    assert "rwth" not in theme.BUILTIN
    assert client.post("/admin/templates/org", data={"org": org_id, "action": "allow"}).status_code == 404


def test_short_and_full_name_and_group_names_per_organisation(app, client, survey):
    """Ralf 2026-10-16: a short name for the pages, a full one for survey texts; group names unique only within an
    organisation."""
    org_id, gid = _org_with_admin(app, client)
    other = _as(app, OTHER, "Kollegin")
    other.post(f"/org/{org_id}/design", data={"action": "names", "name": "COMM",
                                               "long_name": "Lehrstuhl für Kommunikationswissenschaft, RWTH Aachen"})
    with app.app_context():
        org = db.session.get(Organisation, org_id)
        assert (org.name, org.long_name) == ("COMM", "Lehrstuhl für Kommunikationswissenschaft, RWTH Aachen")
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert orgs.affiliation_of(user) == "Lehrstuhl für Kommunikationswissenschaft, RWTH Aachen"
    # the group "Lehrstuhl" is in the new organisation: a second one there is refused, one in "main" is fine
    r = other.post(f"/org/{org_id}/groups", data={"action": "new", "name": "lehrstuhl", "lead": OTHER},
                   follow_redirects=True)
    assert "gibt es schon eine Gruppe" in r.data.decode()
    with app.app_context():
        main = orgs.default_id()
    allow(app, THIRD, keep=True)
    third = _as(app, THIRD, "Dritte")                                                    # at home in "main"
    third.post("/groups/new", data={"name": "Lehrstuhl"})
    with app.app_context():
        found = list(db.session.scalars(db.select(WorkGroup).filter_by(name="Lehrstuhl")))
        assert sorted(orgs.of(g) for g in found) == sorted([org_id, main])


def test_lists_filter_by_organisation_and_group(app, client, survey):
    """Ralf 2026-10-16: people by organisation and group, groups by organisation (administration), people by group
    (management of an organisation)."""
    org_id, gid = _org_with_admin(app, client)
    allow(app, THIRD, keep=True)
    _as(app, THIRD, "Dritte Person")
    def table(url):
        return client.get(url).data.decode().split('id="forschende"')[1].split("</table>")[0]

    people = table(f"/admin/people?f_group={gid}")
    assert "Kollegin" in people and "Dritte Person" not in people
    assert 'name="f_group"' in people and 'name="f_org"' in people
    with app.app_context():
        main = orgs.default_id()
    assert "Dritte Person" in table(f"/admin/people?f_org={main}")
    groups_page = client.get(f"/admin/groups?g_org={main}").data.decode()
    assert 'value="Lehrstuhl"' not in groups_page and 'name="g_org"' in groups_page
    assert 'value="Lehrstuhl"' in client.get(f"/admin/groups?g_org={org_id}").data.decode()
    other = _as(app, OTHER, "Kollegin")
    page = other.get(f"/org/{org_id}/people?p_group={gid}").data.decode()
    assert 'name="p_group"' in page and "Kollegin" in page



def test_the_people_list_names_the_starting_organisation_under_the_platform_name(app, client, survey):
    """Ralf 2026-10-20: a person at home in "Askurious" (the starting organisation under the platform's name) showed
    "–" in the column Organisation of Administration > Personen; only guests have none."""
    from app import brand

    org_id, _ = _org_with_admin(app, client)
    allow(app, THIRD, keep=True)
    _as(app, THIRD, "Dritte Person")
    with app.app_context():
        db.session.get(Organisation, orgs.default_id()).name = brand.NAME
        db.session.commit()
    rows = client.get("/admin/people").data.decode().split('id="forschende"')[1].split("</table>")[0].split("<tr")
    row = next(r for r in rows if "Dritte Person" in r)
    assert row.split('class="admin-name"')[1].split("<td>")[1].startswith(f"{brand.NAME}</td>")  # the cell after the name
    with app.app_context():
        assert orgs.short_of(db.session.scalar(db.select(User).filter_by(orcid=THIRD))) == brand.NAME

def test_user_types_are_edited_on_a_page_of_their_own(app, client):
    """Ralf 2026-10-16: icons to edit and delete in the list of user types, no fold per row; in the management."""
    from app import account_types

    with app.app_context():
        main = orgs.default_id()
        tid = account_types.default_type().id
    section = client.get(f"/org/{main}/types").data.decode().split('id="nutzertypen"')[1].split("</section>")[0]
    assert "Ändern …" not in section and f'href="/org/{main}/types/{tid}"' in section
    edit = client.get(f"/org/{main}/types/{tid}").data.decode()
    assert "Mitarbeitende" in edit and 'name="name_de"' in edit and "data-autosave-form" in edit and "für Konten, die den Typ neu bekommen" in edit


def test_each_organisation_starts_with_staff_only_and_keeps_its_types(app, client, survey):
    """Ralf 2026-10-16: user types per organisation (except admins); each starts with "Mitarbeitende" only."""
    from app import account_types

    org_id, _ = _org_with_admin(app, client)
    with app.app_context():
        mine = account_types.all_types(org_id=org_id)
        assert [t.name_de for t in mine] == ["Mitarbeitende"] and mine[0].is_default and mine[0].key is None
        assert account_types.default_type(org_id).id == mine[0].id
        main = orgs.default_id()
        start = account_types.default_type().id
    other = _as(app, OTHER, "Kollegin")
    page = other.get(f"/org/{org_id}/types").data.decode()
    assert "Masterarbeit" in page and "Nutzertypen" in page                     # a template to add
    assert other.get(f"/org/{org_id}/types/{start}").status_code == 404           # the start's type is not its own
    r = other.post(f"/org/{org_id}/types", data={"action": "delete", "type_id": start})
    assert r.status_code == 404
    assert other.get(f"/org/{main}/types").status_code == 404                     # nor its management

def test_organisation_and_group_filter_one_at_a_time(app, client, survey):
    """Ralf 2026-10-16: two separate filters, not a combined one: choosing a group drops the organisation and back."""
    org_id, gid = _org_with_admin(app, client)
    with app.app_context():
        main = orgs.default_id()
    page = client.get(f"/admin/people?f_org={main}").data.decode()
    group_form = page.split('name="f_group"')[1].split("</form>")[0]
    assert 'name="f_org"' not in group_form                                  # choosing a group drops the organisation
    assert "Lehrstuhl · Lehrstuhl X" in page                                 # every group, named with its organisation


def test_a_new_groups_lead_is_picked_from_the_organisation(app, client, survey):
    """Ralf 2026-10-16: the lead of a new group is chosen from everyone in the organisation (with a search); an ORCID
    iD stays for someone not yet in it."""
    org_id, _ = _org_with_admin(app, client)
    other = _as(app, OTHER, "Kollegin")
    allow(app, THIRD, keep=True)
    _as(app, THIRD, "Dritte Person")                                                     # at home in "main"
    with app.app_context():
        mine = db.session.scalar(db.select(User).filter_by(orcid=OTHER)).id
        outside = db.session.scalar(db.select(User).filter_by(orcid=THIRD)).id
    page = other.get(f"/org/{org_id}/groups").data.decode()
    pick = page.split('name="lead_user"')[1].split("</select>")[0]
    assert f'value="{mine}"' in pick and "Kollegin" in pick and "Dritte Person" not in pick
    assert 'data-filter-select="lead-user"' in page
    # "Neue Gruppe …" opens a dialog, as "Person hinzufügen …" (Ralf 2026-10-20)
    assert '<a class="button" href="#gruppe-anlegen" data-person-add aria-haspopup="dialog">' in page
    assert 'name="lead_user"' in page[page.index('id="gruppe-anlegen"'):]
    other.post(f"/org/{org_id}/groups", data={"action": "new", "name": "Team", "lead_user": mine})
    with app.app_context():
        team = db.session.scalar(db.select(WorkGroup).filter_by(name="Team"))
        assert [(m.orcid, m.user_id, m.role) for m in team.members] == [(OTHER, mine, "lead")]
    r = other.post(f"/org/{org_id}/groups", data={"action": "new", "name": "Fremd", "lead_user": outside},
                   follow_redirects=True)
    with app.app_context():
        assert db.session.scalar(db.select(WorkGroup).filter_by(name="Fremd")) is None
    other.post(f"/org/{org_id}/groups", data={"action": "new", "name": "Neu", "lead": FIFTH})   # not yet in it
    with app.app_context():
        assert db.session.scalar(db.select(WorkGroup).filter_by(name="Neu")).members[0].orcid == FIFTH
    r = other.post(f"/org/{org_id}/groups", data={"action": "new", "name": "Leer"}, follow_redirects=True)
    assert "ein Mitglied auswählen" in r.data.decode()


def test_settings_of_organisations_save_by_themselves(app, client, survey):
    """Ralf 2026-10-16: the management pages of an organisation (Design, a person, groups, user types) and the
    administration's organisations, groups and accounts save their settings by themselves, without "Speichern"."""
    org_id, gid = _org_with_admin(app, client)
    other = _as(app, OTHER, "Kollegin")
    design = other.get(f"/org/{org_id}/design").data.decode()
    assert design.count("data-autosave-form") == 1 and "data-autosave-page" in design
    r = other.post(f"/org/{org_id}/design", data={"action": "names", "name": "COMM", "long_name": "Lehrstuhl", "autosave": "1"})
    assert r.json["status"] == "saved"
    r = other.post(f"/org/{org_id}/design", data={"action": "names", "name": "", "autosave": "1"})
    assert r.json["status"] == "invalid" and "Kurznamen" in r.json["message"]
    with app.app_context():
        assert db.session.get(Organisation, org_id).name == "COMM"
    groups = other.get(f"/org/{org_id}/groups").data.decode()
    assert "data-autosave-form" in groups and '"Umbenennen"' not in groups
    assert other.post(f"/org/{org_id}/groups", data={"action": "rename", "group_id": gid, "name": "Team neu",
                                                     "autosave": "1"}).json["status"] == "saved"
    with app.app_context():
        assert db.session.get(WorkGroup, gid).name == "Team neu"
    for page in (f"/admin/orgs/{org_id}", "/admin/groups"):
        assert "data-autosave-form" in client.get(page).data.decode()
    r = client.post(f"/admin/orgs/{org_id}", data={"action": "caps", "max_accounts": "40", "autosave": "1"})
    assert r.json["status"] == "saved"
    with app.app_context():
        assert db.session.get(Organisation, org_id).max_accounts == 40


def test_an_old_sign_in_is_not_saved_by_itself(app, client, survey, monkeypatch):
    """A setting that needs a fresh sign-in, sent by a form that saves by itself: "not saved", not a silent redirect."""
    from app import accounts

    org_id, _ = _org_with_admin(app, client)
    monkeypatch.setattr(accounts, "_fresh", lambda: False)
    r = client.post(f"/admin/orgs/{org_id}", data={"action": "caps", "max_accounts": "7", "autosave": "1"})
    assert r.json["status"] == "invalid" and "neu anmelden" in r.json["message"]
    with app.app_context():
        assert db.session.get(Organisation, org_id).max_accounts != 7


def test_a_site_admin_creates_an_organisation_without_a_group(app, client, survey):
    """The form "Neue Organisation" on Administration > Organisationen (Ralf 2026-10-18): name, full name, description
    and a first admin; the admin becomes a member and admin, the home stays, the default type comes along."""
    _as(app, OTHER, "Kollegin")
    page = client.get("/admin/orgs").data.decode()
    # a button that opens a dialog, as "Person hinzufügen …" (Ralf 2026-10-20), no folding <details> any more
    assert '<a class="button" href="#organisation-anlegen" data-person-add aria-haspopup="dialog">' in page
    panel = page[page.index('id="organisation-anlegen"'):]
    assert "data-person-add-panel" in panel[:120] and "data-person-add-close hidden" in panel
    assert 'action="/admin/orgs/new"' in panel and "<details class=\"admin-group-new\"" not in page
    r = client.post("/admin/orgs/new", data={"name": " Institut  Y ", "long_name": "Institut Y, RWTH", "description": "Test",
                                             "admin": OTHER}, follow_redirects=True)
    assert "Organisation „Institut Y“ angelegt; Kollegin ist ihr Admin." in r.data.decode()
    org_id = _new_org(app)
    with app.app_context():
        org = db.session.get(Organisation, org_id)
        assert (org.name, org.long_name, org.description) == ("Institut Y", "Institut Y, RWTH", "Test")
        assert orgs.is_admin_of(db.session.scalar(db.select(User).filter_by(orcid=OTHER)), org_id)
        assert orgs.home_id_of_orcid(OTHER) == orgs.default_id()   # the home stays where it was
        assert db.session.scalar(db.select(db.func.count(AccountType.id)).filter_by(org_id=org_id)) == 1
    assert "Institut Y" in client.get("/admin/orgs").data.decode()


def test_a_new_organisation_needs_a_free_name_and_a_valid_admin(app, client, survey):
    with app.app_context():
        main_name = db.session.scalar(db.select(Organisation.name).filter_by(key=orgs.MAIN))
    r = client.post("/admin/orgs/new", data={"name": "  "}, follow_redirects=True)
    assert "Bitte einen Namen für die Organisation angeben." in r.data.decode()
    r = client.post("/admin/orgs/new", data={"name": main_name.upper()}, follow_redirects=True)
    assert "gibt es schon" in r.data.decode()
    r = client.post("/admin/orgs/new", data={"name": "Firma Z", "admin": "keine-adresse"}, follow_redirects=True)
    assert "Bitte eine ORCID iD" in r.data.decode()
    assert _new_org(app) is None                                   # nothing made by a refused form
    r = client.post("/admin/orgs/new", data={"name": "Firma Z"}, follow_redirects=True)
    assert "Organisation „Firma Z“ angelegt. Admins setzen Sie" in r.data.decode()
    with app.app_context():
        assert not db.session.scalars(db.select(OrganisationMember).filter_by(org_id=_new_org(app))).all()


def test_only_site_admins_create_organisations(app, client, survey):
    other = _as(app, OTHER, "Kollegin")
    assert other.post("/admin/orgs/new", data={"name": "Fremd"}).status_code in (302, 403, 404)
    assert _new_org(app) is None
