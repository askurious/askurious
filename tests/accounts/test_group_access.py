"""A survey shared with a group (app/access.py): its members may open it as "viewer" or "editor", live, without
becoming co-owners (authors); "admin" stays with persons named on the survey. And what only a site admin gives a
group: creating surveys, and the theme of an institution."""
import re

from app import theme
from app.extensions import db
from app.models import Survey, SurveyGroup, SurveyMember, User, WorkGroup, WorkGroupMember
from tests.conftest import add, allow, fill_consent

ME = "0000-0000-0000-001X"       # site admin and admin of survey 1 (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _group(client, name="Lehrstuhl", members=((OTHER, "Kollegin"),)):
    client.post("/groups/new", data={"name": name})
    with client.application.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).order_by(WorkGroup.id.desc()))
    for orcid, who in members:
        client.post(f"/groups/{gid}/members", data={"orcid": orcid, "name": who})
    return gid


def _share(client, gid, role="viewer", survey_id=1):
    return client.post(f"/surveys/{survey_id}/access-groups", data={"group": gid, "role": role}, follow_redirects=True).data.decode()


def _link(client, survey_id=1):
    with client.application.app_context():
        return db.session.scalar(db.select(SurveyGroup.id).filter_by(survey_id=survey_id))


def _other(app):
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    return other


def test_share_a_survey_with_a_group_as_viewer(app, client, survey):
    add(client, "text_input")                                                  # item 2
    gid = _group(client)
    other = _other(app)
    assert other.get("/surveys/1").status_code == 404                          # in the group, but nothing shared yet
    assert "Test" not in other.get("/dashboard/").data.decode()
    page = client.get("/surveys/1").data.decode()
    assert "Gruppen mit Zugriff" in page and f'<option value="{gid}">Lehrstuhl</option>' in page
    r = _share(client, gid)
    assert "Die Gruppe „Lehrstuhl“ kann die Befragung jetzt ansehen" in r and "Kollegin" in r   # members are listed
    assert "Diese Gruppe hat schon Zugriff" in _share(client, gid)
    # the member sees it on the start page, with the role and where it comes from
    html = other.get("/dashboard/").data.decode()
    assert "Test" in html and "Ansehen" in html and "über die Gruppe Lehrstuhl" in html
    assert other.get("/surveys/1/editor/").status_code == 200
    assert "Ihre Rolle ist „Ansehen“" in other.get("/surveys/1/editor/").data.decode()
    other.post("/surveys/1/editor/items/2/delete")
    other.post("/surveys/1/meta", data={"title": "Gekapert"})
    assert other.get("/surveys/1/export/data.csv", follow_redirects=True).request.path == "/dashboard/"   # no data for viewers
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.title == "Test" and [i.id for i in s.blocks[1].items] == [2]
        # access, not authorship: the list of co-owners is unchanged
        assert [m.orcid for m in s.members] == [ME]
    settings = other.get("/surveys/1").data.decode()
    assert "Gruppen mit Zugriff" in settings and "Zugriff geben" not in settings   # sees the groups, decides nothing
    assert "Test" in other.get(f"/groups/{gid}").data.decode()                 # the group page lists it


