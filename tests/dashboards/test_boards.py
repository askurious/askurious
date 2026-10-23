"""Dashboards (app/boards/): tiles with results, quotas and field figures, shown by a link without signing in.
What a link shows is summarised: enough answers per tile, no small cells, no texts and no single answers."""
import json
import os
from datetime import timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import boards, transfer
from app.extensions import db
from app.models import Board, BoardShare, Response, Survey, SurveyMember, User, utcnow
from tests.conftest import add, fill_table

D = "/surveys/1/dashboards"
OTHER = "0000-0002-1825-0097"
FEW = "Noch zu wenige Antworten"


def _answer(app, answers, n=1, source="real", status=Response.COMPLETED):
    with app.app_context():
        now = utcnow()
        for _ in range(n):
            db.session.add(Response(survey_id=1, source=source, is_preview=source != "real", status=status, answers=answers,
                                    started_at=now - timedelta(minutes=5), completed_at=now if status == Response.COMPLETED else None))
        db.session.commit()


@pytest.fixture
def study(app, client, survey):
    """Single choice with a text field (2), multiple choice (3), Likert with a reversed statement (4), a number
    (5) and a free text (6)."""
    app.config["BOARD_CHECK_SECONDS"] = 0
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "sc"), ("question", "Womit fahren Sie?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Auto"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Rad"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Sonstiges, und zwar:"), ("option_text", "c"),
        ("option_id", "d"), ("option_code", "4"), ("option_label", "Bahn")]))
    add(client, "multiple_choice")                                  # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "mc"), ("question", "Was besitzen Sie?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Fahrrad"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Lastenrad")]))
    add(client, "likert")                                           # 4
    client.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "TA"), ("question", "Wie sehr stimmen Sie zu?"), ("points", "5"), ("labeling", "full"), ("level", "metric"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Technik hilft"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "Technik stört"), ("stmt_reverse", "s2")]))
    add(client, "text_input")                                       # 5
    client.post("/surveys/1/editor/items/5", data={"code": "km", "question": "Kilometer pro Tag?", "validation": "integer"})
    add(client, "text_input")                                       # 6
    client.post("/surveys/1/editor/items/6", data={"code": "frei", "question": "Was noch?"})
    return client


def _board(client, template="results", **data):
    client.post(f"{D}/new", data={"template": template, **data})
    return 1


def _link(client, **data):
    client.post(f"{D}/1/link", data={"valid": "30", **data})
    with client.application.app_context():
        return db.session.scalars(db.select(BoardShare).order_by(BoardShare.id.desc())).first().token


def _stranger(app):
    return app.test_client()


def _fill(app):
    """12 completed participations: 7 by car, 4 by bike (one typed text), 1 other; everyone owns a bike."""
    _answer(app, {"2": "1", "3": ["1"], "4": {"s1": 4, "s2": 2}, "5": 10, "6": "Geheimtext Alpha"}, n=7)
    _answer(app, {"2": "2", "3": ["1", "2"], "4": {"s1": 5, "s2": 1}, "5": 20}, n=4)
    _answer(app, {"2": {"v": "3", "t": {"3": "Einrad Zeppelin"}}, "3": ["1"], "4": {"s1": 1, "s2": 5}, "5": 95})


# --- the rules for small numbers ------------------------------------------------------------------------


def test_small_cells_are_put_together_so_nothing_follows_by_subtracting():
    rows, merged, ok = boards.merge_small([("A", 20), ("B", 3), ("C", 4), ("D", 0)])
    assert rows == [("A", 20), ("D", 0), (boards.OTHERS, 7)] and merged and ok
    # one small answer alone would follow from the sum: the next smallest joins it
    rows, merged, ok = boards.merge_small([("A", 20), ("B", 9), ("C", 2)])
    assert rows == [("A", 20), (boards.OTHERS, 11)] and ok
    # nothing left to tell apart: the tile shows nothing
    assert boards.merge_small([("A", 20), ("B", 2)])[2] is False
    assert boards.merge_small([("A", 20), ("B", 5)]) == ([("A", 20), ("B", 5)], False, True)


def test_levels_of_a_scale_join_their_neighbour():
    assert boards.merge_adjacent([10, 2, 8, 0, 12]) == [(0, 0, 10), (1, 2, 10), (3, 3, 0), (4, 4, 12)]
    assert boards.merge_adjacent([1, 0, 0, 30]) == [(0, 3, 31)]
    assert boards.merge_adjacent([6, 7, 8]) == [(0, 0, 6), (1, 1, 7), (2, 2, 8)]


def test_classes_of_numbers_never_name_the_lowest_and_highest_answer():
    numbers = [3.5, 18.2, 19.9, 21.0, 22.4, 23.3, 24.8, 26.1, 27.7, 29.9, 31.2, 250.75]
    rows = boards.classes(numbers)
    assert sum(c for _, c in rows) == len(numbers)
    text = " ".join(label for label, _ in rows)
    assert "250" not in text and "3,5" not in text and rows[-1][0].startswith("ab ")
    assert boards.classes([1, 2, 2, 3, 3, 3], scale=True) == [("1", 1), ("2", 2), ("3", 3)]   # stars: one class each
    assert "95" not in str(boards.classes([10] * 7 + [20] * 4 + [95]))


# --- the designer ---------------------------------------------------------------------------------------


def test_the_designer_lists_what_can_be_shown_and_says_why_not(app, study):
    with app.app_context():
        rows = {r["key"]: r for g in boards.candidates(db.session.get(Survey, 1)) for r in g["rows"]}
    assert [v for v, _ in rows["item:2"]["views"]] == ["bars", "columns", "donut", "table"]
    assert [v for v, _ in rows["item:4"]["views"]] == ["stacked", "means", "table"]
    assert [v for v, _ in rows["item:5"]["views"]] == ["number", "dist", "table"]
    assert [v for v, _ in rows["item:6"]["views"]] == ["cloud", "table"]                     # free text: a word cloud (L4)
    assert "item:1" not in rows                                     # the consent is not offered at all (Ralf, 2026-10-03)
    assert "field:count" in rows and "field:days" in rows


def test_templates_and_saving_the_form(app, study):
    _board(study, "results")
    with app.app_context():
        board = db.session.get(Board, 1)
        assert board.name == "Ergebnisbericht"
        assert [t["key"] for t in boards.tiles_of(board)] == ["field:count", "item:2", "item:3", "item:4", "item:5"]
    page = study.get(f"{D}/1").data.decode()
    assert "Womit fahren Sie?" in page and 'value="item:6" disabled' not in page and "data-board-frame" in page
    # the form: bike question first as a ring over the whole row, the number as classes, the Likert gone
    r = study.post(f"{D}/1", headers={"X-Requested-With": "fetch"}, data=MultiDict([
        ("autosave", "1"), ("name", "Für die Stadt"), ("order", "item:3,item:2"), ("pick", "item:2"), ("pick", "item:3"), ("pick", "item:5"),
        ("view-item:2", "donut"), ("w-item:2", "12"), ("pct-item:2", "1"), ("title-item:2", "Verkehrsmittel"),
        ("view-item:3", "nope"), ("n-item:3", "1")]))
    # the page saves by itself (app/autosave.py): the answer says so and brings the form, drawn anew
    saved = r.get_json()
    assert saved["status"] == "saved" and 'id="board-form"' in saved["form"] and "<html" not in saved["form"]
    assert "data-autosave-redraw" in saved["form"] and 'name="name"' not in saved["form"]   # the name stands in the right column
    with app.app_context():
        board = db.session.get(Board, 1)
        tiles = boards.tiles_of(board)
        assert board.name == "Für die Stadt" and [t["key"] for t in tiles] == ["item:3", "item:2", "item:5"]
        assert tiles[1] == {"key": "item:2", "view": "donut", "w": 12, "x": 0, "pct": True, "n": False, "by": "", "title": "Verkehrsmittel"}
        assert tiles[0]["view"] == "bars" and tiles[2]["view"] == "number"    # unknown view: the first one
    assert study.get(f"{D}/").data.decode().count(">Für die Stadt<") == 1   # one row (the name is also in the question before deleting)


def test_no_question_comes_by_itself_and_deleted_ones(app, study):
    """Ralf, 2026-10-03: no automatism, a new question never comes onto a dashboard by itself (the old option
    "also future ones" is gone; a stale form that still sends it changes nothing)."""
    _board(study, "empty")
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("future", "1"), ("pick", "item:2"), ("pick", "item:3")]))
    add(study, "single_choice")                                     # 7: stays off the dashboard
    study.post("/surveys/1/editor/items/7", data=MultiDict([("code", "neu"), ("question", "Neu?"), ("option_id", "a"),
                                                            ("option_code", "1"), ("option_label", "Ja")]))
    with app.app_context():
        board = db.session.get(Board, 1)
        assert [t["key"] for t in boards.tiles_of(board)] == ["item:2", "item:3"] and "future" not in (board.settings or {})
    assert 'name="future"' not in study.get(f"{D}/1").data.decode()
    study.post("/surveys/1/editor/items/3/delete")
    with app.app_context():
        assert [t["key"] for t in boards.tiles_of(db.session.get(Board, 1))] == ["item:2"]
    assert "nicht mehr gibt" in study.get(f"{D}/1").data.decode()


def test_old_future_option_becomes_fixed_tiles_at_startup(app, study):
    _board(study, "empty")
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2")]))
    with app.app_context():
        board = db.session.get(Board, 1)
        board.settings = {"future": True}                           # as before the option was removed
        db.session.commit()
        boards.settle_future()
        board = db.session.get(Board, 1)
        assert [t["key"] for t in boards.tiles_of(board)] == ["item:2", "item:3", "item:4", "item:5"] and board.settings == {}


def test_roles(app, study):
    _board(study)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="Vera", role="viewer", position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Vera")
    other.post("/terms/", data={"accept": "1"})
    assert other.get(f"{D}/1").status_code == 200 and other.get(f"{D}/1/view").status_code == 200   # "Ansehen" sees it
    other.post(f"{D}/1", data={"name": "Gekapert"})
    other.post(f"{D}/new", data={"template": "empty"})
    other.post(f"{D}/1/delete")
    with app.app_context():
        assert [b.name for b in db.session.get(Survey, 1).boards] == ["Ergebnisbericht"]
        db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER)).role = "editor"
        db.session.commit()
    other.post(f"{D}/1", data={"name": "Umbenannt"})                 # "Bearbeiten" builds ...
    other.post(f"{D}/1/link", data={"valid": "7"})                   # ... but nothing leaves the survey without "Admin"
    with app.app_context():
        assert db.session.get(Board, 1).name == "Umbenannt" and db.session.scalar(db.select(BoardShare)) is None
    assert "Rolle „Admin“" in other.get(f"{D}/1").data.decode()
    assert _stranger(app).get(f"{D}/1/view").status_code in (302, 401)


