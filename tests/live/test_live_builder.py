"""Live votes (Philipp, 2026-10-07): a device that comes back starts the running block anew; results open from the
start (the ghost hides them); "Freitext" as a list of boxes; "+ Frage" first, then the type; block heads apart."""
from app.extensions import db
from app.models import Item, Response, Survey
from tests.live.test_live import add, make, steer
from tests.live.test_live_blocks import key


def blocks_vote(app, client, title):
    code, sid, block = make(app, client, title)
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "blocks", "results_open": "1"})
    return code, sid, block


def test_a_device_that_comes_back_starts_the_running_block_anew(app, client):
    code, sid, block = blocks_vote(app, client, "Wieder da")
    q1 = add(app, client, sid, block, "rating", question="Erste Seite")
    add(app, client, sid, block, "page_break")
    q2 = add(app, client, sid, block, "rating", question="Zweite Seite")
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", f"i{q1}": "4"}).json["ok"]
    page = p.get(f"/live/{code}?weiter=1").data.decode()                       # the phone's own load: on to page 2
    assert key(p, code) == f"p-{block}-1" and f'name="i{q2}"' in page
    page = p.get(f"/live/{code}").data.decode()                                # back via the QR code
    assert key(p, code) == f"p-{block}-0" and f'name="i{q1}"' in page
    assert 'value="4" checked' in page                                         # the answer given stays and shows
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", f"i{q1}": "5"}).json["ok"]
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-1", f"i{q2}": "2"}).json["ok"]
    assert key(p, code) == "end"
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(survey_id=sid))
        assert r.answers == {str(q1): 5, str(q2): 2}                           # one participation, the changed answer
    p.get(f"/live/{code}")                                                     # done, and back once more: from the start
    assert key(p, code) == f"p-{block}-0"


def test_question_by_question_is_not_touched_by_coming_back(app, client):
    code, sid, block = make(app, client, "Frage für Frage")
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")
    assert p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "3"}).json["ok"]
    before = key(p, code)
    p.get(f"/live/{code}")
    assert key(p, code) == before != f"q-{q}"                                  # still answered, not asked again


def test_results_open_by_default_and_the_ghost_on_request(app, client):
    code, sid, block = make(app, client, "Offen")
    add(app, client, sid, block, "rating", question="Wie war es?")
    assert steer(client, code, action="start")["show"] is True                 # new default: open
    page = client.get(f"/presenter/{code}/settings").data.decode()
    assert 'name="results_open"' in page and "checked" in page.split('name="results_open"')[1][:40]
    assert "Geist" in page                                                     # the note on the ghost
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme"})  # the tick taken away
    assert steer(client, code, action="start")["show"] is False                # covered by the ghost
    assert steer(client, code, action="results_on")["show"] is True


def test_free_text_is_a_list_of_boxes(app, client):
    code, sid, block = make(app, client, "Freitext")
    page = client.get(f"/presenter/{code}").data.decode()
    assert 'value="free_text"' in page and "Freitext (Liste)" in page
    assert page.index('value="text_input"') < page.index('value="free_text"')  # right after the word cloud
    q = add(app, client, sid, block, "free_text", question="Was nehmen Sie mit?")
    with app.app_context():
        item = db.session.get(Item, q)
        assert item.type == "text_input" and item.config["multiline"] is True and item.config["max_length"] == 1000
        assert q in db.session.get(Survey, sid).live["wall"]
    assert "Freitext (Liste)" in client.get(f"/presenter/{code}").data.decode()
    steer(client, code, action="start")
    for text in ("Die Beispiele waren gut.", "Mehr Zeit für Fragen, bitte."):
        assert app.test_client().post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": text}).json["ok"]
    shown = client.get(f"/presenter/{code}/result.json").json["current"]
    assert shown["kind"] == "wall" and {r[0] for r in shown["rows"]} == {"Die Beispiele waren gut.", "Mehr Zeit für Fragen, bitte."}
    assert steer(client, code, action="wall_off")                              # the presenter may still show a cloud
    assert client.get(f"/presenter/{code}/result.json").json["current"]["kind"] == "words"


def test_free_text_only_in_live_votes(app, client, survey):
    assert client.post("/surveys/1/editor/blocks/2/items", data={"type": "free_text"}).status_code == 400
    assert 'data-type="free_text"' not in client.get("/surveys/1/editor/").data.decode()


def test_add_a_question_first_then_its_type(app, client):
    code, sid, block = make(app, client, "Erst Frage")
    page = client.get(f"/presenter/{code}").data.decode()
    picker = page.split("data-live-add")[1].split("</details>")[0]
    assert "<summary" in picker and picker.index("<summary") < picker.index('name="type"')
    assert '<select name="type"' not in page                                   # no list to choose before the button
    assert picker.count('class="live-add-type') >= 10                          # every type a button of its own


def test_block_heads_stand_apart(app, client):
    code, sid, block = blocks_vote(app, client, "Köpfe")
    page = client.get(f"/presenter/{code}").data.decode()
    assert 'class="live-block-head"' in page
    with open(app.static_folder + "/app.css", encoding="utf-8") as f:
        css = f.read()
    head = css.split(".live-block-head {")[1].split("}")[0]
    assert "background: var(--accent)" in head and "color: var(--on-accent)" in head


def test_the_jump_dots_are_not_placed_on_a_track():
    """The presenter's dots to jump to a question sat on one spot: the mean dot of the result axes shared .live-dot."""
    import re
    from pathlib import Path
    static = Path(__file__).resolve().parents[2] / "app" / "static"
    css = (static / "participant.css").read_text(encoding="utf-8")
    for rule in re.findall(r"(?m)^([^{}/]*\.live-dot(?![-\w])[^{}]*)\{([^}]*)\}", css):
        assert "::" in rule[0] or "absolute" not in rule[1], rule[0]   # ::before is only the larger hit area
    assert 'class="live-dot"' not in (static / "live.js").read_text(encoding="utf-8")


def test_without_results_open_every_slide_starts_behind_the_ghost(app, client):
    """Ralf, 2026-10-23: uncovered once, the ghost stayed off for all later slides; now each slide starts covered."""
    code, sid, block = make(app, client, "Geist")
    add(app, client, sid, block, "rating", question="Erste?")
    add(app, client, sid, block, "rating", question="Zweite?")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme"})  # "Ergebnisse sofort zeigen" off
    assert steer(client, code, action="start")["show"] is False
    assert steer(client, code, action="results_on")["show"] is True
    assert steer(client, code, action="next")["show"] is False                 # the next slide covered again
    steer(client, code, action="results_on")
    assert steer(client, code, action="prev")["show"] is False                 # going back too
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "results_open": "1"})
    steer(client, code, action="results_off")
    assert steer(client, code, action="next")["show"] is False                 # with the tick: kept as it is
    steer(client, code, action="results_on")
    assert steer(client, code, action="prev")["show"] is True
