"""Live votes, flow "In Blöcken": blocks started one at a time, answered page by page, the screen steered apart."""
from app.extensions import db
from app.models import Response, Survey
from tests.live.test_live import add, make, steer


def key(p, code):
    return p.get(f"/live/{code}/state").json["key"]


def test_blocks_are_answered_page_by_page_before_they_are_shown(app, client):
    code, sid, first = make(app, client, "Blöcke")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "blocks", "reactions": "1", "filter": "1"})
    q1 = add(app, client, sid, first, "single_choice", question="Wie fanden Sie den Vortrag?", options=["gut", "mittel", "schlecht"])
    q2 = add(app, client, sid, first, "text_input", question="Ein Wort dazu")
    add(app, client, sid, first, "page_break")
    q3 = add(app, client, sid, first, "rating", question="Wie verständlich?")
    steer(client, code, action="add_block")
    with app.app_context():
        second = max(b.id for b in db.session.get(Survey, sid).blocks)
    q4 = add(app, client, sid, second, "rating", question="Nach der Pause?")
    page = client.get(f"/presenter/{code}").data.decode()
    assert "Seitenumbruch" in page and 'value="add_block"' in page
    assert 'value="page_break"' in page and "Antworten offen" not in page                      # nothing runs yet
    st = steer(client, code, action="start")
    assert st["block"] == first and st["screen"] == q1 and st["show"] is False
    steer(client, code, action="screen", item=q4)                                               # the screen apart
    p = app.test_client()
    assert key(p, code) == f"p-{first}-0"
    form = p.get(f"/live/{code}").data.decode()
    assert f'name="i{q1}"' in form and f'name="i{q2}"' in form and f'name="i{q3}"' not in form   # page 1: two questions
    r = p.post(f"/live/{code}/answer", data={"ref": f"p-{first}-0", f"i{q1}": "9", f"i{q2}": "Spannend"})
    assert r.status_code == 400 and str(q1) in r.json["errors"]                                  # all checked first
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{first}-0", f"i{q1}": "1", f"i{q2}": "Spannend"}).json["ok"]
    assert key(p, code) == f"p-{first}-1"
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{first}-1", f"i{q3}": "4"}).json["ok"]
    assert key(p, code) == "done"                                                                # more may come
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{first}-0", f"i{q1}": "2"}).status_code == 409
    shown = client.get(f"/presenter/{code}/result.json").json
    assert [b["active"] for b in shown["blocks"]] == [True, False] and shown["current"]["id"] == q4
    st = steer(client, code, action="next_block")
    assert key(p, code) == f"p-{second}-0" and st["screen"] == q4                              # the screen follows
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{second}-0", f"i{q4}": "5"}).json["ok"]
    assert key(p, code) == "end"                                                                 # the closing text
    steer(client, code, action="release", block=first)
    assert key(p, code) == "done"                                                                # answered there already
    steer(client, code, action="screen", item=q1)
    steer(client, code, action="results_on")
    assert client.get(f"/presenter/{code}/result.json").json["current"]["n"] == 1
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(survey_id=sid))
        assert r.answers == {str(q1): "1", str(q2): "Spannend", str(q3): 4, str(q4): 5}
        assert r.layout["live_pages"] == {str(first): 2, str(second): 1}
    client.get(f"/presenter/{code}")                                                                   # blocks are kept
    with app.app_context():
        assert len(db.session.get(Survey, sid).blocks) == 2


def test_new_live_votes_go_question_by_question(app, client):
    """New live votes start with 'Frage für Frage' (Ralf, 2026-10-16; before: blocks)."""
    client.post("/presenter/new", data={"title": "Neu", "language": "de"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title="Neu"))
        assert survey.live["flow"] == "questions" and survey.image_style["key"] == "watercolor"     # ink and watercolour


def test_back_to_question_by_question_puts_the_blocks_together(app, client):
    code, sid, first = make(app, client, "Zurück")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "blocks"})
    add(app, client, sid, first, "rating", question="A")
    steer(client, code, action="add_block")
    with app.app_context():
        second = max(b.id for b in db.session.get(Survey, sid).blocks)
    add(app, client, sid, second, "rating", question="B")
    assert "Zurück zu „Frage für Frage“" in client.get(f"/presenter/{code}/settings").data.decode()
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "questions"})
    client.get(f"/presenter/{code}")
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert len(survey.blocks) == 1 and [i.text("question") for i in survey.blocks[0].items] == ["A", "B"]