# --- what a link shows ----------------------------------------------------------------------------------


def test_a_link_shows_the_dashboard_without_signing_in(app, study):
    _board(study)
    token = _link(study, label="Stadt")
    _fill(app)
    guest = _stranger(app)
    r = guest.get(f"/d/{token}")
    page = r.data.decode()
    assert r.status_code == 200 and r.headers["X-Robots-Tag"] == "noindex, nofollow" and r.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in r.headers
    assert "Womit fahren Sie?" in page and "Auto" in page and "58 %" in page and "12</span> Teilnahmen" in page
    assert "/surveys/" not in page and "/dashboard/" not in page                     # no way into the application
    # counted, not who
    with app.app_context():
        share = db.session.scalar(db.select(BoardShare))
        assert share.opens == 1 and share.opened_at is not None
    assert "1-mal geöffnet" in study.get(f"{D}/1").data.decode()


def test_no_texts_no_single_answers_no_small_cells(app, study):
    _board(study)
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("pick", "item:3"), ("pick", "item:4"), ("pick", "item:5"),
                                         ("view-item:5", "dist"), ("n-item:5", "1"), ("pct-item:5", "1")]))
    token = _link(study)
    _fill(app)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "Zeppelin" not in page and "Geheimtext" not in page                       # typed texts never
    # 7 car, 4 bike, 1 other: bike and other are small and become one row; the single "other" is not shown
    assert "Weitere Antworten" in page and "Sonstiges" not in page and "Rad</span>" not in page
    assert "seltene Antworten sind zusammengefasst" in page
    # multiple choice: all 12 own a bike, 4 a cargo bike (a small cell: no number)
    assert "unter 5" in page
    # the number: mean and classes, never the highest answer (looked for in the tiles: the address of the page is random)
    assert "95" not in _stranger(app).get(f"/d/{token}/state").get_json()["html"]
    with app.app_context():
        board = db.session.get(Board, 1)
        with open(boards._path(board), encoding="utf-8") as f:
            kept = f.read()
    # the kept state itself holds nothing else either
    assert "Zeppelin" not in kept and "Geheimtext" not in kept and '"_' not in kept
    state = json.loads(kept)
    km = next(t for t in state["tiles"] if t["key"] == "item:5")
    assert "95" not in " ".join(b["label"] for b in km["bins"]) and set(km["rows"][0]) == {"label", "n", "few", "mean", "median", "sd", "places", "md_places"}
    sc = next(t for t in state["tiles"] if t["key"] == "item:2")
    assert [(r["label"], r["count"]) for r in sc["rows"]] == [("Auto", 7), ("Bahn", 0), ("Weitere Antworten", 5)]
    # answers without an order are sorted, the most frequent first; rows without a number come last
    mc = next(t for t in state["tiles"] if t["key"] == "item:3")
    assert [(r["label"], r.get("count")) for r in mc["rows"]] == [("Fahrrad", 12), ("Lastenrad", None)]
    likert = next(t for t in state["tiles"] if t["key"] == "item:4")
    # the Likert statements: 7, 4 and 1 answers on three levels; the small ones join their neighbours until one
    # group is left, which says nothing: the tile stays empty rather than showing a level with one answer
    assert likert["few"] and all(row["few"] and "segs" not in row for row in likert["rows"])
    _answer(app, {"4": {"s1": 2, "s2": 4}}, n=6)                    # six more on another level
    with app.app_context():
        likert = next(t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"] if t["key"] == "item:4")
    assert [(s["a"], s["b"], s["count"]) for s in likert["rows"][0]["segs"]] == [(0, 1, 7), (2, 2, 0), (3, 4, 11)]
    # the reversed statement is stored recoded and shown as it was answered: 1 (4x), 2 (7x), 4 (6x), 5 (1x)
    assert [(s["a"], s["b"], s["count"]) for s in likert["rows"][1]["segs"]] == [(0, 1, 11), (2, 2, 0), (3, 4, 7)]
    assert likert["merged"] and likert["rows"][1]["reverse"]


def test_too_few_answers_show_nothing(app, study):
    _board(study)
    token = _link(study)
    _answer(app, {"2": "1", "3": ["1"], "4": {"s1": 4, "s2": 2}, "5": 10}, n=9)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert page.count(FEW) == 4 and "9</span> Teilnahmen" in page and "Auto" not in page
    # the designer's preview shows invented numbers there, marked; what a link shows never does
    preview = study.get(f"{D}/1/view?beispiel=1").data.decode()
    assert "Beispielwerte" in preview and FEW not in preview
    assert "Beispielwerte" not in study.get(f"{D}/1/view").data.decode()
    _answer(app, {"2": "1", "3": ["1"], "4": {"s1": 4, "s2": 2}, "5": 10})
    assert FEW not in _stranger(app).get(f"/d/{token}").data.decode()


def test_only_the_data_pool_without_what_quality_control_marks(app, study):
    _board(study)
    study.post("/surveys/1/pool", data={"source": "real"})   # a draft pools its dummies until a pool is saved
    token = _link(study)
    _fill(app)
    _answer(app, {"2": "4"}, n=30, source="dummy")
    _answer(app, {"2": "4"}, n=30, status=Response.ACTIVE)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "12</span> Teilnahmen" in page and "b-band" not in page
    study.post("/surveys/1/quality", data={"source": ["real", "dummy"]})
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "42</span> Teilnahmen" in page and "Test- oder Beispieldaten" in page
    # the figures of the fieldwork count the same sources: 12 real and 30 example participations completed, 30 more begun
    assert "72 begonnen · 42 abgeschlossen" in page
    study.post("/surveys/1/quality", data={"source": ["dummy"]})
    assert "30 begonnen · 30 abgeschlossen" in _stranger(app).get(f"/d/{token}").data.decode()


def test_revoked_run_out_unknown_and_archived_links_look_the_same(app, study):
    _board(study)
    token, second = _link(study), _link(study)
    guest = _stranger(app)
    assert guest.get(f"/d/{token}").status_code == 200
    study.post(f"{D}/1/link/1/withdraw")
    with app.app_context():
        db.session.get(BoardShare, 2).expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    pages = [guest.get(f"/d/{t}") for t in (token, second, "nichtda", "x" * 80)]
    assert {p.status_code for p in pages} == {404} and len({p.data for p in pages}) == 1
    assert guest.get(f"/d/{token}/state").status_code == 404
    third = _link(study)
    assert guest.get(f"/d/{third}").status_code == 200
    study.post("/surveys/1/archive", data={"archived": "1"})
    assert guest.get(f"/d/{third}").status_code == 404
    with app.app_context():
        assert db.session.get(Survey, 1).archived


def test_how_long_a_link_works(app, study):
    _board(study)
    with app.app_context():
        db.session.get(Survey, 1).field_end = utcnow() + timedelta(days=10)
        db.session.commit()
    study.post(f"{D}/1/link", data={"valid": "field"})
    study.post(f"{D}/1/link", data={"valid": "7"})
    study.post(f"{D}/1/link", data={"valid": "9999"})                # not on the list: as "field"
    with app.app_context():
        days = [round((s.expires_at - utcnow()).total_seconds() / 86400) for s in db.session.scalars(db.select(BoardShare).order_by(BoardShare.id))]
    assert days == [24, 7, 24]
    # without an end: works until it is revoked
    study.post(f"{D}/1/link", data={"valid": "never", "label": "Dauerhaft"})
    with app.app_context():
        share = db.session.scalars(db.select(BoardShare).order_by(BoardShare.id.desc())).first()
        assert share.expires_at == boards.UNLIMITED
        token, share_id = share.token, share.id
    page = _stranger(app).get(f"/d/{token}")
    assert page.status_code == 200 and "Der Link gilt bis" not in page.data.decode()
    assert "unbegrenzt gültig" in study.get(f"{D}/1").data.decode()
    study.post(f"{D}/1/link/{share_id}/withdraw")
    assert _stranger(app).get(f"/d/{token}").status_code == 404


def test_a_survey_can_have_a_hundred_dashboards(app, study):
    assert boards.MAX_BOARDS == 100
    for _ in range(12):
        study.post(f"{D}/new", data={"template": "empty"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert len(survey.boards) == 12                              # more than the ten of before
        survey.boards.extend(Board(name=f"B{n}", tiles=[], position=20 + n) for n in range(87))
        db.session.commit()
    for _ in range(2):                                               # the hundredth, and one too many
        study.post(f"{D}/new", data={"template": "empty"})
    with app.app_context():
        assert len(db.session.get(Survey, 1).boards) == 100
    assert "Höchstens 100 Dashboards" in study.get(f"{D}/").data.decode()


def test_the_open_page_gets_a_new_state_with_every_participation(app, study):
    _board(study)
    token = _link(study)
    _fill(app)
    guest = _stranger(app)
    first = guest.get(f"/d/{token}/state").get_json()
    assert first["v"] and "Womit fahren Sie?" in first["html"] and first["n"] == 12
    assert guest.get(f"/d/{token}/state?v={first['v']}").get_json() == {"v": first["v"]}     # nothing new: nothing sent
    _answer(app, {"2": "1", "3": ["1"], "4": {"s1": 4, "s2": 2}, "5": 10})
    second = guest.get(f"/d/{token}/state?v={first['v']}").get_json()
    assert second["v"] != first["v"] and second["n"] == 13 and "62 %" in second["html"]
    # changing the dashboard gives a new state as well
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2")]))
    third = guest.get(f"/d/{token}/state?v={second['v']}").get_json()
    assert third["v"] != second["v"] and "Was besitzen Sie?" not in third["html"]


def test_many_open_dashboards_cost_one_computation(app, study, monkeypatch):
    _board(study)
    token = _link(study)
    _fill(app)
    calls = []
    build = boards.build
    monkeypatch.setattr(boards.compute, "build", lambda board, lang=None, only=None, **kw: calls.append(1) or build(board, lang, only, **kw))
    guest = _stranger(app)
    for _ in range(5):
        guest.get(f"/d/{token}")
        guest.get(f"/d/{token}/state")
    assert len(calls) == 1
    # while another request computes (its lock is there), the state from before is shown
    _answer(app, {"2": "1"})
    with app.app_context():
        lock = boards._path(db.session.get(Board, 1)) + ".lock"
    open(lock, "w").close()
    assert "12</span> Teilnahmen" in guest.get(f"/d/{token}").data.decode() and len(calls) == 1
    os.remove(lock)
    assert "13</span> Teilnahmen" in guest.get(f"/d/{token}").data.decode() and len(calls) == 2


def test_requests_for_links_are_limited_per_sender(app, study):
    _board(study)
    token = _link(study)
    app.config.update(BOARD_LIMIT=3, BOARD_WINDOW=600)
    guest = _stranger(app)
    codes = [guest.get(f"/d/{token}/state", environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert guest.get(f"/d/{token}/state", environ_base={"REMOTE_ADDR": "203.0.113.10"}).status_code == 200


# --- quotas, field figures, lecture hall ----------------------------------------------------------------


def test_field_figures_and_quotas(app, study):
    from app.models import Quota, QuotaCell

    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.max_responses = 48
        survey.quotas.append(Quota(code="gender", name="Geschlecht", cells=[
            QuotaCell(code="w", name="Frauen", target=20, done=5), QuotaCell(code="m", name="Männer", target=20, done=20)]))
        db.session.commit()
    _board(study, "field")
    token = _link(study)
    _fill(app)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "Fortschritt zum Stichprobenziel" in page and "12 von 48" in page and "25 % des Stichprobenziels" in page
    assert "Geschlecht" in page and "5 von 20" in page and "20 von 20" in page and "b-full" in page
    assert "Teilnahmen pro Tag" in page and "5:00 Min." in page and "Abschlussquote" in page
    # only percent for the quota
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "quota:1"), ("view-quota:1", "bars"), ("pct-quota:1", "1")]))
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "25 %" in page and "5 von 20" not in page
    # the spread of the sample next to the spread of the targets: 5 and 20 of 25 reached, the targets half and half
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "quota:1"), ("view-quota:1", "compare"), ("pct-quota:1", "1"), ("n-quota:1", "1")]))
    with app.app_context():
        tile = boards.state(db.session.get(Board, 1), fresh=True)["tiles"][0]
        assert [(r["target_share"], r["done_share"]) for r in tile["rows"]] == [(0.5, 0.2), (0.5, 0.8)]
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "b-pairs" in page and "50 %<br>20 % · 5" in page and "50 %<br>80 % · 20" in page


