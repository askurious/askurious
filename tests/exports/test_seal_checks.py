"""Seal chain: what the checker (app/seal_verify.py) and the chain itself must hold against (code audit 2026-10-08,
S1 certificates that are no CA, S2 doubled or invented rows, S3 an old stamp before a rebuilt tail, S7 a case ending
while the chain is switched on, S8 a broken stamp, S10 the start of the fieldwork waiting for the service)."""
import base64
import hashlib
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app import seal, seal_verify
from app.extensions import db
from app.models import Response, Survey
from app.seal_verify import MESSAGE_DIGEST_OID, SHA256_OID, TSTINFO_OID, _der, _int, _oid_der
from tests import tsa_fake
from tests.privacy.test_erasure import build, take

HERE = os.path.dirname(os.path.dirname(__file__))
NOW = datetime.now(timezone.utc)
KEYS = [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(4)]


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject, issuer, key, sign_key, ca, eku=None, path_length=None, sign_usage=True):
    b = (x509.CertificateBuilder().subject_name(_name(subject)).issuer_name(_name(issuer))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(NOW - timedelta(days=400)).not_valid_after(NOW + timedelta(days=365))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=path_length if ca else None), critical=True))
    if ca and sign_usage:
        b = b.add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                          data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                          crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
    if eku:
        b = b.add_extension(x509.ExtendedKeyUsage(eku), critical=False)
    return b.sign(sign_key, hashes.SHA256())


def _token(digest, when, key, cert, chain, signature=True):
    imprint = _der(0x30, _der(0x30, _oid_der(SHA256_OID) + b"\x05\x00") + _der(0x04, digest))
    tst = _der(0x30, _int(1) + _oid_der("1.2.3.4") + imprint + _int(7)
               + _der(0x18, when.strftime("%Y%m%d%H%M%SZ").encode()))
    encap = _der(0x30, _oid_der(TSTINFO_OID) + _der(0xA0, _der(0x04, tst)))
    attrs = (_der(0x30, _oid_der("1.2.840.113549.1.9.3") + _der(0x31, _oid_der(TSTINFO_OID)))
             + _der(0x30, _oid_der(MESSAGE_DIGEST_OID) + _der(0x31, _der(0x04, hashlib.sha256(tst).digest()))))
    sig = key.sign(_der(0x31, attrs), padding.PKCS1v15(), hashes.SHA256())
    signer = _der(0x30, _int(1) + _der(0x30, cert.issuer.public_bytes() + _int(cert.serial_number))
                  + _der(0x30, _oid_der(SHA256_OID) + b"\x05\x00") + _der(0xA0, attrs)
                  + _der(0x30, _oid_der("1.2.840.113549.1.1.1") + b"\x05\x00")
                  + (_der(0x04, sig) if signature else b""))
    certs = b"".join(c.public_bytes(serialization.Encoding.DER) for c in chain)
    signed = _der(0x30, _int(3) + _der(0x31, _der(0x30, _oid_der(SHA256_OID) + b"\x05\x00")) + encap
                  + _der(0xA0, certs) + _der(0x31, signer))
    return _der(0x30, _oid_der("1.2.840.113549.1.7.2") + _der(0xA0, signed))


ROOT = _cert("Root", "Root", KEYS[0], KEYS[0], True)
ROOTS = {ROOT.fingerprint(hashes.SHA256()).hex(): "Root"}
DIGEST = hashlib.sha256(b"rebuilt chain").digest()
BACKDATED = NOW - timedelta(days=200)


def test_s1_an_end_entity_certificate_cannot_issue_a_timestamp_service():
    leaf = _cert("Some User", "Root", KEYS[1], KEYS[0], False, [ExtendedKeyUsageOID.CLIENT_AUTH])
    evil = _cert("Evil TSA", "Some User", KEYS[2], KEYS[1], False, [ExtendedKeyUsageOID.TIME_STAMPING])
    token = _token(DIGEST, BACKDATED, KEYS[2], evil, (evil, leaf, ROOT))
    with pytest.raises(seal_verify.Invalid, match="not a CA"):
        seal_verify.verify(token, DIGEST, roots=ROOTS)


