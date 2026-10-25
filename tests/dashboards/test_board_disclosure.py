"""Public dashboard links (audit of 2026-10-05, package C): a scale split by groups gives no hidden small cell back
through the group means (B-1), and the word list of associations counts like the results page (B-14)."""
import re
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import BoardShare, Item, Response, utcnow
from tests.conftest import add

D = "/surveys/1/dashboards"


def _answer(app, answers, n):
    with app.app_context():
        now = utcnow()
        for _ in range(n):
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, answers=answers,
                                    started_at=now - timedelta(minutes=5), completed_at=now))
        db.session.commit()


def test_split_means_give_no_hidden_level_back(app, client, survey):
    app.config["BOARD_CHECK_SECONDS"] = 0
    add(client, "single_choice")   # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([("code", "g"), ("question", "Gruppe?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "A"), ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]))
    add(client, "likert")          # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([("code", "TA"), ("question", "Zustimmung?"), ("points", "5"),
        ("labeling", "full"), ("level", "metric"), ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Aussage")]))
    # group A: 10x level 1, 2x level 2, 3x level 3; group B: 15x level 5 -> everyone [10, 2, 3, 0, 15]
    for level, n in ((1, 10), (2, 2), (3, 3)):
        _answer(app, {"2": "1", "3": {"s1": level}}, n)
    _answer(app, {"2": "2", "3": {"s1": 5}}, 15)
    client.post(f"{D}/new", data={"template": "empty"})
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:3"), ("view-item:3", "stacked"), ("n-item:3", "1")]))
    client.post(f"{D}/1/link", data={"valid": "30"})
    with app.app_context():
        token = db.session.scalars(db.select(BoardShare)).first().token
    whole = app.test_client().get(f"/d/{token}").data.decode()
    assert "stimme eher nicht zu bis teils/teils: 16,7 %" in whole  # the tile of everyone puts levels 2 and 3 together
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:3"), ("view-item:3", "means"), ("by-item:3", "item:2")]))
    split = app.test_client().get(f"/d/{token}").data.decode()
    bars = [float(v) for v in re.findall(r'class="b-fill" data-k="item:3:g1-\d" style="--v: ([0-9.]+)%', split)]
    # group A from the levels as shown: (10 x 1 + 5 x 2.5) / 15 = 1.5 -> 12.5 % of the scale 1..5; B: 5 -> 100 %
    assert bars == [12.5, 100.0]
    total = sum((1 + v / 100 * 4) * 15 for v in bars)
    assert total == 97.5  # the sum of the levels as shown: whether 2 + 3 people or 3 + 2 stays open (98 vs 97)
    assert "1,5" in split and "5,0" in split


def test_split_mean_is_no_more_precise_than_its_label(app):
    from app import boards

    groups = [{"label": "A", "rest": False, "raw": {"shape": "nums", "n": 30, "rows": [{"label": "", "n": 30, "_xs": [1.0] * 29 + [2.0]}]}},
              {"label": "B", "rest": False, "raw": {"shape": "nums", "n": 30, "rows": [{"label": "", "n": 30, "_xs": [3.0] * 30}]}}]
    with app.test_request_context():
        out = boards._compare("nums", groups, "de")
    assert out["rows"][0]["means"] == [1.0, 3.0]  # 1.0333 as 1.0, the precision of the label


def test_common_groups_join_every_grouping(app):
    from app import boards

    with app.test_request_context():
        # everyone [10, 2, 3, 0, 15] merges 2-3; group B alone [0, 0, 0, 0, 15] merges nothing
        assert boards._common_groups([[10, 2, 3, 0, 15], [10, 2, 3, 0, 0], [0, 0, 0, 0, 15]]) == [(0, 0), (1, 2), (3, 3), (4, 4)]
        assert boards._common_groups([[6, 6, 6], [6, 6, 6]]) == [(0, 0), (1, 1), (2, 2)]


def test_association_words_count_like_the_results_page(app, client, survey):
    from app import boards

    add(client, "association")
    with app.app_context():
        item = db.session.scalar(db.select(Item).filter_by(type="association"))
        first = f"{item.code}_{item.kind.slots(item)[0]}"
        table = [{first: w} for w in ["Natur", "Natur", "Natur", "Natur.", "natur!", "Wald"]]
        found = boards._words(item, table, "de")
        assert found["_rows"][0] == ("Natur", 5) and found["n"] == 6
        terms = item.kind.terms(item, table)
        assert terms[0][:2] == ("natur", 5)  # the same count on the results page