def test_lecture_hall_shows_the_way_into_the_survey(app, study):
    _board(study)
    token = _link(study, hall="1")
    plain = _link(study)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "board-hall" in page and "Jetzt teilnehmen" in page and "<svg" in page and "/s/1" in page
    assert 'data-every="5"' not in page                               # a draft does not ask for a new state
    assert "board-hall" not in _stranger(app).get(f"/d/{plain}").data.decode()
    assert "board-hall" in study.get(f"{D}/1/view?hoersaal=1").data.decode()
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    assert 'data-every="5"' in _stranger(app).get(f"/d/{token}").data.decode()
    assert 'data-every="15"' in _stranger(app).get(f"/d/{plain}").data.decode()


def test_the_dashboard_wears_the_look_of_the_survey(app, study):
    _board(study)
    token = _link(study)
    with app.app_context():
        from app import theme

        survey = db.session.get(Survey, 1)
        survey.theme = {**theme.standard(), "preset": "custom", "colors": {**theme.STANDARD, "accent": "#aa3355"}}
        db.session.commit()
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "#aa3355" in page and "participant.css" in page and "board.css" in page


# --- copies ---------------------------------------------------------------------------------------------


def test_duplicating_and_the_survey_file_take_the_dashboards_without_links(app, study):
    _board(study)
    _link(study)
    study.post("/surveys/1/duplicate", data={"title": "Zweite", "with_people": "1"})
    with app.app_context():
        twin = db.session.get(Survey, 2)
        items = {i.code: i.id for b in twin.blocks for i in b.items}
        board = twin.boards[0]
        assert board.name == "Ergebnisbericht" and board.shares == []
        assert [t["key"] for t in boards.tiles_of(board)] == ["field:count"] + [f"item:{items[c]}" for c in ("sc", "mc", "TA", "km")]
        assert items["sc"] != 2
        data = transfer.dump(db.session.get(Survey, 1))
        assert "token" not in json.dumps(data["boards"])
        data["boards"].append({"name": "Fremd", "tiles": [{"key": "item:99999"}, {"key": "field:nope"}, {"key": "field:rate", "view": "<script>"}, "x"]})
    with app.test_request_context():
        new, _ = transfer.create(transfer.loads(json.dumps(data).encode()), db.session.get(User, 1), "Dritte")
        ids = {i.code: i.id for b in new.blocks for i in b.items}
        assert [t["key"] for t in boards.tiles_of(new.boards[0])] == ["field:count"] + [f"item:{ids[c]}" for c in ("sc", "mc", "TA", "km")]
        assert boards.tiles_of(new.boards[1]) == [{"key": "field:rate", "view": "number", "w": 6, "x": 0, "pct": True, "n": True, "by": "", "title": ""}]
        assert new.boards[0].shares == []


def test_deleting_a_dashboard_ends_its_links(app, study):
    _board(study)
    token = _link(study)
    _fill(app)
    guest = _stranger(app)
    assert guest.get(f"/d/{token}").status_code == 200
    with app.app_context():
        path = boards._path(db.session.get(Board, 1))
    assert os.path.exists(path)
    study.post(f"{D}/1/delete")
    assert guest.get(f"/d/{token}").status_code == 404 and not os.path.exists(path)
    with app.app_context():
        assert db.session.scalar(db.select(BoardShare)) is None


# --- further question types (stage 2) -------------------------------------------------------------------


@pytest.fixture
def more(app, client, survey):
    """Federal state (2), postal code (3), ranking (4), constant sum (5), date (6)."""
    app.config["BOARD_CHECK_SECONDS"] = 0
    add(client, "federal_state")                                    # 2
    client.post("/surveys/1/editor/items/2", data={"code": "land", "question": "Wo wohnen Sie?"})
    add(client, "postal_code")                                      # 3
    client.post("/surveys/1/editor/items/3", data={"code": "plz", "question": "Ihre Postleitzahl?"})
    for number, type_, code in ((4, "ranking", "rang"), (5, "constant_sum", "summe")):
        add(client, type_)
        client.post(f"/surveys/1/editor/items/{number}", data=MultiDict([
            ("code", code), ("question", "Was ist wichtig?"), ("total", "100"),
            ("option_id", "a"), ("option_code", "1"), ("option_label", "Preis"),
            ("option_id", "b"), ("option_code", "2"), ("option_label", "Tempo"),
            ("option_id", "c"), ("option_code", "3"), ("option_label", "Komfort")]))
    add(client, "date_time")                                        # 6
    client.post("/surveys/1/editor/items/6", data={"code": "tag", "question": "Wann zuletzt?", "parts": "month"})
    return client


def test_states_ranks_shares_and_dates(app, more):
    _board(more)
    with app.app_context():
        rows = {r["key"]: r for g in boards.candidates(db.session.get(Survey, 1)) for r in g["rows"]}
        assert [[v for v, _ in rows[f"item:{n}"]["views"]] for n in (2, 3, 4, 5, 6)] == [
            ["map", "bars", "table"], ["map", "bars", "table"], ["means", "table"], ["means", "table"], ["columns", "table"]]
    token = _link(more)
    _answer(app, {"2": "5", "3": "52062", "4": ["2", "1", "3"], "5": {"1": 50, "2": 30, "3": 20}, "6": "2024-03"}, n=8)
    _answer(app, {"2": "9", "3": "80331", "4": ["2", "3", "1"], "5": {"1": 10, "2": 60, "3": 30}, "6": "2024-04"}, n=6)
    _answer(app, {"2": "11", "3": "10115", "4": ["1", "2", "3"], "5": {"1": 98, "2": 1, "3": 1}, "6": "2025-11"}, n=2)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    tiles_html = _stranger(app).get(f"/d/{token}/state").get_json()["html"]
    assert page.count('class="b-state') == 32 and "52062" not in tiles_html and "80331" not in tiles_html    # two maps, no postal code
    with app.app_context():
        tiles = {t["key"]: t for t in boards.state(db.session.get(Board, 1))["tiles"]}
    for key in ("item:2", "item:3"):
        land = tiles[key]
        # Berlin (2) is small and would follow from the sum: Bayern, the next smallest, joins it
        assert [(r["label"], r["count"]) for r in land["rows"] if r["count"]] == [("Nordrhein-Westfalen", 8)]
        assert land["rest"]["count"] == 8 and land["rest"]["labels"] == ["Bayern", "Berlin"] and land["n"] == 16
    assert tiles["item:3"]["from_plz"] and "Bundesland der Postleitzahl" in page and "Weitere Länder" in page
    # the credit of the postcode areas links OpenStreetMap and the ODbL, the footer the page of the sources, where
    # the derived database is to be had (ODbL 4.3 and 4.6; licence audit 2026-10-21, D-6)
    assert '<a href="https://opendatacommons.org/licenses/odbl/1-0/" target="_blank" rel="noopener">ODbL</a>' in page
    assert '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>' in page
    assert '/sources">Lizenzen und Datenquellen</a>' in page
    rang = tiles["item:4"]
    assert rang["ranks"] and [r["label"] for r in rang["rows"]] == ["Tempo", "Preis", "Komfort"] and rang["bounds"] == [1, 3]
    assert round(rang["rows"][0]["mean"], 3) == round((8 + 6 + 4) / 16, 3) and "Mittlerer Rang" in page
    summe = tiles["item:5"]
    assert round(summe["rows"][0]["mean"], 2) == round((8 * 50 + 6 * 10 + 2 * 98) / 16, 2) and "98" not in json.dumps(summe)
    # months: 8, 6 and 2 answers; the two in November 2025 join their neighbour
    assert [(r["label"], r["count"]) for r in tiles["item:6"]["rows"]] == [("03.2024", 8), ("04.2024 bis 11.2025", 8)]
    # in the designer's preview the map shows invented numbers before the fieldwork
    assert "b-state" in more.get(f"{D}/1/view?beispiel=1").data.decode()


