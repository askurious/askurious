"""IAT answers: block number and error flag only as whole numbers (security and code audit 2026-10-13, TN-26): true
and 1.0 passed as block 1 or as an error."""
import json

from app.extensions import db
from app.models import Item
from tests.items.test_iat import _answer, _iat


def test_true_and_floats_are_no_block_and_no_error_flag(app, client, survey):
    _iat(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        good = _answer(app)
        assert item.kind._parse(item, json.dumps(good)) is not None
        first = next(n for n, t in enumerate(good["t"]) if t[0] == 1)
        for position, value in ((0, True), (0, 1.0), (3, True), (3, 0.0), (3, False)):
            broken = [list(t) for t in good["t"]]
            broken[first][position] = value
            assert item.kind._parse(item, json.dumps({**good, "t": broken})) is None, (position, value)
