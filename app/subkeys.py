"""Keys of their own for each purpose, derived from one key of the server with HKDF (RFC 5869; audit 2026-10-18, CFG-08).

SECRET_KEY served as the HMAC key of many purposes so far, each separated from the others by a prefix of its message
("erase|…", "assignment:…", "crowd:…"). That holds as long as no two message formats can ever look alike; a new
feature that showed HMAC(SECRET_KEY, text a user chose) could become an oracle for another purpose. With subkey()
each purpose gets a key of its own, so the outputs of one say nothing about another.

New purposes take subkey(); so do purposes whose values are only shown and never kept (a key for a row on a page,
the figures of the crowd item, the code word against AI agents). The purposes that keep values or hand them out for
later (deletion codes, invitation links, stored and encrypted secrets, the limits per sender, the daily key of the
security log, receipts of a participation, the results store, diary times, transcription jobs) keep their key as
before: a new key would make deletion codes participants noted down, links already mailed and stored secrets
worthless. They are listed in LEGACY, and tests/platform/test_hardening.py refuses a new direct use of SECRET_KEY
outside that list. Changing one of them needs its own transition (accept the old value next to the new one), as
compute.py does for the Rechenknecht's signatures.
"""
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SALT = b"askurious-subkey-v1"

# the modules that use SECRET_KEY (or app.secret_key) directly, and why they keep doing so
LEGACY = {
    "app/cookies.py": "sessions (Flask's itsdangerous signer with its own key derivation)",
    "app/key_strength.py": "checks the key itself",
    "app/secrets_box.py": "stored secrets: Fernet and AES-SIV keys hashed from the key with a prefix",
    "app/erasure.py": "deletion codes participants noted down",
    "app/survey_invites.py": "links and opt-out hashes of invitation mails already sent",
    "app/browser_receipts.py": "receipts of a participation kept in the browser",
    "app/limits.py": "pseudonyms of senders in the shared counters",
    "app/security_log.py": "the daily key of the address pseudonyms in the security log",
    "app/results_store.py": "the signature of results kept on disk",
    "app/whisper_jobs.py": "job tokens of transcriptions under way",
    "app/diary.py": "join codes of test persons handed out, the prompt times of running diary studies (else DIARY_KEY)",
    "app/constructs.py": "the pseudonyms of the pooled construct data (POOL_KEY, else SECRET_KEY)",
    "app/editor/preview.py": "preview links already handed out (itsdangerous with its own salt)",
    "app/passwords.py": "the cookie of a known device (itsdangerous with its own salt)",
    "app/runtime.py": "the name under which a tab keeps the inputs of a page not yet sent",
    "app/quality.py": "the code word against AI agents of before the change: recognised in answers, never shown",
    "app/live/devices.py": "device ids of live votes already handed out to browsers (prefix live-device|)",
    "app/org_admin.py": "join links of organisations already handed out (itsdangerous with its own salt)",
    "app/__init__.py": "checks for the development key",
    "app/subkeys.py": "derives the keys of this module",
}


def derive(key, purpose, length=32):
    """HKDF-SHA256 of `key` (text or bytes) for `purpose` (text): `length` bytes, different for every purpose."""
    if isinstance(key, str):
        key = key.encode()
    if not key:
        raise ValueError("no key to derive from")
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=SALT, info=purpose.encode()).derive(key)


def subkey(purpose, length=32, app=None):
    """The key of `purpose`, derived from the server's SECRET_KEY."""
    from flask import current_app

    return derive((app or current_app).config.get("SECRET_KEY") or "", purpose, length)


def mac(purpose, message, app=None):
    """HMAC-SHA256 of `message` (text or bytes) under the key of `purpose`, as hex."""
    if isinstance(message, str):
        message = message.encode()
    return hmac.new(subkey(purpose, app=app), message, hashlib.sha256).hexdigest()