def test_answers_without_an_order_are_sorted_by_frequency(app, study):
    _board(study)
    _link(study)
    _answer(app, {"2": "4"}, n=9)
    _answer(app, {"2": "1"}, n=6)
    _answer(app, {"2": "2"}, n=12)
    with app.app_context():
        tile = next(t for t in boards.state(db.session.get(Board, 1))["tiles"] if t["key"] == "item:2")
        assert [r["label"] for r in tile["rows"]] == ["Rad", "Bahn", "Auto", "Sonstiges, und zwar"]  # without the colon (U27)
    # a question set to "ordinal" keeps the order of its answers
    study.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "sc"), ("question", "Womit fahren Sie?"), ("level", "ordinal"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Auto"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Rad"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Sonstiges, und zwar:"), ("option_text", "c"),
        ("option_id", "d"), ("option_code", "4"), ("option_label", "Bahn")]))
    with app.app_context():
        tile = next(t for t in boards.state(db.session.get(Board, 1))["tiles"] if t["key"] == "item:2")
        assert [r["label"] for r in tile["rows"]] == ["Auto", "Rad", "Sonstiges, und zwar", "Bahn"]
    assert "nach Häufigkeit sortieren" not in study.get(f"{D}/1").data.decode()


def test_qr_code_fills_its_frame_and_the_preview_can_be_arranged(app, study):
    _board(study)
    hall = study.get(f"{D}/1/view?hoersaal=1").data.decode()
    qr = hall[hall.index('class="b-qr"'):]
    qr = qr[qr.index("<svg"):qr.index(">", qr.index("<svg"))]
    assert "viewBox" in qr and " width=" not in qr and " height=" not in qr      # no size of its own: it takes the frame's
    # the designer's preview lets tiles be dragged; the page of a link and the plain view do not
    assert "data-arrange" in study.get(f"{D}/1/view?beispiel=1").data.decode()
    assert "data-arrange" not in study.get(f"{D}/1/view").data.decode()
    assert "data-arrange" not in _stranger(app).get(f"/d/{_link(study)}").data.decode()
    # what a drop sends: the order of the tiles
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("order", "item:5,field:count,item:2"),
                                         ("pick", "field:count"), ("pick", "item:2"), ("pick", "item:5")]))
    with app.app_context():
        assert [t["key"] for t in boards.tiles_of(db.session.get(Board, 1))] == ["item:5", "field:count", "item:2"]


# --- MaxDiff, conjoint, price questions (stage 2) -------------------------------------------------------


def _modelled(app, client, setup, n=30, **kw):
    """The question of another test module (item 2) with dummy answers, on a dashboard read from the dummy pool."""
    from app import dummy

    app.config["BOARD_CHECK_SECONDS"] = 0
    setup(client, **kw)
    with app.app_context():
        assert dummy.generate(db.session.get(Survey, 1), n, seed=5) == n
        db.session.commit()
    client.post("/surveys/1/quality", data={"source": ["dummy"]})
    client.post(f"{D}/new", data={"template": "empty"})
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2")]))
    with app.app_context():
        board = db.session.get(Board, 1)
        return next(t for t in boards.state(board, fresh=True)["tiles"] if t["key"] == "item:2")


def test_maxdiff_shows_best_worst_scores(app, client, survey):
    from tests.analysis.test_maxdiff import setup

    tile = _modelled(app, client, setup, versions=2)
    assert tile["shape"] == "score" and tile["view"] == "bars" and 25 <= tile["n"] <= 30
    scores = [r["score"] for r in tile["rows"]]
    assert scores == sorted(scores, reverse=True) and all(-1 <= s <= 1 for s in scores) and len(scores) == len(tile["labels"])
    page = client.get(f"{D}/1/view").data.decode()
    assert "b-diverging" in page and "am besten" in page and " Personen" in page
    assert 'class="b-sources"' in page and "Louviere, J. J." in page and "Van Westendorp" not in page   # D-25


def test_conjoint_importance_is_the_one_of_the_results_page(app, client, survey):
    """The importance comes from the model of the results page, not from the span of the counted shares (Ralf,
    2026-10-12: 37/24/15/13/11 % on the results page, 41/26/14/10/9 % on the dashboard)."""
    from app import cbc_analysis, export
    from tests.analysis.test_conjoint import setup

    tile = _modelled(app, client, setup, n=60)
    assert tile["shape"] == "conjoint" and tile["view"] == "importance" and 50 <= tile["n"] <= 60
    assert tile["method"] == "logit" and "importance_hidden" not in tile
    with app.app_context():
        board = db.session.get(Board, 1)
        survey_ = board.survey
        pool = boards.pool_of(survey_)
        table = export.rows(survey_, pool["all"], pool["sources"], pool["speeder"], pool["speed_real"],
                            speed_sources=pool["sources"], with_hb=False, with_quality=False)
        table = table.keep(lambda r: r.get("qc_marked") != 1)
        item = next(i for b in survey_.blocks for i in b.items if i.id == 2)
        model = cbc_analysis.results(item, table, "de")["model"]
    expected = {a["label"]: a["importance"] for a in model["attributes"]}
    if model.get("price") and model["price"].get("importance") is not None:
        pa = item.kind.price_attribute(item)
        expected[item.kind.attributes(item, "de")[pa]["label"]] = model["price"]["importance"]
    shown = {row["label"]: row["importance"] for row in tile["importances"]}
    assert shown.keys() == expected.keys()
    assert all(abs(shown[k] - expected[k]) < 1e-12 for k in expected if expected[k] is not None)
    # an attribute whose span the data do not set (the design is drawn at random) stays without, as on the results page
    known = [r["importance"] for r in tile["importances"] if r["importance"] is not None]
    assert known == sorted(known, reverse=True) and (not known or abs(sum(known) - 1) < 1e-9)
    assert [r["importance"] is None for r in tile["importances"]] == sorted(r["importance"] is None for r in tile["importances"])
    # the table view names the importance per attribute too; a level chosen fewer than MIN_CELL times is hidden
    assert {a["label"]: a["importance"] for a in tile["attributes"]}.items() >= {
        k: v for k, v in shown.items() if k in {a["label"] for a in tile["attributes"]}}.items()
    assert all(lv["share"] is None or 0 <= lv["share"] <= 1 for a in tile["attributes"] for lv in a["levels"])
    page = client.get(f"{D}/1/view").data.decode()
    assert "(aggregiertes Logit-Modell)" in page and "Spanne der Anteile" not in page
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "levels"), ("n-item:2", "1")]))
    page = client.get(f"{D}/1/view").data.decode()
    assert "b-group" in page and "wenn die Stufe gezeigt wurde" in page


def test_conjoint_importance_from_a_current_hb_estimation(app, client, survey, monkeypatch):
    """Where the results page shows the HB estimation, the dashboard shows its importance as well, and says so."""
    from app import cbc_analysis, conjoint_hb
    from tests.analysis.test_conjoint import setup

    def hb_view(item, table, lang):
        attrs = item.kind.attributes(item, lang)
        share = 1 / len(attrs)
        return {"state": "current", "result": {"attributes": [
            {"code": a["code"], "label": a["label"], "importance": share * (1.5 if n == 0 else 1)}
            for n, a in enumerate(attrs)], "price": None}}

    monkeypatch.setattr(conjoint_hb, "view", hb_view)
    monkeypatch.setattr(cbc_analysis, "estimate", lambda *a, **k: pytest.fail("no logit beside a current HB estimation"))
    tile = _modelled(app, client, setup)
    assert tile["method"] == "hb" and tile["importances"][0]["importance"] > tile["importances"][1]["importance"]
    page = client.get(f"{D}/1/view").data.decode()
    assert "(Hierarchical Bayes)" in page and "wie auf der Ergebnisseite" not in page


def test_adaptive_conjoint_counts_the_preferred_variants(app, client, survey):
    from tests.analysis.test_acbc import setup

    tile = _modelled(app, client, setup, n=24)
    assert tile["shape"] == "conjoint" and tile["n"] >= boards.MIN_TILE and "bevorzugte Variante" in tile["basis"]
    assert tile["method"] in ("hb", "logit") and tile["importances"]
    known = [r["importance"] for r in tile["importances"] if r["importance"] is not None]
    assert abs(sum(known) - 1) < 1e-9
    for attribute in tile["attributes"]:
        shown = [lv["share"] for lv in attribute["levels"] if not lv.get("small")]
        if len(shown) == len(attribute["levels"]):
            assert abs(sum(shown) - 1) < 1e-9      # every winner has one level of each attribute
        else:  # a level few people prefer is not named, and never one alone (it would follow from the others)
            assert len(shown) <= len(attribute["levels"]) - 2 and sum(shown) < 1


def test_price_question_shows_its_points_never_the_curves(app, client, survey):
    from tests.analysis.test_psm import setup

    tile = _modelled(app, client, setup)
    assert tile["shape"] == "points" and [r["key"] for r in tile["rows"]] == ["OPP", "IPP", "PMC", "PME"]
    assert all(r["value"] and "€" in r["value"] for r in tile["rows"]) and set(tile) >= {"n", "rows"} and "curves" not in tile
    page = client.get(f"{D}/1/view").data.decode()
    assert "Optimaler Preis" in page and "b-points" in page and "<polyline" not in page
    assert page.count('<details class="sources">') == 1 and "NSS-Price Sensitivity Meter" in page   # P-4
    # too few people with consistent prices: nothing
    with app.app_context():
        rows = db.session.scalars(db.select(Response).order_by(Response.id)).all()
        for r in rows[5:]:
            db.session.delete(r)
        db.session.commit()
        tile = next(t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"] if t["key"] == "item:2")
        assert tile["few"] and all("value" not in r for r in tile["rows"])


def test_widths_of_the_finer_grid(app, study):
    _board(study, "field")
    with app.app_context():
        board = db.session.get(Board, 1)
        widths = {t["key"]: t["w"] for t in boards.tiles_of(board)}
        assert widths == {"field:count": 3, "field:rate": 3, "field:duration": 3, "field:days": 12}  # no sample target: no tile for it
        # tiles from before the finer grid: half a row, or the whole one
        board.tiles = [{"key": "field:count", "wide": True}, {"key": "field:rate"}, {"key": "field:days", "w": 5}, {"key": "item:2", "w": 4}]
        db.session.commit()
        assert [t["w"] for t in boards.tiles_of(board)] == [12, 6, 6, 4]
    form = study.get(f"{D}/1").data.decode()
    assert 'name="w-item:2"' in form and '<option value="4" selected>ein Drittel</option>' in form and "ganze Zeile" in form
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("pick", "item:3"), ("view-item:2", "bars"),
                                         ("w-item:2", "9"), ("view-item:3", "bars"), ("w-item:3", "7")]))
    with app.app_context():
        assert [t["w"] for t in boards.tiles_of(db.session.get(Board, 1))] == [9, 6]     # 7 is not on the list
    page = study.get(f"{D}/1/view").data.decode()
    assert "b-w9" in page and "b-w6" in page


