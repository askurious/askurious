"""Groups: named lists of researchers (app/groups.py). A group owns nothing; it only carries access to what was
shared with it, so far designs. Leaving or deleting a group ends that access and loses nothing."""
from tests.helpers import rwth_design

from app import theme
from app.extensions import db
from app.models import Contact, Design, DesignGroupShare, WorkGroup, WorkGroupMember
from tests.conftest import allow

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _group(client, name="Lehrstuhl"):
    r = client.post("/groups/new", data={"name": name, "description": "Team"}, follow_redirects=True).data.decode()
    assert f"Gruppe „{name}“ angelegt" in r
    with client.application.app_context():
        return db.session.scalar(db.select(WorkGroup).order_by(WorkGroup.id.desc())).id


def _add(client, gid, orcid, name, role="member"):
    return client.post(f"/groups/{gid}/members", data={"orcid": orcid, "name": name, "role": role}, follow_redirects=True).data.decode()


def _member_id(client, gid, orcid):
    with client.application.app_context():
        return db.session.scalar(db.select(WorkGroupMember.id).filter_by(group_id=gid, orcid=orcid))


def _design(client, name="Hausfarben"):
    with client.application.app_context():
        from app.models import User

        owner = db.session.scalar(db.select(User).filter_by(orcid=ME))
        design = Design(owner_id=owner.id, name=name, data=theme.standard())
        db.session.add(design)
        db.session.commit()
        return design.id


def test_create_a_group_and_add_members(client):
    assert "Sie sind in keiner Gruppe" in client.get("/groups/").data.decode()
    assert "Bitte einen Namen" in client.post("/groups/new", data={"name": "   "}, follow_redirects=True).data.decode()
    gid = _group(client)
    page = client.get(f"/groups/{gid}").data.decode()
    assert "Lehrstuhl" in page and "Team" in page and "Philipp" in page and "Gruppe löschen" in page
    # added directly, by ORCID iD, also before the first sign-in; remembered as a contact
    assert "Kollegin ist jetzt in der Gruppe (Rolle „Mitglied“)" in _add(client, gid, OTHER, "Kollegin")
    assert "schon in der Gruppe" in _add(client, gid, OTHER, "Kollegin")
    assert client.post(f"/groups/{gid}/members", data={"orcid": "nobody"}).status_code == 400
    page = client.get(f"/groups/{gid}").data.decode()
    assert "Kollegin" in page and "noch nicht angemeldet" in page
    with client.application.app_context():
        group = db.session.get(WorkGroup, gid)
        assert [(m.orcid, m.role) for m in group.members] == [(ME, "lead"), (OTHER, "member")]
        assert not group.members[1].signed_up and group.may_create is False  # a pending account (B2)
        assert db.session.scalar(db.select(Contact).filter_by(orcid=OTHER)).name == "Kollegin"
    assert "2 Mitglieder" in client.get("/groups/").data.decode()
    # the first sign-in attaches the membership
    _as(client, OTHER, "Kollegin")
    with client.application.app_context():
        assert db.session.get(WorkGroup, gid).members[1].signed_up
    assert "noch nicht angemeldet" not in client.get(f"/groups/{gid}").data.decode()