def test_s1_a_ca_without_key_cert_sign_or_beyond_its_path_length_does_not_hold():
    no_usage = _cert("CA", "Root", KEYS[1], KEYS[0], True, sign_usage=False)
    tsa = _cert("TSA", "CA", KEYS[2], KEYS[1], False, [ExtendedKeyUsageOID.TIME_STAMPING])
    with pytest.raises(seal_verify.Invalid, match="not a CA"):
        seal_verify.verify(_token(DIGEST, NOW, KEYS[2], tsa, (tsa, no_usage, ROOT)), DIGEST, roots=ROOTS)
    # Root -> Issuing (path length 0) -> Sub-CA -> TSA: one CA too many under the Issuing CA
    issuing = _cert("Issuing", "Root", KEYS[1], KEYS[0], True, path_length=0)
    sub = _cert("Sub", "Issuing", KEYS[3], KEYS[1], True)
    tsa = _cert("TSA", "Sub", KEYS[2], KEYS[3], False, [ExtendedKeyUsageOID.TIME_STAMPING])
    with pytest.raises(seal_verify.Invalid, match="path length"):
        seal_verify.verify(_token(DIGEST, NOW, KEYS[2], tsa, (tsa, sub, issuing, ROOT)), DIGEST, roots=ROOTS)
    # a proper CA path holds
    ok_issuing = _cert("Issuing", "Root", KEYS[1], KEYS[0], True, path_length=1)
    tsa = _cert("TSA", "Issuing", KEYS[2], KEYS[1], False, [ExtendedKeyUsageOID.TIME_STAMPING])
    token = _token(DIGEST, NOW, KEYS[2], tsa, (tsa, ok_issuing, ROOT))
    assert seal_verify.verify(token, DIGEST, roots=ROOTS)
    # a pin of the issuing CA per root: another CA under the same root does not hold
    pin = {next(iter(ROOTS)): {ok_issuing.fingerprint(hashes.SHA256()).hex(): "Issuing"}}
    assert seal_verify.verify(token, DIGEST, roots=ROOTS, issuers=pin)
    other = _cert("Other", "Root", KEYS[3], KEYS[0], True)
    tsa2 = _cert("TSA", "Other", KEYS[2], KEYS[3], False, [ExtendedKeyUsageOID.TIME_STAMPING])
    with pytest.raises(seal_verify.Invalid, match="CA of the timestamp service"):
        seal_verify.verify(_token(DIGEST, NOW, KEYS[2], tsa2, (tsa2, other, ROOT)), DIGEST, roots=ROOTS, issuers=pin)


def test_s1_the_dfn_issuing_ca_is_pinned():
    with open(os.path.join(HERE, "data", "dfn_timestamp.der"), "rb") as f:
        token = f.read()
    digest = hashlib.sha256(b"hello\n").digest()
    assert seal_verify.verify(token, digest)                               # issued by the pinned Issuing CA 2022
    root = next(iter(seal_verify.TRUSTED_ROOTS))
    with pytest.raises(seal_verify.Invalid, match="CA of the timestamp service"):
        seal_verify.verify(token, digest, issuers={root: {"00" * 32: "another CA"}})


def _chain(uid, n_cases, at=None):
    prev, entries = seal_verify.genesis(uid), []
    for seq in range(1, n_cases + 1):
        e = {"seq": seq, "kind": "sealed", "ref": None, "status": "completed", "content": f"{seq:064x}", "reason": None,
             "at": at or f"2026-10-08T10:00:{seq:02d}.000000Z", "prev": prev}
        e["digest"] = seal_verify.link(prev, e)
        prev = e["digest"]
        entries.append(e)
    return {"format": seal_verify.FORMAT, "survey": uid, "entries": entries, "anchors": []}


