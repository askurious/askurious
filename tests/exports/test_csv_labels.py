"""The data CSV with the answers as their labels (app/export.labelled_csv_file; user test 2026-10-27, point 12)."""
import csv
import io

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def _rows(client, **args):
    body = client.get("/surveys/1/export/data.csv", query_string=args).data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(body)))


def test_labels_instead_of_codes_and_the_chosen_options(app, client, survey):
    add(client, "likert")         # item 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "L"), ("question", "Wie sehr?"), ("points", "5"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", "L_1"), ("stmt_text", "Erste")]))
    add(client, "multiple_choice")  # item 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "M"), ("question", "Womit?"), ("option_id", "o1"), ("option_code", "1"), ("option_label", "Bus"),
        ("option_id", "o2"), ("option_code", "2"), ("option_label", "Bahn"), ("option_id", "o3"), ("option_code", "3"), ("option_label", "Rad")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.responses.append(Response(source="real", status=Response.COMPLETED,
                                    answers={"2": {"s1": 4}, "3": ["1", "2"]}))
        db.session.commit()
    codes = _rows(client)
    labels = _rows(client, answers="labels")
    assert codes[0]["L_1"] == "4" and "M" not in codes[0]
    assert labels[0]["L_1"] == "stimme eher zu"
    assert labels[0]["M"] == "Bus; Bahn" and labels[0]["M_1"] == "1" and labels[0]["M_3"] == "0"
    header = list(labels[0])
    assert header.index("M") == header.index("M_1") - 1
