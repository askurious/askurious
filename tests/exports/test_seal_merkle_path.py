"""Seal chain: a timestamp that stamps the root of a Merkle tree over several chains (seal_verify.MERKLE_RULE).

Prepared before the stamps of many chains are bundled into one call of the DFN (Philipp, 2026-10-14): every copy of
the checker handed out from now on already reads such a timestamp, and one without a path holds as before."""
import base64
import hashlib

import pytest

from app import seal_verify
from tests import tsa_fake
from tests.exports.test_seal_checks import _chain


@pytest.fixture(autouse=True)
def trusted(monkeypatch):
    monkeypatch.setitem(seal_verify.TRUSTED_ROOTS, tsa_fake.ROOT, "Test Root")


def _leaf(h):
    return hashlib.sha256(b"\x00" + h).digest()


def _inner(left, right):
    return hashlib.sha256(b"\x01" + left + right).digest()


def _tree(heads):
    """Root and the path of heads[0] in a tree of four leaves: ((a, b), (c, d))."""
    a, b, c, d = (_leaf(bytes.fromhex(h)) for h in heads)
    root = _inner(_inner(a, b), _inner(c, d))
    path = [{"side": "right", "hash": b.hex()}, {"side": "right", "hash": _inner(c, d).hex()}]
    return root, path


def _stamped(path_of, root, seq=2):
    """The chain of three cases with a timestamp of entry seq over `root` (None: over the entry's hash itself)."""
    chain = _chain("abc", 3)
    head = chain["entries"][seq - 1]["digest"]
    chain["anchors"] = [{"seq": seq, "digest": head, "tsa": "x", "time": "", "path": path_of(head),
                         "token": base64.b64encode(tsa_fake.token(root or bytes.fromhex(head))).decode()}]
    return chain


def _others():
    return [hashlib.sha256(str(n).encode()).hexdigest() for n in range(3)]


def test_a_timestamp_over_a_merkle_root_holds_with_its_path():
    head = _chain("abc", 3)["entries"][1]["digest"]
    root, path = _tree([head] + _others())
    chain = _stamped(lambda _h: path, root)
    result = seal_verify.check_chain(chain)
    assert result["problems"] == []
    assert result["last_anchor"][0] == 2
    # a sibling on the left as well: the head as the third leaf of the tree
    a, b, c, d = (_leaf(bytes.fromhex(h)) for h in _others()[:2] + [head] + _others()[2:])
    root = _inner(_inner(a, b), _inner(c, d))
    path = [{"side": "right", "hash": d.hex()}, {"side": "left", "hash": _inner(a, b).hex()}]
    assert seal_verify.check_chain(_stamped(lambda _h: path, root))["problems"] == []


def test_a_wrong_sibling_or_side_does_not_hold():
    head = _chain("abc", 3)["entries"][1]["digest"]
    root, path = _tree([head] + _others())
    wrong = [dict(path[0], hash="0" * 64), path[1]]
    problems = seal_verify.check_chain(_stamped(lambda _h: wrong, root))["problems"]
    assert len(problems) == 1 and problems[0].startswith("timestamp of entry 2: ")
    swapped = [dict(path[0], side="left"), path[1]]
    assert seal_verify.check_chain(_stamped(lambda _h: swapped, root))["problems"]
    # the root itself stamped, but no path given: the token does not stamp the entry's hash
    assert seal_verify.check_chain(_stamped(lambda _h: None, root))["problems"]


@pytest.mark.parametrize("path", [
    "abc", {"side": "left"}, [{"side": "up", "hash": "0" * 64}], [{"side": "left", "hash": "zz" * 32}],
    [{"side": "left", "hash": "00"}], ["x"], [{"side": "left", "hash": "0" * 64}] * 65,
])
def test_a_malformed_path_is_a_problem_not_a_crash(path):
    head = _chain("abc", 3)["entries"][1]["digest"]
    root, _ = _tree([head] + _others())
    problems = seal_verify.check_chain(_stamped(lambda _h: path, root))["problems"]
    assert len(problems) == 1 and problems[0].startswith("timestamp of entry 2: ")


def test_without_a_path_a_timestamp_holds_as_before():
    for path in (None, []):
        chain = _stamped(lambda _h: path, None, seq=2)
        assert seal_verify.check_chain(chain)["problems"] == []
    del chain["anchors"][0]["path"]
    assert seal_verify.check_chain(chain)["problems"] == []


def test_the_path_is_part_of_what_a_checked_stamp_is_kept_under():
    head = _chain("abc", 3)["entries"][1]["digest"]
    root, path = _tree([head] + _others())
    right = _stamped(lambda _h: path, root)
    kept = {}
    assert seal_verify.check_chain(right, verified=kept)["problems"] == []
    right["anchors"][0]["path"] = [dict(path[0], hash="0" * 64), path[1]]  # same digest and token, another path
    assert seal_verify.check_chain(right, verified=kept)["problems"]  # not the result kept for the right path
