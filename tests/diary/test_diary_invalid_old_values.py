"""Old answers with a number that is none ("nan", from before the number fields refused it) take no share of their
person's weight (audit 2026-10-17, M-R1): person 1 with 1 and "nan", person 2 with 5 give the mean (1 + 5) / 2."""
import math
from types import SimpleNamespace

from app import diary, results


def test_a_nan_of_old_takes_no_weight():
    table = [{"resp_id": 1, "v": 1}, {"resp_id": 2, "v": "nan"}, {"resp_id": 3, "v": 5}]
    numbered = {rid: SimpleNamespace(enrolment_id=owner, occasion={"part": "m1"}) for rid, owner in [(1, 1), (2, 1), (3, 2)]}
    weights = diary.question_weights(table, numbered, ["v"])
    assert weights[1] == 1.0 and weights[3] == 1.0
    clean = [results.valid_number(r["v"]) for r in table]
    got = results.numeric_stats(clean, [weights[r["resp_id"]] for r in table], persons=[1, 1, 2])
    assert math.isclose(got["mean"], 3)


def test_texts_and_codes_still_count():
    table = [{"resp_id": 1, "v": "gut"}, {"resp_id": 2, "v": "a"}, {"resp_id": 3, "v": ""}]
    numbered = {rid: SimpleNamespace(enrolment_id=1, occasion={"part": "m1"}) for rid in (1, 2, 3)}
    assert diary.question_weights(table, numbered, ["v"]) == {1: 0.5, 2: 0.5, 3: 1 / 3}
