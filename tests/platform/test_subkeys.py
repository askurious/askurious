"""Keys of their own per purpose (app/subkeys.py, HKDF; audit 2026-10-18, CFG-08): SECRET_KEY was the HMAC key of
many purposes, separated only by prefixes of the messages. New purposes and the values only shown take subkey();
the purposes that keep values (deletion codes, invitation links, stored secrets) are listed in subkeys.LEGACY, and no
new direct use of SECRET_KEY may appear outside that list."""
import os
import re

from app import subkeys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIRECT = re.compile(r'\["SECRET_KEY"\]|get\("SECRET_KEY"|\.secret_key\b')


def test_hkdf_gives_each_purpose_its_own_key():
    one, two = subkeys.derive("k" * 64, "erase"), subkeys.derive("k" * 64, "crowd")
    assert len(one) == 32 and one != two and one == subkeys.derive(b"k" * 64, "erase")
    assert subkeys.derive("l" * 64, "erase") != one
    # RFC 5869 test case 1 holds for the primitive (same algorithm with its salt and info)
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    okm = HKDF(algorithm=hashes.SHA256(), length=42, salt=bytes.fromhex("000102030405060708090a0b0c"),
               info=bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")).derive(bytes.fromhex("0b" * 22))
    assert okm.hex().startswith("3cb25f25faacd57a90434f64d0362f2a")


def test_no_new_direct_use_of_secret_key():
    """A new purpose takes subkeys.subkey(); a module that uses SECRET_KEY directly is listed in subkeys.LEGACY with
    the reason why its values must stay as they are."""
    found = set()
    for folder, _, files in os.walk(os.path.join(ROOT, "app")):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    if DIRECT.search(f.read()):
                        found.add(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    assert sorted(found - set(subkeys.LEGACY)) == []
    assert sorted(set(subkeys.LEGACY) - found) == []  # the list says what is, no more


def test_shown_values_take_their_own_keys(app, survey):
    """The keys of rows below "Bearbeiten" and the code word against AI agents come from subkeys; the code word of
    before is still recognised in an answer (a page shown before the deploy)."""
    from werkzeug.datastructures import MultiDict

    from app import quality
    from app.extensions import db
    from app.models import Response, Survey
    from app.surveys import base

    with app.test_request_context():
        s = db.session.get(Survey, survey)
        assert base._row_key(s, {"name": "kurs", "value": "A"}, False) == "h" + subkeys.mac("link-row", f"{s.id}:kurs=A")[:16]
        new, old = quality.bait_word(s), quality.bait_word(s, legacy=True)
        for word in {new, old}:
            response = Response(survey_id=s.id, paradata={})
            quality.note_bait(s, response, MultiDict({"i3": f"Antwort {word}"}))
            assert response.paradata.get("_ai") == 1, word
        response = Response(survey_id=s.id, paradata={})
        quality.note_bait(s, response, MultiDict({"i3": "nichts davon"}))
        assert not response.paradata.get("_ai")
