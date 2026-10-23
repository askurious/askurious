"""Results page: figures and sections (UI/UX audit 2026-10-05, package G: V52 to V66)."""
from datetime import datetime, timedelta

from werkzeug.datastructures import MultiDict

from app import results
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add
from tests.analysis.test_compare import ROWS, setup


def test_histogram_on_one_axis_for_the_columns_of_a_question():
    a, b = results.histogram([[1, 2, 2, 3], [3, 4]])
    assert a["single"] and [x["count"] for x in a["bars"]] == [1, 2, 1, 0] and [x["count"] for x in b["bars"]] == [0, 0, 1, 1]
    assert a["top"] == b["top"] == 2                      # one y axis for the small multiples
    wide = results.histogram([[0.5, 120.0, 37.2, 60.0]])[0]
    assert not wide["single"] and sum(x["count"] for x in wide["bars"]) == 4 and wide["bars"][0]["lo"] <= 0.5
    assert results.histogram([[]]) == [None]


def test_bars_scale_to_the_largest_share_and_levels_diverge():
    assert results.bar_top([{"share": 0.1}, {"share": 0.31}]) == 0.31 and results.bar_top([]) == 1
    assert results.level_colour(0, 5) == ("var(--div-1)", True) and results.level_colour(4, 5) == ("var(--div-7)", True)
    assert results.level_colour(2, 5) == ("var(--div-4)", False)   # the neutral middle (S11)
    assert "color-mix" in results.level_colour(1, 5)[0]


def test_matrix_overview_group_charts_and_texts(app, client, survey):
    setup(app, client, ROWS)                        # a likert matrix with two statements (item 3), groups (item 2)
    html = client.get("/surveys/1/results?gruppen=2").data.decode()
    assert "Nach Gruppen: Gruppe?" in html and 'class="res-rank-dot"' in html                    # V62
    add(client, "text_input")
    client.post("/surveys/1/editor/items/4", data=MultiDict([("code", "idee"), ("question", "Ihre Idee?")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i, r in enumerate(s.responses):
            r.answers = {**r.answers, "4": "mehr Busse" if i % 2 else "Radwege"}
        item = next(i for b in s.blocks for i in b.items if i.code == "TA")
        item.config = {**item.config, "level": "ordinal"}                  # an ordinal matrix: stacked levels
        db.session.commit()
    html = client.get("/surveys/1/results").data.decode()
    assert 'class="res-stacks"' in html and "Verteilung je Aussage</summary>" in html          # V55
    assert "Antworten lesen (" in html and "mehr Busse" in html                                   # V61


def test_ranking_gets_its_mean_rank_overview(app, client, survey):
    add(client, "ranking")
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, 2)
        codes = [o["code"] for o in item.config["options"]]
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for n in range(12):
            order = codes if n % 3 else list(reversed(codes))
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                        answers={"2": order}))
        db.session.commit()
        data = results.compute(s)
        e = next(e for e in data["items"] if e["item"].id == 2)
        ranks = e["ranks_mean"]
        assert ranks[0]["code"] == codes[0] and ranks[0]["mean"] < ranks[-1]["mean"] and ranks[0]["ci"]
    html = client.get("/surveys/1/results").data.decode()
    assert 'class="res-ranks"' in html and "Verteilung je Option</summary>" in html