def test_a_group_gets_viewer_or_editor_never_admin(app, client, survey):
    """A group can look at the survey or edit it with its raw data (Ralf, 2026-10-05, after M2 of the audit
    2026-10-04 had limited it to "viewer"); "admin" stays with persons named on the survey."""
    gid = _group(client)
    _share(client, gid, "admin")
    with app.app_context():
        assert db.session.get(SurveyGroup, _link(client)).role == "viewer"      # no admin through a group
    link = _link(client)
    page = client.get("/surveys/1").data.decode()
    assert 'aria-label="Rolle der Gruppe Lehrstuhl"' in page and ">Admin</option>" not in page.split('aria-label="Rolle der Gruppe Lehrstuhl"')[1].split("</select>")[0]
    r = client.post("/surveys/1/access-groups/role", data={"link": link, "role": "admin"}, follow_redirects=True)
    assert "Admin wird nur, wer als Person beteiligt ist" in r.data.decode()
    r = client.post("/surveys/1/access-groups/role", data={"link": link, "role": "editor"}, follow_redirects=True)
    assert "Rolle „Bearbeiten“" in r.data.decode()
    other = _other(app)
    other.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"})       # edits
    assert other.get("/surveys/1/export/data.csv").status_code == 200                    # with the raw data
    # admin endpoints stay closed: settings, archiving, co-owners, groups with access, delete (starting is
    # "Bearbeiten" since 2026-10-06, tests/editor/test_editor_fieldwork.py)
    other.post("/surveys/1/meta", data={"title": "Gekapert"})
    other.post("/surveys/1/archive", data={"archived": "1"})
    other.post("/surveys/1/members", data={"orcid": THIRD, "name": "Dritte", "role": "admin"})
    other.post("/surveys/1/access-groups/role", data={"link": link, "role": "viewer"})
    r = other.post("/surveys/1/access-groups/remove", data={"link": link}, follow_redirects=True).data.decode()
    assert "Dafür fehlen die Rechte" in r and "nötig ist „Admin“" in r
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert len(s.blocks[1].items) == 1 and s.title == "Test" and s.status == "draft" and not s.archived
        assert [m.orcid for m in s.members] == [ME] and s.group_links[0].role == "editor"
        assert s.role_of(db.session.scalar(db.select(User).filter_by(orcid=OTHER))) == "editor"
    client.post("/surveys/1/access-groups/role", data={"link": link, "role": "viewer"})       # back to looking
    assert other.get("/surveys/1/export/data.csv", follow_redirects=True).request.path == "/dashboard/"


def test_the_highest_role_counts(app, client, survey):
    gid = _group(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="viewer", position=1))
        db.session.commit()
    _share(client, gid, "editor")
    other = _other(app)
    other.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        other_user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert len(s.blocks[1].items) == 1 and s.role_of(other_user) == "editor"   # the group's role is the higher one
        s.members[1].role = "admin"                                            # the personal role is the higher one now
        db.session.commit()
        assert db.session.get(Survey, 1).role_of(other_user) == "admin"
    assert "Kollegin, Gruppe Lehrstuhl" not in other.get("/dashboard/").data.decode()
    assert "Gruppe Lehrstuhl" in other.get("/dashboard/").data.decode() and "über die Gruppe" not in other.get("/dashboard/").data.decode()


def test_access_is_live_joining_leaving_removing_deleting(app, client, survey):
    gid = _group(client, members=())
    _share(client, gid, "editor")
    other = _other(app)
    assert other.get("/surveys/1").status_code == 404
    client.post(f"/groups/{gid}/members", data={"orcid": OTHER, "name": "Kollegin"})      # joins: access at once
    assert other.get("/surveys/1").status_code == 200
    with app.app_context():
        mid = db.session.scalar(db.select(WorkGroupMember.id).filter_by(group_id=gid, orcid=OTHER))
    other.post(f"/groups/{gid}/members/{mid}/remove")                                     # leaves: gone with the next request
    assert other.get("/surveys/1").status_code == 404
    assert other.post("/surveys/1/editor/blocks/2/items", data={"type": "text_input"}).status_code == 404
    client.post(f"/groups/{gid}/members", data={"orcid": OTHER, "name": "Kollegin"})
    assert other.get("/surveys/1").status_code == 200
    r = client.post("/surveys/1/access-groups/remove", data={"link": _link(client)}, follow_redirects=True).data.decode()
    assert "hat keinen Zugriff mehr" in r                                                 # the survey's admin ends it
    assert other.get("/surveys/1").status_code == 404
    _share(client, gid)
    assert other.get("/surveys/1").status_code == 200
    client.post(f"/groups/{gid}/delete")                                                  # the group is deleted: the survey stays
    assert other.get("/surveys/1").status_code == 404
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s is not None and s.group_links == [] and [m.orcid for m in s.members] == [ME]
        assert db.session.scalar(db.select(db.func.count(SurveyGroup.id))) == 0
        assert len(s.blocks[1].items) == 0


