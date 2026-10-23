"""Live feedback on a talk (app/live/, experimental "slides"; concept konzept-live-feedback-praesentation-2026-10-12.md):
the template "Fragen & Feedback zum Vortrag", the emojis counted per slide (the button "Unklar" went, Ralf 2026-10-16),
the review at the end, the QR code for one's own slides."""
from app.extensions import db
from app.models import LiveFeedback, LiveQuestion, Survey
from tests.conftest import experiments_on
from tests.live.test_live import add, key, make, steer


def _options(app, sid, **more):
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.live = {**survey.live, **more}
        db.session.commit()


def _screen(client, code):
    return client.get(f"/presenter/{code}/result.json").json


def _send(app, code, emoji):
    """One emoji from a phone of its own (a phone may send one burst every REACTION_GAP seconds)."""
    p = app.test_client()
    p.get(f"/live/{code}")
    assert p.post(f"/live/{code}/reaction", data={"e": emoji}).json["ok"]


def test_the_talk_template_for_everyone(app, client):
    """No experimental feature any more (Ralf, 2026-10-17): the template is there without a switch."""
    assert "Fragen &amp; Feedback zum Vortrag" in client.get("/presenter/new?start=vorlage").data.decode()
    client.post("/presenter/new", data={"title": "Vortrag", "language": "de", "template": "vortrag"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey).filter_by(title="Vortrag"))
        st, code = survey.live, survey.code
        assert st["qa"] and st["reactions"] and "😕" in st["emojis"] and st["flow"] == "questions"
        assert survey.setup_step == "fertig" and not [i for b in survey.blocks for i in b.items]
    phone = app.test_client().get(f"/live/{code}").data.decode()
    assert "data-live-unclear" not in phone and "data-live-react" in phone and "😕" in phone
    assert "Fragen stellen" in phone and "Gleich geht es los" not in phone                 # the talk's own note


def test_emojis_count_per_slide(app, client):
    code, sid, block = make(app, client)
    q1 = add(app, client, sid, block, "info", text="Folie eins")
    q2 = add(app, client, sid, block, "rating", question="Wie war es?")
    _send(app, code, "👍")                                                                    # before any slide
    steer(client, code, action="start")
    for e in ("👍", "😮", "👍"):
        _send(app, code, e)
    s = _screen(client, code)
    assert s["emojis_on"] and s["emojis"] == [["👍", 2], ["😮", 1]]                         # the most first
    steer(client, code, action="next")
    assert _screen(client, code)["emojis"] == []
    _send(app, code, "👏")
    with app.app_context():
        rows = db.session.scalars(db.select(LiveFeedback).order_by(LiveFeedback.id)).all()
        assert [(r.item_id, r.kind) for r in rows] == [(None, "e:👍"), (q1, "e:👍"), (q1, "e:😮"), (q1, "e:👍"), (q2, "e:👏")]
    _options(app, sid, reactions=False)
    assert _screen(client, code)["emojis_on"] is False and "emojis" not in _screen(client, code)


def test_the_button_unclear_is_gone_and_its_taps_count_as_the_emoji(app, client):
    code, sid, block = make(app, client)
    _options(app, sid, unclear=True, emojis=["👍", "😕"])                                    # "unclear": an older version
    q1 = add(app, client, sid, block, "info", text="Folie eins")
    steer(client, code, action="start")
    assert "data-live-unclear" not in app.test_client().get(f"/live/{code}").data.decode()
    assert app.test_client().post(f"/live/{code}/unclear").status_code == 404
    with app.app_context():
        run = db.session.get(Survey, sid).live["run"]["id"]
        db.session.add(LiveFeedback(survey_id=sid, run_id=run, item_id=q1, kind="unclear"))
        db.session.commit()
    _send(app, code, "😕")
    assert _screen(client, code)["emojis"] == [["😕", 2]]


