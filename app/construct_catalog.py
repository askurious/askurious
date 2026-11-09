"""The construct catalogue: the seed constructs of the toolbox, read from a snapshot of the manifest of
askurious-constructs (https://github.com/askurious/askurious-constructs) in app/data/constructs/manifest.json.

The manifest carries per construct its metadata in German and English, its licence, tags, the structure in the
element format (type, code, config with item codes) and hashes. Where the licence allows passing the wording on
(own constructs, RatSWD standard questions) it carries the wording too; licensed scales carry only the structure
and a SHA-256 hash of their wording per language. Their wording stays with the installation
(INSTRUMENT_WORDINGS, instance_overlay/instruments.json), and it is used only where its hash matches the documented
one: a scale whose wording differs from the documented version is left out of the toolbox (with a warning in the
log). INSTRUMENT_WORDING_CHECK = False switches the check off (the tests' stand-in wordings).

tools/update_constructs_snapshot.py renews the snapshot from a checkout or a release of the catalogue; the manifest
names its release and commit. An installation can take newer signed releases itself (app/construct_sync.py, nightly
or under Administration > Konstrukte > Katalog): the manifest it took last is the active one (table construct_sync),
else the snapshot. Everything here reads the active one (active()); the derived values of app/constructs.py follow it
(live_set, live_map, live_list). The hashes follow tools/hash.py of the catalogue: canonical JSON (keys sorted, no
spaces, UTF-8), the wording normalised first (NFC, plain quotation marks, white space as single spaces).
"""
import collections.abc
import copy
import functools
import hashlib
import json
import os
import time
import unicodedata

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "constructs", "manifest.json")
# the public minisign key of the catalogue's releases (minisign.pub in askurious-constructs, key ID 8F880D565AD04DE6)
PUBLIC_KEY = "RWTmTdBaVg2Ij3LgzDuGpbt4rr61X/xFBDIwGjVM/N1qXVfJUOMA3FMG"
RELEASES = "https://github.com/askurious/askurious-constructs/releases"
TEXT_FIELDS = ("name", "short", "description", "scoring", "notes")
_ITEM_TEXT_FIELDS = ("prefix", "suffix", "validation_message")
_QUOTES = str.maketrans({
    "„": '"', "“": '"', "”": '"', "‟": '"', "«": '"', "»": '"', "″": '"',
    "‚": "'", "‘": "'", "’": "'", "‛": "'", "‹": "'", "›": "'", "′": "'",
})