def test_only_own_groups_and_only_survey_admins_share(app, client, survey):
    allow(app, OTHER)
    other = _other(app)
    foreign = _group(other, "Fremde Gruppe", members=())                        # the admin of survey 1 is not in it
    assert "Bitte eine Ihrer Gruppen wählen" in _share(client, foreign)
    assert "Bitte eine Ihrer Gruppen wählen" in _share(client, 999)
    assert "Fremde Gruppe" not in client.get("/surveys/1").data.decode()
    assert other.post("/surveys/1/access-groups", data={"group": foreign}).status_code == 404   # no access to survey 1
    assert client.post("/surveys/1/access-groups/remove", data={"link": 999}).status_code == 404
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SurveyGroup.id))) == 0


def test_deleting_a_survey_leaves_the_group(app, client, survey):
    gid = _group(client)
    _share(client, gid)
    client.post("/surveys/1/archive", data={"archived": "1"})
    client.post("/surveys/1/delete")
    with app.app_context():
        assert db.session.get(Survey, 1) is None and db.session.get(WorkGroup, gid) is not None
        assert db.session.scalar(db.select(db.func.count(SurveyGroup.id))) == 0
    assert "noch keine Befragung geteilt" in client.get(f"/groups/{gid}").data.decode()


def test_authors_stay_authors(app, client, survey):
    """Consent, privacy page and the admin's count of co-owners know the co-owners only."""
    fill_consent(client)
    gid = _group(client)
    _share(client, gid, "editor")
    other = _other(app)
    page = client.get("/surveys/1").data.decode()
    consent_panel = page.split('id="consent-facts"')[1]
    assert "Philipp" in consent_panel and "Kollegin" not in consent_panel       # not offered as responsible researcher
    other.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": OTHER})    # admin endpoint anyway
    preview = other.get("/surveys/1/editor/preview", follow_redirects=True).data.decode()
    assert "Kollegin" not in preview
    with app.app_context():
        assert [m.orcid for m in db.session.get(Survey, 1).members] == [ME]


def test_start_page_filter_footer_and_pin(app, client, survey):
    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    assert "dash-groups" not in client.get("/dashboard/").data.decode()        # no group with surveys: no filter
    gid = _group(client)
    _share(client, gid, survey_id=2)
    html = client.get("/dashboard/").data.decode()
    assert 'class="dash-group-choice"' in html and f'<option value="{gid}"' in html
    filtered = client.get(f"/dashboard/?group={gid}").data.decode()
    assert "Zweite" in filtered and ">Test<" not in filtered
    assert ">Test<" in client.get("/dashboard/?group=999").data.decode()       # not a group of mine: no filter
    # the member: the shared survey is listed as theirs, the other one does not exist for them
    with app.app_context():
        s = db.session.get(Survey, 2)
        s.status, s.max_responses = Survey.RUNNING, 10
        db.session.commit()
    other = _other(app)
    page = other.get("/dashboard/").data.decode()
    assert "Zweite" in page and ">Test<" not in page
    # a survey reached through a group can be pinned to the top like an own one
    assert other.post("/dashboard/pin/2").status_code == 302
    assert "is-pinned" in other.get("/dashboard/").data.decode()
    assert other.post("/dashboard/pin/1").status_code == 404                   # no access: nothing to pin


def test_phone_link_of_a_group_member_ends_with_the_access(app, client, survey):
    add(client, "info")
    gid = _group(client)
    _share(client, gid)
    other = _other(app)
    html = other.get("/surveys/1/editor/preview?device=mobile").data.decode()
    path = "/" + re.search(r'value="(https?://[^"]+/p/[^"]+)"', html).group(1).split("://", 1)[1].split("/", 1)[1]
    phone = app.test_client()
    assert phone.get(path, follow_redirects=True).status_code == 200
    client.post("/surveys/1/access-groups/remove", data={"link": _link(client)})
    assert phone.get(path, follow_redirects=True).status_code == 404
    assert phone.post(path + "/comment", data={"block_id": "2", "text": "x"}).status_code == 404


