"""Text highlighting on a dashboard (Ralf, 2026-10-03: as on the results page): the text with a bar under every word,
a word marked by fewer than MIN_CELL people unmarked; and the most marked words per category."""
from werkzeug.datastructures import MultiDict

from app import boards
from app.extensions import db
from app.models import Survey
from tests.dashboards.test_boards import D, _answer
from tests.items.test_highlight_cloze import setup_highlight


def test_highlight_tile_shows_the_text_with_its_marks(app, client, survey):
    setup_highlight(client)                                         # item 2: "Der Roboter hilft im Haushalt, aber er ist laut."
    with app.app_context():
        rows = {r["key"]: r for g in boards.candidates(db.session.get(Survey, 1)) for r in g["rows"]}
    assert rows["item:2"]["reason"] == "" and [v for v, _ in rows["item:2"]["views"]] == ["marks", "top"]
    client.post(f"{D}/new", data={"template": "empty"})
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2")]))
    _answer(app, {"2": {"2": 1}}, n=8)                              # "Roboter" positive, 8 people
    _answer(app, {"2": {"9": 2}}, n=3)                              # "laut" negative, only 3: stays unmarked
    page = client.get(f"{D}/1/view").data.decode()
    assert 'class="b-hl-w" title="Roboter: positiv 8, negativ 0"' in page
    assert 'title="laut' not in page and "11 Personen" in page
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "top"), ("w-item:2", "6")]))
    page = client.get(f"{D}/1/view").data.decode()
    assert "<li>Roboter <span" in page and "kein Wort oft genug markiert" in page


def test_threshold_of_a_dashboard_is_chosen_in_the_designer(app, client, survey):
    """Ralf, 2026-10-03: from how many answers the tiles show numbers is chosen in the designer's right column."""
    setup_highlight(client)
    client.post(f"{D}/new", data={"template": "empty"})
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2")]))
    _answer(app, {"2": {"2": 1}}, n=6)
    assert "Noch zu wenige Antworten" in client.get(f"{D}/1/view").data.decode()       # 6 < 10, the standard
    page = client.get(f"{D}/1").data.decode()
    assert 'name="min_tile" form="board-form"' in page and '<option value="10" selected>10 Antworten (Standard)' in page
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("min_tile", "5")]))
    with app.app_context():
        from app.models import Board
        assert boards.min_tile_of(db.session.get(Board, 1)) == 5
    page = client.get(f"{D}/1/view").data.decode()
    assert "Noch zu wenige Antworten" not in page and 'title="Roboter: positiv 6' in page
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("min_tile", "20")]))   # not offered: back to the standard
    with app.app_context():
        assert boards.min_tile_of(db.session.get(Board, 1)) == 10


def test_sofort_still_protects_words_marked_by_fewer_than_five_people(app, client, survey):
    """TN-35: immediate dashboards must not disclose an individual's highlighted words."""
    app.config["BOARD_CHECK_SECONDS"] = 0
    setup_highlight(client)
    client.post(f"{D}/new", data={"template": "empty"})
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("min_tile", "1")]))
    assert "sofort (ab der ersten Antwort)" in client.get(f"{D}/1").data.decode()
    _answer(app, {"2": {"2": 1}}, n=1)
    page = client.get(f"{D}/1/view").data.decode()
    assert 'title="Roboter: positiv 1, negativ 0"' not in page
    _answer(app, {"2": {"2": 1}}, n=4)
    page = client.get(f"{D}/1/view").data.decode()
    assert 'title="Roboter: positiv 5, negativ 0"' in page
