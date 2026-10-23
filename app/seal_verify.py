# SPDX-License-Identifier: MIT
#
# MIT License
#
# Copyright (c) 2026 RWTH Aachen University. Authors: Philipp Brauner, Ralf Philipsen.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
"""Checking a seal chain of Askurious ("Siegelkette", app/seal.py) and RFC 3161 timestamps.

This file stands on its own: Python 3.9 or later and the package cryptography (pip install cryptography), nothing of
Askurious. Askurious uses it itself and puts it into every download of a seal chain, so that anyone can check a data
file without trusting the platform:

    python seal_verify.py seal.json                 the chain and its timestamps
    python seal_verify.py seal.json daten.csv       and which cases of the chain the data file lacks
    python seal_verify.py --completed seal.json data.csv   the same for a file of completed participations only
    python seal_verify.py stempel.tsr datei.zip            a timestamp of a file (the pre-registration snapshot)
    python seal_verify.py --server URL seal.json data.csv  the unstamped end compared with the chain at URL (an
                                                           installation other than Askurious's own, OWN_HOSTS)
    python seal_verify.py --offline seal.json data.csv     without asking the server

What it checks:
  chain       every entry's hash follows from the one before (h_n = SHA-256 of h_n-1 and the entry); a removed,
              changed or reordered entry breaks every later one
  timestamps  every timestamp of the DFN (or another RFC 3161 service) is signed by a certificate that leads to a
              pinned root (TRUSTED_ROOTS) through CAs only (BasicConstraints, keyCertSign, path length), issued by
              the pinned CA of the service (TSA_ISSUERS), was valid at the time stamped, and stamps exactly the hash
              of the chain at its position: the chain up to there existed no later than that time. An entry whose
              time lies in the future does not hold. A timestamp may also stamp the root of a Merkle tree over
              the heads of several chains (one call of the service for many surveys); it then carries the path
              from its entry's hash to that root (MERKLE_RULE below)
  the end     entries after the last stamp are relied on only when the chain on the server holds them unchanged:
              anyone can append entries to a file, such as invented deletion records. The server is the one named
              with --server, or else the address in seal.json (field "url") only when it is https on one of
              Askurious's own hosts (OWN_HOSTS): the address comes from the file being checked, and a forged file
              could name a server serving the forged chain. Without a server to trust (--offline, not reachable,
              or an address in the file that is not Askurious's own) the check goes up to the stamped state only, the result is "holds only up to entry n" (exit code 3), and cases
              whose deletion record lies in the unstamped end count as missing
  data file   every case of the chain is in the file (column seal_seq), unless the chain records its deletion (an
              entry "deleted" or "purged"); cases the file holds although the chain deleted them are named too, as
              are case numbers in more than one row, rows without seal_hash, and rows of a real participation that
              ended (status completed, screenout, quotafull, excluded) without a case number. A data file exported
              without unfinished cases or without speeders lacks those cases by design: the report names the status
              of every case missing, so that it can be told apart from a removal. Only rows with a case number are
              covered: the report counts the others by source and status, a real row without a status counts as
              unsealed, and a file without the column seal_seq or status does not hold
  --completed a data file that holds the completed participations only (the public package on Zenodo or Coscine):
              only completed cases of the chain must be in it or have a deletion record; the cases that ended
              otherwise (screen-out, quota full, excluded) are left out by design and counted, not missed. The verdict
              then is "holds for the cases in the file": it says nothing about whether those others are complete

What it does not check: the answer values. The hash of a case's content (column seal_hash) is computed from the
answers as stored on the server, with a random salt that is deleted with the case; a data file cannot be recomputed
into it. It ties a row to its entry. So the check of a data file shows the completeness and origin of its cases
(which cases there are, which were deleted, none invented), not whether the values in a row are the ones given.
The platform itself checks the values against the hashes every hour (the check page /verify/ shows the result).
"""
import base64
import csv
import hashlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone

FORMAT = "queryous-seal/1"

# MERKLE_RULE: a timestamp (an element of "anchors") may carry "path", a list of steps {"side": "left" | "right",
# "hash": 64 hex digits}. Without a path (or with an empty one) the token stamps the entry's hash itself, as every
# timestamp has so far. With a path the token stamps the root of a Merkle tree over the heads of several chains:
#     node = SHA-256(0x00 + entry hash)                         the leaf
#     node = SHA-256(0x01 + sibling + node)   side "left"       the sibling stands left of the node
#     node = SHA-256(0x01 + node + sibling)   side "right"      the sibling stands right of the node
# for each step in order (hashes as bytes, not hex); the last node is the root. The prefixes 0x00 and 0x01 keep a leaf
# from passing for an inner node. Whoever builds such a tree (app/seal.py, once the stamps of many chains are bundled)
# uses exactly this rule, and the format stays queryous-seal/1: a checker of this version already reads both kinds.
MERKLE_MAX_STEPS = 64  # a tree of more than 2**64 chains does not exist; a longer path is a forged file