def test_headings_and_texts_between_the_tiles(app, study):
    _board(study, "empty")
    # the designer's buttons send a new key and its kind
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("pick", "text:abc12345"), ("kind-text:abc12345", "heading")]))
    study.post(f"{D}/1", data=MultiDict([
        ("name", "A"), ("order", "text:abc12345,item:2"), ("pick", "item:2"), ("view-item:2", "bars"), ("w-item:2", "6"),
        ("pick", "text:abc12345"), ("kind-text:abc12345", "heading"), ("w-text:abc12345", "12"), ("text-text:abc12345", "Wege zur Arbeit"),
        ("pick", "text:zz99zz99"), ("kind-text:zz99zz99", "text"),
        ("pick", "text:../../etc"), ("pick", "text:" + "a" * 40)]))                 # no keys of this kind
    study.post(f"{D}/1", data=MultiDict([
        ("name", "A"), ("order", "text:abc12345,item:2,text:zz99zz99"), ("pick", "item:2"), ("view-item:2", "bars"), ("w-item:2", "6"),
        ("pick", "text:abc12345"), ("kind-text:abc12345", "heading"), ("w-text:abc12345", "12"), ("text-text:abc12345", "Wege zur Arbeit"),
        ("pick", "text:zz99zz99"), ("kind-text:zz99zz99", "text"), ("w-text:zz99zz99", "4"),
        ("text-text:zz99zz99", "**Hinweis:** Zahlen <script>alert(1)</script>\r\n\r\n- erstens\r\n- zweitens" + "x" * 3000)]))
    with app.app_context():
        tiles = boards.tiles_of(db.session.get(Board, 1))
        assert [(t["key"], t["w"]) for t in tiles] == [("text:abc12345", 12), ("item:2", 6), ("text:zz99zz99", 4)]
        assert tiles[0]["kind"] == "heading" and tiles[2]["kind"] == "text" and len(tiles[2]["text"]) == boards.MAX_TEXT
    token = _link(study)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert '<h2 class="b-section">Wege zur Arbeit</h2>' in page and "<strong>Hinweis:</strong>" in page and "<li>erstens</li>" in page
    assert "<script>alert" not in page and "&lt;script&gt;" in page                    # a text is escaped like the texts of a questionnaire
    form = study.get(f"{D}/1").data.decode()
    assert 'name="text-text:zz99zz99"' in form and "data-board-add" in form and 'value="text:abc12345"' in form
    # they go along into a copy and a file
    study.post("/surveys/1/duplicate", data={"title": "Zweite", "with_people": "1"})
    with app.app_context():
        twin = boards.tiles_of(db.session.get(Survey, 2).boards[0])
        assert [t["key"].split(":")[0] for t in twin] == ["text", "item", "text"] and twin[0]["text"] == "Wege zur Arbeit"
        data = transfer.dump(db.session.get(Survey, 1))
    with app.test_request_context():
        new, _ = transfer.create(transfer.loads(json.dumps(data).encode()), db.session.get(User, 1), "Dritte")
        again = boards.tiles_of(new.boards[0])
        assert [(t["key"], t.get("kind")) for t in again if t["view"] == "text"] == [("text:abc12345", "heading"), ("text:zz99zz99", "text")]
        assert again[2]["text"].startswith("**Hinweis:**") and again[2]["w"] == 4


def test_print_button_and_pictures_only_where_they_belong(app, study):
    _board(study)
    token = _link(study)
    public = _stranger(app).get(f"/d/{token}").data.decode()
    assert 'id="board-print"' in public and "data-images" not in public              # everyone may print; pictures are for members
    member = study.get(f"{D}/1/view").data.decode()
    assert 'id="board-print"' in member and "data-images" in member
    preview = study.get(f"{D}/1/view?beispiel=1").data.decode()
    assert 'id="board-print"' not in preview and "data-images" not in preview
    assert 'id="board-print"' not in study.get(f"{D}/1/view?hoersaal=1").data.decode()


def test_numbers_of_one_kind_are_rounded_alike(app, study):
    assert boards._num(3.0) == "3,00" and boards._num(2.666) == "2,67" and boards._num(12, 0) == "12" and boards._num(None) == "–"
    assert boards._num(93367.06, 0) == "93.367"  # the formatter of the results page (V70)
    _board(study)
    _answer(app, {"4": {"s1": 3, "s2": 3}, "5": 12}, n=10)           # means of exactly 3 and 12
    _answer(app, {"4": {"s1": 4, "s2": 3}}, n=5)
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:4"), ("pick", "item:5"), ("view-item:4", "means"), ("n-item:4", "1"),
                                         ("view-item:5", "number"), ("n-item:5", "1")]))
    page = study.get(f"{D}/1/view").data.decode()
    assert "M 3,33 · SD 0,49" in page and "M 3,00 · SD 0,00" in page          # not "M 3 · SD 0"
    # M and SD of whole answers (age, minutes) with one decimal (UI/UX audit 2026-10-09, X65), the median without (V71)
    assert ">12,0</span>" in page and "Median 12 " in page and "SD 0,0" in page


# --- languages ----------------------------------------------------------------------------------------------


def test_a_dashboard_speaks_the_languages_of_its_survey(app, study):
    from app.models import Item

    _board(study)
    token = _link(study)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.texts = {**item.texts, "en": {"question": "How do you travel?", "options": {"a": "Car", "b": "Bike", "c": "Other:", "d": "Train"}}}
        db.session.commit()
    _fill(app)
    guest = _stranger(app)
    german = guest.get(f"/d/{token}").data.decode()
    assert '<html lang="de"' in german and "Womit fahren Sie?" in german and "58 %" in german and "Weitere Antworten" in german
    assert german.count("?lang=") == 2 and 'aria-current="true">Deutsch<' in german        # the switch: the two languages of the survey
    english = guest.get(f"/d/{token}?lang=en").data.decode()
    assert '<html lang="en"' in english and "How do you travel?" in english and "Car" in english and "58%" in english
    assert "Other answers" in english and "rare answers are put together" in english and "Too few answers yet" in english
    assert "Womit fahren Sie?" not in english and "Weitere Antworten" not in english and "Teilnahmen" not in english and "Noch zu wenige" not in english
    assert "12</span> Participations" in english and "Summarised numbers only" in english and 'aria-current="true">English<' in english
    assert "state?lang=en" in english                                  # the open page asks for its own language
    live = guest.get(f"/d/{token}/state?lang=en").get_json()
    assert "How do you travel?" in live["html"] and "Womit" not in live["html"]
    # a number: decimal point in English, comma in German; every value with one decimal
    with app.app_context():
        board = db.session.get(Board, 1)
        assert os.path.basename(boards._path(board)) == "board-1.json" and os.path.basename(boards._path(board, "en")) == "board-1-en.json"
        assert os.path.exists(boards._path(board, "en")) and boards._path(board, "xx") == boards._path(board)
        km = {lang: next(t for t in boards.state(board, lang=lang)["tiles"] if t["key"] == "item:5") for lang in ("de", "en")}
        assert " bis unter " in km["de"]["bins"][0]["label"] and " to under " in km["en"]["bins"][0]["label"]
    assert "Mean" in english and "Mittelwert" not in english and "20.4" in english and "20,4" in german
    # a language the survey does not have: its own
    assert '<html lang="de"' in guest.get(f"/d/{token}?lang=fr").data.decode()
    # deleting the dashboard removes the states in every language
    with app.app_context():
        paths = [boards._path(db.session.get(Board, 1)), boards._path(db.session.get(Board, 1), "en")]
    study.post(f"{D}/1/delete")
    assert not any(os.path.exists(p) for p in paths)


def test_the_dashboards_own_words_in_a_language_of_the_catalog(app, study):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.languages = ["de", "en", "fr"]
        db.session.commit()
    _board(study, "field")
    token = _link(study)
    _fill(app)
    french = _stranger(app).get(f"/d/{token}?lang=fr").data.decode()
    assert '<html lang="fr"' in french and "Taux d’achèvement" in french and "Participations par jour" in french
    assert "12 commencées · 12 terminées" in french and "Chiffres agrégés uniquement" in french and "5:00 min" in french
    assert "Français" in french and "Abschlussquote" not in french


def test_a_tile_can_begin_further_right_and_leave_a_gap(app, study):
    _board(study, "empty")
    study.post(f"{D}/1", data=MultiDict([
        ("name", "A"), ("pick", "item:2"), ("pick", "item:3"), ("pick", "item:5"),
        ("view-item:2", "bars"), ("w-item:2", "4"), ("x-item:2", "1"),
        ("view-item:3", "bars"), ("w-item:3", "4"), ("x-item:3", "9"),           # a third at the right: the middle stays free
        ("view-item:5", "number"), ("w-item:5", "6"), ("x-item:5", "9")]))       # half a row cannot begin after two thirds
    with app.app_context():
        tiles = boards.tiles_of(db.session.get(Board, 1))
        assert [(t["key"], t["w"], t["x"]) for t in tiles] == [("item:2", 4, 1), ("item:3", 4, 9), ("item:5", 6, 0)]
        assert boards.start_of({"x": 2}, 4) == 0 and boards.start_of({"x": True}, 4) == 0 and boards.start_of({"x": 7}, 6) == 7
    page = study.get(f"{D}/1/view").data.decode()
    assert 'data-key="item:3" data-w="4" data-x="9" style="--x: 9; --w: 4"' in page and page.count("b-x ") == 2
    form = study.get(f"{D}/1").data.decode()
    assert 'name="x-item:3"' in form and '<option value="9" selected>nach zwei Dritteln</option>' in form
    start = form[form.index('name="x-item:5"'):]
    assert "nach zwei Dritteln" not in start[:start.index("</select>")]          # only the places the width leaves


# --- comparison between groups ------------------------------------------------------------------------------


