"""Data access for R and Python (app/data_access.py): read tokens per survey and person on the export page, the CSV,
.rds and codebook downloads with the token in the Authorization header, the access log and the limits."""
import re
from datetime import datetime, timedelta

from werkzeug.datastructures import MultiDict

from app import limits
from app.extensions import db
from app.models import AccessLog, DataToken, Response, Survey, SurveyMember, User, UserLogin, once_hash, utcnow
from tests.conftest import add
from tests.exports.test_rds import read
from tests.accounts.test_roles import OTHER, _join, _login_other

API = "/api/v1/surveys/1"


def _answers(app, client):
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for g in ("1", "2"):
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2),
                                        started_at=datetime(2026, 9, 2), answers={"2": g}))
        db.session.add(Response(survey_id=1, status=Response.COMPLETED, completed_at=datetime(2026, 9, 2),
                                started_at=datetime(2026, 9, 2), answers={"2": "1"}, source="dummy", is_preview=True))
        db.session.commit()


def _create(client, name="Laptop", days=None):
    """Make a token on the export page; returns (response, the token shown once or None)."""
    r = client.post("/surveys/1/data-access/new", data={"name": name, **({"days": days} if days is not None else {})})
    found = re.search(r'value="(askdt_[\w-]+)"', r.get_data(as_text=True))
    return r, found.group(1) if found else None


def _get(client, path, token, **kw):
    return client.get(path, headers={"Authorization": f"Bearer {token}"}, **kw)


def _api(app):
    """A client without a session: a script."""
    return app.test_client()


def test_token_is_shown_once_and_only_its_hash_is_stored(app, client, survey):
    r, token = _create(client)
    assert r.status_code == 200 and token
    html = r.get_data(as_text=True)
    assert "Ihr neues Token „Laptop“" in html and f"ASKURIOUS_TOKEN={token}" in html
    with app.app_context():
        row = db.session.scalar(db.select(DataToken))
        assert row.token_hash == once_hash(token) and row.name == "Laptop" and row.user_id == 1
        assert (row.expires_at - row.created_at).days == 90                               # the default
        assert token not in str({c.name: getattr(row, c.name) for c in DataToken.__table__.columns})
    page = client.get("/surveys/1/data").get_data(as_text=True)
    assert token not in page and "Laptop" in page and "noch nie benutzt" in page          # listed, never again shown
    # the snippets name the survey's addresses, the token comes from the environment
    assert 'Sys.getenv(&#34;ASKURIOUS_TOKEN&#34;)' in page and "/api/v1/surveys/1/data.rds" in page
    assert "os.environ[&#34;ASKURIOUS_TOKEN&#34;]" in page and "/api/v1/surveys/1/data.csv" in page


def test_expiry_is_limited(app, client, survey):
    assert _create(client, days=366)[1] is None and _create(client, days=0)[1] is None
    assert _create(client, name="  ")[1] is None
    assert _create(client, days=365)[1]
    with app.app_context():
        row = db.session.scalar(db.select(DataToken))
        assert (row.expires_at - row.created_at).days == 365


def test_csv_equals_the_export_button_and_rds_works(app, client, survey):
    _answers(app, client)
    _, token = _create(client)
    api = _api(app)
    r = _get(api, f"{API}/data.csv", token)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store" and "Set-Cookie" not in r.headers
    assert r.data == client.get("/surveys/1/export/data.csv").data
    # the choices of the export page: separator, sources, marked ones
    for query in ("?sep=semicolon", "?source=real", "?source=real&source=dummy&all=1&drop=1"):
        assert _get(api, f"{API}/data.csv{query}", token).data == client.get(f"/surveys/1/export/data.csv{query}").data, query
    assert _get(api, f"{API}/data.csv?source=real", token).data.count(b"\r\n") == 3   # header and two real ones
    assert _get(api, f"{API}/codebook.csv", token).data == client.get("/surveys/1/export/codebook.csv").data
    r = _get(api, f"{API}/data.rds", token)
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('.rds"')
    assert r.data == client.get("/surveys/1/export/data.rds").data
    values, attrs = read(r.data)["gender"]
    assert sorted(values) == [1.0, 1.0, 2.0] and attrs["label"][0] == ["Geschlecht?"]
    with app.app_context():
        assert db.session.scalar(db.select(DataToken)).last_used_at is not None