# The hosts of Askurious whose chain this file asks without --server (audit 2026-10-11, H1). The address in seal.json
# (field "url") comes from the very file being checked: whoever forges the file (cases removed, deletion records
# appended) could name a server of their own that serves the forged chain. So only these names count, over https and
# at the path of a chain (/verify/<code>/seal.json); any other installation is named with --server. The file runs on
# its own at third parties, so the list is written here: the researchers' address of every brand (deploy/docker,
# RESEARCHER_HOST and BRANDS, app/brand.py) and its participants' address, which forwards /verify/ to it.
# tests/exports/test_seal_verify_hosts.py keeps it in step with deploy/docker/deploy-docker.sh.
OWN_HOSTS = ("askurious.de", "ask.askurious.de", "polltergeist.de", "ask.polltergeist.de")

# SHA-256 of the root certificates a timestamp may lead to: the DFN-Verein Community Root CA 2022 (valid until 2042),
# root of the timestamp service of the DFN (http://zeitstempel.dfn.de)
TRUSTED_ROOTS = {
    "3cdc2c9e9e5a36cb5888fd1796cb912f846253b682c1b32057532033510c7bb6": "DFN-Verein Community Root CA 2022",
}
# SHA-256 of the CAs that may issue the certificate of the timestamp service, per root: under the DFN root only the
# DFN-Verein Community Issuing CA 2022 (valid until 2042), which issued "PN: Zeitstempel 2026" of the DFN. Every
# other CA under that root (path length 1 allows one more below the Issuing CA) cannot issue a timestamp certificate
# that holds here. A root without an entry here (one put into TRUSTED_ROOTS by hand) has no such pin.
TSA_ISSUERS = {
    "3cdc2c9e9e5a36cb5888fd1796cb912f846253b682c1b32057532033510c7bb6": {
        "bad041d62916b6a3809714791f86f17d5470cb3d6f8c7a87cbb6fbc560b8c7a2": "DFN-Verein Community Issuing CA 2022",
    },
}

SHA256_OID = "2.16.840.1.101.3.4.2.1"
HASHES = {SHA256_OID: "sha256", "2.16.840.1.101.3.4.2.2": "sha384", "2.16.840.1.101.3.4.2.3": "sha512"}
MESSAGE_DIGEST_OID = "1.2.840.113549.1.9.4"
TIMESTAMPING_OID = "1.3.6.1.5.5.7.3.8"
TSTINFO_OID = "1.2.840.113549.1.9.16.1.4"


class Invalid(Exception):
    """A timestamp or a chain that does not hold."""


# --- DER, as much as a timestamp needs --------------------------------------------------------------------------

def _tlv(data, pos):
    """(tag, start of the content, end, start of the element) of the DER element at pos."""
    if pos + 2 > len(data):
        raise Invalid("DER truncated")
    tag, length, head = data[pos], data[pos + 1], pos + 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or head + n > len(data):
            raise Invalid("DER length not supported")
        length = int.from_bytes(data[head:head + n], "big")
        head += n
    if head + length > len(data):
        raise Invalid("DER truncated")
    return tag, head, head + length, pos


def _children(data, start=None, end=None):
    """[(tag, content start, end, element start)] of the elements between start and end."""
    pos, end = (0 if start is None else start), (len(data) if end is None else end)
    out = []
    while pos < end:
        el = _tlv(data, pos)
        out.append(el)
        pos = el[2]
    return out


def _only(data, el):
    """The children of a constructed element."""
    return _children(data, el[1], el[2])


