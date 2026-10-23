"""An admin who had just signed in was sent to sign in again when adding a person, and what the form held was gone
(Ralf, 2026-10-15). A plain ORCID sign-in never counted as fresh, because ORCID passes a browser still signed in
there straight through. Now a round trip for which ORCID did ask for the password counts, and when a fresh sign-in
is still needed, the form's entries come back afterwards (accounts.keep_entries/refill, app.js)."""
import html
import json
import os
import re
import time

from app import accounts
from app.extensions import db
from app.models import User
from tests.accounts.test_review_small_accounts import ME, _orcid_answers, _round_trip


def test_a_plain_orcid_sign_in_with_the_password_typed_just_now_is_fresh(app, monkeypatch):
    _orcid_answers(app, monkeypatch, orcid=ME, name="Philipp")         # ORCID asked for the password just now
    c = app.test_client()
    start = _round_trip(c)
    assert "scope=openid" in start and "prompt=login" not in start
    with c.session_transaction() as s:
        assert "signed_in_at" in s and "oauth_began" not in s
    c.post("/terms/", data={"accept": "1"})
    r = c.post("/admin/accounts/new", data={"name": "Gast", "email": "gast@example.org"})
    assert "/auth/login" not in r.headers.get("Location", "")          # no second sign-in
    with app.app_context():
        assert db.session.scalar(db.select(User).filter_by(name="Gast")) is not None


def test_a_browser_passed_through_by_orcid_still_signs_in_again(app, monkeypatch):
    _orcid_answers(app, monkeypatch, orcid=ME, name="Philipp", signed_in_ago=3600)
    c = app.test_client()
    _round_trip(c)
    with c.session_transaction() as s:
        assert "signed_in_at" not in s and "_user_id" in s
    c.post("/terms/", data={"accept": "1"})
    r = c.post("/admin/accounts/new", data={"name": "Gast", "email": "gast@example.org"})
    assert "/auth/login" in r.headers["Location"] and "fresh=1" in r.headers["Location"]


def _refill(page):
    found = re.search(r'data-fresh-refill="([^"]*)"', page)
    return json.loads(html.unescape(found.group(1))) if found else None


def test_the_entries_come_back_after_the_fresh_sign_in(app, client):
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time() - accounts.FRESH_SECONDS - 5
    sent = {"name": "Gast Person", "email": "gast@example.org", "affiliation": "Uni", "may_create": "1",
            "password": "geheim-geheim"}
    r = client.post("/admin/accounts/new", data=sent)
    assert "/auth/login" in r.headers["Location"]
    with app.app_context():
        assert db.session.scalar(db.select(User).filter_by(name="Gast Person")) is None
    with client.session_transaction() as s:
        assert "gast@example.org" not in s[accounts.REFILL_KEY]       # encrypted in the cookie
    assert "Eingaben stehen wieder im Formular" in client.get("/admin/people").data.decode()
    assert _refill(client.get("/admin/people").data.decode()) is None   # not before the fresh sign-in
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time()                                   # back from signing in again
    assert _refill(client.get("/account/").data.decode()) is None        # only on the page it belongs to
    kept = _refill(client.get("/admin/people").data.decode())
    assert kept["action"] == "/admin/accounts/new"
    assert dict(kept["fields"]) == {"name": "Gast Person", "email": "gast@example.org", "affiliation": "Uni",
                                    "may_create": "1"}                     # no password, no CSRF token
    assert _refill(client.get("/admin/people").data.decode()) is None   # once


def test_entries_of_another_account_or_left_too_long_are_dropped(app, client):
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time() - accounts.FRESH_SECONDS - 5
    client.post("/admin/admit", data={"action": "add", "orcid": "0000-0002-1825-0097", "note": "Kollegin"})
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time()
    with app.test_request_context():
        from app import secrets_box

        with client.session_transaction() as s:
            entry = json.loads(secrets_box.decrypt(s[accounts.REFILL_KEY]))
            assert dict(entry["fields"]) == {"action": "add", "orcid": "0000-0002-1825-0097", "note": "Kollegin"}
            entry["at"] = time.time() - 3 * accounts.FRESH_SECONDS
            s[accounts.REFILL_KEY] = secrets_box.encrypt(json.dumps(entry))
    assert _refill(client.get("/admin/people").data.decode()) is None
    with client.session_transaction() as s:
        assert accounts.REFILL_KEY not in s


def test_app_js_fills_the_form_that_sent_them(app):
    script = open(os.path.join(app.static_folder, "app.js"), encoding="utf-8").read()
    part = script[script.index('document.querySelector("[data-fresh-refill]")'):]
    assert "entry.action" in part and "dispatchEvent" in part and "password" in part
