"""The construct catalogue (app/construct_catalog.py, snapshot of askurious-constructs) gives exactly the seeds the
code had before (roundtrip): every construct's item template and its metadata in German and English, and the
licence sets derived from it. The comparison is tests/data/constructs_12002c4.json, the seeds of app/constructs.py
and app/ratswd.py at commit 12002c4, the specs of licensed scales built with the stand-in wording of
tests/data/instruments.json (never a licensed text)."""
import json
import logging
import os

from app import construct_catalog as catalog
from app import constructs, ratswd

FROZEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "constructs_12002c4.json")
STANDIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "instruments.json")
# what changed on purpose since 12002c4: (key, field) -> the new German text's start
CHANGED = {("gender", "notes"): "Antwortkategorien nach Spiel, Haimson & Lottridge (2019)"}
CHANGED_EN = {("gender", "notes"): "Answer categories after Spiel, Haimson & Lottridge (2019)"}
SEED_FIELDS = ("seed_version", "status", "name", "short", "description", "authors", "year", "citation", "doi",
               "scoring", "notes", "tags")


def _frozen():
    with open(FROZEN, encoding="utf-8") as f:
        return json.load(f)


def _standin():
    with open(STANDIN, encoding="utf-8") as f:
        return json.load(f)["instruments"]


def test_every_construct_gives_todays_spec():
    frozen, standin = _frozen(), _standin()
    assert sorted(c["key"] for c in catalog.entries()) == sorted(s["key"] for s in frozen["seeds"])
    for seed in frozen["seeds"]:
        c = catalog.entry(seed["key"])
        if catalog.licensed(c):
            assert c["wording"] == {}, seed["key"]  # never a licensed wording in the snapshot
            built = catalog.spec(seed["key"], standin, check=False)
        else:
            built = catalog.spec(seed["key"])
        assert built == seed["spec"], seed["key"]


def test_every_construct_gives_todays_metadata():
    frozen = _frozen()
    for seed in frozen["seeds"]:
        got = catalog.seed(catalog.entry(seed["key"]))
        for f in SEED_FIELDS:
            if (seed["key"], f) in CHANGED:
                assert got[f].startswith(CHANGED[(seed["key"], f)]), (seed["key"], f)
            else:
                assert got[f] == seed[f], (seed["key"], f)
    en = {c["key"]: catalog.seed_en(c) for c in catalog.entries()}
    assert en == constructs.SEEDS_EN and set(en) == set(frozen["seeds_en"])
    for key, fields in frozen["seeds_en"].items():
        assert set(en[key]) == set(fields), key
        for f, value in fields.items():
            if (key, f) in CHANGED_EN:
                assert en[key][f].startswith(CHANGED_EN[(key, f)]), (key, f)
            else:
                assert en[key][f] == value, (key, f)
    assert {s["key"] for s in constructs.SEEDS} == {s["key"] for s in frozen["seeds"]}


def test_licence_sets_are_as_before():
    """The gender question stays hidden from guests: its answer categories follow Spiel, Haimson & Lottridge (2019),
    licence "source-attributed" in the catalogue, not Askurious' own wording (Ralf, 2026-11-09)."""
    frozen = _frozen()
    assert catalog.entry("gender")["licence"]["wording"] == "source-attributed"
    assert "gender" not in constructs.OWN_WORDING and not constructs.wording_public("gender")
    assert sorted(constructs.OWN_WORDING) == frozen["own_wording"]
    assert sorted(constructs.ZIS) == frozen["zis"]
    assert sorted(constructs.PERMISSION_REQUIRED) == frozen["permission_required"]
    assert sorted(constructs.NOT_SHARED) == frozen["not_shared"]
    assert {k: sorted(v) for k, v in constructs.WORKING_LANGS.items()} == frozen["working_langs"]
    assert sorted(constructs.INSTRUMENTS) == frozen["instruments"]
    assert sorted(ratswd.KEYS) == sorted(frozen["ratswd_keys"]) and ratswd.NOT_POOLED == set(frozen["ratswd_keys"])
    assert constructs.REFERENCE == frozen["reference"]


