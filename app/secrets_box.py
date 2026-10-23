"""Symmetric encryption for secrets at rest (OAuth tokens, signing keys), with Fernet (AES + HMAC).

The key comes from TOKEN_KEY or, if unset, is derived from SECRET_KEY. Changing either without keeping the old one
in TOKEN_KEY_FALLBACKS (or SECRET_KEY_FALLBACKS) makes stored tokens unreadable; people then simply connect again. Setting TOKEN_KEY on a server that ran without
it loses nothing: what was locked with the key from SECRET_KEY still opens with it, as long as SECRET_KEY stays
(security audit 2026-10-09, BT16); new and changed secrets are locked with TOKEN_KEY, which then survives a new
SECRET_KEY.

seal/unseal: short opaque tokens for links (the return link of an external section, app/external.py), with AES-SIV
under a key of their own per purpose. AES-SIV is deterministic: the same content gives the same token, so a link
can be made again and is the same link; nobody can read or change what is in it.

Two boxes (audit 2026-10-22, E-01): what the participants' containers have to open (the header value of a web service,
the signing keys of an external section and of panel links, the seals of return links and diary entries) goes in the
box PARTICIPANT, locked with PARTICIPANT_TOKEN_KEY; everything else (second factors, OAuth tokens, own AI keys, ...)
stays with TOKEN_KEY, which the participants' containers then need no longer. Without PARTICIPANT_TOKEN_KEY the box
PARTICIPANT is TOKEN_KEY's as before, and TOKEN_KEY always opens it too, so nothing breaks while it is set up and
app/key_rotation.py locks the survey keys anew.
"""
import base64
import hashlib

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from flask import current_app


PARTICIPANT = "participant"


def _raw(box=None):
    if box == PARTICIPANT and current_app.config.get("PARTICIPANT_TOKEN_KEY"):
        return current_app.config["PARTICIPANT_TOKEN_KEY"]
    return current_app.config.get("TOKEN_KEY") or current_app.config["SECRET_KEY"]


def _raws(box=None):
    """The keys that open, the one that locks first: TOKEN_KEY, the TOKEN_KEYs of before (TOKEN_KEY_FALLBACKS), then
    SECRET_KEY (what was locked before TOKEN_KEY was set) and the SECRET_KEYs of before. A key is changed without
    losing anything: the old one goes into the fallbacks, app/key_rotation.py locks everything anew, then the fallback
    can go (audit 2026-10-13, KR-6; BETRIEB.md, "Schlüssel wechseln")."""
    config = current_app.config
    keys = []
    first = ([config.get("PARTICIPANT_TOKEN_KEY"), *(config.get("PARTICIPANT_TOKEN_KEY_FALLBACKS") or [])]
             if box == PARTICIPANT else [])
    for key in [*first, _raw(), *(config.get("TOKEN_KEY_FALLBACKS") or []), config["SECRET_KEY"],
                *(config.get("SECRET_KEY_FALLBACKS") or [])]:
        if key and key not in keys:
            keys.append(key)
    return keys


def _fernet_of(raw):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("token-box:" + raw).encode()).digest()))


def _fernet(box=None):
    return _fernet_of(_raw(box))


def encrypt(text, box=None):
    return _fernet(box).encrypt(text.encode()).decode() if text else ""


def is_current(token, box=None):
    """The token opens with the key that locks now (no rotation needed for it)."""
    try:
        _fernet(box).decrypt(token.encode())
        return True
    except InvalidToken:
        return False


def reencrypt(token, box=None):
    """`token` locked anew with the current key of `box`; the same token when it is current already, when it is empty
    or when no key opens it (it stays as it is, nothing is lost by trying)."""
    if not token or is_current(token, box):
        return token
    text = decrypt(token, box)
    return encrypt(text, box) if text else token


def decrypt(token, box=None):
    if not token:
        return ""
    for raw in _raws(box):
        try:
            return _fernet_of(raw).decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    return ""


def _siv(purpose, raw=None):
    return AESSIV(hashlib.sha512((f"siv:{purpose}:" + (raw or _raw())).encode()).digest())


def seal(purpose, data, box=None):
    """`data` (bytes) as a URL-safe token without padding: encrypted and authenticated."""
    return base64.urlsafe_b64encode(_siv(purpose, _raw(box)).encrypt(data, None)).decode().rstrip("=")


def unseal(purpose, token, box=None):
    """The bytes sealed for this purpose, or None for anything else (changed, cut, made for another purpose)."""
    try:
        raw = base64.urlsafe_b64decode((token + "=" * (-len(token) % 4)).encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        return None
    for key in _raws(box):
        try:
            return _siv(purpose, key).decrypt(raw, None)
        except (InvalidTag, ValueError, TypeError):
            continue
    return None
