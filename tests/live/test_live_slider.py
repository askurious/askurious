"""Live votes: the editing page with a slider (Zahl schätzen) chosen opens (crash on queryous.de, 2026-10-03)."""
from app.extensions import db
from tests.live.test_live import add, make


def test_the_editing_page_opens_with_a_slider(app, client):
    code, sid, block = make(app, client, "Regler")
    q = add(app, client, sid, block, "slider", question="Wie viele?")
    assert client.get(f"/presenter/{code}?item={q}").status_code == 200
    assert client.get(f"/surveys/{sid}/editor/items/{q}?embed=1").status_code == 200


def test_a_rating_slider_from_0_to_100_comes_in_ten_classes(app, client):
    from tests.live.test_live import send, steer

    code, sid, block = make(app, client, "Skala 100")
    q = add(app, client, sid, block, "rating", question="Wie sicher?")
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, q)
        item.config = {**item.config, "style": "slider", "min": 0, "max": 100}
        db.session.commit()
    steer(client, code, action="start")
    for v in ("5", "15", "95", "100"):
        assert send(app.test_client(), code, {f"i{q}": v}).json["ok"]
    steer(client, code, action="results_on")
    shown = client.get(f"/presenter/{code}/result.json").json["current"]
    assert len(shown["rows"]) == 10 and [r[1] for r in shown["rows"]] == [1, 1, 0, 0, 0, 0, 0, 0, 0, 2]
    assert shown["rows"][0][0] == "0–10" and shown["mean"] == 53.8