def _split_board(study, by="item:2", **views):
    """A dashboard with the multiple choice (3), the Likert (4) and the number (5), each split by a question."""
    _board(study, "empty")
    fields = [("name", "A")]
    for key in ("item:3", "item:4", "item:5"):
        fields += [("pick", key), (f"view-{key}", views.get(key, "")), (f"n-{key}", "1"), (f"pct-{key}", "1"), (f"by-{key}", by)]
    study.post(f"{D}/1", data=MultiDict(fields))


def _tiles(app, lang=None):
    with app.app_context():
        return {t["key"]: t for t in boards.state(db.session.get(Board, 1), fresh=True, lang=lang)["tiles"]}


def test_tiles_split_by_the_answers_of_another_question(app, study):
    _split_board(study)
    with app.app_context():
        assert list(boards.groupers(db.session.get(Survey, 1))) == ["item:2"]         # single choice with two to eight answers
        assert [t["by"] for t in boards.tiles_of(db.session.get(Board, 1))] == ["item:2"] * 3
    # car: 12 people, bike: 11, train: 3 (too few for a group of its own), other: nobody
    _answer(app, {"2": "1", "3": ["1"], "4": {"s1": 4, "s2": 2}, "5": 10}, n=12)
    _answer(app, {"2": "2", "3": ["1", "2"], "4": {"s1": 2, "s2": 2}, "5": 30}, n=11)
    _answer(app, {"2": "4", "3": ["2"], "4": {"s1": 5, "s2": 1}, "5": 95}, n=3)
    tiles = _tiles(app)
    for tile in tiles.values():
        # the three by train are too few: they join the next smallest group, so their answers do not follow by subtracting
        assert [(g["label"], g["n"]) for g in tile["split"]["groups"]] == [("Auto", 12), ("Weitere Gruppen", 14)]
        assert tile["split"]["merged_groups"] and tile["by_title"] == "Womit fahren Sie?" and tile["n"] == 26
    likert = tiles["item:4"]["split"]
    assert [r["label"] for r in likert["rows"]] == ["Technik hilft", "Technik stört"] and likert["lo"] == 1 and likert["hi"] == 5
    # the three at level 5 are a small cell of the other group: levels 2 to 5 are put together in both groups, and
    # the means come from the levels as shown (B-1 of the audit 2026-10-05), else 12 x 4 + 14 x 2.64 gave them back
    assert likert["rows"][0]["means"] == [3.5, 3.5]
    number = tiles["item:5"]["split"]
    # one decimal, as the label (B-1): (11 x 30 + 3 x 95) / 14 = 43.93 reads 43.9
    assert number["rows"][0]["means"] == [10, 43.9] and "_xs" not in json.dumps(tiles["item:5"])
    multi = tiles["item:3"]["split"]
    # everyone by car owns a bike (12 of 12), nobody a cargo bike; in the other group all 14 own a cargo bike, and
    # 11 of 14 a bike: the three without one are a small cell, so that share is not shown
    assert {r["label"]: r["shares"] for r in multi["rows"]} == {"Fahrrad": [1, None], "Lastenrad": [0, 1]}
    token = _link(study)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert page.count("aufgeteilt nach: Womit fahren Sie?") == 3 and "kleine Gruppen sind zusammengefasst" in page
    assert "Weitere Gruppen" in page and "Bahn" not in page and "95" not in _stranger(app).get(f"/d/{token}/state").get_json()["html"]
    # as a table
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:4"), ("view-item:4", "table"), ("n-item:4", "1"), ("by-item:4", "item:2")]))
    table = study.get(f"{D}/1/view").data.decode()
    assert "<th class=\"b-num\">Auto" in table and "3,5" in table
    # the designer offers the question, but never a question for itself
    form = study.get(f"{D}/1").data.decode()
    assert 'name="by-item:4"' in form and '<option value="item:2" selected>Womit fahren Sie?</option>' in form
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "bars"), ("by-item:2", "item:2"),
                                         ("pick", "item:4"), ("view-item:4", "means"), ("by-item:4", "item:99")]))
    with app.app_context():
        assert [t["by"] for t in boards.tiles_of(db.session.get(Board, 1))] == ["", ""]
    # the question itself is never offered for its own tile; the other group variables are (app/groupvars.py)
    form = study.get(f"{D}/1").data.decode()
    own = form[form.index('name="by-item:2"'):].split("</select>")[0]
    assert '<option value="item:2"' not in own and '<option value="mc:3:1">' in own and '<option value="klassen:5">' in own


def test_too_small_groups_are_not_shown_apart(app, study):
    _split_board(study)
    _answer(app, {"2": "1", "3": ["1"], "4": {"s1": 4, "s2": 2}, "5": 10}, n=12)
    _answer(app, {"2": "2", "3": ["1"], "4": {"s1": 2, "s2": 2}, "5": 30}, n=6)      # one group of 12, the rest only 6
    tiles = _tiles(app)
    assert all("split" not in t and t["unsplit"] and not t["few"] for t in tiles.values())
    page = study.get(f"{D}/1/view").data.decode()
    assert page.count("Noch nicht aufgeteilt: zu wenige Antworten je Gruppe") == 3 and "aufgeteilt nach:" not in page


def test_answers_split_by_groups_have_no_small_cells(app, study):
    add(study, "single_choice")                                     # 7: the groups
    study.post("/surveys/1/editor/items/7", data=MultiDict([
        ("code", "g"), ("question", "Gruppe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "Alt"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Jung")]))
    _board(study, "empty")
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "bars"), ("n-item:2", "1"), ("by-item:2", "item:7")]))
    # old: 10 car, 8 bike, 2 train · young: 9 car, 3 bike, 8 train
    for group, cars, bikes, trains in (("1", 10, 8, 2), ("2", 9, 3, 8)):
        _answer(app, {"7": group, "2": "1"}, n=cars)
        _answer(app, {"7": group, "2": "2"}, n=bikes)
        _answer(app, {"7": group, "2": "4"}, n=trains)
    split = _tiles(app)["item:2"]["split"]
    # bike (3 among the young) and train (2 among the old) are small in one group: both are put together in both
    # (an answer nobody gave stays: a zero says nothing about anyone)
    assert split["cats"] == ["Auto", "Sonstiges, und zwar", "Weitere Antworten"] and split["merged"]
    assert split["shares"] == [[0.5, 0, 0.5], [0.45, 0, 0.55]] and [g["n"] for g in split["groups"]] == [20, 20]
    page = study.get(f"{D}/1/view").data.decode()
    assert "b-seg-plain" in page and "seltene Antworten sind zusammengefasst" in page and ">Rad<" not in page


def test_a_split_goes_along_into_copies_and_other_languages(app, study):
    _split_board(study)
    _answer(app, {"2": "1", "3": ["1"], "4": {"s1": 4, "s2": 2}, "5": 10}, n=12)
    _answer(app, {"2": "2", "3": ["1", "2"], "4": {"s1": 2, "s2": 2}, "5": 30}, n=11)
    english = study.get(f"{D}/1/view?lang=en").data.decode()
    assert "split by: " in english and "aufgeteilt nach" not in english
    study.post("/surveys/1/duplicate", data={"title": "Zweite", "with_people": "1"})
    with app.app_context():
        twin = db.session.get(Survey, 2)
        ids = {i.code: i.id for b in twin.blocks for i in b.items}
        assert {t["by"] for t in boards.tiles_of(twin.boards[0])} == {f"item:{ids['sc']}"}
        data = transfer.dump(db.session.get(Survey, 1))
    with app.test_request_context():
        new, _ = transfer.create(transfer.loads(json.dumps(data).encode()), db.session.get(User, 1), "Dritte")
        ids = {i.code: i.id for b in new.blocks for i in b.items}
        assert {t["by"] for t in boards.tiles_of(new.boards[0])} == {f"item:{ids['sc']}"}


# --- a link for one panel provider ------------------------------------------------------------------------


def _providers(app, client):
    """A field report with one question in it; 12 participations came with src=a, 6 with src=b, 3 without."""
    client.post("/surveys/1/panel", data={"param_name": "src", "param_mode": "store", "param_required": "0",
                                          "param_allowed": "a, b"})
    _board(client, template="field")
    with app.app_context():
        board = db.session.get(Board, 1)
        board.tiles = list(board.tiles) + [{"key": "item:2", "view": "bars", "w": 6}]
        now = utcnow()
        for value, n, open_ in (("a", 12, 4), ("b", 6, 0), (None, 3, 0)):
            for k in range(n + open_):
                done = k < n
                db.session.add(Response(
                    survey_id=1, source="real", is_preview=False, answers={"2": "1"}, params={"src": value} if value else None,
                    status=Response.COMPLETED if done else Response.ACTIVE, started_at=now - timedelta(minutes=5),
                    completed_at=now if done else None))
        db.session.commit()


def _figures(app, only=None):
    with app.app_context():
        found = boards.build(db.session.get(Board, 1), only=only)
        return found, {t["name"]: t for t in found["tiles"] if t.get("shape") == "field"}


def test_a_link_for_a_provider_counts_its_participations_and_shows_no_questions(app, study):
    _providers(app, study)
    everyone, figures = _figures(app)
    assert figures["count"]["value"] == "21" and any(t.get("key") == "item:2" for t in everyone["tiles"])
    own, figures = _figures(app, ("src", "a"))
    assert own["only"] == ["src", "a"] and own["n"] == 12
    assert figures["count"]["value"] == "12" and "16 begonnen · 12 abgeschlossen" in figures["count"]["sub"]
    assert figures["rate"]["value"] == "75 %" and sum(r["count"] for r in figures["days"]["rows"]) == 12
    assert not any(t.get("key") == "item:2" for t in own["tiles"])            # no results of questions
    assert _figures(app, ("src", "b"))[1]["count"]["value"] == "6"

    token = _link(study, qp="src=a")
    whole = _link(study)
    guest = _stranger(app)
    page = guest.get(f"/d/{token}").data.decode()
    assert "Nur Teilnahmen mit src = a" in page and "Womit fahren Sie?" not in page
    assert "16 begonnen · 12 abgeschlossen" in page
    assert "Womit fahren Sie?" not in guest.get(f"/d/{token}/state").get_json()["html"]
    page = guest.get(f"/d/{whole}").data.decode()
    assert "Nur Teilnahmen mit" not in page and "Womit fahren Sie?" in page and "25 begonnen · 21 abgeschlossen" in page
    designer = study.get(f"{D}/1").data.decode()
    assert "nur src = a" in designer and '<option value="src=b">src = b</option>' in designer

    # one more participation of the provider: its link follows
    with app.app_context():
        db.session.add(Response(survey_id=1, source="real", is_preview=False, answers={"2": "1"}, params={"src": "a"},
                                status=Response.COMPLETED, started_at=utcnow() - timedelta(minutes=5), completed_at=utcnow()))
        db.session.commit()
    assert "17 begonnen · 13 abgeschlossen" in guest.get(f"/d/{token}/state").get_json()["html"]