def test_s2_doubled_invented_and_unhashed_rows_do_not_hold():
    chain = _chain("abc", 3)
    h = [e["content"] for e in chain["entries"]]
    rows = [{"seal_seq": "1", "seal_hash": h[0], "status": "completed", "source": "real"},
            {"seal_seq": "1", "seal_hash": h[0], "status": "completed", "source": "real"},   # doubled case
            {"seal_seq": "2", "seal_hash": "", "status": "completed", "source": "real"},     # hash removed
            {"seal_seq": "3", "seal_hash": h[2], "status": "completed", "source": "real"},
            {"seal_seq": "", "seal_hash": "", "status": "screenout", "source": "real"},      # invented case
            {"seal_seq": "", "seal_hash": "", "status": "completed", "source": "dummy"},     # by design without
            {"seal_seq": "", "seal_hash": "", "status": "completed", "source": "test"},
            {"seal_seq": "", "seal_hash": "", "status": "active", "source": "real"}]
    out = seal_verify.check_rows(chain, rows)
    assert out["duplicate"] == [1] and out["nohash"] == [2] and out["unsealed"] == [5]
    assert out["missing"] == {} and out["changed"] == [] and out["unknown"] == []
    lines, ok = seal_verify.report(chain, rows)
    assert not ok and any("more than one row" in line for line in lines)
    assert any("without a case number: 5" in line for line in lines) and any("without seal_hash: 2" in line for line in lines)
    whole = [r for n, r in enumerate(rows) if n not in (1, 4)]
    whole[1] = {**whole[1], "seal_hash": h[1]}
    out = seal_verify.check_rows(chain, whole)
    assert not (out["duplicate"] or out["nohash"] or out["unsealed"] or out["missing"])


def _anchored(chain, seq=1):
    first = chain["entries"][seq - 1]
    chain["anchors"] = [{"seq": seq, "digest": first["digest"], "tsa": "x", "time": "",
                         "token": base64.b64encode(tsa_fake.token(bytes.fromhex(first["digest"]))).decode()}]
    return chain


def test_s3_an_old_unstamped_tail_holds_only_up_to_the_stamp(monkeypatch, tmp_path):
    monkeypatch.setitem(seal_verify.TRUSTED_ROOTS, tsa_fake.ROOT, "Test Root")
    rebuilt = _anchored(_chain("abc", 3))                       # entries of 2026-10-08 10:00, checked days later
    later = datetime(2026, 10, 12, tzinfo=timezone.utc)
    lines, ok = seal_verify.report(rebuilt, now=later)
    assert not ok and lines[-1].startswith("RESULT: holds only up to entry 1")
    assert any(line.startswith("NOT STAMPED: entries 2 to 3") for line in lines)
    # within two hours, too: offline an unstamped end is never relied on (audit 2026-10-10, S2: its time is the
    # file's word); with the chain on the server holding it unchanged it holds
    lines, ok = seal_verify.report(rebuilt, now=datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc))
    assert not ok and lines[-1].startswith("RESULT: holds only up to entry 1")
    lines, ok = seal_verify.report(rebuilt, now=datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc), server=rebuilt)
    assert ok and lines[-1] == "RESULT: holds."
    path = tmp_path / "seal.json"
    path.write_text(__import__("json").dumps(rebuilt), encoding="utf-8")
    assert seal_verify.main(["x", str(path)]) == 3              # an own result, not 0
    # entries of a case hold the day only (app/seal.py): written at the latest by the end of that day
    today = NOW.strftime("%Y-%m-%d")
    fresh = _anchored(_chain("abc", 3, at=today))
    assert seal_verify.report(fresh, server=fresh)[1]
    lines, ok = seal_verify.report(_anchored(_chain("abc", 3, at=today)), now=NOW + timedelta(days=1, hours=3))
    assert not ok and lines[-1].startswith("RESULT: holds only up to entry 1")
    # a broken chain stays "does not hold"
    broken = _anchored(_chain("abc", 3))
    broken["entries"][2]["content"] = "0" * 64
    assert seal_verify.report(broken, now=later)[0][-1] == "RESULT: does not hold, see above."


