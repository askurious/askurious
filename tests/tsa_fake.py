"""A timestamp service for the tests (RFC 3161, as the DFN answers): its own root and a certificate for timestamps,
built once per test session. post() stands in for requests.post of app/seal.py; ROOT is the SHA-256 of the root to
put into seal_verify.TRUSTED_ROOTS."""
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.seal_verify import (MESSAGE_DIGEST_OID, SHA256_OID, TSTINFO_OID, _children, _der, _int, _oid_der, _only,
                             _tlv)

NOW = datetime.now(timezone.utc)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject, issuer, key, sign_key, ca, eku=None):
    builder = (x509.CertificateBuilder().subject_name(_name(subject)).issuer_name(_name(issuer))
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(NOW - timedelta(days=1)).not_valid_after(NOW + timedelta(days=365))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if ca:  # a CA as seal_verify demands of every issuer (code audit 2026-10-08, S1)
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False, data_encipherment=False,
            key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False),
            critical=True)
    if eku:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku), critical=True)
    return builder.sign(sign_key, hashes.SHA256())


ROOT_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ROOT_CERT = _cert("Test Root", "Test Root", ROOT_KEY, ROOT_KEY, True)
TSA_CERT = _cert("Test TSA", "Test Root", TSA_KEY, ROOT_KEY, False, [ExtendedKeyUsageOID.TIME_STAMPING])
ROOT = ROOT_CERT.fingerprint(hashes.SHA256()).hex()
calls = []


def token(digest, nonce=None, when=None, key=TSA_KEY, cert=TSA_CERT):
    """A timestamp token for a SHA-256 value."""
    when = when or datetime.now(timezone.utc)
    imprint = _der(0x30, _der(0x30, _oid_der(SHA256_OID) + b"\x05\x00") + _der(0x04, digest))
    tst = _der(0x30, _int(1) + _oid_der("1.2.3.4") + imprint + _int(len(calls) + 1)
               + _der(0x18, when.strftime("%Y%m%d%H%M%SZ").encode()) + (_int(nonce) if nonce is not None else b""))
    encap = _der(0x30, _oid_der(TSTINFO_OID) + _der(0xA0, _der(0x04, tst)))
    attrs = (_der(0x30, _oid_der("1.2.840.113549.1.9.3") + _der(0x31, _oid_der(TSTINFO_OID)))
             + _der(0x30, _oid_der(MESSAGE_DIGEST_OID) + _der(0x31, _der(0x04, hashlib.sha256(tst).digest()))))
    signature = key.sign(_der(0x31, attrs), padding.PKCS1v15(), hashes.SHA256())
    issuer = cert.issuer.public_bytes()
    signer = _der(0x30, _int(1) + _der(0x30, issuer + _int(cert.serial_number))
                  + _der(0x30, _oid_der(SHA256_OID) + b"\x05\x00") + _der(0xA0, attrs)
                  + _der(0x30, _oid_der("1.2.840.113549.1.1.1") + b"\x05\x00") + _der(0x04, signature))
    certs = b"".join(c.public_bytes(serialization.Encoding.DER) for c in (cert, ROOT_CERT))
    signed = _der(0x30, _int(3) + _der(0x31, _der(0x30, _oid_der(SHA256_OID) + b"\x05\x00")) + encap
                  + _der(0xA0, certs) + _der(0x31, signer))
    return _der(0x30, _oid_der("1.2.840.113549.1.7.2") + _der(0xA0, signed))


def reply(digest, nonce=None):
    return _der(0x30, _der(0x30, _int(0)) + token(digest, nonce))


class _Answer:
    def __init__(self, content):
        self.content, self.status_code = content, 200

    def raise_for_status(self):
        pass


def post(url, data=None, timeout=None, headers=None, **kw):
    """What the DFN does with a TimeStampReq: stamp its hash, echo its nonce."""
    calls.append(url)
    req = _only(data, _tlv(data, 0))
    imprint = _only(data, req[1])
    digest = data[imprint[1][1]:imprint[1][2]]
    nonce = int.from_bytes(data[req[2][1]:req[2][2]], "big")
    return _Answer(reply(digest, nonce))


def down(url, data=None, timeout=None, headers=None, **kw):
    raise OSError("timestamp service not reachable")


__all__ = ["ROOT", "post", "down", "token", "reply", "calls", "_children"]