def test_members_see_the_group_leads_manage_it(client):
    gid = _group(client)
    _add(client, gid, OTHER, "Kollegin")
    _as(client, OTHER, "Kollegin")
    page = client.get(f"/groups/{gid}").data.decode()
    assert "Philipp" in page and "verlassen" in page
    assert "Gruppe löschen" not in page and "Name oder ORCID iD" not in page and ">entfernen<" not in page
    listing = client.get("/groups/").data.decode()
    assert "Lehrstuhl" in listing and "Leitung: Philipp" in listing
    assert "Neue Gruppe" not in listing                                       # only who may create surveys creates groups
    assert client.post("/groups/new", data={"name": "Eigene"}).status_code == 403
    lead_id = _member_id(client, gid, ME)
    for url, data in ((f"/groups/{gid}/edit", {"name": "Meins"}), (f"/groups/{gid}/members", {"orcid": THIRD, "name": "Dritte"}),
                      (f"/groups/{gid}/members/{lead_id}/role", {"role": "member"}),
                      (f"/groups/{gid}/members/{lead_id}/remove", {}), (f"/groups/{gid}/delete", {})):
        assert "Das kann nur die Leitung der Gruppe" in client.post(url, data=data, follow_redirects=True).data.decode(), url
    with client.application.app_context():
        group = db.session.get(WorkGroup, gid)
        assert group.name == "Lehrstuhl" and len(group.members) == 2 and group.members[0].role == "lead"
    # to everyone else the group does not exist
    _as(client, THIRD, "Dritte")
    assert client.get(f"/groups/{gid}").status_code == 404
    assert client.post(f"/groups/{gid}/delete").status_code == 404
    assert client.post(f"/groups/{gid}/members", data={"orcid": THIRD, "name": "Dritte"}).status_code == 404
    assert "Sie sind in keiner Gruppe" in client.get("/groups/").data.decode()


def test_one_lead_always_stays(client):
    gid = _group(client)
    me = _member_id(client, gid, ME)
    assert "einzige Mitglied" in client.post(f"/groups/{gid}/members/{me}/remove", follow_redirects=True).data.decode()
    _add(client, gid, OTHER, "Kollegin")
    other = _member_id(client, gid, OTHER)
    assert "Mindestens eine Person muss die Gruppe leiten" in client.post(
        f"/groups/{gid}/members/{me}/role", data={"role": "member"}, follow_redirects=True).data.decode()
    assert "Mindestens eine Person muss die Gruppe leiten" in client.post(
        f"/groups/{gid}/members/{me}/remove", follow_redirects=True).data.decode()
    assert client.post(f"/groups/{gid}/members/{other}/role", data={"role": "boss"}).status_code == 400
    assert "Rolle „Leitung“" in client.post(f"/groups/{gid}/members/{other}/role", data={"role": "lead"}, follow_redirects=True).data.decode()
    assert "Sie haben die Gruppe „Lehrstuhl“ verlassen" in client.post(f"/groups/{gid}/members/{me}/remove", follow_redirects=True).data.decode()
    assert client.get(f"/groups/{gid}").status_code == 404
    with client.application.app_context():
        assert [(m.orcid, m.role) for m in db.session.get(WorkGroup, gid).members] == [(OTHER, "lead")]


def test_rename_and_limits(client, monkeypatch):
    from app import groups

    gid = _group(client)
    client.post(f"/groups/{gid}/edit", data={"name": "  Institut   für  Beispiele " + "x" * 200, "description": "d" * 900})
    with client.application.app_context():
        group = db.session.get(WorkGroup, gid)
        assert group.name.startswith("Institut für Beispiele x") and len(group.name) == 80 and len(group.description) == 500
    assert "Bitte einen Namen" in client.post(f"/groups/{gid}/edit", data={"name": ""}, follow_redirects=True).data.decode()
    monkeypatch.setattr(groups, "MAX_MEMBERS", 2)
    _add(client, gid, OTHER, "Kollegin")
    assert "Mehr als 2 Mitglieder" in _add(client, gid, THIRD, "Dritte")
    monkeypatch.setattr(groups, "MAX_MEMBERS", 200)
    monkeypatch.setattr(groups, "MAX_GROUPS", 1)
    assert "Mehr als 1 Gruppen je Person" in client.post("/groups/new", data={"name": "Zwei"}, follow_redirects=True).data.decode()
    monkeypatch.setattr(groups, "MAX_GROUPS", 2)
    second = _group(client, "Zwei")
    monkeypatch.setattr(groups, "MAX_GROUPS", 1)
    assert "schon in 1 Gruppen" in _add(client, second, OTHER, "Kollegin")


