"""/rechenknecht/ is open to anyone (an outside Rechenknecht calls it), and a result may be 64 MB: the app checks what
the headers of a signed message can say before it reads the body (security audit 2026-10-13, DP-12)."""
import time

import pytest

from app import compute

SECRET = "s" * 40


@pytest.fixture
def body_never_read(app, monkeypatch):
    """Every attempt to read the request body fails the test."""
    def refuse(self, *args, **kwargs):
        raise AssertionError("the body was read")

    monkeypatch.setattr(app.request_class, "get_data", refuse)
    app.config.update(COMPUTE_URL="https://rechner.example.org", COMPUTE_SECRET=SECRET)


@pytest.mark.parametrize("headers", [
    {},                                                                            # no signature at all
    {"X-Compute-Time": str(int(time.time())), "X-Compute-Signature": "nope"},      # not a signature's form
    {"X-Compute-Time": str(int(time.time()) - 3600), "X-Compute-Signature": "a" * 64},   # too old
    {"X-Compute-Time": "²", "X-Compute-Signature": "a" * 64},
    {"X-Compute-Time": "9" * 5000, "X-Compute-Signature": "a" * 64},            # int conversion must stay bounded
])
def test_unsigned_messages_are_refused_before_the_body_is_read(app, body_never_read, headers):
    client = app.test_client()
    for path in ("/rechenknecht/ergebnis", "/rechenknecht/stand"):
        assert client.post(path, data=b"x" * 1000, headers=headers).status_code == 403


def test_without_a_rechenknecht_nothing_is_read(app, monkeypatch):
    def refuse(self, *args, **kwargs):
        raise AssertionError("the body was read")

    monkeypatch.setattr(app.request_class, "get_data", refuse)
    app.config.update(COMPUTE_URL="", COMPUTE_SECRET="")
    headers = {"X-Compute-Time": str(int(time.time())), "X-Compute-Signature": "a" * 64}
    assert app.test_client().post("/rechenknecht/ergebnis", data=b"x", headers=headers).status_code == 403


def test_a_signed_message_still_passes(app):
    """The check before the body lets every correctly signed message through (the signature over the body follows)."""
    app.config.update(COMPUTE_URL="https://rechner.example.org", COMPUTE_SECRET=SECRET)
    body = compute.pack({"job": "j" * 20, "progress": 0.5})
    headers = compute.signed_headers(SECRET, "/stand", body)
    r = app.test_client().post("/rechenknecht/stand", data=body, headers=headers)
    assert r.status_code in (200, 400, 404)                                        # past the signature: the job is unknown
    bad = dict(headers, **{"X-Compute-Signature": "0" * 64})                       # right form, wrong signature
    assert app.test_client().post("/rechenknecht/stand", data=body, headers=bad).status_code == 403
