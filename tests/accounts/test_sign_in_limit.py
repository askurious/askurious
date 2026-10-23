"""Open sign-up up to a limit (app/signin.py, gruppen-freischaltung-konzept.md Stufe 3, E9-E14): "every ORCID holder"
has three states, only accounts that came by the switch alone count, a full limit puts new people on a waiting list,
raising it lets the first of them in, Slack hears of 80 % and full once per limit."""
from app import notify, signin
from app.extensions import db
from app.models import SignInWait, SiteSetting, User, UserLogin, WorkGroupMember

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
IDS = ["0000-0002-1825-0097", "0000-0000-0000-0124", "0000-0000-0000-0239", "0000-0000-0000-0343"]


def _orcid(app, monkeypatch, orcid, name="Neu", c=None):
    """Sign in through the ORCID round trip with ORCID answering for this iD, in a fresh browser (or in `c`)."""
    from app import auth, orcid_api

    class Answer:
        status_code = 200

        @staticmethod
        def json():
            return {"orcid": orcid, "name": name}

    app.config["ORCID_CLIENT_ID"] = "client"
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: Answer)
    c = c or app.test_client()
    c.post("/auth/login")
    with c.session_transaction() as s:
        state = s["oauth_state"]
    return c.get(f"/auth/orcid/callback?state={state}&code=x", follow_redirects=True).data.decode()


def _mode(client, mode, limit=None):
    data = {"action": "mode", "mode": mode}
    if limit:
        data["limit"] = limit
    return client.post("/admin/admit", data=data, follow_redirects=True).data.decode()


def _set_limit(app, n):
    """A limit below the steps, for the test: the setting as the switch stores it."""
    with app.app_context():
        db.session.get(SiteSetting, signin.LIMIT).value = str(n)
        db.session.commit()


def _came_via(app, orcid):
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        login = db.session.get(UserLogin, user.id) if user else None
        return login.came_via if login else None


def test_three_states_and_the_count(app, client, monkeypatch):
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True  # as on the servers (tests/conftest.py switches it off)
    told = []
    monkeypatch.setattr(notify, "signup_limit", lambda used, top, level: told.append((used, top, level)))
    page = client.get("/admin/people").data.decode()
    assert "Freie Anmeldung mit ORCID" in page and 'name="mode" value="off" checked' in page
    page = _mode(client, "cap", 50)
    assert "bis 50 Konten so dazugekommen sind" in page and "0 von 50 Plätzen belegt" in page
    _set_limit(app, 2)
    assert "Kein Konto" not in _orcid(app, monkeypatch, IDS[0])
    assert _came_via(app, IDS[0]) == "open"
    with app.app_context():
        assert "create" in db.session.get(UserLogin, db.session.scalar(db.select(User.id).filter_by(orcid=IDS[0]))).kinds.values()  # E14
    assert told == []
    _orcid(app, monkeypatch, IDS[1])
    assert told == [(2, 2, "full")]                                          # once, at full (80 % of 2 is skipped)
    # full: the next one waits, with its place
    page = _orcid(app, monkeypatch, IDS[2], "Dritte")
    assert "Sie stehen auf der Warteliste" in page and "Platz 1" in page
    assert "Platz 2" in _orcid(app, monkeypatch, IDS[3], "Vierte")
    assert "Platz 1" in _orcid(app, monkeypatch, IDS[2], "Dritte")          # asking again keeps the place
    page = client.get("/admin/people").data.decode()
    assert "2 von 2 Plätzen belegt" in page and "Die Obergrenze ist erreicht" in page and "Warteliste" in page
    # those who are in stay in
    assert "Warteliste" not in _orcid(app, monkeypatch, IDS[0])
    # raising lets the first of the list in
    page = _mode(client, "cap", 50)
    assert "2 Personen von der Warteliste sind jetzt freigeschaltet" in page
    _set_limit(app, 3)
    with app.app_context():
        rows = {w.orcid: w.admitted_at for w in db.session.scalars(db.select(SignInWait))}
        assert rows[IDS[2]] is not None and rows[IDS[3]] is not None
    _orcid(app, monkeypatch, IDS[2])
    assert _came_via(app, IDS[2]) == "open"
    with app.app_context():
        assert db.session.get(SignInWait, IDS[2]) is None                     # off the list once signed in