def test_wrong_missing_or_foreign_tokens_are_refused(app, client, survey):
    _, token = _create(client)
    api = _api(app)
    assert api.get(f"{API}/data.csv").status_code == 401
    assert api.get(f"{API}/data.csv").headers["WWW-Authenticate"].startswith("Bearer")
    assert _get(api, f"{API}/data.csv", token + "x").status_code == 401
    assert api.get(f"{API}/data.csv", headers={"Authorization": f"Basic {token}"}).status_code == 401
    # never in the address, not even together with a right header
    assert api.get(f"{API}/data.csv?token={token}").status_code == 400
    assert _get(api, f"{API}/data.csv?access_token={token}", token).status_code == 400
    # a token of another survey
    client.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    assert _get(api, "/api/v1/surveys/2/data.csv", token).status_code == 401
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(AccessLog.id))) == 1   # only the creation


def test_expired_and_revoked_tokens_stop(app, client, survey):
    _, token = _create(client)
    api = _api(app)
    assert _get(api, f"{API}/data.csv", token).status_code == 200
    with app.app_context():
        row = db.session.scalar(db.select(DataToken))
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
        token_id = row.id
    assert _get(api, f"{API}/data.csv", token).status_code == 401
    assert "abgelaufen am" in client.get("/surveys/1/data").get_data(as_text=True)
    _, second = _create(client, name="Server")
    with app.app_context():
        second_id = db.session.scalar(db.select(DataToken.id).filter_by(name="Server"))
    client.post(f"/surveys/1/data-access/{second_id}/revoke")
    assert _get(api, f"{API}/data.csv", second).status_code == 401
    with app.app_context():
        assert db.session.get(DataToken, second_id) is None and db.session.get(DataToken, token_id) is not None


def test_viewer_cannot_create_and_a_lost_right_stops_the_token(app, client, survey):
    _join(app, "viewer")
    _login_other(app, client)
    assert "Ihre Rolle „Ansehen“ erlaubt kein Herunterladen der Daten und damit kein Token" in client.get(
        "/surveys/1/data").get_data(as_text=True)
    r, token = _create(client)
    assert r.status_code == 302 and token is None
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(DataToken.id))) == 0
        member = db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER))
        member.role = "editor"
        db.session.commit()
    _, token = _create(client)
    api = _api(app)
    assert _get(api, f"{API}/data.csv", token).status_code == 200
    with app.app_context():                                       # back to "Ansehen": the token stops at once
        db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER)).role = "viewer"
        db.session.commit()
    assert _get(api, f"{API}/data.csv", token).status_code == 403
    page = client.get("/surveys/1/data").get_data(as_text=True)    # still listed for them to revoke, marked
    assert "funktioniert nicht: Die Person darf die Daten derzeit nicht herunterladen" in page
    with app.app_context():                                       # removed from the survey
        db.session.delete(db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER)))
        db.session.commit()
    assert _get(api, f"{API}/data.csv", token).status_code == 403


def test_blocked_or_deleted_account_and_deleted_survey(app, client, survey):
    _join(app, "editor")
    _login_other(app, client)
    _, token = _create(client)
    api = _api(app)
    with app.app_context():
        other = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        db.session.add(UserLogin(user_id=other.id, status=UserLogin.BLOCKED))
        db.session.commit()
    assert _get(api, f"{API}/data.csv", token).status_code == 403
    # the survey goes: its tokens go with it
    from app import lifecycle

    with app.app_context(), app.test_request_context():
        lifecycle.delete(db.session.get(Survey, 1), force=True)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(DataToken.id))) == 0
    assert _get(api, f"{API}/data.csv", token).status_code == 404


def test_account_deletion_takes_its_tokens(app, client, survey):
    from app import accounts

    _join(app, "editor")
    _login_other(app, client)
    _create(client)
    with app.app_context():
        other = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        with app.test_request_context():
            accounts.delete(other, by="admin")
        assert db.session.scalar(db.select(db.func.count(DataToken.id))) == 0


