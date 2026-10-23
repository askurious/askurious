"""Code review 2026-10-15, F8: an element of "Positionierung" is never placed and "kann ich nicht einschätzen" at
once; the tick wins over a position sent with it, as on the page."""
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item
from tests.analysis.test_position_association import setup_pos


def test_the_tick_wins_over_a_position_sent_with_it(app, client, survey):
    setup_pos(client, allow_skip=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        answer = item.kind.parse_answer(item, MultiDict([("i2_ki", "10,10"), ("i2_ak", "50,60"), ("i2_skip", "ak")]))
        assert answer == {"ki": [10.0, 10.0], "_skip": ["ak"]}
        values = item.kind.export_values(item, answer)
        assert values["tech_ak_x"] is None and values["tech_ak_skip"] == 1 and values["tech_ki_skip"] == 0
