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
        assert {f: got[f] for f in SEED_FIELDS} == {f: seed[f] for f in SEED_FIELDS}, seed["key"]
    assert {c["key"]: catalog.seed_en(c) for c in catalog.entries()} == frozen["seeds_en"]
    assert constructs.SEEDS_EN == frozen["seeds_en"]
    assert {s["key"]: {f: s[f] for f in SEED_FIELDS} for s in constructs.SEEDS} == {
        s["key"]: {f: s[f] for f in SEED_FIELDS} for s in frozen["seeds"]}


def test_licence_sets_are_as_before():
    frozen = _frozen()
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