def test_s8_a_broken_timestamp_is_a_finding_not_a_crash(monkeypatch):
    tsa = _cert("TSA", "Root", KEYS[2], KEYS[0], False, [ExtendedKeyUsageOID.TIME_STAMPING])
    with pytest.raises(seal_verify.Invalid, match="without a signature"):
        seal_verify.verify(_token(DIGEST, NOW, KEYS[2], tsa, (tsa, ROOT), signature=False), DIGEST, roots=ROOTS)
    for junk in (b"", b"\x30\x03\x02\x01\x01", b"\x30\x80" + b"\x00" * 10, os.urandom(64)):
        with pytest.raises(seal_verify.Invalid):
            seal_verify.verify(junk, DIGEST, roots=ROOTS)
        with pytest.raises(seal_verify.Invalid):
            seal_verify.info(junk)
        with pytest.raises(seal_verify.Invalid):
            seal_verify.token_from_reply(junk)
    chain = _chain("abc", 2)
    chain["anchors"] = [{"seq": 2, "digest": chain["entries"][1]["digest"], "token": "not base64 !"},
                        {"seq": 2, "digest": chain["entries"][1]["digest"]},
                        {"seq": 2, "digest": chain["entries"][1]["digest"],
                         "token": base64.b64encode(_token(DIGEST, NOW, KEYS[2], tsa, (tsa, ROOT), False)).decode()}]
    del chain["entries"][0]["kind"]
    result = seal_verify.check_chain(chain)
    assert len(result["problems"]) == 4 and result["last_anchor"] is None
    assert seal_verify.report(chain)[0][-1] == "RESULT: does not hold, see above."


def test_s8_check_now_reports_a_broken_stamp(app, client, survey):
    from app.models import SealAnchor
    from tests.exports.test_seal import sealed_survey

    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    with app.app_context():
        s = db.session.get(Survey, 1)
        head = seal.entries(s.uid)[-1]
        tsa = _cert("TSA", "Root", KEYS[2], KEYS[0], False, [ExtendedKeyUsageOID.TIME_STAMPING])
        db.session.add(SealAnchor(survey_uid=s.uid, seq=head.seq, digest=head.digest, tsa="x", time=datetime(2026, 10, 8),
                                  token=_token(bytes.fromhex(head.digest), NOW, KEYS[2], tsa, (tsa, ROOT), False)))
        db.session.commit()
    r = client.post("/surveys/1/export/seal-chain/check", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and "without a signature" in r.data.decode()


def test_s10_the_start_of_the_fieldwork_waits_shortly_for_the_service(app, client, survey, monkeypatch):
    app.config["TSA_URL"] = "http://tsa.test"
    monkeypatch.setitem(seal_verify.TRUSTED_ROOTS, tsa_fake.ROOT, "Test Root")
    waits = []

    def post(url, data=None, timeout=None, headers=None, **kw):
        waits.append(timeout)
        return tsa_fake.post(url, data=data, timeout=timeout, headers=headers)

    monkeypatch.setattr(requests, "post", post)
    build(app, client)                                          # starts the fieldwork: the pre-registration is stamped
    assert waits == [seal.QUICK] and seal.QUICK == (3, 5)
    from app import prereg

    with app.app_context():
        s = db.session.get(Survey, 1)
        [(name, *_)] = prereg.listing(s)
        os.remove(prereg.stamp_path(s, name))
        assert prereg.stamp_missing() == 1                      # the housekeeping, in the background: the full wait
    assert waits[-1] == seal.TIMEOUT


def test_s7_a_case_ending_while_the_chain_is_switched_on_is_sealed(app, client, survey):
    """PostgreSQL, READ COMMITTED: enable() has set seal=True but not committed; a participation ends meanwhile. It
    must wait for the switch and then seal itself (SQLite has one writer: the race cannot happen there)."""
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            pytest.skip("the race needs two writers at once (PostgreSQL)")
    from tests.conftest import CONSENT_OK

    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)                        # begun, not ended
    done = []

    def end():
        with app.app_context():
            r = db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False))
            r.status, r.completed_at = Response.COMPLETED, datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()
            done.append(r.id)

    with app.app_context():
        seal.enable(db.session.get(Survey, 1))                  # not committed yet
        t = threading.Thread(target=end)
        t.start()
        t.join(1.5)
        waited = t.is_alive()
        db.session.commit()
    t.join(10)
    assert waited and done
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [e.kind for e in seal.entries(s.uid)] == ["enabled", "sealed"]
        assert seal.check(s)["problems"] == []
