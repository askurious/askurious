"""Join links and lead links of groups (app/joinlinks.py, gruppen-freischaltung-konzept.md E2, E5-E8): a lead who may
create surveys makes a link with validity and highest number of joins, whoever opens it and signs in is a member at
once; a site admin gives a group its lead with a lead link used once."""
import re

from app import joinlinks
from app.auth import _may_sign_in, may_create_surveys
from app.extensions import db
from app.models import GroupJoinLink, MemberInvite, User, WorkGroup, WorkGroupMember
from tests.conftest import mail_on

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"
FOURTH = "0000-0000-0000-0239"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


_SEMINARS = iter(range(1, 1000))


def _admin_group(client, may_create="1"):
    """A group made in the administration, led by OTHER (signed in now); names are unique within an organisation."""
    data = {"name": f"Seminar {next(_SEMINARS)}", "lead": OTHER, "lead_name": "Kollegin"}
    if may_create:
        data["members_create"] = "1"
    client.post("/admin/groups/new", data=data)
    with client.application.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).order_by(WorkGroup.id.desc()))
    _as(client, OTHER, "Kollegin")
    return gid


def _make(client, gid, days=14, uses=2):
    page = client.post(f"/join/group/{gid}", data={"days": days, "max_uses": uses}, follow_redirects=True).data.decode()
    return re.search(r'id="join-link-new" value="([^"]+)"', page).group(1).split("/join/")[1], page


def test_lead_makes_a_link_and_people_join(app, client):
    gid = _admin_group(client)
    page = client.get(f"/groups/{gid}").data.decode()
    assert "Beitrittslink" in page and "Link erzeugen" in page
    token, page = _make(client, gid)
    assert "Der neue Link wird nur jetzt angezeigt" in page and "0 von 2 genutzt" in page
    assert "join-link-new" not in client.get(f"/groups/{gid}").data.decode()  # once
    with app.app_context():
        assert db.session.scalar(db.select(GroupJoinLink)).token_hash != token  # only the hash
    # signed in: one click
    guest = app.test_client()
    _as(guest, THIRD, "Dritte")
    assert "Beitreten als Dritte" in guest.get(f"/join/{token}").data.decode()
    assert "Sie sind jetzt in der Gruppe „Seminar " in guest.post(f"/join/{token}", follow_redirects=True).data.decode()
    with app.app_context():
        member = db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=THIRD))
        assert member.role == "member" and member.via == "link"
        assert may_create_surveys(db.session.scalar(db.select(User).filter_by(orcid=THIRD)))  # the group's tick
    assert "über Beitrittslink" in client.get(f"/groups/{gid}").data.decode()
    # with ORCID: the token waits encrypted in the session, the member is made before the sign-in check
    stranger = app.test_client()
    app.config["ORCID_CLIENT_ID"] = "test"
    assert "Mit ORCID anmelden und beitreten" in stranger.get(f"/join/{token}").data.decode()
    assert "/auth/login" in stranger.post(f"/join/{token}", data={"way": "orcid"}).headers["Location"]
    with stranger.session_transaction() as s:
        held = s[joinlinks.SESSION_KEY]
    assert token not in str(held)
    assert not _may_sign_in_ctx(app, FOURTH)
    with app.test_request_context():
        from flask import session

        session[joinlinks.SESSION_KEY] = held
        assert joinlinks.accept_after_orcid(FOURTH, "Vierte").endswith(f"/groups/{gid}")
    assert _may_sign_in_ctx(app, FOURTH)
    # two of two used: the link is dead
    assert "gilt nicht mehr" in app.test_client().get(f"/join/{token}", follow_redirects=True).data.decode()


def _may_sign_in_ctx(app, orcid):
    with app.app_context():
        return _may_sign_in(orcid)


def test_only_leads_who_may_create_make_links(app, client):
    gid = _admin_group(client, may_create=None)  # the lead came in by the group and may not create
    page = client.get(f"/groups/{gid}").data.decode()
    assert "Beitrittslinks erzeugt nur eine Leitung, die selbst Befragungen anlegen darf" in page
    assert client.post(f"/join/group/{gid}", data={"days": 14, "max_uses": 5}).status_code == 403
    # a plain member may not either
    _as(client, ME, "Philipp")
    gid2 = _admin_group(client)
    client.post(f"/groups/{gid2}/members", data={"orcid": THIRD, "name": "Dritte"})
    _as(client, THIRD, "Dritte")
    assert "Das kann nur die Leitung" in client.post(f"/join/group/{gid2}", data={"days": 14, "max_uses": 5},
                                                     follow_redirects=True).data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(GroupJoinLink.id))) == 0
    _as(client, OTHER, "Kollegin")
    assert client.post(f"/join/group/{gid2}", data={"days": 3, "max_uses": 5}).status_code == 400


