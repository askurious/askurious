"""Results of a semantic differential: a polarity profile between the poles (location per pair), the frequencies per
point only in a closed fold (Ralf, 2026-10-03)."""
from werkzeug.datastructures import MultiDict

from app import results
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import add

E = "/surveys/1/editor"


def setup(app, client, answers, level=None):
    add(client, "semantic")                                         # item 2
    client.post(E + "/items/2", data=MultiDict([
        ("code", "bild"), ("points", "5"),
        ("pair_id", "p1"), ("pair_code", ""), ("pair_left", "langsam"), ("pair_right", "schnell"),
        ("pair_id", "p2"), ("pair_code", ""), ("pair_left", "bequem"), ("pair_right", "unbequem"), ("pair_reverse", "p2")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        if level:
            item = db.session.get(Item, 2)
            item.config = {**item.config, "level": level}
        for a in answers:
            s.responses.append(Response(status=Response.COMPLETED, answers={"2": a}))
        db.session.commit()


def profile(app):
    with app.app_context():
        data = results.compute(db.session.get(Survey, 1))
        return next(e for e in data["items"] if e["item"].id == 2)["profile"]


def test_ordinal_profile_shows_median_and_quartiles_between_the_poles(app, client, survey):
    setup(app, client, [{"p1": v, "p2": 1} for v in (1, 2, 4, 4, 5)])
    pf = profile(app)
    assert pf["points"] == 5 and [t["x"] for t in pf["ticks"]] == [5, 27.5, 50, 72.5, 95]
    first, second = pf["rows"]
    assert (first["left"], first["right"]) == ("langsam", "schnell")
    cell = first["cells"][0]
    assert cell["kind"] == "ord" and cell["median"] == 4 and (cell["q1"], cell["q3"]) == (2, 4) and cell["n"] == 5
    assert cell["mid"] == 72.5 and cell["lo"] == 27.5 and cell["hi"] == 72.5
    # reversed pair: recoded (1 -> 5) and shown with swapped poles, so the values still run left to right
    assert (second["left"], second["right"]) == ("unbequem", "bequem") and second["cells"][0]["median"] == 5
    assert pf["series"][0]["line"] == "72.5,0.5 95.0,1.5"


def test_metric_profile_shows_mean_and_sd(app, client, survey):
    setup(app, client, [{"p1": v, "p2": 3} for v in (2, 4)], level="metric")
    cell = profile(app)["rows"][0]["cells"][0]
    assert cell["kind"] == "num" and cell["mean"] == 3 and round(cell["sd"], 3) == 1.414
    assert profile(app)["metric"]


def test_page_draws_the_profile_and_folds_the_frequencies(app, client, survey):
    setup(app, client, [{"p1": 2, "p2": 4}, {"p1": 3, "p2": 4}])
    html = client.get("/surveys/1/results?go=1").data.decode()
    assert 'class="sd-profile"' in html and "Punkt: Median, Balken: Quartile Q1–Q3" in html
    fold = html[html.index('<details class="res-dist">'):]
    assert "Verteilung je Begriffspaar" in fold[:200] and "res-bars" in fold[:fold.index("</details>")]
