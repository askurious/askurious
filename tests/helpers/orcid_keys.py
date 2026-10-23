"""ID tokens as ORCID signs them (RS256), with a key pair of the tests instead of ORCID's (app/orcid_token.py)."""
import base64
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def id_token(sub, auth_time, key=KEY, kid=KID, alg="RS256", **claims):
    """An ID token for the client "client" of the tests' ORCID (https://orcid.org), signed with `key`."""
    now = time.time()
    body = {"iss": "https://orcid.org", "aud": "client", "sub": sub, "exp": now + 600, "iat": now,
            "auth_time": auth_time, **claims}
    head = _b64(json.dumps({"alg": alg, "kid": kid}).encode())
    payload = _b64(json.dumps(body).encode())
    signature = key.sign(f"{head}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{payload}.{_b64(signature)}"


def serve(monkeypatch, key=KEY, kid=KID, calls=None):
    """ORCID's key list (JWKS) answers with this public key; `calls` (a list) notes every fetch."""
    from app import orcid_token

    orcid_token.forget()

    def fetch(base):
        if calls is not None:
            calls.append(base)
        return {kid: key.public_key()}

    monkeypatch.setattr(orcid_token, "_fetch_keys", fetch)