def test_admins_see_and_revoke_every_token_others_only_their_own(app, client, survey):
    _create(client, name="Vom Admin")
    _join(app, "editor")
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    _create(other, name="Von der Kollegin")
    admin_page = client.get("/surveys/1/data").get_data(as_text=True)
    assert "Token dieser Befragung" in admin_page and "Von der Kollegin" in admin_page and "Vom Admin" in admin_page
    other_page = other.get("/surveys/1/data").get_data(as_text=True)
    assert "Ihre Token" in other_page and "Von der Kollegin" in other_page and "Vom Admin" not in other_page
    with app.app_context():
        mine = db.session.scalar(db.select(DataToken.id).filter_by(name="Vom Admin"))
        theirs = db.session.scalar(db.select(DataToken.id).filter_by(name="Von der Kollegin"))
    assert other.post(f"/surveys/1/data-access/{mine}/revoke").status_code == 404   # not someone else's
    assert client.post(f"/surveys/1/data-access/{theirs}/revoke").status_code == 302
    with app.app_context():
        assert db.session.get(DataToken, theirs) is None and db.session.get(DataToken, mine) is not None


def test_access_log_names_the_token(app, client, survey):
    _, token = _create(client, name="Laptop")
    api = _api(app)
    _get(api, f"{API}/data.csv?source=real", token)
    _get(api, f"{API}/data.rds", token)
    _get(api, f"{API}/data.csv", token + "x")                          # refused: not written down
    with app.app_context():
        token_id = db.session.scalar(db.select(DataToken.id))
    client.post(f"/surveys/1/data-access/{token_id}/revoke")
    with app.app_context():
        rows = [(r.action, r.survey_code, r.detail, r.who) for r in db.session.scalars(db.select(AccessLog).order_by(AccessLog.id))]
    assert [r[0] for r in rows] == ["Datenzugang: Token angelegt", "Datenexport CSV (Token)", "Datenexport .rds (Token)",
                                    "Datenzugang: Token widerrufen"]
    assert all(r[1] == "1" and "Token „Laptop“" in r[2] and "Philipp" in r[3] for r in rows)
    assert "source=real" in rows[1][2]


def test_downloads_per_token_and_wrong_tokens_are_limited(app, client, survey, monkeypatch):
    monkeypatch.setitem(app.config, "TOKEN_LIMIT", 2)
    monkeypatch.setattr(limits, "_tokens", {})
    _, token = _create(client)
    api = _api(app)
    assert [_get(api, f"{API}/codebook.csv", token).status_code for _ in range(3)] == [200, 200, 429]
    monkeypatch.setitem(app.config, "TOKEN_TRY_LIMIT", 2)
    monkeypatch.setattr(limits, "_network", lambda: "198.51.100.5")  # one sender; counted in the database (audit 2026-10-13)
    assert [_get(api, f"{API}/data.csv", "falsch").status_code for _ in range(3)] == [401, 401, 429]
    assert _get(api, f"{API}/data.csv", token).status_code == 429                   # this sender waits
    assert "Retry-After" in _get(api, f"{API}/data.csv", token).headers


def test_researcher_side_only(app, client, survey):
    from app import hosts

    assert hosts.side_of("data_access.api_csv") == hosts.RESEARCHER
    app.config.update(SIDE="participant", PARTICIPANT_BASE_URL="https://ask.example.org")
    _, token = _create(app.test_client(), name="x")  # not reachable there at all
    assert token is None
    assert _get(app.test_client(), f"{API}/data.csv", "egal").status_code == 404


def test_not_for_a_live_vote(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.live = {}
        db.session.commit()
    assert "Datenzugang für R und Python" not in client.get("/surveys/1/data").get_data(as_text=True)
    assert _create(client)[0].status_code == 404


def test_token_needs_the_current_terms_of_use(app, client, survey):
    """Code review 2026-10-15, H8: a data token kept working after the terms changed, unlike an agent token. Now it
    answers 403 with a hint until its person accepts them."""
    _answers(app, client)
    _, token = _create(client)
    assert _get(_api(app), f"{API}/data.csv", token).status_code == 200
    with app.app_context():
        db.session.get(User, 1).terms_version = "2000-01-01"           # terms changed since
        db.session.commit()
    r = _get(_api(app), f"{API}/data.csv", token)
    assert r.status_code == 403 and "Nutzungsbedingungen" in r.get_json()["error"]
    client.post("/terms/", data={"accept": "1"})
    assert _get(_api(app), f"{API}/data.csv", token).status_code == 200