def test_revoke_and_expire(app, client):
    gid = _admin_group(client)
    token, _page = _make(client, gid)
    with app.app_context():
        lid = db.session.scalar(db.select(GroupJoinLink.id))
    assert "Link widerrufen" in client.post(f"/join/revoke/{lid}", follow_redirects=True).data.decode()
    assert "gilt nicht mehr" in app.test_client().get(f"/join/{token}", follow_redirects=True).data.decode()
    token, _page = _make(client, gid)
    with app.app_context():
        from datetime import timedelta

        from app.models import utcnow

        row = db.session.scalar(db.select(GroupJoinLink))
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert "gilt nicht mehr" in app.test_client().get(f"/join/{token}", follow_redirects=True).data.decode()
    # deleting the group takes its links with it
    _make(client, gid)
    client.post(f"/groups/{gid}/delete")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(GroupJoinLink.id))) == 0


def test_join_by_mail_sends_an_invitation_of_the_lead(app, client):
    sent = mail_on(app)
    gid = _admin_group(client)
    token, _page = _make(client, gid, uses=1)
    guest = app.test_client()
    page = guest.post(f"/join/{token}", data={"way": "email", "email": "neu@example.org"}, follow_redirects=True).data.decode()
    assert "Wir haben eine E-Mail an neu@example.org geschickt" in page
    assert sent[-1]["To"] == "neu@example.org" and "Seminar" in sent[-1]["Subject"]
    with app.app_context():
        invite = db.session.scalar(db.select(MemberInvite))
        assert invite.group_id == gid and invite.role == "member"
        assert db.session.scalar(db.select(GroupJoinLink)).uses == 1  # each mail counts
    assert "gilt nicht mehr" in guest.get(f"/join/{token}", follow_redirects=True).data.decode()


def test_without_mail_every_group_link_offers_an_account_with_password(app, client):
    """Ralf 2026-10-24: a person without ORCID must be able to join by a link; before, the account with e-mail address
    and password stood only on links of courses."""
    gid = _admin_group(client)
    token, _page = _make(client, gid)
    guest = app.test_client()
    page = guest.get(f"/join/{token}").data.decode()
    assert "Konto anlegen und beitreten" in page and "Lehrveranstaltung" not in page
    good = "lange genug passphrase 2026"
    page = guest.post(f"/join/{token}", data={"way": "password", "name": "Ohne Orcid", "email": "ohne@example.org",
                                              "password": good, "repeat": good}, follow_redirects=True).data.decode()
    assert "Konto angelegt, Sie sind jetzt in „Seminar " in page
    with app.app_context():
        member = db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, name="Ohne Orcid"))
        assert member is not None and member.role == "member" and member.via == "link"
    with app.app_context():                             # the organisation's cap of accounts holds here too
        from app import orgs

        orgs.main().max_accounts = orgs.counts(orgs.main())["homes"]
        db.session.commit()
    full = app.test_client().post(f"/join/{token}", data={"way": "password", "name": "Zu Viel",
                                                          "email": "zuviel@example.org", "password": good,
                                                          "repeat": good}, follow_redirects=True).data.decode()
    assert "keine freien Konten" in full
    mail_on(app)                                       # with mail on, the address is confirmed by an invitation
    assert "Konto anlegen und beitreten" not in app.test_client().get(f"/join/{token}").data.decode()


def test_admin_lead_link(app, client):
    page = client.post("/admin/groups/new", data={"name": "Ohne Leitung", "lead": "", "lead_link": "1", "members_create": "1"},
                       follow_redirects=True).data.decode()
    assert "Schicken Sie den Leitungslink" in page and "Leitungslink für „Ohne Leitung“" in page
    token = re.search(r'id="lead-link-new" value="([^"]+)"', page).group(1).split("/join/")[1]
    assert "lead-link-new" not in client.get("/admin/groups").data.decode()
    assert "offener Leitungslink" in client.get("/admin/groups").data.decode()
    with app.app_context():
        group = db.session.scalar(db.select(WorkGroup).filter_by(name="Ohne Leitung"))
        gid = group.id
        assert group.members == []
    guest = app.test_client()
    _as(guest, THIRD, "Dritte")
    assert "Leitung übernehmen als Dritte" in guest.get(f"/join/{token}").data.decode()
    guest.post(f"/join/{token}")
    with app.app_context():
        member = db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid))
        assert member.orcid == THIRD and member.role == "lead" and member.via == "leadlink"
        assert db.session.scalar(db.select(db.func.count(GroupJoinLink.id))) == 0  # used once
    # also for an existing group, and only for site admins
    assert "Leitungslink für „Ohne Leitung“ erzeugt" in client.post(f"/admin/groups/{gid}/lead", data={"action": "link"},
                                                                   follow_redirects=True).data.decode()
    assert guest.post(f"/admin/groups/{gid}/lead", data={"action": "link"}).status_code == 403
    # without lead and without the tick: the site admin leads it, as everyone does by default (2026-10-24)
    assert "Sie leiten sie" in client.post("/admin/groups/new", data={"name": "X", "lead": ""},
                                           follow_redirects=True).data.decode()
