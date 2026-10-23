"""ACBC price limit (security and code audit 2026-10-13, TN-25): nan and inf were taken as numbers, and without a
known page any number was taken as a limit."""
from app.extensions import db
from app.items import acbc
from app.models import Item
from tests.analysis.test_acbc import evaluate, setup


def test_number_takes_no_nan_or_inf():
    assert acbc._number("12,5") == 12.5
    for text in ("nan", "inf", "-inf", "Infinity", "1e999"):
        assert acbc._number(text, None) is None, text


def test_a_limit_needs_a_page_that_offered_it(app, client, survey):
    setup(client, rules=True, price=True)
    meta = {"phase": "price", "after": 1}
    with app.app_context():
        item = db.session.get(Item, 2)
        for value in ("500", "nan", "inf"):
            assert not evaluate(item, {"i2_pc": value}, meta).ok, value
        assert evaluate(item, {"i2_pc": "none"}, meta).answers[2]["rules"] == {"p1": {"cap": None}}