def test_snapshot_names_release_commit_and_hashes():
    release, commit = catalog.release()
    assert release and commit and len(commit) == 40
    for c in catalog.manifest()["constructs"]:
        assert c["hash"]["structure"] == catalog.structure_hash(c), c["key"]
        for lang, info in c["languages"].items():
            if info["wording"] == "repo":  # the hash of the catalogue's tools/hash.py, computed here the same way
                assert c["hash"]["wording"][lang] == catalog.wording_hash(c["wording"][lang]), (c["key"], lang)
            else:
                assert info["sha256"].startswith("sha256:") and lang not in c["wording"], (c["key"], lang)


def test_wording_hash_normalises_like_the_catalogue():
    a = {"question": "Wie  finden Sie „das“ Video?\n", "items": {"X_1": "Café"}}
    b = {"for": "ask:x@1.0", "question": 'Wie finden Sie "das" Video?', "items": {"X_1": "Café"}}
    assert catalog.wording_hash(a) == catalog.wording_hash(b)
    assert catalog.wording_hash(a) != catalog.wording_hash({"question": "Wie fanden Sie das Video?"})


def test_a_differing_wording_leaves_the_scale_out(app, caplog):
    """The installation's wording counts only where its hash matches the documented one (INSTRUMENT_WORDING_CHECK);
    the stand-ins of the tests never match."""
    with app.app_context():
        app.config["INSTRUMENT_WORDING_CHECK"] = True
        constructs._WARNED.clear()
        try:
            with caplog.at_level(logging.WARNING):
                assert constructs._instrument_spec("asku") is None
                assert constructs._instrument_spec("asku") is None
            warned = [r for r in caplog.records if "asku" in r.getMessage() and "differs" in r.getMessage()]
            assert len(warned) == 1  # once per version of the file
            assert not constructs.instrument_available("asku") and constructs.instrument_available("kamb")
        finally:
            app.config["INSTRUMENT_WORDING_CHECK"] = False
        assert constructs._instrument_spec("asku")["texts"]["de"]["statements"]["s1"] == "Testaussage 1 von ASKU"


def test_a_matching_wording_is_used():
    standin = _standin()
    c = catalog.entry("asku")
    wordings = catalog.overlay_wordings(c, standin)
    documented = {lang: {**info, "sha256": catalog.wording_hash(wordings[lang])} for lang, info in c["languages"].items()}
    patched = {**c, "languages": documented}
    assert catalog.differing_languages(patched, wordings) == []
    assert catalog.differing_languages(c, wordings) == sorted(wordings)
    assert catalog.to_spec(patched, wordings)["texts"]["en"]["question"] == standin["asku"]["question"]["en"]


def test_an_entry_with_the_wrong_number_of_items_is_ignored():
    c = catalog.entry("asku")
    assert catalog.overlay_wordings(c, {"asku": {"question": {"de": "Q"}, "statements": [{"de": "a"}]}}) == {}
    assert catalog.spec("asku", {}, check=False) is None
    assert catalog.spec("gibtsnicht") is None


def _minisign(data, comment="timestamp:1 file:manifest.json", prehash=True):
    """A minisign key pair of its own (never the catalogue's) and the signature of `data` in minisign's format."""
    import base64
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    secret = Ed25519PrivateKey.generate()
    key_id = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    raw = secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public = base64.b64encode(b"Ed" + key_id + raw).decode()
    message = hashlib.blake2b(data, digest_size=64).digest() if prehash else data
    sig = secret.sign(message)
    glob = secret.sign(sig + comment.encode())
    text = (f"untrusted comment: test\n{base64.b64encode((b'ED' if prehash else b'Ed') + key_id + sig).decode()}\n"
            f"trusted comment: {comment}\n{base64.b64encode(glob).decode()}\n")
    return public, text


