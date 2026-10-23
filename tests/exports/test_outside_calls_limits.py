"""Calls to Zenodo, ORCID and doi.org (security and code audit 2026-10-13): the Zenodo sign-in with PKCE like ORCID's
(IN-5), and each call with a limit on the whole time and on the size of the answer (IN-7, app/bounded.py)."""
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from app import bounded, citations, orcid_api, zenodo, zenodo_auth


def test_zenodo_sign_in_with_pkce(app, client, survey, monkeypatch):
    app.config.update(ZENODO_SANDBOX_CLIENT_ID="cid", ZENODO_SANDBOX_CLIENT_SECRET="csecret", ZENODO_CLIENT_ID="",
                      ZENODO_CLIENT_SECRET="", PUBLIC_BASE_URL="https://you.example")
    posted = []

    class Answer:
        status_code = 200

        def json(self):
            return {"access_token": "acc", "expires_in": 3600}

    monkeypatch.setattr(zenodo_auth.requests, "post", lambda url, data=None, **kw: posted.append(data) or Answer())
    q = parse_qs(urlparse(client.post("/zenodo/connect/sandbox").headers["Location"]).query)
    assert q["code_challenge_method"] == ["S256"]
    with client.session_transaction() as sess:
        state, verifier = sess["zenodo_oauth"]["state"], sess["zenodo_oauth"]["verifier"]
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [digest] and verifier not in str(q)
    client.get(f"/zenodo/callback?code=abc&state={state}")
    assert posted[-1]["code_verifier"] == verifier


class _Endless:
    """An answer that never ends: a byte at a time, as long as it is read."""
    status_code, headers = 200, {}

    def iter_content(self, size):
        while True:
            yield b"x" * size

    def raise_for_status(self):
        pass

    def close(self):
        pass


def test_orcid_and_doi_answers_are_read_only_up_to_their_limit(app, monkeypatch):
    monkeypatch.setattr(bounded.requests, "get", lambda url, **kw: _Endless())
    with app.app_context():
        assert orcid_api._get("/0000-0002-1825-0097/person") is None
    with pytest.raises(bounded.TooLarge):
        citations._get_json("https://doi.org/10.1234/x")
    monkeypatch.setattr(citations, "WHOLE_SECONDS", 0)
    with pytest.raises(bounded.TooSlow):
        citations._get_json("https://doi.org/10.1234/x")


def test_zenodo_answers_have_a_limit(monkeypatch):
    monkeypatch.setattr(bounded.requests, "request", lambda method, url, **kw: _Endless())
    monkeypatch.setattr(zenodo, "MAX_ANSWER", 1024 * 1024)
    with pytest.raises(zenodo.ZenodoError):
        zenodo.Client("token").request("GET", "/deposit/depositions")
