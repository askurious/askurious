"""The nudge ("Hinweis") of a multiple choice whose options are carried from an earlier question (audit data storage
and calculations 2026-10-21, A1): no tick counts as open also when some carried options were not shown."""
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.items.choice import MultipleChoice
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

NUDGE = "Diese Frage ist noch offen"


def _carry_survey(client):
    fill_consent(client)
    add(client, "multiple_choice")   # 2
    add(client, "page_break")        # 3
    add(client, "multiple_choice")   # 4 carried
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "geraete"), ("question", "Welche?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Smartphone"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Tablet"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Laptop")]))
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "abends"), ("question", "Welche abends?"), ("answer_mode", "nudge"),
        ("carry_item", "2"), ("carry_which", "chosen")]))


def test_nudge_with_carried_options_partly_hidden(app, client, survey):
    _carry_survey(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        url = "/s/" + db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data=MultiDict([("nav", "next"), ("i2", "1"), ("i2", "2")]))   # option 3 is not carried
    html = p.post(url, data={"nav": "next"}).get_data(as_text=True)
    assert NUDGE in html
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.status == Response.ACTIVE


def test_is_missing_reads_the_ticks():
    kind = MultipleChoice()
    assert kind.is_missing(None, {"v": [], "t": {}, "h": ["3"]})
    assert kind.is_missing(None, [])
    assert kind.is_missing(None, None)
    assert not kind.is_missing(None, {"v": ["1"], "t": {}, "h": ["3"]})
    assert not kind.is_missing(None, ["1"])