def test_only_the_switch_alone_counts(app, client, monkeypatch):
    _mode(client, "cap", 50)
    _set_limit(app, 1)
    # a member of a group comes in without counting
    client.post("/admin/groups/new", data={"name": "Seminar", "lead": IDS[0], "lead_name": "Leitung"})
    _orcid(app, monkeypatch, IDS[0])
    assert _came_via(app, IDS[0]) is None
    _orcid(app, monkeypatch, IDS[1])
    assert _came_via(app, IDS[1]) == "open"
    assert "Warteliste" in _orcid(app, monkeypatch, IDS[2])
    # a deleted account gives its place back: the first of the list moves up
    with app.app_context():
        from app import accounts

        user = db.session.scalar(db.select(User).filter_by(orcid=IDS[1]))
        accounts.delete(user, "admin", admin=db.session.scalar(db.select(User).filter_by(orcid=ME)))
    client.get("/admin/people")                                                      # the panel lets the next one in
    assert "Warteliste" not in _orcid(app, monkeypatch, IDS[2])
    assert _came_via(app, IDS[2]) == "open"


def test_off_and_without_limit(app, client, monkeypatch):
    assert "nicht freigeschaltet" in _orcid(app, monkeypatch, IDS[0])        # off: no waiting list
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SignInWait.orcid))) == 0
    _mode(client, "all")
    _orcid(app, monkeypatch, IDS[0])
    assert _came_via(app, IDS[0]) == "open"                                    # counted, should a limit come later
    _mode(client, "off")
    assert "nicht freigeschaltet" in _orcid(app, monkeypatch, IDS[1])
    assert client.post("/admin/admit", data={"action": "mode", "mode": "cap", "limit": 7}).status_code == 400
    # removing from the waiting list
    _mode(client, "cap", 50)
    _set_limit(app, 1)
    _orcid(app, monkeypatch, IDS[2])
    with app.app_context():
        db.session.add(SignInWait(orcid=IDS[3], name="Vierte"))
        db.session.commit()
    assert "Von der Warteliste genommen" in client.post("/admin/admit", data={"action": "unwait", "orcid": IDS[3]},
                                                        follow_redirects=True).data.decode()
    with app.app_context():
        assert db.session.get(SignInWait, IDS[3]) is None
        assert db.session.scalar(db.select(WorkGroupMember.id)) is None


def test_waiting_list_page_with_email(app, client, monkeypatch):
    """The page of the waiting list (auth.waiting_list, Philipp 2026-10-13): place, the iD ORCID confirmed, an
    optional address checked like every other, one mail when a place is free, and the address gone after it."""
    from tests.conftest import mail_on

    sent = mail_on(app)
    _mode(client, "cap", 50)
    _set_limit(app, 1)
    _orcid(app, monkeypatch, IDS[0])
    browser = app.test_client()
    page = _orcid(app, monkeypatch, IDS[1], "Zweite", c=browser)
    assert "Platz 1" in page and IDS[1] in page and "von ORCID bestätigt" in page and 'name="email"' in page
    # a wrong address is refused and stored nowhere
    bad = browser.post("/auth/waiting-list", data={"action": "email", "email": "keine adresse"})
    assert bad.status_code == 422 and "gültige E-Mail-Adresse" in bad.data.decode()
    browser.post("/auth/waiting-list", data={"action": "email", "email": " Zweite@Example.ORG "})
    with app.app_context():
        assert db.session.get(SignInWait, IDS[1]).email == "zweite@example.org"
    assert "wird per E-Mail benachrichtigt" in client.get("/admin/people").data.decode()
    # a browser that ORCID confirmed nothing for gets no entry, and cannot change one
    stranger = app.test_client()
    page = stranger.get("/auth/waiting-list").data.decode()
    assert "Mit ORCID anmelden" in page and IDS[1] not in page
    stranger.post("/auth/waiting-list", data={"action": "email", "email": "fremd@example.org"})
    stranger.post("/auth/waiting-list", data={"action": "leave"})
    with app.app_context():
        assert db.session.get(SignInWait, IDS[1]).email == "zweite@example.org"
    assert sent == []
    # a place is free: one mail, and the address goes
    _mode(client, "cap", 50)
    assert len(sent) == 1 and sent[0]["To"] == "zweite@example.org" and IDS[1] in sent[0].get_content()
    with app.app_context():
        row = db.session.get(SignInWait, IDS[1])
        assert row.admitted_at is not None and row.email is None
    assert "Ein Platz ist für Sie frei" in browser.get("/auth/waiting-list").data.decode()


def test_leaving_the_waiting_list(app, client, monkeypatch):
    _mode(client, "cap", 50)
    _set_limit(app, 1)
    _orcid(app, monkeypatch, IDS[0])
    browser = app.test_client()
    page = _orcid(app, monkeypatch, IDS[1], c=browser)
    assert 'name="email"' not in page                                         # mail off: no field
    page = browser.post("/auth/waiting-list", data={"action": "leave"}, follow_redirects=True).data.decode()
    assert "nicht mehr auf der Warteliste" in page
    with app.app_context():
        assert db.session.get(SignInWait, IDS[1]) is None