def test_the_qr_code_large_on_every_screen(app, client):
    code, sid, block = make(app, client, "QR")
    assert steer(client, code, action="qr_on")["qr"] is True
    assert client.get(f"/presenter/{code}/result.json").json["qr"] is True
    steer(client, code, action="qr_off")
    assert client.get(f"/presenter/{code}/result.json").json["qr"] is False


def test_a_semantic_differential_on_the_screen(app, client):
    from tests.live.test_live import send

    code, sid, block = make(app, client, "Polarität")
    q = add(app, client, sid, block, "semantic", question="Die Vorlesung war …")
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, q)
        item.config = {**item.config, "points": 5, "pairs": [{"id": "p1", "code": "a", "reverse": False}, {"id": "p2", "code": "b", "reverse": False}]}
        item.texts = {"de": {"question": "Die Vorlesung war …", "pairs": {"p1": {"left": "langweilig", "right": "spannend"},
                                                                     "p2": {"left": "schwer", "right": "leicht"}}}}
        db.session.commit()
    assert "Semantisches Differential" in client.get(f"/presenter/{code}").data.decode()
    steer(client, code, action="start")
    for a, b in (("5", "2"), ("4", "2"), ("5", "3")):
        assert send(app.test_client(), code, {f"i{q}_p1": a, f"i{q}_p2": b}).json["ok"]
    steer(client, code, action="results_on")
    shown = client.get(f"/presenter/{code}/result.json").json["current"]
    assert shown["kind"] == "semdiff" and shown["points"] == 5
    assert shown["rows"][0] == {"left": "langweilig", "right": "spannend", "counts": [0, 0, 0, 1, 2], "mean": 4.7}
    assert shown["rows"][1]["counts"] == [0, 2, 1, 0, 0]


def test_all_back_throws_the_run_away_and_starts_it_afresh(app, client):
    code, sid, block = make(app, client, "Zurück auf Anfang")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "blocks", "qa": "1", "reactions": "1"})
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    st = steer(client, code, action="start")
    name = st["run"]["name"]
    p = app.test_client()
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", f"i{q}": "4"}).json["ok"]
    p.post(f"/live/{code}/qa", data={"text": "Eine Frage"})
    assert key(p, code) == "end"
    assert "data-restart" in client.get(f"/presenter/{code}/speaker").data.decode()
    st = steer(client, code, action="restart")
    assert st["runs"] == [] and st["run"]["name"] == name                    # no new run, the same one afresh
    assert st["block"] == block and st["screen"] == q and st["show"] is False
    assert key(p, code) == f"p-{block}-0"                                    # the same phone answers again
    with app.app_context():
        from app.models import LiveQuestion, Response

        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 0
        assert db.session.scalar(db.select(db.func.count(LiveQuestion.id)).filter_by(survey_id=sid)) == 0


def test_a_quiz_starts_with_a_random_nickname(app, client):
    from app.live import NICK_ANIMALS, random_nick

    assert random_nick("de").split(" ")[-1] in {a for a, _, _ in NICK_ANIMALS} and random_nick("en").count(" ") == 1
    code, sid, block = make(app, client, "Quiz")
    q = add(app, client, sid, block, "single_choice", question="2+2?", options=["3", "4"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "2"})
    page = app.test_client().get(f"/live/{code}").data.decode()
    import re
    name = re.search(r'id="live-nick" name="name" maxlength="30" value="([^"]+)"', page)
    assert name and any(name.group(1).endswith(" " + a) for a, _, _ in NICK_ANIMALS)
    assert "<code>" in client.get(f"/presenter/{code}/screen").data.decode()


def test_the_nickname_goes_with_every_answer(app, client):
    """A name typed without the tick was lost with the next load of the page ("Antwort ändern", a new question, a
    rejoin) and the leaderboard named the random one (Ralf, 2026-10-08): the answer carries the field."""
    code, sid, block = make(app, client, "Quiz")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "filter": "1"})
    q = add(app, client, sid, block, "single_choice", question="2+2?", options=["3", "4"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "2"})
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")                                                     # a random name waits in the session

    def send(data):
        return p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", **data})

    assert send({f"i{q}": "2", "live_nick": "  Kim  B. "}).json["ok"]
    assert 'value="Kim B."' in p.get(f"/live/{code}?aendern=1").data.decode()
    assert send({f"i{q}": "1", "live_nick": "Kim"}).json["ok"]       # the answer changed, the name too
    assert send({f"i{q}": "2", "live_nick": "Arsch"}).json["ok"]     # the filter keeps the name before
    assert send({f"i{q}": "2"}).json["ok"]                           # an answer without the field keeps it
    steer(client, code, action="board_on")
    assert [r["name"] for r in client.get(f"/presenter/{code}/result.json").json["current"]["rows"]] == ["Kim"]
    with app.app_context():
        assert db.session.scalar(db.select(Response).filter_by(survey_id=sid)).layout["live_nick"] == "Kim"


