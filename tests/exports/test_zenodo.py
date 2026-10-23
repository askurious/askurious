import pytest

from app import zenodo
from app.extensions import db
from app.models import Survey
from tests.conftest import add, fill_consent


class FakeZenodo:
    """Records requests and answers like the deposit API."""

    def __init__(self):
        self.calls, self.files, self.meta, self.published, self.reserved = [], {}, None, False, False

    def __call__(self, method, url, headers=None, timeout=None, json=None, data=None, stream=False):
        self.calls.append((method, url))
        assert headers["Authorization"] == "Bearer secret-token"

        class R:
            def __init__(self, payload, status=200):
                self.status_code, self._p = status, payload
                self.content = b"x"
                self.text = ""

            def json(self):
                return self._p

        dep = {"id": 42, "links": {"bucket": "https://sandbox.zenodo.org/api/files/b", "html": "https://sandbox.zenodo.org/deposit/42"},
               "metadata": {}, "submitted": self.published, **({"doi": "10.5072/zenodo.42"} if self.published else {}),
               "files": [{"filename": n, "links": {"self": f"https://sandbox.zenodo.org/api/files/b/{n}"}} for n in self.files]}
        if method == "POST" and url.endswith("/deposit/depositions"):
            return R(dep, 201)
        if method == "POST" and url.endswith("/records/42/draft/pids/doi"):  # the DOI through the records API
            self.reserved = True
            return R({"doi": "10.5072/zenodo.42"}, 201)
        if method == "PUT" and "/deposit/depositions/42" in url:
            self.meta = json["metadata"]
            return R(dep)
        if method == "PUT" and "/files/b/" in url:
            self.files[url.rsplit("/", 1)[1]] = data
            return R({})
        if method == "DELETE":
            self.files.pop(url.rsplit("/", 1)[1], None)
            return R({})
        if method == "POST" and url.endswith("/actions/publish"):
            self.published = True
            dep["submitted"] = True
            dep["doi"] = "10.5072/zenodo.42"
            return R(dep, 202)
        if method == "GET":
            return R(dep)
        raise AssertionError((method, url))


@pytest.fixture
def fake(monkeypatch):
    """Zenodo's API, and this researcher connected through "Mit Zenodo verbinden" (the only way to upload)."""
    from app import zenodo_auth

    f = FakeZenodo()
    monkeypatch.setattr(zenodo.requests, "request", f)
    monkeypatch.setattr(zenodo_auth, "available", lambda target: True)
    monkeypatch.setattr(zenodo_auth, "access_token", lambda user, target: "secret-token")
    monkeypatch.setattr(zenodo_auth, "connection", lambda user, target: object())
    return f


def test_upload_update_and_read_the_state_back(app, client, survey, fake):
    fill_consent(client, open_data="1", license="CC BY 4.0")
    client.post("/surveys/1/members", data={"orcid": "0000-0002-0000-0001", "name": "Julia Offermann"})
    add(client, "likert")
    r = client.post("/surveys/1/zenodo", data={"target": "sandbox", "action": "upload"},
                    follow_redirects=True)
    assert "Entwurf auf Zenodo angelegt" in r.data.decode()
    assert set(fake.files) == {"data.csv", "codebook.csv", "questionnaire.md", "README.md", "survey.json", "survey-material.pdf", "checksums.sha256"}
    assert [c["orcid"] for c in fake.meta["creators"]] == ["0000-0000-0000-001X", "0000-0002-0000-0001"]
    assert fake.meta["creators"][1]["name"] == "Offermann, Julia" and fake.meta["license"] == "cc-by-4.0"
    assert fake.meta["title"].endswith(" (survey data and materials)") and not fake.meta["title"].startswith("Data and")
    assert "prereserve_doi" not in fake.meta and not fake.reserved                       # no DOI from Askurious
    rel = {r["relation"]: r["identifier"] for r in fake.meta["related_identifiers"]}
    assert "/s/" in rel["isDerivedFrom"] and rel["isCompiledBy"].startswith("http") and "Askurious" in fake.meta["notes"]
    assert b"`Q1`" in fake.files["questionnaire.md"] and b"https://orcid.org/0000-0002-0000-0001" in fake.files["README.md"]
    with app.app_context():
        assert db.session.get(Survey, 1).zenodo["doi"] == ""                              # a DOI only once published on Zenodo
    # second upload refreshes the same draft (no new deposition)
    client.post("/surveys/1/zenodo", data={"target": "sandbox", "action": "upload"})
    assert sum(1 for m, u in fake.calls if m == "POST" and u.endswith("/deposit/depositions")) == 1
    assert "Veröffentlichen …" not in client.get("/surveys/1/data").data.decode()     # Askurious never publishes
    assert client.post("/surveys/1/zenodo", data={"action": "publish"}).status_code == 302
    assert not any(u.endswith("/actions/publish") for _, u in fake.calls)
    fake.published = True                                                               # the researchers published on Zenodo
    client.post("/surveys/1/zenodo", data={"target": "sandbox", "action": "refresh"})
    with app.app_context():
        assert db.session.get(Survey, 1).zenodo["state"] == "published"
    html = client.get("/surveys/1/data").data.decode()
    assert "Veröffentlicht" in html and "10.5072/zenodo.42" in html and "secret-token" not in html


