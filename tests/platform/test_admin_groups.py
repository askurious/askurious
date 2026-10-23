"""Groups made by site admins (gruppen-freischaltung-konzept.md, Ralf 2026-10-10, E1-E4): the admin is no member,
names the first lead by ORCID iD or the address of an account, "Mitglieder dürfen eigene Befragungen anlegen" is ticked
at first, and a lead from outside can be appointed later. Adding and removing members stays with the group (H7)."""
from app import orcid_api
from app.auth import may_admit, may_create_surveys
from app.extensions import db
from app.models import User, UserLogin, WorkGroup, WorkGroupMember
from tests.conftest import CREATE_ALL

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"
FOURTH = "0000-0000-0000-0239"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _new(client, **form):
    data = {"name": "Lehrstuhl X", "lead": OTHER, "lead_name": "Kollegin", "members_create": "1"}
    data.update(form)
    return client.post("/admin/groups/new", data={k: v for k, v in data.items() if v is not None},
                       follow_redirects=True).data.decode()


def _last_group(client):
    with client.application.app_context():
        return db.session.scalar(db.select(WorkGroup).order_by(WorkGroup.id.desc()))


def test_admin_creates_a_group_without_being_in_it(client):
    page = client.get("/admin/groups").data.decode()
    assert "Gruppe anlegen" in page and "Wer dazukommt, bekommt „Anlegen“" in page
    # "Neue Gruppe …" opens a dialog, as "Person hinzufügen …" (Ralf 2026-10-20)
    assert '<a class="button" href="#gruppe-anlegen" data-person-add aria-haspopup="dialog">' in page
    assert 'action="/admin/groups/new"' in page[page.index('id="gruppe-anlegen"'):]
    page = _new(client)
    assert "Gruppe „Lehrstuhl X“ angelegt; Kollegin leitet sie. Sie selbst sind nicht Mitglied." in page
    with client.application.app_context():
        group = db.session.scalar(db.select(WorkGroup).filter_by(name="Lehrstuhl X"))
        assert group.may_create is True
        assert [(m.orcid, m.role, m.name) for m in group.members] == [(OTHER, "lead", "Kollegin")]
        assert group.created_by_id == db.session.scalar(db.select(User.id).filter_by(orcid=ME))
    # the admin does not see it under "Gruppen", the lead does, and may create and bring in people
    assert "Lehrstuhl X" not in client.get("/groups/").data.decode()
    _as(client, OTHER, "Kollegin")
    assert "Lehrstuhl X" in client.get("/groups/").data.decode()
    with client.application.app_context():
        lead = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert db.session.get(UserLogin, lead.id).kinds == CREATE_ALL  # "Anlegen" given once by the group (E3)
        assert may_create_surveys(lead) and may_admit(lead, THIRD)
    gid = _last_group(client).id
    page = client.post(f"/groups/{gid}/members", data={"orcid": THIRD, "name": "Dritte"}, follow_redirects=True).data.decode()
    assert "Dritte ist jetzt in der Gruppe" in page
    _as(client, THIRD, "Dritte")
    with client.application.app_context():
        assert may_create_surveys(db.session.scalar(db.select(User).filter_by(orcid=THIRD)))


def test_without_the_tick_members_only_sign_in(client):
    _new(client, members_create=None)
    assert _last_group(client).may_create is False
    _as(client, OTHER, "Kollegin")
    with client.application.app_context():
        lead = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert not may_create_surveys(lead) and not may_admit(lead, THIRD)


def test_lead_by_account_address_and_errors(client, monkeypatch):
    with client.application.app_context():
        user = User(orcid="q-abcdefghijklmnop", name="Ohne Orcid", number=900, id=900)
        db.session.add(user)
        db.session.add(UserLogin(user_id=900, email="ohne@example.org"))
        db.session.commit()
    assert "angelegt; Ohne Orcid leitet sie" in _new(client, lead="Ohne@Example.org", lead_name="")
    with client.application.app_context():
        assert db.session.scalar(db.select(WorkGroupMember.user_id).order_by(WorkGroupMember.id.desc())) == 900
    assert "kein Konto" in _new(client, name="Zwei", lead="niemand@example.org")
    assert "Bitte eine ORCID iD" in _new(client, name="Drei", lead="kein Schlüssel")
    assert "Bitte einen Namen" in _new(client, name="  ")
    # the name comes from ORCID when not given; an iD that does not exist is refused
    monkeypatch.setattr(orcid_api, "lookup", lambda orcid: {"orcid": orcid, "name": "Aus Orcid"} if orcid == THIRD else None)
    assert "Aus Orcid leitet sie" in _new(client, name="Vier", lead=f"https://orcid.org/{THIRD}", lead_name="")
    assert "Diese ORCID iD gibt es nicht" in _new(client, name="Fünf", lead=FOURTH, lead_name="")
    with client.application.app_context():
        assert set(db.session.scalars(db.select(WorkGroup.name))) == {"Lehrstuhl X", "Vier"}


def test_admin_appoints_a_lead_from_outside_but_adds_no_members(client):
    _new(client)
    gid = _last_group(client).id
    page = client.post(f"/admin/groups/{gid}/lead", data={"lead": THIRD, "lead_name": "Dritte"},
                       follow_redirects=True).data.decode()
    assert "Dritte leitet jetzt die Gruppe" in page
    with client.application.app_context():
        assert sorted((m.orcid, m.role) for m in db.session.get(WorkGroup, gid).members) == sorted([(OTHER, "lead"), (THIRD, "lead")])
    # an existing member is made lead the same way, not added twice
    client.post(f"/groups/{gid}/members", data={"orcid": FOURTH, "name": "Vierte"})  # the admin is no lead: refused
    with client.application.app_context():
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=FOURTH)) is None
    # only for site admins
    _as(client, OTHER, "Kollegin")
    assert client.post(f"/admin/groups/{gid}/lead", data={"lead": FOURTH, "lead_name": "Vierte"}).status_code == 403
    assert client.post("/admin/groups/new", data={"name": "X", "lead": FOURTH}).status_code == 403
