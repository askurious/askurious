"""Dashboards: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit_2026_10_12_a import E, Item, Response, Survey, add, boards, db, export, results, timedelta, utcnow  # noqa: F401
from tests.helpers.audit_cr8_a import app, client, survey  # noqa: F401
from tests.helpers.audit_cr8_j import read  # noqa: F401
from tests.helpers.audit_review_fixes import Board, MultiDict, _member, _split, _split_board, study as study__review_fixes, test_boards  # noqa: F401
from tests.helpers.audit_review_medium import study as study__review_medium  # noqa: F401


# --- from tests/audits/test_audit_2026_10_12_a.py: Audit Daten und Berechnungen 2026-10-12, package A (crashes and wrong figures): S-1, S-3, W-1, M-1, SEM-1, SEM-2, ---


def test_dashboard_number_tile_leaves_out_dont_know(app, client, survey):
    add(client, "rating")  # item 2
    client.post(f"{E}/items/2", data={"code": "zuf", "question": "Wie zufrieden?", "style": "slider", "min": "0",
                                      "max": "100", "step": "1", "dont_know": "1", "level": "metric"})
    with app.app_context():
        now = utcnow()
        for v in (60, 70, 80, -98, -98):
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                    answers={"2": v}, started_at=now - timedelta(minutes=5), completed_at=now))
        db.session.commit()
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        assert boards.shape_of(item)[0] == "num"
        table = export.rows(s, False, ("real",), None, True, with_hb=False, with_quality=False)
        tile = boards.tiles._numbers(item, table, "de")
        assert sorted(tile["rows"][0]["_xs"]) == [60.0, 70.0, 80.0] and tile["n"] == 3
        page = results.compute(s)
        col = next(c for e in page["items"] if e["item"].id == 2 for c in e["columns"])
        assert col["n"] == 3


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_e9_dashboard_back_under_a_number_taken_by_another_survey(app, client, survey):
    from app.models import Board

    client.post("/surveys/1/dashboards/new", data={"name": "A"})
    client.post("/surveys/1/dashboards/1/delete")
    client.post("/surveys/new", data={"title": "Zwei", "languages": ["de"], "default_language": "de"})
    client.post("/surveys/2/dashboards/new", data={"name": "B"})
    with app.app_context():
        board = db.session.scalar(db.select(Board).filter_by(name="B"))
        assert board.id != 1  # the number of the deleted dashboard is not given again
        board.id = 1  # as on a database from before: the other survey has the number by now
        db.session.commit()
    r = client.post("/surveys/1/editor/undo", follow_redirects=True)
    assert r.status_code == 200 and "Seine Nummer ist inzwischen vergeben" in r.data.decode()
    with app.app_context():
        assert [(b.survey_id, b.name) for b in db.session.scalars(db.select(Board))] == [(2, "B")]


# --- from tests/audits/test_cr8_j.py: Code review 2026-10-07, package J (tidying up): dead code, dead templates and CSS, one counter for the limits. ---


def test_f6_and_f19_dead_branches_are_gone():
    # the former parts of a redrawn form (data-autosave-part="<form id>") are gone; the attribute came back with a
    # user of its own, the parts the server draws after a save (autosave.part, pre-test 2026-10-20, 20)
    assert "data-autosave-part=\"' + form.id" not in read("app.js")
    board = read("board.js")
    assert '"board-refresh"' not in board and "boardImage" not in board and "window.boardRefresh" in board


# --- from tests/audits/test_review_fixes.py: What the code review of 2026-10-02 found and what holds since (code-review-2026-10-02.md, S1 to S7). ---


def test_split_tile_with_a_gone_answer_in_the_first_group(app, study__review_fixes):
    _split_board(study__review_fixes)
    test_boards._answer(app, {"7": "1", "2": "1"}, n=10)
    test_boards._answer(app, {"7": "1", "2": "9"}, n=6)   # code 9: an option that was removed later
    test_boards._answer(app, {"7": "2", "2": "1"}, n=12)
    test_boards._answer(app, {"7": "2", "2": "2"}, n=6)
    token = test_boards._link(study__review_fixes)
    assert test_boards._stranger(app).get(f"/d/{token}").status_code == 200
    for shares in _split(app).values():
        assert abs(sum(shares.values()) - 1) < 1e-9


def test_split_tile_with_a_gone_answer_in_a_later_group(app, study__review_fixes):
    _split_board(study__review_fixes)
    test_boards._answer(app, {"7": "1", "2": "1"}, n=12)
    test_boards._answer(app, {"7": "1", "2": "2"}, n=6)
    test_boards._answer(app, {"7": "2", "2": "1"}, n=10)
    test_boards._answer(app, {"7": "2", "2": "9"}, n=6)
    shares = _split(app)
    assert abs(sum(shares["Jung"].values()) - 1) < 1e-9  # the six answers "9" are part of the shares, as of n
    assert abs(sum(shares["Alt"].values()) - 1) < 1e-9


def test_dashboard_with_a_valid_link_is_changed_by_admins_only(app, study__review_fixes):
    """M4: an editor does not decide what a link shows that an admin has given out."""
    test_boards._board(study__review_fixes, "field")
    editor = _member(app, "editor")
    form = MultiDict([("name", "Feldbericht"), ("pick", "item:2")])
    editor.post(f"{test_boards.D}/1", data=MultiDict([("name", "Vorher"), ("pick", "field:count")]))
    with app.app_context():
        assert db.session.get(Board, 1).name == "Vorher"  # no link yet: the role "Bearbeiten" is enough
    token = test_boards._link(study__review_fixes)
    test_boards._fill(app)
    before = test_boards._stranger(app).get(f"/d/{token}").data.decode()
    # the page saving by itself (app/autosave.py) is told why nothing was saved
    refused = editor.post(f"{test_boards.D}/1", data=MultiDict([*form.items(multi=True), ("autosave", "1")])).get_json()
    assert refused["status"] == "invalid" and "hat gültige Links" in refused["message"]
    editor.post(f"{test_boards.D}/1", data=form)
    with app.app_context():
        assert db.session.get(Board, 1).name == "Vorher"
    page = editor.get(f"{test_boards.D}/1").data.decode()
    assert "hat gültige Links" in page and '<fieldset class="board-fieldset" disabled>' in page
    assert ("Womit fahren Sie?" in test_boards._stranger(app).get(f"/d/{token}").data.decode()) == ("Womit fahren Sie?" in before)
    study__review_fixes.post(f"{test_boards.D}/1", data=form)  # the admin
    with app.app_context():
        assert db.session.get(Board, 1).name == "Feldbericht"


# --- from tests/audits/test_review_medium.py: The medium findings of the code review of 2026-10-02 (code-review-2026-10-02.md, M5 to M16). ---


def test_public_dashboard_moves_in_steps_of_five(app, study__review_medium):
    test_boards._board(study__review_medium, "empty")
    study__review_medium.post(f"{test_boards.D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "bars"), ("n-item:2", "1")]))
    token = test_boards._link(study__review_medium)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        db.session.commit()
    test_boards._answer(app, {"2": "1"}, n=12)
    app.config["BOARD_CHECK_SECONDS"] = 0
    guest = test_boards._stranger(app)
    first = guest.get(f"/d/{token}").data.decode()
    with app.app_context():
        kept = boards.state(db.session.get(Board, 1), steps=True)
        assert kept["until"] and kept["tiles"][0]["n"] == 12  # the step is a moment shared by all links (S3)
    test_boards._answer(app, {"2": "1"}, n=4)      # four more: the link still shows the state of twelve
    with app.app_context():
        board = db.session.get(Board, 1)
        assert boards.state(board, steps=True)["tiles"][0]["n"] == 12
        assert boards.state(board, fresh=True, live=True)["tiles"][0]["n"] == 16   # who is signed in sees what is there now
    guest.get(f"/d/{token}")
    with app.app_context():  # ... and what the members saw does not show through the link: its state is kept apart
        assert boards.state(db.session.get(Board, 1), steps=True)["tiles"][0]["n"] == 12
    test_boards._answer(app, {"2": "1"}, n=1)      # five more than the state of the link: made anew
    with app.app_context():
        assert boards.state(db.session.get(Board, 1), steps=True)["tiles"][0]["n"] == 17
    assert first


def test_lecture_hall_link_shows_every_answer_at_once(app, study__review_medium):
    """Ralf, 2026-10-02: in the lecture hall every single answer shows at once; other links move in steps."""
    test_boards._board(study__review_medium, "empty")
    study__review_medium.post(f"{test_boards.D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "bars"), ("n-item:2", "1")]))
    plain, hall = test_boards._link(study__review_medium), test_boards._link(study__review_medium, hall="1")
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    app.config["BOARD_PUBLIC_SECONDS"] = 30        # as on a server: the state of a link is kept for a while
    test_boards._answer(app, {"2": "1"}, n=12)
    guest = test_boards._stranger(app)

    def shown(token):
        return guest.get(f"/d/{token}/state").get_json()

    before = {token: shown(token) for token in (plain, hall)}
    test_boards._answer(app, {"2": "1"}, n=1)
    assert shown(hall) != before[hall]             # the thirteenth answer is there at once
    assert shown(plain) == before[plain]           # the other link still shows twelve
    test_boards._answer(app, {"2": "1"}, n=1)
    assert shown(hall) != before[hall] and shown(plain) == before[plain]