def test_the_review_groups_questions_and_emojis_by_slide(app, client):
    code, sid, block = make(app, client)
    _options(app, sid, qa=True)
    q1 = add(app, client, sid, block, "rating", question="Wie war es?")
    add(app, client, sid, block, "info", text="Zweite Folie")
    p, other = app.test_client(), app.test_client()
    p.get(f"/live/{code}"), other.get(f"/live/{code}")
    assert p.post(f"/live/{code}/qa", data={"text": "Gibt es die Folien online?"}).json["ok"]   # before any slide
    steer(client, code, action="start")
    assert p.post(f"/live/{code}/qa", data={"text": "Was heißt das?"}).json["ok"]
    assert other.post(f"/live/{code}/qa", data={"text": "Und warum?"}).json["ok"]
    for e in ("👍", "😮", "👍"):
        _send(app, code, e)
    with app.app_context():
        asked = {q.text: q.item_id for q in db.session.scalars(db.select(LiveQuestion))}
        assert asked == {"Gibt es die Folien online?": None, "Was heißt das?": q1, "Und warum?": q1}
        top = db.session.scalar(db.select(LiveQuestion).filter_by(text="Und warum?"))
    p.post(f"/live/{code}/qa/{top.id}/vote")
    steer(client, code, action="next")
    for e in ("👏", "👏", "👏", "👏"):
        _send(app, code, e)
    steer(client, code, action="review_on")
    s = _screen(client, code)
    assert s["current"]["kind"] == "review" and s["current"]["n"] == 3
    rows = s["current"]["rows"]
    assert rows[0]["id"] is None and rows[0]["title"] == "Allgemein" and rows[0]["emojis"] == []
    assert [q["text"] for q in rows[0]["questions"]] == ["Gibt es die Folien online?"]
    assert rows[1]["id"] == q1 and rows[1]["title"] == "Wie war es?" and rows[1]["emojis"] == [["👍", 2], ["😮", 1]]
    assert [q["text"] for q in rows[1]["questions"]] == ["Und warum?", "Was heißt das?"]          # the most voted first
    assert rows[2]["pos"] == 2 and rows[2]["title"] == "Folie 2" and rows[2]["emojis"] == [["👏", 4]]
    steer(client, code, action="review_sort", by="unclear")                                       # the most emojis first
    assert [r["pos"] for r in _screen(client, code)["current"]["rows"]] == [2, 1, 0]
    steer(client, code, action="screen", item=q1)                                                 # back to a slide
    assert _screen(client, code)["current"]["kind"] != "review"


def test_a_new_start_or_a_deleted_run_drops_the_counts(app, client):
    code, sid, block = make(app, client)
    add(app, client, sid, block, "info", text="Eins")
    steer(client, code, action="start")
    _send(app, code, "👍")
    steer(client, code, action="restart")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(LiveFeedback.id))) == 0
    _send(app, code, "👍")
    with app.app_context():
        old = db.session.get(Survey, sid).live["run"]["id"]
    steer(client, code, action="new_run")
    steer(client, code, action="run_delete", run=old)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(LiveFeedback.id))) == 0


def test_the_reactions_csv_counts_per_run_and_slide(app, client):
    code, sid, block = make(app, client)
    _options(app, sid, emojis=["👍", "😕"])
    add(app, client, sid, block, "info", text="Eins")
    add(app, client, sid, block, "info", text="Zwei")
    steer(client, code, action="start")
    steer(client, code, action="next")
    for e in ("😕", "👍", "😕"):
        _send(app, code, e)
    lines = client.get(f"/presenter/{code}/reactions.csv").data.decode("utf-8-sig").splitlines()
    assert lines[0] == "durchgang;folie;emoji;anzahl" and lines[1].endswith(";2;😕;2") and lines[2].endswith(";2;👍;1")
    assert "reactions.csv" in client.get(f"/surveys/{sid}/data").data.decode()
    assert app.test_client().get(f"/presenter/{code}/reactions.csv").status_code in (302, 401)


def test_the_csv_names_the_slide(app, client):
    code, sid, block = make(app, client)
    _options(app, sid, qa=True)
    add(app, client, sid, block, "info", text="Eins")
    add(app, client, sid, block, "info", text="Zwei")
    steer(client, code, action="start")
    steer(client, code, action="next")
    p = app.test_client()
    p.get(f"/live/{code}")
    p.post(f"/live/{code}/qa", data={"text": "Frage zu zwei"})
    lines = client.get(f"/presenter/{code}/audience-questions.csv").data.decode("utf-8-sig").splitlines()
    assert lines[0].endswith(";folie") and lines[1].endswith(";2")


def test_the_qr_code_for_ones_own_slides(app, client):
    code, sid, block = make(app, client)
    assert f"/presenter/{code}/qr.png" in client.get(f"/presenter/{code}/present").data.decode()
    png = client.get(f"/presenter/{code}/qr.png")
    assert png.status_code == 200 and png.mimetype == "image/png" and png.data.startswith(b"\x89PNG")
    assert f'filename="qr-{code}.png"' in png.headers["Content-Disposition"]
    svg = client.get(f"/presenter/{code}/qr.svg")
    assert svg.mimetype == "image/svg+xml" and b"<svg" in svg.data
    assert app.test_client().get(f"/presenter/{code}/qr.png").status_code in (302, 401)       # signed in only


def test_the_setting_unclear_is_gone(app, client):
    code, sid, block = make(app, client)
    experiments_on(client, "slides")
    page = client.get(f"/presenter/{code}/settings").data.decode()
    assert 'name="unclear"' not in page and "je Folie gezählt" in page


def test_the_privacy_page_tells_about_emojis(app, client):
    code, sid, block = make(app, client)
    with app.app_context():
        uid = db.session.get(Survey, sid).public_key
    assert "Ringvorlesung" in app.test_client().get(f"/s/{code}/privacy").data.decode()  # also under the live code
    assert "Schicken Sie ein Emoji" in app.test_client().get(f"/s/{uid}/privacy").data.decode()   # reactions are on
    _options(app, sid, reactions=False)
    assert "Schicken Sie ein Emoji" not in app.test_client().get(f"/s/{uid}/privacy").data.decode()
    assert "Unklar" not in app.test_client().get(f"/s/{uid}/privacy").data.decode()
