"""The ID token of ORCID counts only with a valid signature (app/orcid_token.py; audit 2026-10-18, AUTH-2): a token
with another key, without a signature, for another client or iD, or one whose keys cannot be read never makes a
sign-in "fresh". No call goes to ORCID: the tests serve their own key list (tests/helpers/orcid_keys.py)."""
import base64
import json
import time

from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth, orcid_token
from tests.helpers import orcid_keys

ID = "0000-0002-1825-0097"


def _fresh(app, token, orcid=ID):
    with app.test_request_context():
        return auth._signed_in_since(token, time.time() - 30, orcid)


def test_a_signed_token_of_orcid_counts(app, monkeypatch):
    app.config.update(ORCID_CLIENT_ID="client")
    orcid_keys.serve(monkeypatch)
    assert _fresh(app, orcid_keys.id_token(ID, time.time()))
    assert not _fresh(app, orcid_keys.id_token(ID, time.time() - 3600))  # an old sign-in at ORCID stays old


def test_a_token_without_a_valid_signature_is_not_fresh(app, monkeypatch):
    """It was: the payload was read without the signature, so any token naming a recent auth_time counted."""
    app.config.update(ORCID_CLIENT_ID="client")
    orcid_keys.serve(monkeypatch)
    now = time.time()
    payload = base64.urlsafe_b64encode(json.dumps({"sub": ID, "auth_time": now}).encode()).decode().rstrip("=")
    assert not _fresh(app, f"e30.{payload}.sig")  # the form the tests used before 2026-10-18
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert not _fresh(app, orcid_keys.id_token(ID, now, key=other))  # signed, but not with ORCID's key
    none = orcid_keys.id_token(ID, now).split(".")
    head = base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": orcid_keys.KID}).encode()).decode().rstrip("=")
    assert not _fresh(app, f"{head}.{none[1]}.")
    assert not _fresh(app, "not a token") and not _fresh(app, None)


def test_issuer_audience_expiry_and_id_are_checked(app, monkeypatch):
    app.config.update(ORCID_CLIENT_ID="client")
    orcid_keys.serve(monkeypatch)
    now = time.time()
    assert not _fresh(app, orcid_keys.id_token(ID, now, aud="another-client"))
    assert not _fresh(app, orcid_keys.id_token(ID, now, iss="https://evil.example"))
    assert not _fresh(app, orcid_keys.id_token(ID, now, exp=now - 3600))
    assert not _fresh(app, orcid_keys.id_token("0000-0000-0000-0124", now))  # the token of another iD
    assert _fresh(app, orcid_keys.id_token(ID, now, aud=["client", "x"]))


def test_keys_that_cannot_be_read_mean_not_fresh_and_are_not_asked_for_every_time(app, monkeypatch):
    app.config.update(ORCID_CLIENT_ID="client")
    calls = []

    def down(base):
        calls.append(base)
        raise orcid_token.requests.ConnectionError("ORCID down")

    monkeypatch.setattr(orcid_token, "_fetch_keys", down)
    token = orcid_keys.id_token(ID, time.time())
    assert not _fresh(app, token) and not _fresh(app, token)
    assert calls == ["https://orcid.org"]  # the second sign-in does not wait for ORCID again (RETRY_SECONDS)


def test_the_keys_are_kept_and_an_unknown_key_asks_again(app, monkeypatch):
    app.config.update(ORCID_CLIENT_ID="client")
    calls, served = [], {orcid_keys.KID: orcid_keys.KEY.public_key()}
    monkeypatch.setattr(orcid_token, "_fetch_keys", lambda base: calls.append(base) or dict(served))
    assert _fresh(app, orcid_keys.id_token(ID, time.time())) and _fresh(app, orcid_keys.id_token(ID, time.time()))
    assert len(calls) == 1
    new = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    served["rotated"] = new.public_key()  # ORCID turned to a new key
    monkeypatch.setattr(orcid_token, "RETRY_SECONDS", 0)
    assert _fresh(app, orcid_keys.id_token(ID, time.time(), key=new, kid="rotated"))
    assert len(calls) == 2