def test_a_group_member_may_sign_in_but_creates_no_surveys(app, client):
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    app.config["DEV_LOGIN"] = False                                           # as in production
    assert other.get("/dashboard/").status_code == 302                        # neither listed nor invited
    app.config["DEV_LOGIN"] = True
    gid = _group(client)
    _add(client, gid, OTHER, "Kollegin")
    app.config["DEV_LOGIN"] = False
    page = other.get("/dashboard/")
    assert page.status_code == 200 and "page-head-actions" not in page.data.decode()
    assert "</svg> Gruppen</a>" in page.data.decode()                         # in a group: the header shows the page
    app.config["DEV_LOGIN"] = True
    client.post(f"/groups/{gid}/delete")
    app.config["DEV_LOGIN"] = False
    assert other.get("/dashboard/").status_code == 302


def test_header_shows_groups_only_where_it_leads_somewhere(app, client):
    assert "</svg> Gruppen</a>" in client.get("/dashboard/").data.decode()    # may create groups
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert "</svg> Gruppen</a>" not in other.get("/dashboard/", follow_redirects=True).data.decode()


def test_people_search_offers_group_members_first(client, survey, monkeypatch):
    from app import orcid_api

    monkeypatch.setattr(orcid_api, "search", lambda q: [])
    gid = _group(client)
    _add(client, gid, OTHER, "Kollegin Beispiel")
    with client.application.app_context():                                    # not a contact: found through the group alone
        db.session.execute(db.delete(Contact))
        db.session.commit()
    page = client.get("/surveys/1?q=Beispiel").data.decode()
    assert "Kollegin Beispiel" in page and '<span class="badge">Gruppe</span>' in page
    # in the group itself, members are not offered again
    second = _group(client, "Zwei")
    assert "Kollegin Beispiel" in client.get(f"/groups/{second}?q=Beispiel").data.decode()
    assert "Keine Treffer" in client.get(f"/groups/{gid}?q=Beispiel").data.decode()


def test_share_a_design_with_a_group(app, client):
    gid = _group(client)
    did = _design(client)
    _add(client, gid, OTHER, "Kollegin")
    page = client.get("/designs/").data.decode()
    assert "Mit Gruppe teilen" in page and f'<option value="{gid}">Lehrstuhl</option>' in page
    assert "Bitte eine Ihrer Gruppen wählen" in client.post(f"/designs/{did}/share-group", data={"group": "999"}, follow_redirects=True).data.decode()
    r = client.post(f"/designs/{did}/share-group", data={"group": gid}, follow_redirects=True).data.decode()
    assert "Design mit der Gruppe „Lehrstuhl“ geteilt" in r and "Mit Gruppe teilen" not in r   # no group left to share with
    assert "schon geteilt" in client.post(f"/designs/{did}/share-group", data={"group": gid}, follow_redirects=True).data.decode()
    assert "Hausfarben" in client.get(f"/groups/{gid}").data.decode()
    # a member can use and duplicate it, not change it
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    page = other.get("/designs/").data.decode()
    assert "Hausfarben" in page and "von Philipp" in page and "über die Gruppe Lehrstuhl" in page
    assert other.post(f"/designs/{did}/rename", data={"name": "Meins"}).status_code == 404
    assert other.post(f"/designs/{did}/share-group", data={"group": gid}).status_code == 404
    # duplicating makes an own design: only for who may create surveys (H9 of the security audit 2026-10-03)
    assert "Eigene Designs legt an" in other.post(f"/designs/{did}/duplicate", follow_redirects=True).data.decode()
    allow(app, OTHER)
    assert "Design dupliziert" in other.post(f"/designs/{did}/duplicate", follow_redirects=True).data.decode()
    # someone who joins later has it at once; outside the group nobody does
    third = app.test_client()
    _as(third, THIRD, "Dritte")
    assert "Hausfarben" not in third.get("/designs/").data.decode()
    assert third.post(f"/designs/{did}/duplicate").status_code == 404
    _add(client, gid, THIRD, "Dritte")
    assert "Hausfarben" in third.get("/designs/").data.decode()
    # removed from the group: gone again; the copy made before stays
    client.post(f"/groups/{gid}/members/{_member_id(client, gid, THIRD)}/remove")
    assert "Hausfarben" not in third.get("/designs/").data.decode()
    assert "Hausfarben (Kopie)" in other.get("/designs/").data.decode()
    # the owner ends the share
    client.post(f"/designs/{did}/unshare-group", data={"group": gid})
    assert "Mit mir geteilt" in other.get("/designs/").data.decode() and "über die Gruppe" not in other.get("/designs/").data.decode()
    assert other.post(f"/designs/{did}/duplicate").status_code == 404