def test_sample_size_page_knows_group_access(app, client, survey):
    gid = _group(client)
    other = _other(app)
    assert "„Test“" not in other.get("/sample-size/?survey=1").data.decode()
    _share(client, gid)
    assert "Test" in other.get("/sample-size/?survey=1").data.decode()


def test_wizard_team_step_offers_groups(app, client, survey):
    """The setup wizard's step "Team" shows the same panel: a new survey can be shared with a group right away."""
    gid = _group(client)
    page = client.get("/surveys/1/setup/team").data.decode()
    assert "Gruppen mit Zugriff" in page and f'<option value="{gid}">Lehrstuhl</option>' in page
    r = client.post("/surveys/1/access-groups", data={"group": gid, "role": "editor", "next": "/surveys/1/setup/team"})
    assert r.headers["Location"].endswith("/surveys/1/setup/team")             # back in the wizard
    with app.app_context():
        assert db.session.get(SurveyGroup, _link(client)).role == "editor"   # the role asked for


def test_admin_lets_a_group_create_surveys(app, client):
    other = _other(app)
    assert "Neue Befragung" not in other.get("/dashboard/", follow_redirects=True).data.decode()
    gid = _group(client)
    page = other.get("/dashboard/").data.decode()
    assert "Neue Befragung" not in page                                        # in a group: signs in, creates nothing
    assert "Neue Gruppe" not in other.get("/groups/").data.decode()
    assert other.post(f"/admin/groups/{gid}", data={"action": "may_create", "value": "1"}).status_code == 403
    r = client.post(f"/admin/groups/{gid}", data={"action": "may_create", "value": "1"}, follow_redirects=True).data.decode()
    assert "haben jetzt „Anlegen“" in r and "<strong>ja</strong>" in r
    assert "</svg> Fragebogen</a>" in other.get("/dashboard/").data.decode()
    assert "Neue Gruppe" in other.get("/groups/").data.decode()                # who may create surveys may create groups
    other.post("/surveys/new", data={"title": "Eigene", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        assert db.session.scalar(db.select(Survey.title)) == "Eigene"
    # taken back on the group: the members keep "Anlegen" (E3 of konzept-stufe-anlegen-2026-10-18.md), an admin
    # takes it on the account
    client.post(f"/admin/groups/{gid}", data={"action": "may_create", "value": "0"})
    assert "</svg> Fragebogen</a>" in other.get("/dashboard/").data.decode()
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "0"})
    assert "an die Admins der Plattform" in other.post("/surveys/new", data={"title": "Noch eine"}, follow_redirects=True).data.decode()
    assert "Eigene" in other.get("/dashboard/").data.decode()


def test_several_groups_and_a_note_when_none_is_left(app, client, survey):
    """A survey can be shared with several groups. The choice lists the own groups without access; when none is
    left it says so instead of disappearing."""
    first = _group(client, "Erste", members=())
    assert "data-no-more-groups" not in client.get("/surveys/1").data.decode()
    _share(client, first)
    page = client.get("/surveys/1").data.decode()
    assert "Zugriff geben" not in page and "data-no-more-groups" in page and "Ihre Gruppe hat schon Zugriff" in page
    second = _group(client, "Zweite", members=((OTHER, "Kollegin"),))
    page = client.get("/surveys/1").data.decode()
    assert "Zugriff geben" in page and f'<option value="{second}">Zweite</option>' in page
    assert f'<option value="{first}">' not in page and "data-no-more-groups" not in page
    _share(client, second, "editor")
    page = client.get("/surveys/1").data.decode()
    assert "Alle Ihre Gruppen haben schon Zugriff" in page and "Erste" in page and "Zweite" in page
    with app.app_context():
        assert sorted((l.group.name, l.role) for l in db.session.get(Survey, 1).group_links) == [("Erste", "viewer"), ("Zweite", "editor")]
    assert _other(app).get("/surveys/1/export/data.csv").status_code == 200   # through "Zweite": with the raw data
    # someone who only looks at the survey gets no such note
    assert "data-no-more-groups" not in _other(app).get("/surveys/1").data.decode()
