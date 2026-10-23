"""Strings in the R script of the structural equation model (audit 2026-10-13, DA-1): answer codes and group names
went into R string literals raw, so an answer code `2", system("calc.exe"), "x` ran as R code on the researcher's
computer when the downloaded script was run."""
import random

from werkzeug.datastructures import MultiDict

from app import sem_model
from app.extensions import db
from app.models import Response, Survey
from tests.analysis.test_plan_moderation import _items, _plan
from tests.analysis.test_sem_survey import E, _scale
from tests.conftest import add, experiments_on

EVIL = '2", system("calc.exe"), "x'


def test_answer_code_is_an_escaped_string_in_the_r_script(app, client, survey):
    experiments_on(client, "sem")
    for number, code, k in ((2, "tb", 4), (3, "einst", 4), (4, "absicht", 3)):
        _scale(client, number, code, k)
    add(client, "single_choice")
    client.post(f"{E}/items/5", data=MultiDict({"code": "gruppe", "question": "Gruppe?", "option_id": ["a", "b"],
                                                "option_code": ["1", EVIL], "option_label": ["A", "B"]}))
    rng = random.Random(3)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for _ in range(300):
            g = rng.random() < 0.5
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers={
                "2": _items(rng, rng.gauss(0, 1), 4), "3": _items(rng, rng.gauss(0, 1), 4),
                "4": _items(rng, rng.gauss(0, 1), 3), "5": EVIL if g else "1"}))
        db.session.commit()
    _plan(client, "gruppe")
    with app.app_context():
        text = sem_model.r_script(db.session.get(Survey, 1), "daten.rds")
    assert text
    assert 'system("calc.exe")' not in text
    assert r'"2\", system(\"calc.exe\"), \"x"' in text


def test_r_string_and_comment_helpers():
    assert sem_model._r_str('a"b\\c\nd\x00') == r'"a\"b\\c\nd"'
    assert sem_model._r_comment("Titel'\n\"); system(1) #") == "Titel ); system(1) #"