def test_only_own_groups_and_leaving_ends_own_shares(app, client):
    gid = _group(client)
    did = _design(client)
    _add(client, gid, OTHER, "Kollegin", role="lead")
    client.post(f"/designs/{did}/share-group", data={"group": gid})
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert "Hausfarben" in other.get("/designs/").data.decode()
    client.post(f"/groups/{gid}/members/{_member_id(client, gid, ME)}/remove")   # the owner leaves
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(DesignGroupShare.id))) == 0
        assert db.session.get(Design, did) is not None                          # the design stays with its owner
    assert "Hausfarben" not in other.get("/designs/").data.decode()
    # no sharing with a group one is not in
    assert "Bitte eine Ihrer Gruppen wählen" in client.post(f"/designs/{did}/share-group", data={"group": gid}, follow_redirects=True).data.decode()


def test_deleting_a_group_or_a_design_loses_nothing_else(app, client):
    gid = _group(client)
    did, second = _design(client), _design(client, "Zweites")
    _add(client, gid, OTHER, "Kollegin")
    client.post(f"/designs/{did}/share-group", data={"group": gid})
    client.post(f"/designs/{second}/share-group", data={"group": gid})
    client.post(f"/designs/{second}/delete")
    with app.app_context():
        assert db.session.get(WorkGroup, gid) is not None
        assert db.session.scalar(db.select(db.func.count(DesignGroupShare.id))) == 1
    assert "Gruppe „Lehrstuhl“ gelöscht" in client.post(f"/groups/{gid}/delete", follow_redirects=True).data.decode()
    with app.app_context():
        assert db.session.get(WorkGroup, gid) is None and db.session.get(Design, did) is not None
        assert db.session.scalar(db.select(db.func.count(DesignGroupShare.id))) == 0
        assert db.session.scalar(db.select(db.func.count(WorkGroupMember.id))) == 0
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert "Hausfarben" not in other.get("/designs/").data.decode()


def test_a_design_of_the_rwth_design_is_usable_in_a_group(app, client):
    """Ralf 2026-10-19: no theme is reserved any more. A design made from the RWTH organisation design is usable by
    the group and keeps everything of it."""
    gid = _group(client)
    did = _design(client)
    with app.app_context():
        design = db.session.get(Design, did)
        design.data = rwth_design.look()
        db.session.commit()
    _add(client, gid, OTHER, "Kollegin")
    client.post(f"/designs/{did}/share-group", data={"group": gid})
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    page = other.get("/designs/").data.decode()
    assert "Hausfarben" in page and "vorbehalten" not in page
    with app.app_context():
        from app import designs

        assert designs.look(db.session.get(Design, did))["preset"] == "org"


def test_admin_sees_renames_and_deletes_groups(app, client):
    allow(app, OTHER)
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    gid = _group(other, "RWTH Aachen")
    assert other.get("/admin/groups").status_code == 403
    page = client.get("/admin/groups").data.decode()
    assert 'value="RWTH Aachen"' in page and "Kollegin" in page
    assert client.get(f"/groups/{gid}").status_code == 404                     # an admin is not a member
    assert "RWTH Aachen" in client.get("/admin/people").data.decode()               # column "Gruppen" of the researchers
    client.post(f"/admin/groups/{gid}", data={"name": "AG Kollegin"})
    assert "AG Kollegin" in other.get("/groups/").data.decode()
    assert "Gruppe „AG Kollegin“ gelöscht" in client.post(f"/admin/groups/{gid}", data={"action": "delete"}, follow_redirects=True).data.decode()
    assert "Sie sind in keiner Gruppe" in other.get("/groups/").data.decode()
    assert client.post("/admin/groups/999", data={"name": "x"}).status_code == 404


