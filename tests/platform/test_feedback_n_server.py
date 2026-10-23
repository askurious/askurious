"""The number of participants a feedback element's comparison rested on (<code>_n of the topic map, the topic
profile and the scale profile) is worked out by the server in a participation, not taken from the hidden field
(code review 2026-10-08, N7)."""
from flask import g
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, utcnow
from tests.conftest import add
from tests.platform.test_topic_feedback import build, people


def _parsed(app, item_id, sent, answers=None):
    with app.app_context(), app.test_request_context():
        item = db.session.get(Item, item_id)
        g.participation = Response(survey_id=1, source="real", answers=answers or {})
        return item.kind.parse_answer(item, {f"i{item_id}_n": sent}, "de", {})


def test_topic_map_and_profile_count_on_the_server(app, client, survey):
    build(app, client)
    people(app, [((2, 3, 5), (4, 4, 1))] * 5)
    for item_id in (4, 5):
        assert _parsed(app, item_id, "999999999") == {"n": 5}
        assert _parsed(app, item_id, "0") == {"n": 5}  # the page showed it; the number is the server's
        assert _parsed(app, item_id, "") is None       # nothing shown, nothing stored
    with app.app_context():
        item = db.session.get(Item, 4)
        assert item.kind.parse_answer(item, {"i4_n": "3"}) == {"n": 3}  # dummy runs outside a request: as sent


def test_too_few_for_a_comparison_is_zero_whatever_was_sent(app, client, survey):
    build(app, client)
    people(app, [((2, 3, 5), (4, 4, 1))] * 4)
    assert _parsed(app, 4, "4000") == {"n": 0} and _parsed(app, 5, "4000") == {"n": 0}


def test_scale_profile_counts_on_the_server(app, client, survey):
    add(client, "rating", code="r")                                                       # 2
    add(client, "page_break")                                                             # 3
    add(client, "scale_profile")                                                          # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([("code", "profil"), ("question", "Ihr Profil"),
                                                            ("sources", "2"), ("show_sample", "1")]))
    with app.app_context():
        for _ in range(6):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": 3}))
        db.session.commit()
    assert _parsed(app, 4, "123456", {"2": 4}) == {"n": 6}
