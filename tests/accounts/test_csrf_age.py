"""A signed-in researcher's CSRF token counts CSRF_HOURS (audit 2026-10-18, INJ-4): WTF_CSRF_TIME_LIMIT None kept a
token for the whole session. An open page fetches a fresh one (/auth/csrf-token, app.js), so a long editing session
goes on; signing in again gives a new token too (auth.remember)."""
import re
import time


def _token(c):
    r = c.get("/auth/csrf-token")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    return r.get_json()["token"]


def test_a_researchers_token_ends_after_its_hours_and_a_fresh_one_goes_on(app, client, survey, monkeypatch):
    app.config.update(WTF_CSRF_ENABLED=True, CSRF_HOURS=12)
    old = _token(client)
    assert client.post("/dashboard/pin/1", data={"csrf_token": old}).status_code == 302
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 13 * 3600)
    assert client.post("/dashboard/pin/1", data={"csrf_token": old}).status_code == 400  # older than 12 hours
    fresh = _token(client)                                                           # what the open page fetches
    assert fresh != old and client.post("/dashboard/pin/1", data={"csrf_token": fresh}).status_code == 302


def test_signing_in_again_ends_the_old_token(app, client, survey):
    app.config.update(WTF_CSRF_ENABLED=True)
    old = _token(client)
    client.post("/auth/logout", data={"csrf_token": old})
    client.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    assert client.post("/dashboard/pin/1", data={"csrf_token": old}).status_code == 400


def test_the_page_names_where_to_fetch_a_fresh_token(app, client, survey):
    page = client.get("/dashboard/").data.decode()
    assert re.search(r'<meta name="csrf-token" content="[^"]+" data-refresh="/auth/csrf-token">', page)