def test_nicknames_are_normalised_before_the_filter_and_the_board(app, client):
    """Abgleich 2026-10-15, TN-13: the word filter compared with casefold only, so full-width or other look-alike
    letters passed it and showed on the screen as typed. Now NFKC before the filter and on the board."""
    code, sid, block = make(app, client, "Quiz")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "filter": "1"})
    q = add(app, client, sid, block, "single_choice", question="2+2?", options=["3", "4"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "2"})
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")
    full_width = "".join(chr(ord(c) + 0xFEE0) for c in "Arsch")                 # Ａｒｓｃｈ
    assert p.post(f"/live/{code}/name", data={"name": full_width}).status_code == 400
    assert p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "2", "live_nick": "Ｋｉｍ"}).json["ok"]
    steer(client, code, action="board_on")
    assert [r["name"] for r in client.get(f"/presenter/{code}/result.json").json["current"]["rows"]] == ["Kim"]

def test_the_nickname_goes_with_a_page_of_a_block(app, client):
    code, sid, block = make(app, client, "Quiz")
    with app.app_context():
        s = db.session.get(Survey, sid)
        s.live = {**s.live, "flow": "blocks", "teams": ["Rot", "Blau"]}
        db.session.commit()
    q = add(app, client, sid, block, "single_choice", question="2+2?", options=["3", "4"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "2"})
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")
    data = {"ref": f"p-{block}-0", f"i{q}": "2", "live_nick": "Kim", "live_team": "Rot"}
    assert p.post(f"/live/{code}/answer", data=data).json["ok"]
    assert 'value="Kim"' in p.get(f"/live/{code}").data.decode()              # back on the page: the name stays
    assert p.post(f"/live/{code}/answer", data={**data, "live_team": "Grün", "page": str(q)}).json["ok"]
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(survey_id=sid))
        assert (r.layout["live_nick"], r.layout["live_team"]) == ("Kim", "")  # no team of the vote: none


def test_a_tap_sends_where_every_question_takes_one(app, client):
    from tests.live.test_live import add

    code, sid, block = make(app, client, "Ein Tippen")
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    w = add(app, client, sid, block, "text_input", question="Ein Wort?")
    assert 'name="instant"' in client.get(f"/presenter/{code}/settings").data.decode()
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "instant": "1"})
    steer(client, code, action="start")                                     # question by question: the rating first
    page = app.test_client().get(f"/live/{code}").data.decode()
    assert "data-instant" in page and "p-nav is-instant" in page
    steer(client, code, action="screen", item=w)
    assert "data-instant" not in app.test_client().get(f"/live/{code}").data.decode()   # typing needs „Senden“
    assert q


def test_a_block_is_deleted_from_its_head_and_its_name_takes_no_drop(app, client):
    """Ralf, 2026-10-16: blocks could not be deleted on the live page, and a slide dragged by its text landed in a
    block name as its address."""
    code, sid, first = make(app, client, "Löschen")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "blocks"})
    add(app, client, sid, first, "rating", question="Wie verständlich?")
    steer(client, code, action="add_block")
    with app.app_context():
        second = max(b.id for b in db.session.get(Survey, sid).blocks)
    add(app, client, sid, second, "rating", question="Nach der Pause?")
    page = client.get(f"/presenter/{code}").data.decode()
    assert f"/blocks/{second}/delete" in page and "data-no-drop" in page and 'draggable="false"' in page
    assert steer(client, code, action="start")["block"] == first                         # the first block runs
    page = client.get(f"/presenter/{code}").data.decode()
    assert f"/blocks/{first}/delete" not in page and f"/blocks/{second}/delete" in page
    r = client.post(f"/surveys/{sid}/editor/blocks/{second}/delete")
    assert r.status_code == 302 and f"/presenter/{code}" in r.headers["Location"]
    with app.app_context():
        assert [b.id for b in db.session.get(Survey, sid).blocks] == [first]