def _refused(call):
    try:
        call()
    except ValueError as exc:
        return str(exc)
    raise AssertionError("accepted")


def test_signature_of_a_release():
    data = b'{"format": 1}'
    for prehash in (True, False):
        public, sig = _minisign(data, prehash=prehash)
        assert catalog.verify_signature(data, sig, public) == "timestamp:1 file:manifest.json"
        assert "does not match" in _refused(lambda: catalog.verify_signature(data + b" ", sig, public))
        changed = sig.replace("timestamp:1", "timestamp:2")
        assert "does not match" in _refused(lambda: catalog.verify_signature(data, changed, public))
    assert "another key" in _refused(lambda: catalog.verify_signature(data, sig, catalog.PUBLIC_KEY))
    assert "unreadable" in _refused(lambda: catalog.verify_signature(data, "nonsense", public))


def test_update_tool_refuses_licensed_wording(tmp_path):
    from tools import update_constructs_snapshot as tool

    manifest = json.loads(json.dumps(catalog.manifest()))
    entry = next(c for c in manifest["constructs"] if c["key"] == "asku")
    entry["wording"] = {"de": {"question": "x"}}
    source, out = tmp_path / "m.json", tmp_path / "out.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        tool.main(["--manifest", str(source), "--out", str(out)])
    except SystemExit as exc:
        assert "asku: licensed construct with wording" in str(exc)
    else:
        raise AssertionError("taken")
    assert not out.exists()
    entry["wording"] = {}
    source.write_text(json.dumps(manifest), encoding="utf-8")
    assert tool.main(["--manifest", str(source), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == catalog.manifest()


def test_wordings_folder_counts_before_instruments_json(app, tmp_path, caplog):
    """instance_overlay/wordings/<key>/<lang>.json (items by their codes) wins over instruments.json for its
    constructs; a file that names other codes is left out; the start reports what is missing or differs. Stand-in
    texts only."""
    standin = _standin()["asku"]
    folder = tmp_path / "wordings" / "asku"
    folder.mkdir(parents=True)
    codes = [st["code"] for st in catalog.entry("asku")["structure"]["config"]["statements"]]
    de = {"for": "ask:asku@1.0", "question": "Ordnerfrage", "items": {c: f"Ordneraussage {n}" for n, c in enumerate(codes, 1)}}
    (folder / "de.json").write_text(json.dumps(de), encoding="utf-8")
    (folder / "en.json").write_text(json.dumps({"question": "Q", "items": {"ASKU_9": "x"}}), encoding="utf-8")
    with app.app_context():
        app.config["INSTRUMENT_WORDINGS_DIR"] = str(tmp_path / "wordings")
        constructs._FOLDER.clear()
        try:
            found = constructs.instrument_wordings()
            assert "_wordings" in found["asku"] and "statements" in found["bfi10"]  # folder for asku, the file for the rest
            spec = constructs._instrument_spec("asku")
            assert spec["texts"]["de"]["question"] == "Ordnerfrage" and set(spec["texts"]) == {"de"}  # en: wrong codes
            assert spec["texts"]["de"]["statements"]["s1"] == "Ordneraussage 1"
            assert spec["texts"]["de"]["labels"] == catalog.entry("asku")["structure"]["labels"]["de"]
            constructs._REPORTED.clear()
            with caplog.at_level(logging.INFO):
                state = constructs.report_wordings()
            assert ("asku", ["de"]) in state["differing"]  # the stand-in is not the documented wording
            assert any("differs" in r.getMessage() and "asku (de)" in r.getMessage() for r in caplog.records)
        finally:
            app.config["INSTRUMENT_WORDINGS_DIR"] = ""
            constructs._FOLDER.clear()
    assert standin["question"]["de"] != "Ordnerfrage"
