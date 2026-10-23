"""Seal chain: one timestamp of the DFN per round for all chains, over a blinded Merkle tree (seal.anchor_round,
SEAL_MERKLE; Ralf, 2026-10-16, konzept-merkle-zeitstempel-2026-10-16.md)."""
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from app import seal, seal_verify
from app.extensions import db
from app.models import SealAnchor, Survey
from tests import tsa_fake
from tests.exports.test_seal import sealed_survey, tsa  # noqa: F401  (fixture)
from tests.privacy.test_erasure import take


def _heads(n):
    return [hashlib.sha256(str(i).encode()).hexdigest() for i in range(n)]


@pytest.mark.parametrize("n", [1, 2, 3, 5, 17])
def test_every_path_leads_to_the_root_and_all_paths_are_alike(n):
    heads = _heads(n)
    root, paths = seal.merkle_tree(heads)
    assert [seal_verify.merkle_root(bytes.fromhex(h), p) for h, p in zip(heads, paths)] == [root] * n
    assert {len(p) for p in paths} == {seal.MERKLE_DEPTH + 1}   # the size of the round does not show


def test_a_path_names_no_other_chain():
    """The heads of other chains are public (their seal.json): neither they nor their leaves stand in a path."""
    heads = _heads(4)
    _, paths = seal.merkle_tree(heads)
    known = set(heads) | {hashlib.sha256(b"\x00" + bytes.fromhex(h)).hexdigest() for h in heads}
    assert not known & {step["hash"] for p in paths for step in p}
    # a second tree over the same heads gives other paths and another root (fresh blinding)
    assert seal.merkle_tree(heads)[0] != seal.merkle_tree(heads)[0]


def _other_chain(app, uid, n=2):
    """A chain of n entries for a survey uid without a survey row (as for a deleted survey)."""
    with app.app_context():
        conn = db.session.connection()
        for _ in range(n):
            seal._append(conn, uid, "sealed", status="completed", digest_of="0" * 64)
        db.session.commit()


def _chain_of(uid):
    return seal.chain(SimpleNamespace(uid=uid, title="x"))


def test_one_call_stamps_every_chain_and_each_holds_with_its_path(app, client, survey, tsa):
    app.config["SEAL_MERKLE"] = True
    uid = sealed_survey(app, client)
    for answer in ("a", "b"):
        take(app.test_client(), uid, answer)
    _other_chain(app, "otherchain01")
    _other_chain(app, "otherchain02", 3)
    with app.app_context():
        mine = db.session.get(Survey, 1).uid
        before = len(tsa_fake.calls)
        assert seal.anchor_due() == 3 and len(tsa_fake.calls) == before + 1   # one call for three chains
        assert seal.due() == []
        rows = db.session.scalars(db.select(SealAnchor)).all()
        assert len({r.token for r in rows}) == 1 and all(len(r.path) == seal.MERKLE_DEPTH + 1 for r in rows)
        for chain_uid in (mine, "otherchain01", "otherchain02"):
            result = seal_verify.check_chain(_chain_of(chain_uid))
            assert result["problems"] == [] and result["last_anchor"] is not None
        # a path changed by a single bit does not hold
        data = _chain_of("otherchain01")
        step = data["anchors"][0]["path"][3]
        step["hash"] = ("1" if step["hash"][0] == "0" else "0") + step["hash"][1:]
        assert seal_verify.check_chain(data)["problems"]
    # the download carries the path, and the checker in it reads it
    r = client.get("/surveys/1/export/seal-chain.zip")
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        data = json.loads(z.read("seal.json"))
        assert "Merkle-Baums" in z.read("LIESMICH.txt").decode()
    assert data["anchors"][0]["path"] and seal_verify.check_chain(data)["problems"] == []


def test_a_round_waits_for_the_gap_and_the_old_way_stays_without_the_switch(app, client, survey, tsa):
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    _other_chain(app, "otherchain01")
    with app.app_context():
        before = len(tsa_fake.calls)
        assert seal.anchor_due() == 2 and len(tsa_fake.calls) == before + 2   # off: one call per chain, no path
        assert all(r.path is None for r in db.session.scalars(db.select(SealAnchor)))
    app.config["SEAL_MERKLE"] = True
    _other_chain(app, "otherchain01")
    with app.app_context():
        assert seal.anchor_round() == 1
        _other_chain(app, "otherchain02")
        assert seal.anchor_round() == 0                     # another round only after ROUND_GAP (any process)
        assert seal.anchor_round(force=True) == 1
        # an old stamp without a path and a new one with it hold side by side in one chain
        result = seal_verify.check_chain(_chain_of("otherchain01"))
        assert result["problems"] == [] and result["anchors"] == 2


def test_the_close_of_the_fieldwork_is_stamped_alone_and_at_once(app, client, survey, monkeypatch):
    app.config["SEAL_MERKLE"] = True
    now, rounds = [], []
    monkeypatch.setattr(seal, "anchor_now", lambda uids: now.append(sorted(uids)))
    monkeypatch.setattr(seal, "anchor_in_background", lambda: rounds.append(1))
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "eins")
    client.post("/surveys/1/close")
    with app.app_context():
        assert now == [[db.session.get(Survey, 1).uid]] and rounds == []


def test_a_round_that_fails_waits_for_the_next(app, client, survey, tsa, monkeypatch):
    import requests

    app.config["SEAL_MERKLE"] = True
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    monkeypatch.setattr(requests, "post", tsa_fake.down)
    with app.app_context():
        assert seal.anchor_due() == 0 and len(seal.due()) == 1
    monkeypatch.setattr(requests, "post", tsa_fake.post)
    with app.app_context():
        assert seal.anchor_due() == 1 and seal.due() == []