def _oid(raw):
    first = raw[0]
    parts, value = [str(min(first // 40, 2)), str(first - 40 * min(first // 40, 2))], 0
    for b in raw[1:]:
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(str(value))
            value = 0
    return ".".join(parts)


def _len(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _der(tag, content):
    return bytes([tag]) + _len(len(content)) + content


def _int(n):
    raw = n.to_bytes(n.bit_length() // 8 + 1, "big", signed=False)
    return _der(0x02, raw.lstrip(b"\x00") if len(raw) > 1 and raw[1] < 0x80 and raw[0] == 0 else raw)


def _oid_der(dotted):
    nums = [int(x) for x in dotted.split(".")]
    out = bytes([40 * nums[0] + nums[1]])
    for n in nums[2:]:
        chunk = [n & 0x7F]
        n >>= 7
        while n:
            chunk.append(0x80 | (n & 0x7F))
            n >>= 7
        out += bytes(reversed(chunk))
    return _der(0x06, out)


def request(digest, nonce):
    """A TimeStampReq (RFC 3161) for a SHA-256 value (32 bytes): version 1, the hash, a nonce, with the certificates."""
    if len(digest) != 32:
        raise ValueError("a SHA-256 value has 32 bytes")
    imprint = _der(0x30, _der(0x30, _oid_der(SHA256_OID) + b"\x05\x00") + _der(0x04, digest))
    return _der(0x30, _int(1) + imprint + _int(nonce) + b"\x01\x01\xff")


def _readable(fn):
    """Every error of reading a token (a field missing, a value cryptography does not take) as Invalid: a broken
    timestamp is a finding of the check, never a crash of it."""
    import functools

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Invalid:
            raise
        except Exception as exc:
            raise Invalid(f"timestamp cannot be read ({type(exc).__name__}: {exc})") from exc
    return wrapped


@_readable
def token_from_reply(reply):
    """The timestamp token of a TimeStampResp, or Invalid if the service did not grant it."""
    top = _tlv(reply, 0)
    parts = _only(reply, top)
    status = _only(reply, parts[0])
    code = int.from_bytes(reply[status[0][1]:status[0][2]], "big")
    if code not in (0, 1) or len(parts) < 2:  # granted, granted with modifications
        raise Invalid(f"timestamp refused (status {code})")
    return reply[parts[1][3]:parts[1][2]]


def _signed_data(token):
    content_info = _only(token, _tlv(token, 0))
    return _only(token, _only(token, content_info[1])[0])  # [0] EXPLICIT SignedData


@_readable
def info(token):
    """What a token says, unchecked: {"imprint", "hash", "time", "nonce", "serial", "tst" (the DER of TSTInfo)}."""
    sd = _signed_data(token)
    encap = _only(token, sd[2])
    if _oid(token[encap[0][1]:encap[0][2]]) != TSTINFO_OID:
        raise Invalid("not a timestamp token")
    octets = _only(token, encap[1])[0]
    tst = token[octets[1]:octets[2]]
    fields = _only(tst, _tlv(tst, 0))
    imprint = _only(tst, fields[2])
    alg = _oid(tst[_only(tst, imprint[0])[0][1]:_only(tst, imprint[0])[0][2]])
    raw_time = tst[fields[4][1]:fields[4][2]].decode("ascii")
    stamp = datetime.strptime(raw_time.rstrip("Z").split(".")[0], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    nonce = next((int.from_bytes(tst[f[1]:f[2]], "big") for f in fields[5:] if f[0] == 0x02), None)
    return {"imprint": tst[imprint[1][1]:imprint[1][2]], "hash": HASHES.get(alg, alg), "time": stamp, "nonce": nonce,
            "serial": int.from_bytes(tst[fields[3][1]:fields[3][2]], "big"), "tst": tst}


def _check_ca(cert, below):
    """A certificate that issued another one of the path: a CA (BasicConstraints critical with ca=True), allowed to
    sign certificates (KeyUsage keyCertSign), and its path length not exceeded by the below CAs under it. Without
    this, the holder of any end-entity certificate under the root could issue a "timestamp service" of their own."""
    from cryptography import x509

    try:
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        raise Invalid("a certificate of the path is not a CA") from None
    if not constraints.critical or not constraints.value.ca or not usage.key_cert_sign:
        raise Invalid("a certificate of the path is not a CA")
    if constraints.value.path_length is not None and below > constraints.value.path_length:
        raise Invalid("a certificate of the path exceeds its path length")


@_readable
def verify(token, digest, roots=None, issuers=None):
    """Check a token against the SHA-256 value it should stamp: the hash it names, the signature, and the certificates
    up to a trusted root, valid at the time stamped, every issuer a CA, the issuer of the signing certificate pinned
    (TSA_ISSUERS) for the roots that have a pin. Returns the time (UTC) or raises Invalid."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    roots = TRUSTED_ROOTS if roots is None else roots
    issuers = TSA_ISSUERS if issuers is None else issuers
    about = info(token)
    if about["hash"] != "sha256" or about["imprint"] != digest:
        raise Invalid("the timestamp stamps another value")
    sd = _signed_data(token)
    certs = [x509.load_der_x509_certificate(token[c[3]:c[2]]) for el in sd[3:] if el[0] == 0xA0
             for c in _only(token, el)]
    signer_info = _only(token, _only(token, sd[-1])[0])
    attrs = next((el for el in signer_info if el[0] == 0xA0), None)
    if attrs is None:
        raise Invalid("timestamp without signed attributes")
    alg = HASHES.get(_oid(token[_only(token, signer_info[2])[0][1]:_only(token, signer_info[2])[0][2]]))
    if alg is None:
        raise Invalid("hash of the signature not supported")
    algo = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}[alg]()
    signed = b"\x31" + token[attrs[3] + 1:attrs[2]]  # the attributes are signed as a SET
    wanted = hashlib.new(alg, about["tst"]).digest()
    seen = None
    for attr in _only(token, attrs):
        parts = _only(token, attr)
        if _oid(token[parts[0][1]:parts[0][2]]) == MESSAGE_DIGEST_OID:
            value = _only(token, parts[1])[0]
            seen = token[value[1]:value[2]]
    if seen != wanted:
        raise Invalid("the signed attributes do not belong to this timestamp")
    signature_el = next((el for el in signer_info[3:] if el[0] == 0x04), None)
    if signature_el is None:
        raise Invalid("timestamp without a signature")
    signature = token[signature_el[1]:signature_el[2]]
    signer = None
    for cert in certs:
        key = cert.public_key()
        try:
            if isinstance(key, rsa.RSAPublicKey):
                key.verify(signature, signed, padding.PKCS1v15(), algo)
            elif isinstance(key, ec.EllipticCurvePublicKey):
                key.verify(signature, signed, ec.ECDSA(algo))
            else:
                continue
            signer = cert
            break
        except InvalidSignature:
            continue
    if signer is None:
        raise Invalid("signature does not hold")
    try:
        usage = signer.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if TIMESTAMPING_OID not in {u.dotted_string for u in usage}:
            raise Invalid("the signing certificate is not for timestamps")
    except x509.ExtensionNotFound:
        raise Invalid("the signing certificate is not for timestamps") from None
    when, current, path = about["time"], signer, [signer]
    for depth in range(6):
        if not current.not_valid_before_utc <= when <= current.not_valid_after_utc:
            raise Invalid("a certificate was not valid at the time stamped")
        if depth:
            _check_ca(current, depth - 1)
        fingerprint = current.fingerprint(hashes.SHA256()).hex()
        if fingerprint in roots:
            pinned = issuers.get(fingerprint)
            if pinned is not None and (len(path) < 2 or path[1].fingerprint(hashes.SHA256()).hex() not in pinned):
                raise Invalid("the signing certificate is not issued by the CA of the timestamp service")
            return when
        issuer = None
        for cand in certs:
            if cand is not current and cand.subject == current.issuer:
                try:
                    current.verify_directly_issued_by(cand)
                    issuer = cand
                    break
                except (ValueError, TypeError, InvalidSignature):
                    continue
        if issuer is None:
            raise Invalid("the certificates do not lead to a trusted root")
        current = issuer
        path.append(issuer)
    raise Invalid("the certificates do not lead to a trusted root")


# --- the chain ---------------------------------------------------------------------------------------------------

def genesis(survey_uid):
    """The hash before the first entry of a survey's chain."""
    return hashlib.sha256(f"queryous-seal|{survey_uid}".encode()).hexdigest()


def link(prev, entry):
    """The hash of an entry: SHA-256 of the one before and of the entry's fields, separated by |."""
    fields = [prev, str(entry["seq"]), entry["kind"], str(entry.get("ref") or ""), entry.get("status") or "",
              entry.get("content") or "", entry.get("reason") or "", entry["at"]]
    return hashlib.sha256("|".join(fields).encode()).hexdigest()


SKEW = timedelta(minutes=10)  # clocks may differ this much; an entry time later than now plus this does not hold


def _written_from(at):
    """The earliest moment an entry can have been written, from its field at: the time itself, or the start of the
    day for an entry that holds the day only; None if unreadable."""
    try:
        if len(at) == 10:
            return datetime.strptime(at, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.strptime(at.rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def merkle_root(digest, path):
    """The hash a timestamp stamps (MERKLE_RULE): `digest` itself (bytes) without a path, else the root its path
    leads to. Raises Invalid on a path that is not a list of steps as the rule says."""
    if not path:
        return digest
    if not isinstance(path, list):
        raise Invalid("its Merkle path is not a list")
    if len(path) > MERKLE_MAX_STEPS:
        raise Invalid(f"its Merkle path has more than {MERKLE_MAX_STEPS} steps")
    node = hashlib.sha256(b"\x00" + digest).digest()
    for n, step in enumerate(path, start=1):
        side = step.get("side") if isinstance(step, dict) else None
        sibling = step.get("hash") if isinstance(step, dict) else None
        try:
            sibling = bytes.fromhex(sibling) if isinstance(sibling, str) and len(sibling) == 64 else None
        except ValueError:
            sibling = None
        if side not in ("left", "right") or sibling is None:
            raise Invalid(f"step {n} of its Merkle path is not a side and a hash")
        node = hashlib.sha256(b"\x01" + (sibling + node if side == "left" else node + sibling)).digest()
    return node


def _stamp_key(a):
    """What a checked timestamp is kept under (check_chain, verified): the hash it stamps, its Merkle path, its token
    and the roots and CAs trusted, so that a change of those checks it anew."""
    token = a.get("token")
    path = hashlib.sha256(json.dumps(a.get("path"), sort_keys=True, default=str).encode()).hexdigest()
    return (a.get("digest"), path, hashlib.sha256(str(token).encode()).hexdigest(),
            tuple(sorted(TRUSTED_ROOTS)), tuple(sorted((k, tuple(sorted(v))) for k, v in TSA_ISSUERS.items())))


def check_chain(chain, now=None, verified=None):
    """Problems of a chain (a list of texts; empty = whole): links, numbering, times in the future, timestamps. Also
    what the chain holds: {"problems", "entries", "cases", "gone", "anchors", "last_anchor"}. verified: a dict that
    keeps the result of each timestamp checked (the platform keeps one per process, so that a growing chain does not
    check every stamp again; audit 2026-10-10, S5)."""
    problems, entries = [], chain.get("entries") or []
    prev = genesis(chain.get("survey", ""))
    by_seq = {}
    latest = (now or datetime.now(timezone.utc)) + SKEW
    for n, e in enumerate(entries, start=1):
        if e.get("seq") != n:
            problems.append(f"entry {n}: number {e.get('seq')} out of order")
        if e.get("prev") != prev:
            problems.append(f"entry {n}: does not follow the entry before")
        try:
            holds = link(prev, e) == e.get("digest")
        except Exception:  # a field missing or of another type: a finding, not a crash
            holds = False
        if not holds:
            problems.append(f"entry {n}: hash does not match its content")
        written = _written_from(e.get("at"))
        if written is not None and written > latest:
            problems.append(f"entry {n}: its time lies in the future")
        prev = e.get("digest") or ""
        by_seq[e.get("seq")] = e
    cases, gone = state(entries)
    last = None
    for a in chain.get("anchors") or []:
        e = by_seq.get(a.get("seq"))
        if e is None or e.get("digest") != a.get("digest"):
            problems.append(f"timestamp of entry {a.get('seq')}: not a hash of this chain")
            continue
        key = _stamp_key(a) if verified is not None else None
        if key is not None and key in verified:
            when = verified[key]
        else:
            try:
                stamped = merkle_root(bytes.fromhex(a["digest"]), a.get("path"))
                when = verify(base64.b64decode(a["token"]), stamped)
            except Exception as exc:  # Invalid, and whatever a changed file brings (no token, not base64)
                when = Invalid(str(exc))
            if key is not None:
                verified[key] = when
        if isinstance(when, Exception):
            problems.append(f"timestamp of entry {a.get('seq')}: {when}")
            continue
        if last is None or a["seq"] >= last[0]:
            last = (a["seq"], when)
    return {"problems": problems, "entries": len(entries), "cases": cases, "gone": gone,
            "anchors": len(chain.get("anchors") or []), "last_anchor": last}


def state(entries):
    """({case number: (status, content) as last recorded}, {case numbers deleted})."""
    cases, gone = {}, set()
    for e in entries:
        kind = e.get("kind")
        if kind == "sealed":
            cases[e.get("seq")] = (e.get("status"), e.get("content"))
        elif kind == "amended" and e.get("ref") in cases:
            cases[e["ref"]] = (e.get("status"), e.get("content"))
        elif kind == "deleted":
            gone.add(e.get("ref"))
        elif kind in ("purged", "closed"):
            gone |= set(cases)
    return cases, gone


ENDED = ("completed", "screenout", "quotafull", "excluded")  # a real participation with this status has a case number


def check_rows(chain, rows):
    """Compare a data file (dicts with seal_seq and seal_hash) with a chain: {"missing": {case: status} without a
    deletion, "deleted": cases the chain deleted although the file holds them, "unknown": numbers the chain does not
    know, "changed": cases whose seal_hash is not the last one recorded, "duplicate": case numbers in more than one
    row, "unsealed": rows (numbered from 1 after the header) of a real participation that ended but have no case
    number, "nohash": cases whose seal_hash is empty, "rows": rows with a case number, "columns": columns the check
    needs that the file lacks (seal_seq, status), "without": {"source/status": count} of the rows without a case
    number}. Rows of test runs and dummy data (column source) and unfinished participations have no case number by
    design; the report names how many there are, since the chain does not cover them. A real row without a status
    counts as unsealed (audit 2026-10-09, K3: invented rows without seal_seq and status passed)."""
    cases, gone = state(chain.get("entries") or [])
    seen = set()
    out = {"missing": {}, "deleted": [], "unknown": [], "changed": [], "duplicate": [], "unsealed": [], "nohash": [],
           "rows": 0, "columns": [], "without": {}}
    if rows:
        out["columns"] = [c for c in ("seal_seq", "status") if c not in rows[0]]
    for number, row in enumerate(rows, start=1):
        raw = (row.get("seal_seq") or "").strip()
        if not raw:
            source = (row.get("source") or "").strip() or "real"
            status = (row.get("status") or "").strip()
            key = f"{source}/{status or '?'}"
            out["without"][key] = out["without"].get(key, 0) + 1
            if source == "real" and (status in ENDED or not status):
                out["unsealed"].append(number)
            continue
        out["rows"] += 1
        try:
            n = int(float(raw))
        except ValueError:
            out["unknown"].append(raw)
            continue
        if n in seen:
            out["duplicate"].append(n)
            continue
        seen.add(n)
        given = (row.get("seal_hash") or "").strip()
        if n in gone:
            out["deleted"].append(n)
        elif n not in cases:
            out["unknown"].append(n)
        elif not given:
            out["nohash"].append(n)
        elif given != cases[n][1]:
            out["changed"].append(n)
    out["missing"] = {n: status for n, (status, _) in sorted(cases.items()) if n not in seen and n not in gone}
    return out


def read_rows(path):
    """The rows of a data file of Askurious (comma, semicolon or tab, UTF-8 with or without BOM)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    first = text.split("\n", 1)[0]
    sep = max((";", ",", "\t"), key=first.count)
    return list(csv.DictReader(io.StringIO(text), delimiter=sep))


STALE_HOURS = 2  # the chain is stamped every hour: a tail unstamped for longer is a sign of a rewritten chain
PARTIAL = "RESULT: holds only up to entry"


def _written_by(at):
    """The latest moment an entry can have been written, from its field at: the time itself, or the end of the day for
    an entry that holds the day only (a case's entry, app/seal.py); None if unreadable."""
    start = _written_from(at)
    if start is None:
        return None
    return start + timedelta(days=1) if len(at) == 10 else start


def stale_tail(chain, stamped, now=None):
    """(entry number, time) of the first entry after the stamped one (number stamped) that claims a time in the future
    (an end appended by hand, audit 2026-10-10, S2), else of the first that existed more than STALE_HOURS ago by its
    time, or None. The entries after it existed no earlier than it, so the tail has been waiting for a stamp at least
    that long."""
    now = now or datetime.now(timezone.utc)
    tail = [e for e in chain.get("entries") or [] if isinstance(e.get("seq"), int) and e["seq"] > stamped]
    for e in tail:
        start = _written_from(e.get("at"))
        if start is not None and start > now + SKEW:
            return e["seq"], start
    times = [t for t in (_written_by(e.get("at")) for e in tail) if t is not None]
    if not tail or not times or now - min(times) <= timedelta(hours=STALE_HOURS):
        return None
    return tail[0]["seq"], min(times)


def against_server(chain, server):
    """Problems of a chain (from a file) against the chain the platform serves for it (a list of texts; empty = every
    entry of the file is on the server, unchanged). Entries the server has beyond the file are fine: the chain grew."""
    if not isinstance(server, dict) or server.get("format") != FORMAT:
        return ["the chain on the server could not be read"]
    if server.get("survey") != chain.get("survey"):
        return ["the chain on the server belongs to another survey"]
    by_seq = {e.get("seq"): e.get("digest") for e in server.get("entries") or []}
    absent, other = [], []
    for e in chain.get("entries") or []:
        if e.get("seq") not in by_seq:
            absent.append(e.get("seq"))
        elif by_seq[e.get("seq")] != e.get("digest"):
            other.append(e.get("seq"))
    out = []
    if absent:
        out.append("entries the chain on the server does not have (appended to the file?): "
                   + ", ".join(map(str, absent[:30])) + (" …" if len(absent) > 30 else ""))
    if other:
        out.append("entries that differ from the chain on the server: " + ", ".join(map(str, other[:30]))
                   + (" …" if len(other) > 30 else ""))
    return out


def _case_number(row):
    raw = (row.get("seal_seq") or "").strip()
    try:
        return int(float(raw)) if raw else None
    except ValueError:
        return None


COMPLETED_ONLY = "RESULT: holds for the cases in the file"
VALUES_NOTE = ("What this shows: which cases the file holds and which were deleted (completeness and origin of the "
               "cases), not the answer values: the hash of a case uses a salt that is not exported, so the values in "
               "a row cannot be recomputed into it.")


def report(chain, rows=None, now=None, completed=False, server=None):
    """The check as readable lines and whether everything holds. server: the chain the platform serves (fetched by
    main), or None (offline). Entries after the last timestamp count only if the server holds them unchanged; offline,
    the chain and the data file are checked up to the stamped state and what holds ends in PARTIAL, not in "holds"
    (main returns 3): anyone can append entries to a file, such as deletion records of cases removed from the data
    (audit 2026-10-10, S2). completed: the data file holds completed participations only (--completed); the other
    cases of the chain are not missed, and what holds ends in COMPLETED_ONLY."""
    result = check_chain(chain, now=now)
    lines = [f"Survey {chain.get('title') or ''} ({chain.get('survey')}), chain of {result['entries']} entries, "
             f"{len(result['cases'])} cases, {len(result['gone'])} deleted."]
    ok, partial = not result["problems"], None
    lines += [f"PROBLEM: {p}" for p in result["problems"]]
    trusted = None  # the last entry the data check relies on, if not all
    if server is not None:
        found = against_server(chain, server)
        if found:
            ok = False
            lines += [f"PROBLEM: {p}" for p in found]
    if result["last_anchor"]:
        seq, when = result["last_anchor"]
        lines.append(f"Timestamps: {result['anchors']}; entries 1 to {seq} existed no later than "
                     f"{when:%Y-%m-%d %H:%M:%S} UTC.")
        if seq < result["entries"]:
            if server is not None:
                lines.append(f"Entries {seq + 1} to {result['entries']} are not stamped yet; they were compared with "
                             "the chain on the server.")
            else:
                trusted, partial = seq, (seq, when)
                lines.append(f"NOT STAMPED: entries {seq + 1} to {result['entries']}, and the chain on the server was "
                             f"not compared with them: checked only up to the stamped state (entry {seq}).")
                late = stale_tail(chain, seq, now)
                if late is not None:
                    lines.append(f"Entry {late[0]} gives {late[1]:%Y-%m-%d %H:%M} UTC, more than {STALE_HOURS} hours "
                                 "ago or in the future, without a stamp (the chain is stamped every hour): what "
                                 f"follows entry {seq} could have been rebuilt.")
    else:
        lines.append("No timestamp that holds: the chain shows no removal inside it, but could have been rebuilt as a whole.")
        ok = False
    if rows is not None:
        whole = chain
        if trusted is not None:  # only the stamped part: deletion records appended after it are not relied on
            whole = {**chain, "entries": [e for e in chain.get("entries") or []
                                          if isinstance(e.get("seq"), int) and e["seq"] <= trusted]}
            beyond = [r for r in rows if (_case_number(r) or 0) > trusted]
            rows = [r for r in rows if (_case_number(r) or 0) <= trusted]
            if beyond:
                lines.append(f"Rows with a case number after entry {trusted}, not checked: {len(beyond)}.")
        rows_check = check_rows(whole, rows)
        lines.append(f"Data file: {rows_check['rows']} rows with a case number.")
        if rows_check["columns"]:
            ok = False
            lines.append("PROBLEM, the data file lacks the column(s) " + ", ".join(rows_check["columns"])
                         + ": rows without a case number cannot be told apart from invented ones.")
        if rows_check["without"]:
            lines.append(f"Rows without a case number, not covered by the chain: {sum(rows_check['without'].values())} "
                         "(source/status: " + ", ".join(f"{k} {n}" for k, n in sorted(rows_check["without"].items()))
                         + "). Only rows with a case number are covered.")
        left_out = {}
        if completed:  # the other cases are not in such a file by design: counted, not missed
            for n, status in list(rows_check["missing"].items()):
                if status != "completed":
                    left_out[status or "?"] = left_out.get(status or "?", 0) + 1
                    del rows_check["missing"][n]
            lines.append("Only completed participations checked (--completed): "
                         + (", ".join(f"{k} {n}" for k, n in sorted(left_out.items())) if left_out else "no")
                         + " cases of the chain that ended otherwise are left out by design and not checked.")
        if rows_check["missing"]:
            ok = False
            by_status = {}
            for n, status in rows_check["missing"].items():
                by_status.setdefault(status or "?", []).append(n)
            for status, nums in sorted(by_status.items()):
                lines.append(f"MISSING without deletion record ({status}): {len(nums)} cases: "
                             + ", ".join(map(str, nums[:30])) + (" …" if len(nums) > 30 else ""))
            if trusted is not None:
                later = sorted(set(rows_check["missing"]) & state(chain.get("entries") or [])[1])
                if later:
                    lines.append(f"Of these, {len(later)} have a deletion record only after entry {trusted}, which is "
                                 "not stamped: it counts only once the chain on the server confirms it (check online, "
                                 "or with --server).")
        for key, text in (("deleted", "deleted in the chain, still in the file"), ("unknown", "case numbers the chain does not know"),
                          ("changed", "seal_hash differs from the last one recorded"),
                          ("duplicate", "case numbers in more than one row"),
                          ("unsealed", "rows of a real participation that ended, without a case number"),
                          ("nohash", "cases without seal_hash")):
            if rows_check[key]:
                ok = False
                lines.append(f"PROBLEM, {text}: " + ", ".join(map(str, rows_check[key][:30])))
        lines.append(VALUES_NOTE)
    if ok and partial:
        lines.append(f"{PARTIAL} {partial[0]} (stamped {partial[1]:%Y-%m-%d %H:%M:%S} UTC): checked only up to the "
                     "stamped state, see above.")
        return lines, False
    if ok and completed and rows is not None:
        lines.append(f"{COMPLETED_ONLY}: every completed case of the chain is in it or has a deletion record.")
        return lines, ok
    lines.append("RESULT: holds." if ok else "RESULT: does not hold, see above.")
    return lines, ok


def fetch(url, timeout=20):
    """The chain the platform serves at url (its seal.json), or raises."""
    import urllib.request

    if not url.startswith(("https://", "http://")):
        raise ValueError("not a web address")
    req = urllib.request.Request(url, headers={"User-Agent": "seal_verify.py", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (http or https only, checked above)
        data = json.loads(resp.read(200_000_000).decode("utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("not a seal chain of Askurious")
    return data


def own_url(url):
    """True when url (the field "url" of a seal.json) is the address of a chain on one of Askurious's own hosts:
    https, a host of OWN_HOSTS (no port other than 443, no user name), the path /verify/<code>/seal.json, no query."""
    import re
    from urllib.parse import urlsplit

    if not isinstance(url, str) or len(url) > 300:
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "https" and (parts.hostname or "") in OWN_HOSTS and port in (None, 443)
            and "@" not in parts.netloc and not parts.query and not parts.fragment
            and re.fullmatch(r"/verify/[A-Za-z0-9-]{1,80}/seal\.json", parts.path) is not None)


def check_file(stamp_path, file_path):
    """A timestamp (.tsr, the reply of the service) against the file it should stamp."""
    with open(stamp_path, "rb") as f:
        reply = f.read()
    with open(file_path, "rb") as f:
        digest = hashlib.sha256(f.read()).digest()
    try:
        when = verify(token_from_reply(reply), digest)
    except (Invalid, ValueError, IndexError) as exc:
        print(f"RESULT: does not hold: {exc}")
        return 1
    print(f"SHA-256 {digest.hex()}")
    print(f"RESULT: holds; the file existed no later than {when:%Y-%m-%d %H:%M:%S} UTC.")
    return 0


def main(argv):
    args, completed, offline, server_url = [], False, False, None
    rest = list(argv[1:])
    while rest:
        a = rest.pop(0)
        if a == "--completed":
            completed = True
        elif a == "--offline":
            offline = True
        elif a == "--server" and rest:
            server_url = rest.pop(0)
        else:
            args.append(a)
    argv = [argv[0]] + args
    if len(argv) < 2:
        print(__doc__.split("\n\n")[1])
        return 2
    if argv[1].endswith(".tsr") and len(argv) > 2:
        return check_file(argv[1], argv[2])
    with open(argv[1], encoding="utf-8") as f:
        chain = json.load(f)
    if chain.get("format") != FORMAT:
        print(f"Not a seal chain of Askurious ({FORMAT}).")
        return 2
    server = None
    newest = max((e.get("seq") for e in chain.get("entries") or [] if isinstance(e.get("seq"), int)), default=0)
    stamped = max((a.get("seq") for a in chain.get("anchors") or [] if isinstance(a.get("seq"), int)), default=0)
    # the server is asked when named, or when the file has an unstamped end; the address in the file only when it is
    # one of Askurious's own (H1): the file being checked never names the server it is checked against
    named = chain.get("url")
    url = server_url or (named if own_url(named) else None)
    if not offline and not url and named and newest > stamped:
        from urllib.parse import urlsplit

        try:
            host = urlsplit(str(named)).hostname or "?"
        except ValueError:
            host = "?"
        print(f"The address in seal.json (host {host[:100]!r}) is not one of Askurious's own ({', '.join(OWN_HOSTS)}, "
              "https): the unstamped end is not compared with it. To compare it with that installation, name it "
              "yourself: --server URL.")
    if not offline and url and (server_url or newest > stamped):
        try:
            server = fetch(url)
            print(f"Compared with the chain on the server: {url}")
        except Exception as exc:  # offline, moved, refused: the check goes on up to the stamped state
            print(f"The chain on the server could not be loaded ({url}: {exc}); checked offline.")
    lines, ok = report(chain, read_rows(argv[2]) if len(argv) > 2 else None, completed=completed, server=server)
    print("\n".join(lines))
    return 0 if ok else 3 if lines[-1].startswith(PARTIAL) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