def test_a_group_carries_no_right_to_edit_a_design_only_a_person_gets_it(app, client):
    """A design shared with a group is used and duplicated by its members, the leads too. Changing it takes the
    right of a person: the owner shares it with that person and allows editing. Renaming, sharing and deleting
    stay with the owner."""
    gid = _group(client)
    did = _design(client)
    _add(client, gid, OTHER, "Kollegin", role="lead")
    _add(client, gid, THIRD, "Dritte")
    look = {**theme.STANDARD, "font": "serif", "font_scale": "100", "radius": "round", "accent": "#8a1c3b"}
    lead, member = app.test_client(), app.test_client()
    _as(lead, OTHER, "Kollegin")
    _as(member, THIRD, "Dritte")
    client.post(f"/designs/{did}/share-group", data={"group": gid})
    # the lead of the group: uses and duplicates, does not edit
    for who in (lead, member):
        page = who.get("/designs/").data.decode()
        assert "Hausfarben" in page and f'href="/designs/{did}"' not in page
        assert f'href="/designs/{did}"' not in who.get(f"/groups/{gid}").data.decode()
        assert who.get(f"/designs/{did}").status_code == 404
        assert who.post(f"/designs/{did}", data=look).status_code == 404
        assert who.get(f"/designs/{did}/sample").status_code == 404
        assert who.get(f"/designs/{did}/look").status_code == 200           # the small view on the tile
    # the right to edit is given to a person the design is shared with, by the owner only
    assert client.post(f"/designs/{did}/editor", data={"orcid": OTHER, "allow": "1"}).status_code == 404   # not shared in person
    client.post(f"/designs/{did}/share", data={"orcid": OTHER})
    assert lead.get(f"/designs/{did}").status_code == 404                     # shared in person: still only use
    assert lead.post(f"/designs/{did}/editor", data={"orcid": OTHER, "allow": "1"}).status_code == 404
    assert "Bearbeiten erlauben" in client.get("/designs/").data.decode()
    r = client.post(f"/designs/{did}/editor", data={"orcid": OTHER, "allow": "1"}, follow_redirects=True).data.decode()
    assert "kann das Design jetzt auch bearbeiten" in r and "darf bearbeiten" in r and "Entziehen" in r
    assert f'href="/designs/{did}"' in lead.get("/designs/").data.decode()
    assert f'href="/designs/{did}"' in lead.get(f"/groups/{gid}").data.decode()
    assert lead.get(f"/designs/{did}").status_code == 200
    r = lead.post(f"/designs/{did}", data=look, follow_redirects=True).data.decode()
    assert "Design gespeichert" in r and "Es gehört Philipp; Sie haben das Recht, es zu bearbeiten" in r
    assert lead.get(f"/designs/{did}/sample").status_code == 200
    with app.app_context():
        data = theme.clean(db.session.get(Design, did).data)
        assert data["colors"]["accent"] == "#8a1c3b" and (data["font"], data["radius"]) == ("serif", "round")
    # ownership stays with the person
    assert lead.post(f"/designs/{did}/rename", data={"name": "Meins"}).status_code == 404
    assert lead.post(f"/designs/{did}/delete").status_code == 404
    assert lead.post(f"/designs/{did}/share", data={"orcid": THIRD}).status_code == 404
    assert lead.post(f"/designs/{did}/unshare-group", data={"group": gid}).status_code == 404
    assert member.get(f"/designs/{did}").status_code == 404                   # the right of one person, not of the group
    # the owner takes the right, or ends the share: no editing; sharing again does not bring the right back
    r = client.post(f"/designs/{did}/editor", data={"orcid": OTHER}, follow_redirects=True).data.decode()
    assert "nicht mehr bearbeiten" in r and lead.get(f"/designs/{did}").status_code == 404
    client.post(f"/designs/{did}/editor", data={"orcid": OTHER, "allow": "1"})
    assert lead.get(f"/designs/{did}").status_code == 200
    client.post(f"/designs/{did}/unshare", data={"orcid": OTHER})
    assert lead.post(f"/designs/{did}", data=look).status_code == 404
    assert "Hausfarben" in lead.get("/designs/").data.decode()                # still there through the group
    client.post(f"/designs/{did}/share", data={"orcid": OTHER})
    assert lead.get(f"/designs/{did}").status_code == 404
    with app.app_context():
        assert theme.clean(db.session.get(Design, did).data)["colors"]["accent"] == "#8a1c3b"
    assert client.get(f"/designs/{did}").status_code == 200                    # the owner always


