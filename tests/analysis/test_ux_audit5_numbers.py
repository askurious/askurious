"""Numbers and notation (UI/UX audit 2026-10-05, package H: V67 to V75): one formatter for results page, dashboards
and Word, decimals per measure, counts without ".0", the mode of nominal answers, intervals as [a; b], the quota
line, the table of response patterns."""
import re

from werkzeug.datastructures import MultiDict

from app import manuscript_tables, units
from app.extensions import db
from app.models import Survey
from tests.conftest import add
from tests.analysis.test_compare import ROWS, setup


def test_one_formatter_with_a_dot_between_thousands():
    assert units.stat(93367.06, 0) == "93.367" and units.stat(93367.06, 2) == "93.367,06"
    assert units.stat(2122, 0) == "2.122" and units.stat(53.016) == "53,02" and units.stat(None) == "–"
    assert units.stat(-0.004) == "0,00" and units.stat(-1.5, 1) == "−1,5" and units.stat(0.35, 2, signed=True) == "+0,35"
    assert units.stat(1234.5, 1, "en") == "1,234.5"
    assert units.stat_places([93367.1]) == 0 and units.stat_places([53.0]) == 2
    assert units.whole([46, 50.0]) and not units.whole([46.5])
    # Word writes the same way
    assert manuscript_tables.fmt(93367.06, "de", places=0) == "93.367" and manuscript_tables.fmt(-0.5, "de") == "−0,50"
    assert manuscript_tables.fmt(0.85, "en", bounded=True) == ".85"


def _cells(html):
    return re.findall(r'<td class="num">(.*?)</td>', html)


def test_counts_in_the_cross_table_are_whole(app, client, survey):
    setup(app, client, ROWS)
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "farbe"), ("question", "Farbe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Rot"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Blau")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i, r in enumerate(s.responses):
            r.answers = {**r.answers, "4": "2" if i % 2 else "1"}
        db.session.commit()
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    # no "11.0": counts of the comparison are whole numbers
    assert not [c for c in _cells(html) if re.search(r"\d\.\d", re.sub(r"<[^>]+>", "", c))]
    assert "Welch t(" in html and re.search(r"Welch t\(\d+,\d\)", html)     # Welch df with one decimal (V69)


def test_mode_of_nominal_answers_names_the_answer(app, client, survey):
    add(client, "single_choice")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "land"), ("question", "Land?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Bayern"),
        ("option_id", "b"), ("option_code", "13"), ("option_label", "Sachsen"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Hessen")]))
    from datetime import datetime, timedelta

    from app.models import Response

    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for code in ["13"] * 5 + ["1"] * 2 + ["3"]:
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={"2": code}))
        db.session.commit()
    html = client.get("/surveys/1/results").data.decode()
    assert "Modus: Sachsen" in html and "Modus 13" not in html


def test_quota_summary_without_a_hanging_dot(app):
    with app.test_request_context():
        from flask import render_template_string

        class Q:
            name = "Geschlecht"

        report = [{"quota": Q(), "sum": {"done": 0, "target": 300},
                   "cells": [{"state": "offen"}, {"state": "offen"}]}]
        html = render_template_string('{% include "surveys/_quota_summary.html" %}', summary_report=report,
                                      summary_rule="", survey=type("S", (), {"id": 1})())
    assert "2 offen</span>" in html and "2 offen ·" not in html