def test_guard_without_open_data_and_missing_token(app, client, survey, fake):
    fill_consent(client)  # open data off
    r = client.post("/surveys/1/zenodo", data={"action": "upload"}, follow_redirects=True)
    assert "nicht veröffentlicht" in r.data.decode() and not fake.calls
    from app import zenodo_auth

    zenodo_auth.access_token = lambda user, target: None                                # not connected
    fill_consent(client, open_data="1", license="CC BY 4.0")  # an explicit licence before connecting to Zenodo
    r = client.post("/surveys/1/zenodo", data={"token": "secret-token", "confirm_no_open_data": "1", "action": "upload"},
                    follow_redirects=True)
    assert "„Mit Zenodo verbinden“" in r.data.decode() and not fake.calls       # a typed token counts for nothing


def test_questionnaire_markdown_covers_all_types(app, client, survey):
    from app import export, items

    for t in items.REGISTRY:
        if t != "consent" and not getattr(items.REGISTRY[t], "interview_only", False):
            add(client, t)
    with app.app_context():
        md = export.questionnaire_md(db.session.get(Survey, 1))
    assert md.startswith("# Test") and "## Block 0: " in md and "## Block 1: " in md and "`Q" in md


def test_oauth_connect_refresh_disconnect(app, client, survey, monkeypatch):
    from datetime import timedelta
    from urllib.parse import parse_qs, urlparse

    from app import secrets_box, zenodo_auth
    from app.models import ZenodoConnection, utcnow

    app.config.update(ZENODO_SANDBOX_CLIENT_ID="cid", ZENODO_SANDBOX_CLIENT_SECRET="csecret",
                      ZENODO_CLIENT_ID="", ZENODO_CLIENT_SECRET="",  # not from a local .env
                      PUBLIC_BASE_URL="https://you.example")
    posted = []

    class R:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    def fake_post(url, data=None, timeout=None, stream=False):
        posted.append(data)
        if data["grant_type"] == "authorization_code":
            return R({"access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600})
        return R({"access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600})

    monkeypatch.setattr(zenodo_auth.requests, "post", fake_post)
    r = client.post("/zenodo/connect/sandbox", data={"next": "/surveys/1/data"})
    url = urlparse(r.headers["Location"])
    q = parse_qs(url.query)
    assert url.netloc == "sandbox.zenodo.org" and q["scope"] == ["deposit:write"]  # drafts only
    assert q["redirect_uri"] == ["https://you.example/zenodo/callback"]
    assert client.get("/zenodo/callback?code=x&state=wrong").status_code == 302  # rejected, nothing stored
    client.post("/zenodo/connect/sandbox", data={"next": "/surveys/1/data"})
    with client.session_transaction() as sess:
        state = sess["zenodo_oauth"]["state"]
    r = client.get(f"/zenodo/callback?code=abc&state={state}")
    assert r.headers["Location"].endswith("/surveys/1/data")
    with app.app_context():
        conn = db.session.scalar(db.select(ZenodoConnection))
        assert conn.access_token != "acc-1" and secrets_box.decrypt(conn.access_token) == "acc-1"  # encrypted at rest
        from app.models import User

        user = db.session.scalar(db.select(User))
        assert zenodo_auth.access_token(user, "sandbox") == "acc-1"
        conn.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
        assert zenodo_auth.access_token(user, "sandbox") == "acc-2"  # refreshed
        assert posted[-1]["grant_type"] == "refresh_token" and posted[-1]["refresh_token"] == "ref-1"
    assert "verbunden" in client.get("/surveys/1/data").data.decode()
    client.post("/zenodo/disconnect/sandbox", data={"next": "/surveys/1/data"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ZenodoConnection.id))) == 0
    assert client.post("/zenodo/connect/zenodo").status_code == 404  # production not configured


def test_the_images_go_into_the_package(app, client, survey, fake):
    import os

    from app import media
    from app.models import Asset, Item

    fill_consent(client, open_data="1", license="CC BY 4.0")
    add(client, "image")
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.scalar(db.select(Item).filter_by(type="image"))
        folder = media.upload_folder(s.uid)
        os.makedirs(folder, exist_ok=True)
        for name, status, source in (("a1.png", Asset.APPROVED, "ai"), ("a2.png", Asset.REJECTED, "ai")):
            with open(os.path.join(folder, name), "wb") as f:
                f.write(b"\x89PNG fake")
            item.assets.append(Asset(filename=name, status=status, source=source, prompt="ein Roboter im Wohnzimmer", model="gpt-image-1"))
        from app import asset_rights
        from app.models import User

        asset_rights.record(s, item.assets[0], {"basis": "ai", "creator": "Philipp Brauner", "source": "OpenAI / gpt-image-1",
                            "license": "Reviewed provider terms permit public archiving", "public_confirm": "1"},
                            db.session.get(User, 1))
        db.session.commit()
        code = item.code
    client.post("/surveys/1/zenodo", data={"target": "sandbox", "action": "upload"})
    import io
    import zipfile

    packed = zipfile.ZipFile(io.BytesIO(fake.files["images.zip"]))                      # one file for Zenodo's limits
    assert sorted(packed.namelist()) == [f"image_{code}_1.png", "images.csv"]          # approved ones only
    table = packed.read("images.csv")
    assert b"ein Roboter im Wohnzimmer" in table and b"gpt-image-1" in table and b"images.zip" in fake.files["README.md"]



def test_only_the_registered_targets_are_offered(app, client, survey):
    # connecting happens on the account page; the export page always shows the block and says what is missing
    # (Philipp, 2026-10-08; before only with a connection, Ralf 2026-10-04)
    app.config.update(ZENODO_CLIENT_ID="cid", ZENODO_CLIENT_SECRET="cs", ZENODO_SANDBOX_CLIENT_ID="", ZENODO_SANDBOX_CLIENT_SECRET="")
    html = client.get("/account/connections").data.decode()
    html = html[html.index('id="zenodo"'):].split("</section>")[0]  # the Coscine form next to it takes a token
    assert "Mit Zenodo verbinden" in html and "Sandbox" not in html and 'name="token"' not in html   # no personal tokens
    html = client.get("/surveys/1/data").data.decode()
    assert 'id="zenodo"' in html and "mit Zenodo verbinden, dann hier hochladen" in html   # not connected: says so
    assert "Als Entwurf hochladen" not in html
    from app import zenodo_auth

    real = zenodo_auth.connection
    zenodo_auth.connection = lambda user, target: object() if target == "zenodo" else None
    try:
        html = client.get("/surveys/1/data").data.decode()
        assert 'id="zenodo"' in html and "Als Entwurf hochladen" in html and "Sandbox" not in html
    finally:
        zenodo_auth.connection = real
    app.config.update(ZENODO_CLIENT_ID="", ZENODO_CLIENT_SECRET="")
    assert "Verbindung zu Zenodo" not in client.get("/account/connections").data.decode()          # not set up: not on the account page
    html = client.get("/surveys/1/data").data.decode()
    assert 'id="zenodo"' in html and "Zenodo ist auf diesem Server nicht eingerichtet" in html


def test_closing_recommends_zenodo_once(app, client, survey):
    from app.models import Response

    app.config.update(ZENODO_CLIENT_ID="cid", ZENODO_CLIENT_SECRET="cs")
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="p1", answers={}))
        db.session.commit()
    html = client.post("/surveys/1/close", follow_redirects=True).data.decode()
    assert "data-zenodo-hint" in html and "Zum Zenodo-Upload" in html and "/surveys/1/data#zenodo" in html
    assert "data-zenodo-hint" not in client.get("/surveys/1/data").data.decode()     # once
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    html = client.post("/surveys/1/close", data={"pause": "1"}, follow_redirects=True).data.decode()
    assert "data-zenodo-hint" not in html                                                # pausing: no recommendation
    app.config.update(ZENODO_CLIENT_ID="", ZENODO_CLIENT_SECRET="")
    html = client.post("/surveys/1/close", follow_redirects=True).data.decode()        # from the pause, no Zenodo here
    assert "geschlossen" in html and "data-zenodo-hint" not in html