def test_a_link_only_for_a_provider_the_survey_knows(app, study):
    _providers(app, study)
    page = study.post(f"{D}/1/link", data={"valid": "30", "qp": "src=zz"}, follow_redirects=True).data.decode()
    assert "Diesen Anbieter gibt es nicht" in page
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(BoardShare.id))) == 0


def test_the_kept_states_of_providers_go_with_the_dashboard(app, study):
    _providers(app, study)
    token = _link(study, qp="src=b")
    _stranger(app).get(f"/d/{token}")
    with app.app_context():
        board = db.session.get(Board, 1)
        path = boards._path(board, None, ("src", "b"))
        assert os.path.exists(path) and path != boards._path(board)
        boards.forget(board)
        assert not os.path.exists(path)


# --- questions inside loops -----------------------------------------------------------------------------


@pytest.fixture
def looped(app, client, survey):
    """A single choice (2) and a number (3) in block 2, asked once per robot (two rows of the loop)."""
    from tests.editor.test_loops import loop_url

    app.config["BOARD_CHECK_SECONDS"] = 0
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "use"), ("question", "Würden Sie {{robot}} nutzen?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "ja"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "nein, {{robot}} nicht")]))
    add(client, "text_input")                                       # 3
    client.post("/surveys/1/editor/items/3", data={"code": "preis", "question": "Was darf er kosten?", "validation": "integer"})
    fill_table(client, loop_url(client), robot=["Pepper", "Nao"])
    with app.app_context():
        from app import loops
        from app.models import Block

        rows = [r["id"] for r in loops.config(db.session.get(Block, 2))["rows"]]
    return client, rows


def test_a_question_in_a_loop_is_a_tile_per_pass(app, looped):
    client, (pepper, nao) = looped
    with app.app_context():
        known = boards._known(db.session.get(Survey, 1))
    assert known[f"item:2@{pepper}"]["label"] == "Würden Sie Pepper nutzen?" and "Durchgang 1" in known[f"item:2@{pepper}"]["hint"]
    assert known[f"item:3@{nao}"]["label"] == "Was darf er kosten? · Nao"   # the same wording in every pass: the row follows
    assert "item:2" not in known
    _answer(app, {f"2#{pepper}": "1", f"2#{nao}": "2", f"3#{pepper}": 100, f"3#{nao}": 40}, n=8)
    _answer(app, {f"2#{pepper}": "2", f"2#{nao}": "2", f"3#{pepper}": 300, f"3#{nao}": 60}, n=6)
    _board(client)   # the template "results" takes every tile there is
    with app.app_context():
        tiles = {t["key"]: t for t in boards.build(db.session.get(Board, 1))["tiles"]}
    first, second = tiles[f"item:2@{pepper}"], tiles[f"item:2@{nao}"]
    assert first["title"] == "Würden Sie Pepper nutzen?" and first["n"] == 14
    assert {r["label"]: r["count"] for r in first["rows"]} == {"ja": 8, "nein, Pepper nicht": 6}
    assert {r["label"]: r["count"] for r in second["rows"]} == {"nein, Nao nicht": 14, "ja": 0}
    assert tiles[f"item:3@{nao}"]["title"] == "Was darf er kosten? · Nao"
    token = _link(client)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    assert "Würden Sie Pepper nutzen?" in page and "nein, Nao nicht" in page and "{{robot}}" not in page


def test_loop_tiles_keep_their_row_in_copies(app, looped):
    client, (pepper, nao) = looped
    assert boards.remap(f"item:2@{pepper}", {2: 7}, {}) == f"item:7@{pepper}"
    assert boards.remap("item:2@../x", {2: 7}, {}) is None and boards.remap("item:2", {2: 7}, {}) == "item:7"
    assert boards.item_ref(f"item:2@{nao}") == (2, nao) and boards.item_ref("quota:2") == (None, None)


# --- embedding and the end page -------------------------------------------------------------------------


def test_only_the_address_for_frames_may_be_framed_by_other_sites(app, study):
    _fill(app)
    _board(study)
    token = _link(study)
    plain = _stranger(app).get(f"/d/{token}")
    assert plain.headers["X-Frame-Options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in plain.headers["Content-Security-Policy"]
    framed = _stranger(app).get(f"/d/{token}/embed")
    assert framed.status_code == 200 and "X-Frame-Options" not in framed.headers
    assert "frame-ancestors *" in framed.headers["Content-Security-Policy"] and framed.headers["X-Robots-Tag"] == "noindex, nofollow"
    page = framed.data.decode()
    assert 'class="b-embedded"' in page and "Womit fahren Sie?" in page and 'id="board-print"' not in page
    assert _stranger(app).get("/d/gibtesnicht/embed").status_code == 404
    designer = study.get(f"{D}/1").data.decode()
    assert "In eine andere Seite einbetten" in designer and f"/d/{token}/embed" in designer and "&lt;iframe" in designer
    study.post(f"{D}/1/link/1/withdraw")
    assert _stranger(app).get(f"/d/{token}/embed").status_code == 404


def test_the_end_page_links_a_dashboard_made_for_it(app, study):
    from tests.conftest import CONSENT_OK, fill_consent

    fill_consent(study)
    _board(study)
    token = _link(study, end="1")
    assert "auf der Endseite" in study.get(f"{D}/1").data.decode()
    with app.app_context():
        board = db.session.get(Board, 1)
        assert boards.end_share(board).token == token and boards.end_share(board).label == "Endseite der Befragung"
    study.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        url = f"/s/{db.session.get(Survey, 1).code}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    for _ in range(8):
        page = p.post(url, data={"nav": "next"}, follow_redirects=True).data.decode()
        if "p-end" in page:
            break
    assert "p-end" in page and f"/d/{token}" in page and "Ergebnisse ansehen" in page
    # a revoked link is gone from the end page; a hall link never gets there
    study.post(f"{D}/1/link/1/withdraw")
    assert f"/d/{token}" not in p.get(url, follow_redirects=True).data.decode()
    hall = _link(study, end="1", hall="1")
    with app.app_context():
        assert boards.end_share(db.session.get(Board, 1)) is None
    assert f"/d/{hall}" not in p.get(url, follow_redirects=True).data.decode()


def test_the_copy_of_a_survey_has_no_link_on_its_end_page(app, study):
    _board(study)
    _link(study, end="1")
    with app.app_context():
        source = db.session.get(Survey, 1)
        new = Survey(title="Kopie", languages=["de"], default_language="de")
        boards.copy_to(source, new, {}, {})
        assert "end_share" in source.boards[0].settings and "end_share" not in new.boards[0].settings


# --- the same numbers as the results page (maths audit 2026-10-03, section 1) ------------------------------


def test_multiple_choice_shares_rest_on_those_who_saw_the_answer(app, study):
    """D-3: an option only some people saw is a share of those, as on the results page (50 %, not 25 %)."""
    _board(study)
    _answer(app, {"3": ["1", "2"]}, n=6)                            # cargo bike seen and ticked
    _answer(app, {"3": ["1"]}, n=6)                                 # seen, not ticked
    _answer(app, {"3": {"v": ["1"], "h": ["2"]}}, n=12)             # not shown (-77)
    tiles = _tiles(app)
    mc = tiles["item:3"]
    assert mc["n"] == 24 and mc["partial"]
    assert [(r["label"], r["count"], r["share"]) for r in mc["rows"]] == [("Fahrrad", 24, 1.0), ("Lastenrad", 6, 0.5)]
    page = study.get(f"{D}/1/view").data.decode()
    assert "Anteil an denen, die die Antwort gesehen haben" in page


def test_participations_left_out_by_quality_control_are_counted(app, study):
    """D-4: with unfinished participations in the data pool the tile said "−4 davon ... ausgeschlossen"."""

    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.data_pool = {"sources": ["real"], "all": True}
        now = utcnow()
        for seconds in [300] * 10 + [10]:                           # ten completed, one speeder
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"2": "1"},
                                    started_at=now - timedelta(seconds=seconds), completed_at=now))
        db.session.commit()
    _answer(app, {"2": "1"}, n=5, status=Response.ACTIVE)
    _board(study, "field")
    with app.app_context():
        count = next(t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"] if t["key"] == "field:count")
    assert count["value"] == "15" and count["sub"] == "16 begonnen · 11 abgeschlossen · 1 davon von der Qualitätssicherung ausgeschlossen"


def _ordinal_likert(study):
    study.post("/surveys/1/editor/items/4", data=MultiDict([
        ("code", "TA"), ("question", "Wie sehr stimmen Sie zu?"), ("points", "5"), ("labeling", "full"), ("level", "ordinal"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Technik hilft"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "Technik stört"), ("stmt_reverse", "s2")]))


def test_ordinal_scales_show_medians_not_means(app, study):
    """D-6: a scale set to "ordinal" gets the median (as the results page), no mean and SD, no view of means."""
    _ordinal_likert(study)
    _board(study, "empty")
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:4"), ("view-item:4", "means"), ("n-item:4", "1")]))
    with app.app_context():
        survey = db.session.get(Survey, 1)
        row = next(r for g in boards.candidates(survey) for r in g["rows"] if r["key"] == "item:4")
        assert "means" not in [v for v, _ in row["views"]]
        assert boards.tiles_of(db.session.get(Board, 1))[0]["view"] == "stacked"
    _answer(app, {"4": {"s1": 4, "s2": 2}}, n=7)
    _answer(app, {"4": {"s1": 2, "s2": 4}}, n=5)
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:4"), ("view-item:4", "table"), ("n-item:4", "1")]))
    tile = _tiles(app)["item:4"]
    levels = tile["levels"]
    # 5 on level 2, 7 on level 4: the median is level 4; the reversed statement the same answers, recoded
    assert [r["median"] for r in tile["rows"]] == [levels[3], levels[1]]
    assert all(r["mean"] is None and r["sd"] is None for r in tile["rows"])
    page = study.get(f"{D}/1/view").data.decode()
    assert '<th class="b-num">Md</th>' in page and '<th class="b-num">M</th>' not in page


def test_ordinal_scales_split_by_groups_compare_medians(app, study):
    _ordinal_likert(study)
    _split_board(study, **{"item:4": "stacked"})
    _answer(app, {"2": "1", "4": {"s1": 4, "s2": 2}}, n=12)
    _answer(app, {"2": "2", "4": {"s1": 2, "s2": 2}}, n=6)
    _answer(app, {"2": "2", "4": {"s1": 5, "s2": 2}}, n=6)
    split = _tiles(app)["item:4"]["split"]
    assert split["ordinal"]
    hilft = split["rows"][0]
    # car: all on 4; the others: half on 2, half on 5, so the median lies between them and both are named
    assert hilft["means"] == [4, 3.5] and hilft["texts"][1].count("–") == 1
    page = study.get(f"{D}/1/view").data.decode()
    assert "· Median" in page


def test_scale_tiles_say_when_mean_and_sd_are_estimated_and_swap_the_ends_of_reversed_statements(app, study):
    """D-16: mean and SD from levels put together are named as estimated in every view; a reversed statement
    (recoded mean) names its swapped ends."""
    _board(study, "empty")
    _fill(app)
    _answer(app, {"4": {"s1": 2, "s2": 4}}, n=6)
    for view in ("table", "means"):
        study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:4"), ("view-item:4", view), ("n-item:4", "1")]))
        page = study.get(f"{D}/1/view").data.decode()
        assert "aus den zusammengefassten Stufen geschätzt" in page, view
        tile = _tiles(app)["item:4"]
        assert f"umgepolt: links „{tile['levels'][-1]}“, rechts „{tile['levels'][0]}“" in page, view


def test_word_cloud_shows_only_words_named_often_enough(app, study):
    """Decision L4 (live concept): free text as a word cloud, a word only from MIN_CELL mentions
    on; longer texts never; not in a template, only when picked."""
    _board(study, "empty")
    study.post(f"{D}/1", data=MultiDict([("name", "Wolke"), ("pick", "item:6"), ("view-item:6", "cloud")]))
    _answer(app, {"6": "Fahrrad"}, n=4)
    _answer(app, {"6": "fahrrad "}, n=2)                                          # case and spaces do not count
    _answer(app, {"6": "zu Fuß"}, n=5)
    _answer(app, {"6": "Bus"}, n=4)                                               # 4: too rare, left out
    _answer(app, {"6": "Ich fahre meistens mit dem Rad zur Arbeit"}, n=6)       # a sentence: never a word
    token = _link(study)
    page = _stranger(app).get(f"/d/{token}").data.decode()
    cloud = page.split('class="b-cloud"')[1].split("</p>")[0]
    assert "Fahrrad" in cloud and "zu Fuß" in cloud and "Bus" not in cloud and "Arbeit" not in page
    assert "mindestens 5-mal" in page
    _board(study, "results")
    with app.app_context():
        assert "item:6" not in [t["key"] for t in boards.tiles_of(db.session.get(Board, 2))]


def test_designer_tiles_left_with_every_element_and_no_list_to_tick(app, study):
    """Ralf, 2026-10-03: the left column holds the tiles; every element not yet on the dashboard stands below with
    a "+"; no list to tick, the name moves to the right column."""
    _board(study, "results")
    html = study.get(f"{D}/1").data.decode()
    assert "data-board-include=" in html and "Weitere Kacheln" in html and "Auswählen" not in html
    assert 'name="name" form="board-form"' in html and 'type="checkbox" name="pick"' not in html


def test_a_price_question_in_a_loop_has_its_points_per_pass(app, client, survey):
    """Bucket list: the price sensitivity meter in a loop is a tile per pass, as the other question types."""
    from app import dummy
    from tests.editor.test_loops import loop_url
    from tests.analysis.test_psm import setup

    app.config["BOARD_CHECK_SECONDS"] = 0
    setup(client, question="Was darf {{robot}} kosten?")
    fill_table(client, loop_url(client), robot=["Pepper", "Nao"])
    with app.app_context():
        from app import loops
        from app.models import Block

        pepper, nao = [r["id"] for r in loops.config(db.session.get(Block, 2))["rows"]]
        survey = db.session.get(Survey, 1)
        known = boards._known(survey)
        assert "item:2" not in known and known[f"item:2@{nao}"]["shape"] == "points"
        assert known[f"item:2@{nao}"]["label"] == "Was darf Nao kosten?"
        assert dummy.generate(survey, 30, seed=5) == 30
        db.session.commit()
    client.post("/surveys/1/quality", data={"source": ["dummy"]})
    client.post(f"{D}/new", data={"template": "empty"})
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", f"item:2@{pepper}"), ("pick", f"item:2@{nao}")]))
    with app.app_context():
        tiles = {t["key"]: t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"]}
    for rid, name in ((pepper, "Pepper"), (nao, "Nao")):
        tile = tiles[f"item:2@{rid}"]
        assert tile["shape"] == "points" and tile["title"] == f"Was darf {name} kosten?" and not tile["few"]
        assert [r["key"] for r in tile["rows"]] == ["OPP", "IPP", "PMC", "PME"] and all("€" in r["value"] for r in tile["rows"])
    page = client.get(f"{D}/1/view").data.decode()
    assert "Was darf Pepper kosten?" in page and "{{robot}}" not in page


def test_results_page_says_the_dashboards_leave_out_test_runs(app, client, study):
    # a draft without a saved pool: test runs are in the pool of the results page, never on a dashboard (Ralf, 2026-10-06)
    _answer(app, {"2": "1"}, n=3, source="test")
    _answer(app, {"2": "2"}, n=12)
    html = client.get("/surveys/1/results?go=1").data.decode()
    assert "lassen die 3 Testdurchläufe weg" not in html                                   # no dashboard yet
    with app.app_context():
        db.session.add(Board(survey_id=1, name="B", tiles=[]))
        db.session.commit()
    html = client.get("/surveys/1/results?go=1").data.decode()
    assert "Die Dashboards lassen die 3 Testdurchläufe weg" in html


def test_dashboard_classes_follow_the_histogram_of_the_results_page():
    from app import results

    xs = [18 + (i * 7) % 82 for i in range(161)]
    bins, _ = boards._classes(xs)
    page = results.histogram([xs])[0]["bars"]
    grid = {(b["lo"], b["hi"]) for b in page}
    inner = [(a, b) for a, b, _ in bins if a is not None and b is not None]
    assert inner and set(inner) <= grid                                                # the same classes, ends open
    assert bins[0][0] is None                                                          # lowest answers in an open class


def test_periods_put_together_read_from_start_to_end():
    with boards.speaking("de"):
        assert boards._join("1910–1919", "1920–1929") == "1910–1929"
        assert boards._join("3.10.", "5.10.") == "3.10. bis 5.10."


def test_a_link_renders_each_state_once_for_every_phone(app, study, monkeypatch):
    """A lecture hall: every phone asks every few seconds; a new state is rendered once per process, not once per phone
    (Ralf, 2026-10-10). The members' view renders its own tiles (it shows tiles a link leaves out)."""
    _board(study)
    token = _link(study, label="Hörsaal", hall="1")
    _fill(app)
    boards._rendered.clear()
    calls = []
    real = boards.render_tiles
    monkeypatch.setattr(boards.render, "render_tiles", lambda board, state: calls.append(state["key"]) or real(board, state))
    phones = [_stranger(app) for _ in range(5)]
    seen = [phone.get(f"/d/{token}/state").get_json() for phone in phones]
    assert len(calls) == 1 and len({s["v"] for s in seen}) == 1 and all(s["html"] == seen[0]["html"] for s in seen)
    assert "Womit fahren Sie?" in seen[0]["html"]
    # a phone that has the state gets only the version
    assert "html" not in phones[0].get(f"/d/{token}/state?v={seen[0]['v']}").get_json()
    # one more participation: a new state, rendered once more
    _answer(app, {"2": "2", "3": ["1"], "4": {"s1": 3, "s2": 3}, "5": 5})
    again = [phone.get(f"/d/{token}/state").get_json() for phone in phones]
    assert len(calls) == 2 and again[0]["v"] != seen[0]["v"] and all(s["html"] == again[0]["html"] for s in again)
    study.get(f"{D}/1/state")
    assert len(calls) == 3


def test_two_people_in_the_designer_do_not_overwrite_each_other(app, study):
    """Code review 2026-10-15, I3: the designer's form carries the fingerprint of the dashboard (base); a save based
    on an outdated page is not applied and the page says who saved in between."""
    import re

    _board(study)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="Vera", role="editor", position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Vera")
    other.post("/terms/", data={"accept": "1"})
    mine = re.search(r'name="base" value="(\w+)"', study.get(f"{D}/1").data.decode()).group(1)
    theirs = re.search(r'name="base" value="(\w+)"', other.get(f"{D}/1").data.decode()).group(1)
    assert mine == theirs
    r = other.post(f"{D}/1", headers={"X-Requested-With": "fetch"},
                   data=MultiDict([("autosave", "1"), ("base", theirs), ("name", "Von Vera"), ("pick", "item:2")]))
    assert r.get_json()["status"] == "saved" and r.get_json()["base"] != theirs
    r = study.post(f"{D}/1", headers={"X-Requested-With": "fetch"},
                   data=MultiDict([("autosave", "1"), ("base", mine), ("name", "Meins"), ("pick", "item:3")]))
    assert r.get_json()["status"] == "conflict" and r.get_json()["who"] == "Vera"
    with app.app_context():
        board = db.session.get(Board, 1)
        assert board.name == "Von Vera" and [t["key"] for t in boards.tiles_of(board)] == ["item:2"]
    # the form sent without autosave: the page again, with the conflict and the own inputs
    html = study.post(f"{D}/1", data=MultiDict([("base", mine), ("name", "Meins"), ("pick", "item:3")])).data.decode()
    assert "Nicht gespeichert" in html and "Vera" in html
    # keeping the own version: the base of the saved one
    r = study.post(f"{D}/1", headers={"X-Requested-With": "fetch"},
                   data=MultiDict([("autosave", "1"), ("base", r.get_json()["base"]), ("name", "Meins"), ("pick", "item:3")]))
    assert r.get_json()["status"] == "saved"
    with app.app_context():
        assert db.session.get(Board, 1).name == "Meins"