def test_a_design_of_the_rwth_design_is_edited_with_the_right_to_edit(app, client):
    gid = _group(client)
    did = _design(client)
    with app.app_context():
        db.session.get(Design, did).data = rwth_design.look()
        db.session.commit()
    _add(client, gid, OTHER, "Kollegin", role="lead")
    client.post(f"/designs/{did}/share-group", data={"group": gid})
    client.post(f"/designs/{did}/share", data={"orcid": OTHER})
    client.post(f"/designs/{did}/editor", data={"orcid": OTHER, "allow": "1"})
    lead = app.test_client()
    _as(lead, OTHER, "Kollegin")
    assert lead.get(f"/designs/{did}").status_code == 200  # no theme is reserved any more (Ralf 2026-10-19)
    assert f'href="/designs/{did}"' in lead.get("/designs/").data.decode()


def test_design_with_several_groups_and_a_note_when_none_is_left(app, client):
    first = _group(client, "Erste")
    did = _design(client)
    client.post(f"/designs/{did}/share-group", data={"group": first})
    page = client.get("/designs/").data.decode()
    assert "Mit Gruppe teilen" not in page and "Ihre Gruppe hat das Design schon" in page
    second = _group(client, "Zweite")
    page = client.get("/designs/").data.decode()
    assert "Mit Gruppe teilen" in page and f'<option value="{second}">Zweite</option>' in page and "data-no-more-groups" not in page
    client.post(f"/designs/{did}/share-group", data={"group": second})
    assert "Alle Ihre Gruppen haben das Design schon" in client.get("/designs/").data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(DesignGroupShare.id))) == 2


def test_share_from_the_group_page(app, client, survey):
    """UI audit 2026-10-04, R95: the group page offers "+ Befragung teilen" and "+ Design teilen" for what one may
    share and is not shared yet, and lists the shared linked elements; ending a share asks the same everywhere."""
    from app.models import SurveyGroup

    gid = _group(client)
    did = _design(client)
    page = client.get(f"/groups/{gid}").data.decode()
    assert "+ Befragung teilen" in page and "+ Design teilen" in page and 'id="linked"' in page
    assert "Bitte etwas wählen" in client.post(f"/groups/{gid}/share", data={"kind": "design", "id": "999"}, follow_redirects=True).data.decode()
    r = client.post(f"/groups/{gid}/share", data={"kind": "design", "id": did}, follow_redirects=True).data.decode()
    assert "Das Design „Hausfarben“ ist jetzt mit der Gruppe „Lehrstuhl“ geteilt" in r and "+ Design teilen" not in r
    r = client.post(f"/groups/{gid}/share", data={"kind": "survey", "id": 1}, follow_redirects=True).data.decode()
    assert "ist jetzt mit der Gruppe" in r and "+ Befragung teilen" not in r
    with app.app_context():
        assert db.session.scalar(db.select(SurveyGroup).filter_by(group_id=gid, survey_id=1)) is not None
    # someone else in the group cannot share what is not theirs
    _add(client, gid, OTHER, "Kollegin")
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert "Bitte etwas wählen" in other.post(f"/groups/{gid}/share", data={"kind": "design", "id": did}, follow_redirects=True).data.decode()
    # the designs page ends the share with × and the same question as elsewhere
    page = client.get("/designs/").data.decode()
    assert 'data-confirm="Freigabe für die Gruppe „Lehrstuhl“ beenden?"' in page and 'data-confirm-ok="Freigabe beenden"' in page
