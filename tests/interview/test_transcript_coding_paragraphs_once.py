"""Interviews kodieren: the evaluations that name the place of many passages ("B03, Pos. 12, 00:14:32") split each
transcript into paragraphs once, not once per coding or per overlapping pair (app/transcript_coding_view.py,
Paragraphs and spotter). Before, the overview and categories.xlsx took minutes for 40 interviews with 1 500 codings
each (performance audit 2026-11-11). Guarded by counting the calls of paragraphs(), not by the clock; and the places
stay exactly those that spot() gives for a single passage."""
import re

import pytest

from app import transcript_coding_view as view
from app.extensions import db
from app.models import Response
from tests.interview.test_interviewer import add as add_interviewer
from tests.interview.test_interviewer import begin, open_link
from tests.interview.test_transcript_coding import _transcript
from tests.interview.test_transcript_coding_categories_xlsx import _sheets
from tests.interview.test_transcript_coding_review import _setup, _sub

PARAS = 12
PER_INTERVIEW = 15


def _text(n):
    """A transcript of PARAS paragraphs; every other one ends with a time mark."""
    return "\n\n".join(f"{'IB'[i % 2]}: Absatz {i + 1} von Interview {n}, mit etwas Text darin."
                       + (f" #00:{i:02d}:{(7 * i + n) % 60:02d}-0#" if i % 2 else "") for i in range(PARAS))


def _world(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    doubt = _sub(client, base, main, "Zweifel")
    rid2 = begin(open_link(app, code, add_interviewer(client, code, "Bert Zwei"))[0], code)
    rids = [rid, rid2]
    texts = {}
    for n, r in enumerate(rids, 1):
        texts[r] = _text(n)
        _transcript(app, r, texts[r])
        for j in range(PER_INTERVIEW):  # overlapping passages of both subcategories, spread over the paragraphs
            start = texts[r].index(f"Absatz {j % PARAS + 1} ")
            cat = (tired, doubt)[j % 2]
            res = client.post(f"{base}/i/{r}/codings", json={"start": start, "end": start + 40 + 3 * j, "cat": cat})
            assert res.json["ok"], res.json
    return base, rids, texts, tired, doubt


@pytest.fixture
def calls(monkeypatch):
    count = []
    real = view.paragraphs

    def counted(text):
        count.append(1)
        return real(text)

    monkeypatch.setattr(view, "paragraphs", counted)  # Paragraphs looks it up there
    return count


def _labels(app, rids):
    with app.app_context():
        return {r: (db.session.get(Response, r).interview or {}).get("label") for r in rids}


def _expected(app, rids, texts):
    """Every place a passage can have, as spot() gives it for one passage alone (the old way)."""
    labels = _labels(app, rids)
    return {r: {view.spot(labels[r], texts[r], m.start()) for m in re.finditer(r"Absatz \d+ ", texts[r])} for r in rids}


def test_the_places_are_those_of_a_single_passage(app, client):
    base, rids, texts, tired, doubt = _world(app, client)
    labels = _labels(app, rids)
    expected = set().union(*_expected(app, rids, texts).values())
    # an example of the expected form: paragraph 4 of the first interview, with its own time mark
    assert view.spot(labels[rids[0]], texts[rids[0]], texts[rids[0]].index("Absatz 4 ")) == f"{labels[rids[0]]}, Pos. 4, 00:03:22"
    page = client.get(f"{base}/overview?ov=all&ov_a={tired}&ov_b={doubt}").data.decode()
    shown = set(re.findall(r"(?:%s), Pos\. \d+(?:, \d\d:\d\d:\d\d)?" % "|".join(map(re.escape, labels.values())), page))
    assert shown and shown <= expected and len(shown) > 4
    sheets = {name: rows for name, rows, _root in _sheets(client.get(f"{base}/categories.xlsx").data)}
    places = {cell for row in sheets["Stellen"][1:] for cell in row if ", Pos. " in str(cell)}
    assert places and places <= expected and len(places) > 4
    passages = client.get(f"{base}/passages?cat={tired}&cat={doubt}").data.decode()
    assert set(re.findall(r"(?:%s), Pos\. \d+(?:, \d\d:\d\d:\d\d)?" % "|".join(map(re.escape, labels.values())), passages)) <= expected


@pytest.mark.parametrize("page", ["overview", "overview?ov=all&ov_para=1", "categories.xlsx", "categories.csv", "passages"])
def test_paragraphs_once_per_interview_not_per_coding(app, client, calls, page):
    base, rids, texts, tired, doubt = _world(app, client)
    if page == "overview":  # the cell lists the pairs with their places
        page = f"overview?ov=all&ov_a={tired}&ov_b={doubt}"
    if page == "passages":
        page = f"passages?cat={tired}&cat={doubt}"
    calls.clear()
    assert client.get(f"{base}/{page}").status_code == 200
    # categories.xlsx places the passages three times (sheet "Stellen", further examples, overlaps): once per interview
    # each, whatever the number of codings (2 × 15 here; before, one call per coding or pair)
    assert len(calls) <= 3 * len(rids), len(calls)
