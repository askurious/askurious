"""The ID token of ORCID's token answer (scope "openid"), checked before its sign-in time counts (app/auth.py,
_signed_in_since).

ORCID signs the token with RS256; its public keys are at <ORCID_BASE_URL>/oauth/jwks. The signature, the issuer
(ORCID_BASE_URL), the audience (ORCID_CLIENT_ID), the expiry and the iD (sub) are checked; only then is auth_time
read. Until 2026-10-18 the payload was read without the signature, because the token comes straight from ORCID over
https in exchange for the code; a token from anywhere else could then have claimed a fresh sign-in (audit
2026-10-18, AUTH-2). Whatever goes wrong (keys not reachable, an unknown key, a bad signature) means "not fresh":
the person is signed in, a protected action asks for a fresh sign-in once more.

The keys are kept KEYS_SECONDS per server process; an unknown key id asks ORCID again, at most every RETRY_SECONDS,
and so does a failed fetch."""
import base64
import json
import threading
import time

import requests
from flask import current_app

KEYS_SECONDS = 24 * 3600
RETRY_SECONDS = 300
MAX_ANSWER = 64 * 1024  # bytes of the key list (app/bounded.py)
CLOCK_SLACK = 120  # seconds the clocks of ORCID and of this server may differ (as auth.FRESH_SLACK)

_lock = threading.Lock()
_kept = {"at": None, "tried": None, "keys": {}, "base": None}


def _b64(text):
    text = str(text)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _number(text):
    return int.from_bytes(_b64(text), "big")


def _fetch_keys(base):
    """{key id: RSA public key} from ORCID's JWKS address. Raises on any failure (tests replace this)."""
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    from app import bounded

    answer = bounded.request("GET", f"{base}/oauth/jwks", max_bytes=MAX_ANSWER, seconds=10, via=requests.get,
                             headers={"Accept": "application/json"})
    answer.raise_for_status()
    keys = {}
    for jwk in (answer.json() or {}).get("keys") or []:
        if not isinstance(jwk, dict) or jwk.get("kty") != "RSA" or jwk.get("use", "sig") != "sig":
            continue
        keys[str(jwk.get("kid") or "")] = RSAPublicNumbers(_number(jwk["e"]), _number(jwk["n"])).public_key()
    return keys


def _keys(kid):
    """The kept keys, fetched again when they are old, from another ORCID address, or lack `kid`."""
    base = current_app.config["ORCID_BASE_URL"]
    now = time.monotonic()
    with _lock:
        fresh = _kept["base"] == base and _kept["at"] is not None and now - _kept["at"] < KEYS_SECONDS
        if fresh and (kid in _kept["keys"] or not kid and len(_kept["keys"]) == 1):
            return _kept["keys"]
        if _kept["base"] == base and _kept["tried"] is not None and now - _kept["tried"] < RETRY_SECONDS:
            return _kept["keys"] if fresh else {}
        if _kept["base"] != base:
            _kept.update(at=None, keys={}, base=base)
        _kept["tried"] = now
    try:
        keys = _fetch_keys(base)
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        current_app.logger.warning("ORCID keys (JWKS) not read: %s", type(e).__name__)
        return {}
    with _lock:
        _kept.update(at=time.monotonic(), keys=keys, base=base)
    return keys


def forget():
    """Drop the kept keys (tests, a change of ORCID_BASE_URL)."""
    with _lock:
        _kept.update(at=None, tried=None, keys={}, base=None)


def claims(id_token, orcid=None):
    """The claims of a valid ID token of ORCID for this installation (and this iD), else None."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        head, body, signature = str(id_token).split(".")
        header = json.loads(_b64(head))
        if not isinstance(header, dict) or header.get("alg") != "RS256":
            return None  # never "none" or an HMAC with a public key as secret
        kid = str(header.get("kid") or "")
        keys = _keys(kid)
        key = keys.get(kid) if kid else (next(iter(keys.values())) if len(keys) == 1 else None)
        if key is None:
            return None
        key.verify(_b64(signature), f"{head}.{body}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        data = json.loads(_b64(body))
    except (InvalidSignature, ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    cfg = current_app.config
    audience = data.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    try:
        expired = float(data["exp"]) < time.time() - CLOCK_SLACK
    except (KeyError, TypeError, ValueError):
        return None
    if (data.get("iss") != cfg["ORCID_BASE_URL"] or cfg["ORCID_CLIENT_ID"] not in audiences or expired
            or (orcid is not None and data.get("sub") != orcid)):
        return None
    return data
