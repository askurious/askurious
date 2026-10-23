"""The certificate check of the status page (app/status_node.py; audit 2026-10-18, CFG-13): it switched the check of
the chain off and counted a self-signed certificate as fine as long as it had days left, so a name without a Let's
Encrypt certificate (nginx/self-signed.sh's stand-in) stayed green while browsers warned. Now the chain and the
name are checked as a browser checks them; only names in STATUS_TLS_UNTRUSTED_OK may be untrusted."""
import ssl
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import status_node


def _der(self_signed=True, days=60):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ask.example.org")])
    issuer = subject if self_signed else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Some CA")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=days, hours=1)).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.DER)


def _fake(monkeypatch, trusted, der):
    calls = []

    def peer(host, port, verify):
        calls.append(verify)
        if verify and not trusted:
            raise ssl.SSLCertVerificationError("self-signed certificate")
        return der

    monkeypatch.setattr(status_node, "_peer_cert", peer)
    return calls


def test_a_self_signed_certificate_is_a_disruption(monkeypatch):
    monkeypatch.delenv("STATUS_TLS_UNTRUSTED_OK", raising=False)
    calls = _fake(monkeypatch, trusted=False, der=_der())
    ok, _ms, detail = status_node.check("tls", "ask.example.org:443")
    assert not ok and detail == "60 self-signed" and calls == [True, False]  # checked first, read only to count days


def test_an_untrusted_issuer_or_name_is_a_disruption(monkeypatch):
    monkeypatch.delenv("STATUS_TLS_UNTRUSTED_OK", raising=False)
    _fake(monkeypatch, trusted=False, der=_der(self_signed=False))
    ok, _ms, detail = status_node.check("tls", "ask.example.org:443")
    assert not ok and detail == "60 untrusted"


def test_a_trusted_certificate_counts_its_days_as_before(monkeypatch):
    _fake(monkeypatch, trusted=True, der=_der(self_signed=False))
    assert status_node.check("tls", "ask.example.org:443")[:3:2] == (True, "60")
    _fake(monkeypatch, trusted=True, der=_der(self_signed=False, days=3))
    assert not status_node.check("tls", "ask.example.org:443")[0]


def test_only_names_listed_may_be_untrusted(monkeypatch):
    """STATUS_TLS_UNTRUSTED_OK: the explicit list, e.g. a new name before its Let's Encrypt certificate; among several
    names an untrusted one not listed is what the detail names."""
    monkeypatch.setenv("STATUS_TLS_UNTRUSTED_OK", "Ask.Example.org")
    _fake(monkeypatch, trusted=False, der=_der())
    ok, _ms, detail = status_node.check("tls", "ask.example.org:443")
    assert ok and detail == "60 self-signed"

    def peer(host, port, verify):
        if verify and host != "ask.example.org":
            raise ssl.SSLCertVerificationError("x")
        return _der(days=90 if host == "ask.example.org" else 80)

    monkeypatch.setattr(status_node, "_peer_cert", peer)
    monkeypatch.setenv("STATUS_TLS_UNTRUSTED_OK", "")
    ok, _ms, detail = status_node.check("tls", "ask.example.org|new.example.org:443")
    assert not ok and detail == "80 self-signed new.example.org"


def test_the_check_uses_the_systems_trust_and_the_name():
    import inspect

    source = inspect.getsource(status_node._peer_cert)
    assert "create_default_context()" in source and "if not verify" in source
    assert "verify=True" in inspect.getsource(status_node._cert_days)
