"""Ranking and constant sum: their starting state (start order, even split) is itself a complete answer. Whether it was
confirmed without a change stands in its own column <code>_unchanged, with its codebook line (UI/UX audit 2026-10-16,
105: "Startwerte sind gültige Antworten")."""
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add
from tests.items.test_ranking import setup as ranking_setup


def test_ranking_says_whether_the_start_order_was_kept(app, client, survey):
    ranking_setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        ids = [o["id"] for o in item.config["options"]]                     # a, b, c = codes 1, 2, 3
        shown = [ids[2], ids[0], ids[1]]                                     # the random start order: 3, 1, 2
        assert item.kind.kept_start(item, ["3", "1", "2"], shown) == 1
        assert item.kind.kept_start(item, ["1", "3", "2"], shown) == 0
        assert item.kind.kept_start(item, None, shown) is None
        assert item.kind.kept_start(item, ["1", "2", "3"], []) == 1          # without a random order: the list order
        from app import export

        s = db.session.get(Survey, 1)
        cols = dict(export.columns(s))
        assert cols["prio_unchanged"].startswith("Startreihenfolge unverändert bestätigt")
        book = {row[0]: row for row in export.codebook(s)}
        assert book["prio_unchanged"][2] == "Kennzeichen" and "1 = ja" in book["prio_unchanged"][3]
        assert export.label_en("prio_unchanged", cols["prio_unchanged"]).startswith("Starting order confirmed unchanged")


def test_constant_sum_says_whether_the_even_split_was_kept(app, client, survey):
    add(client, "constant_sum")                                              # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "budget"), ("question", "Verteilen Sie 100 Punkte."), ("total", "100"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Bildung"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Verkehr"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Kultur")]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.kept_start(item, {"1": 34, "2": 33, "3": 33}, []) == 1   # as shown: the first gets the rest
        assert item.kind.kept_start(item, {"1": 33, "2": 34, "3": 33}, []) == 0
        assert item.kind.kept_start(item, None, []) is None
        from app import export

        assert "budget_unchanged" in dict(export.columns(db.session.get(Survey, 1)))