@functools.lru_cache(maxsize=None)
def manifest(path=PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


ACTIVE_SECONDS = 30  # how long a process keeps the active manifest before it looks for a newer one
_active = {"checked": None, "id": None, "data": None}


def active():
    """The active manifest: the one taken last on this installation (construct_sync, status "applied"), else the
    snapshot in the code. Outside an app context, or before the table exists, the snapshot."""
    try:
        from flask import has_app_context
    except ImportError:
        return manifest()
    if not has_app_context():
        return manifest()
    now = time.monotonic()
    if _active["checked"] is not None and now - _active["checked"] < ACTIVE_SECONDS:
        return _active["data"]
    data, row = manifest(), None
    try:
        from sqlalchemy import text

        from app.extensions import db

        with db.engine.connect() as con:  # a connection of its own: a failure never spoils the request's transaction
            row = con.execute(text("SELECT id FROM construct_sync WHERE status = 'applied' ORDER BY id DESC LIMIT 1")
                              ).scalar()
            if row is not None and row != _active["id"]:
                stored = con.execute(text("SELECT manifest FROM construct_sync WHERE id = :i"), {"i": row}).scalar()
                stored = json.loads(stored) if isinstance(stored, str) else stored
                if isinstance(stored, dict) and isinstance(stored.get("constructs"), list):
                    data = stored
                else:
                    row = None
            elif row is not None:
                data = _active["data"]
    except Exception:  # no table yet (before migration 0118) or no database: the snapshot
        row = None
    _active.update(checked=now, id=row, data=data)
    return data


def invalidate():
    """Look for the active manifest anew on the next call (after a sync in this process)."""
    _active.update(checked=None, id=None, data=None)


def release(m=None):
    """(release, commit) of the active manifest (or of `m`), e.g. ("2026.11.0", "6347d72…")."""
    data = m if m is not None else active()
    return data.get("release"), data.get("commit")


def entries(m=None):
    """The constructs of the active catalogue (or of `m`) that may be inserted (not withdrawn), in its order."""
    return [c for c in (m if m is not None else active())["constructs"] if c.get("status") != "retired"]


def retired(m=None):
    return [c for c in (m if m is not None else active())["constructs"] if c.get("status") == "retired"]


def entry(key, m=None):
    return next((c for c in (m if m is not None else active())["constructs"] if c["key"] == key), None)


# ---- values derived from the active manifest, recomputed when it changes ----

class _Live:
    def __init__(self, build):
        self._build, self._for, self._value = build, None, None

    def get(self):
        m = active()
        if m is not self._for:
            self._value, self._for = self._build(m), m
        return self._value


class live_set(collections.abc.Set):
    """A set built by `build(manifest)` from the active manifest; `in`, iteration and len as a frozenset."""

    def __init__(self, build):
        self._live = _Live(lambda m: frozenset(build(m)))

    def __contains__(self, value):
        return value in self._live.get()

    def __iter__(self):
        return iter(self._live.get())

    def __len__(self):
        return len(self._live.get())

    def __repr__(self):
        return f"live_set({sorted(self._live.get())!r})"


class live_map(collections.abc.Mapping):
    """A mapping built by `build(manifest)` from the active manifest."""

    def __init__(self, build):
        self._live = _Live(build)

    def __getitem__(self, key):
        return self._live.get()[key]

    def __iter__(self):
        return iter(self._live.get())

    def __len__(self):
        return len(self._live.get())


class live_list(collections.abc.Sequence):
    """A list built by `build(manifest)` from the active manifest."""

    def __init__(self, build):
        self._live = _Live(build)

    def __getitem__(self, index):
        return self._live.get()[index]

    def __len__(self):
        return len(self._live.get())


def major(c):
    """The major number of a construct's version: the seed_version of the toolbox."""
    return int(str(c["version"]).split(".", 1)[0])


def licensed(c):
    """True for a construct whose wording is not in the catalogue (it comes from the installation)."""
    return any(info.get("wording") == "overlay" for info in (c.get("languages") or {}).values())


# ---- hashes (as tools/hash.py of the catalogue) ----

def _normalise(obj):
    if isinstance(obj, str):
        return " ".join(unicodedata.normalize("NFC", obj).translate(_QUOTES).split())
    if isinstance(obj, list):
        return [_normalise(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _normalise(v) for k, v in obj.items()}
    return obj


def _sha256(obj):
    data = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def wording_hash(wording):
    return _sha256(_normalise({k: v for k, v in wording.items() if k != "for"}))


def structure_hash(c):
    return _sha256(c["structure"])


# ---- metadata as seeds ----

def authors_text(c):
    if c.get("authors_text"):
        return c["authors_text"]
    names = [a.get("family") or a.get("name") for a in c.get("authors") or []]
    if not names:
        return ""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " & " + names[-1]


def tags(c):
    """["facet:name", …] as the toolbox stores them, the facet "bezug" included."""
    out = []
    for facet, names in (c.get("tags") or {}).items():
        out += [f"{facet}:{n}" for n in (names if isinstance(names, list) else [names])]
    return out


def seed(c):
    """A construct as a seed of the toolbox (German fields; the English ones in seed_en). "spec" is a function that
    gives the item template, or None for a licensed scale without a fitting wording in the installation."""
    source = (c.get("sources") or [{}])[0]
    out = {"key": c["key"], "seed_version": major(c), "version": c["version"], "status": c["status"],
           "authors": authors_text(c),
           "year": c.get("year"), "citation": source.get("citation", ""), "doi": source.get("doi", ""),
           "tags": tags(c), "spec": functools.partial(spec, c["key"])}
    for field in TEXT_FIELDS:
        out[field] = c[field]["de"]
    return out


def seed_en(c):
    return {field: c[field]["en"] for field in TEXT_FIELDS if c[field].get("en")}


# ---- item templates ----

def to_spec(c, wordings):
    """The item template (type, code, config, texts) of construct `c` with {language: wording}."""
    s = c["structure"]
    config = copy.deepcopy(s.get("config") or {})
    labels = s.get("labels") or {}
    order = list((c.get("languages") or {}).keys())
    texts = {}
    for lang in [lang for lang in order if lang in wordings] + [lang for lang in wordings if lang not in order]:
        w = wordings[lang]
        if s["type"] == "likert" and labels and lang not in labels and "labels" not in w:
            continue  # a scale of others offers only the languages its response labels are documented in
        t = {}
        if "question" in w:
            t["question"] = w["question"]
        if "labels" in w:
            t["labels"] = list(w["labels"])
        elif lang in labels:
            t["labels"] = list(labels[lang])
        items = w.get("items") or {}
        if config.get("statements"):
            t["statements"] = {st["id"]: items[st["code"]] for st in config["statements"]}
        if config.get("pairs"):
            t["pairs"] = {p["id"]: {"left": items[p["code"]]["left"], "right": items[p["code"]]["right"]}
                          for p in config["pairs"]}
        if config.get("options"):
            options = w.get("options") or {}
            t["options"] = {o["id"]: options[o["code"]] for o in config["options"]}
        for field in _ITEM_TEXT_FIELDS:
            if field in w:
                t[field] = w[field]
        texts[lang] = t
    return {"type": s["type"], "code": s["code"], "config": config, "texts": texts}


def overlay_wordings(c, instruments):
    """{language: wording} of licensed construct `c` from the installation's wordings
    ({key: {"question": {lang: text}, "statements": [{lang: text}, …]}}, instruments.json), items by their codes; {}
    if there is no fitting entry."""
    found = instruments.get(c["key"]) if isinstance(instruments, dict) else None
    if not isinstance(found, dict):
        return {}
    question, statements = found.get("question"), found.get("statements")
    rows = (c["structure"].get("config") or {}).get("statements") or []
    if not isinstance(question, dict) or not isinstance(statements, list) or len(statements) != len(rows):
        return {}
    out = {}
    for lang, text in question.items():
        if isinstance(text, str) and all(isinstance(s, dict) and isinstance(s.get(lang), str) for s in statements):
            out[lang] = {"question": text, "items": {r["code"]: s[lang] for r, s in zip(rows, statements)}}
    return out


def differing_languages(c, wordings):
    """The languages of `wordings` whose hash differs from the documented one (or that the catalogue does not
    document)."""
    langs = c.get("languages") or {}
    return sorted(lang for lang, w in wordings.items()
                  if (langs.get(lang) or {}).get("sha256") != wording_hash(w))


def open_spec(c):
    """The item template of a construct whose wording is in the catalogue."""
    return to_spec(c, c.get("wording") or {})


def spec(key, instruments=None, check=True, log=None):
    """The item template of construct `key`; a licensed scale takes its wording from `instruments` (the
    installation's) and is None without a fitting one, or with `check` when a hash differs from the documented one."""
    c = entry(key)
    if c is None:
        return None
    if not licensed(c):
        return open_spec(c)
    wordings = overlay_wordings(c, instruments or {})
    if not wordings:
        return None
    if check:
        differing = differing_languages(c, wordings)
        if differing:
            if log is not None:
                log.warning("construct %s: the wording of the installation (%s) differs from the documented version "
                            "%s of the catalogue; left out of the toolbox", key, ", ".join(differing), c["version"])
            return None
    built = to_spec(c, wordings)
    return built if built["texts"] else None


# ---- signature of a release (minisign, Ed25519) ----

def verify_signature(data, signature, public_key=None):
    """Check a minisign signature (the text of manifest.json.sig) of `data` (bytes) against `public_key` (the base64
    line of minisign.pub). Returns the trusted comment; raises ValueError if the signature does not hold."""
    import base64

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = base64.b64decode((public_key or PUBLIC_KEY).strip())
        lines = [line for line in signature.splitlines() if line.strip()]
        sig = base64.b64decode(lines[1].strip())
        trusted = lines[2].strip()
        global_sig = base64.b64decode(lines[3].strip())
    except (IndexError, ValueError) as exc:
        raise ValueError("unreadable signature or key") from exc
    if len(key) != 42 or key[:2] != b"Ed" or len(sig) != 74 or sig[:2] not in (b"Ed", b"ED"):
        raise ValueError("not a minisign Ed25519 key or signature")
    if sig[2:10] != key[2:10]:
        raise ValueError("signed with another key")
    if not trusted.startswith("trusted comment: "):
        raise ValueError("no trusted comment")
    comment = trusted[len("trusted comment: "):]
    message = hashlib.blake2b(data, digest_size=64).digest() if sig[:2] == b"ED" else data
    verifier = Ed25519PublicKey.from_public_bytes(key[10:42])
    try:
        verifier.verify(sig[10:74], message)
        verifier.verify(global_sig, sig[10:74] + comment.encode("utf-8"))
    except InvalidSignature as exc:
        raise ValueError("the signature does not match") from exc
    return comment


def check_manifest(data):
    """Problems of a manifest before it becomes the snapshot: its format, and that no licensed construct carries
    wording."""
    problems = []
    if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("constructs"), list):
        return ["not a construct catalogue manifest of format 1"]
    for c in data["constructs"]:
        key = c.get("key", "?") if isinstance(c, dict) else "?"
        if not isinstance(c, dict) or not {"key", "version", "structure", "languages", "licence"} <= set(c):
            problems.append(f"{key}: incomplete entry")
        elif licensed(c) and c.get("wording"):
            problems.append(f"{key}: licensed construct with wording")
    return problems
