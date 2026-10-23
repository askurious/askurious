"""Lone surrogates in answers (audit 2026-10-13, TN-20 and TN-21): JSON can write "\\ud800", no UTF-8 file can hold it.
One anonymous POST with such a text in a map marker's follow-up question made every data export of the survey fail
with a 500."""
import json

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response
from tests.items.test_map_marker import setup


def test_map_marker_text_is_stored_without_surrogates_and_control_characters(app, client, survey):
    """TN-20: base.load_json read with json.loads, so "\\ud800" and "\\u0000" went into the stored answer."""
    setup(client)
    raw = r'[{"lat": 50.77, "lon": 6.08, "q": {"a1": "x\ud800y\u0000z\u001b", "b2": 3}}]'
    with app.app_context():
        item = db.session.get(Item, 2)
        answer = item.kind.parse_answer(item, MultiDict({"i2": raw}))
        assert answer[0]["q"]["a1"] == "xyz"


def test_map_marker_text_question_refuses_a_list(app, client, survey):
    from app.items.base import AnswerError

    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, MultiDict({"i2": json.dumps([{"lat": 50.77, "lon": 6.08,
                                                                        "q": {"a1": ["x"], "b2": 3}}])}))


def test_json_of_requests_and_fields_drops_lone_surrogates():
    """TN-21: the diary app's offline entry reads request.get_json (_CleanJSON) and clean_deep kept surrogates; a valid
    pair stays one character."""
    from app.request_values import _CleanJSON, clean_text, safe_loads

    assert safe_loads(r'{"a\udc00": ["x\ud800", "😀"]}') == {"a": ["x", "\U0001F600"]}
    assert _CleanJSON(json).loads(r'{"form": {"i2": "a\udfffb"}}') == {"form": {"i2": "ab"}}
    assert clean_text("a\ud800b") == "ab"


def test_diary_offline_entry_stores_no_surrogate(app):
    """TN-21 end to end at the request: a JSON body with a lone surrogate arrives cleaned at the view."""
    from flask import request

    with app.test_request_context("/x", method="POST", data=r'{"form": {"i2": "a\ud800b"}}',
                                  content_type="application/json"):
        assert request.get_json()["form"]["i2"] == "ab"


def test_exports_survive_an_answer_stored_with_a_surrogate(app, client, survey):
    """TN-20: answers stored before the fix stay exportable; the surrogate is written as U+FFFD."""
    setup(client)
    with app.app_context():
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="1-abc",
                                answers={"2": [{"lat": 50.77, "lon": 6.08, "q": {"a1": "x\ud800y", "b2": 3}}]}))
        db.session.commit()
    for url in ("/surveys/1/export/data.csv", "/surveys/1/export/data.sav", "/surveys/1/export/data.rds",
                "/surveys/1/export/data.omv", "/surveys/1/export/spss.zip"):
        rv = client.get(url)
        assert rv.status_code == 200, url
    csv_text = client.get("/surveys/1/export/data.csv").data.decode("utf-8")
    assert "x�y" in csv_text


def test_long_format_cells_drop_surrogates():
    from app.export import _cell

    assert _cell("x\ud800y", False) == "x�y"
    assert _cell("=x\udc00", False) == "'=x�"


def test_web_service_text_values_drop_surrogates():
    """TN-20: a web service's JSON answer is stored as an answer too."""
    from app.webservice import _as

    assert _as("a\ud800b\u0000", "text") == "ab"
