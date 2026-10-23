"""Security audit of 2026-10-03, package 3 "Öffentliche Links": one step for all links of a survey (S3), quota
tiles in steps (H3), the quota link /q/ with a limit, without archived surveys and with a kept count (S10), the
members' view without a computation on every request (H4), no frame for lecture hall links (H11)."""
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from app import boards, quotas
from app.extensions import db
from app.models import Board, BoardShare, Item, Quota, QuotaCell, QuotaShare, Response, Survey, utcnow
from tests.dashboards import test_boards
from tests.quotas import test_quota_field
from tests.dashboards.test_boards import D, study  # noqa: F401  (the fixture)
from tests.quotas.test_quotas import Q


def _answer(app, code, n=1, quota=None):
    """n completed participations that took the means of transport `code`; quota: (quota id, cell id) they
    completed in."""
    with app.app_context():
        now = utcnow()
        for _ in range(n):
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                    answers={"2": code}, started_at=now - timedelta(minutes=5), completed_at=now,
                                    quota={str(quota[0]): {"cell": quota[1], "res": "in", "held": False, "done": True}} if quota else None))
        db.session.commit()


def _running(app):
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        item = db.session.get(Item, 2)
        item.texts = {**item.texts, "en": {"question": "How do you travel?", "options": {"a": "Car", "b": "Bike", "c": "Other:", "d": "Train"}}}
        db.session.commit()


def _n(guest, token, lang=None):
    return guest.get(f"/d/{token}/state" + (f"?lang={lang}" if lang else "")).get_json()["n"]


# --- S3: one step for every language and every dashboard ----------------------------------------------------


def test_languages_and_dashboards_of_a_survey_rest_on_the_same_step(app, study):
    test_boards._board(study)
    first = test_boards._link(study)
    _running(app)
    _answer(app, "1", 8)
    _answer(app, "2", 8)
    _answer(app, "4", 5)                                            # 21 participations, train 24 %
    guest = test_boards._stranger(app)
    assert _n(guest, first) == 21 and "24 %" in guest.get(f"/d/{first}").data.decode()
    _answer(app, "4")                                               # one more person takes the train
    # the report's way round the steps: another language, a second dashboard. Every variant shows the step
    assert _n(guest, first) == 21 and _n(guest, first, "en") == 21
    assert "24%" in guest.get(f"/d/{first}?lang=en").data.decode() and "27%" not in guest.get(f"/d/{first}?lang=en").data.decode()
    test_boards._board(study)                                       # dashboard 2, made after the step
    study.post(f"{D}/2", data=MultiDict([("name", "B"), ("pick", "item:2"), ("view-item:2", "table"), ("pct-item:2", "1")]))
    study.post(f"{D}/2/link", data={"valid": "30"})
    with app.app_context():
        second = db.session.scalar(db.select(BoardShare).filter_by(board_id=2)).token
    assert _n(guest, second) == 21 and _n(guest, second, "en") == 21
    # changing a dashboard makes its state anew, on the same participations
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("pick", "item:3")]))
    assert _n(guest, first) == 21 and _n(guest, first, "en") == 21
    # the members and a lecture hall link see every answer at once (Ralf, 2026-10-02)
    assert study.get(f"{D}/1/state").get_json()["n"] == 22
    hall = test_boards._link(study, hall="1")
    assert _n(guest, hall) == 22
    # MIN_CELL more than the step counted: the next step, for every variant together
    _answer(app, "1", 3)
    assert _n(guest, first) == 21 and _n(guest, second, "en") == 21
    _answer(app, "1")
    assert {_n(guest, first), _n(guest, first, "en"), _n(guest, second), _n(guest, second, "en")} == {26}


def test_a_step_lies_a_margin_back_and_counts_what_came_by_then(app, study):
    app.config["BOARD_STEP_MARGIN"] = 60
    test_boards._board(study)
    _running(app)
    _answer(app, "1", 3)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        until = boards.step_cutoff(survey)
        assert timedelta(seconds=55) < utcnow() - until < timedelta(seconds=65)
        pool = boards.pool_of(survey)
        done = db.session.scalars(db.select(Response)).all()
        assert not any(boards.in_step(r, until, pool) for r in done)  # stamped a moment ago: not in this step
        assert all(boards.in_step(r, utcnow(), pool) for r in done)
        assert not boards.in_step(None, utcnow(), pool)
        assert boards.step_cutoff(survey) == until                  # kept for the survey
    _answer(app, "1", 5)
    with app.app_context():
        assert boards.step_cutoff(db.session.get(Survey, 1)) > until  # five more: a new step


def test_the_step_rule_holds_only_while_the_survey_runs_and_not_for_figures_alone(app, study):
    test_boards._board(study, "field")
    with app.app_context():
        board = db.session.get(Board, 1)
        assert not boards.stepped(board)                            # a draft
        board.survey.status = Survey.RUNNING
        assert not boards.stepped(board)                            # figures of the fieldwork alone
        board.tiles = list(board.tiles) + [{"key": "item:2", "view": "bars", "w": 6}]
        assert boards.stepped(board) and not boards.stepped(board, ("src", "a"))


# --- H3: quota tiles in steps; a note where a quota link is made ---------------------------------------------


def test_quota_tiles_follow_the_step(app, study):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.quotas.append(Quota(code="mode", name="Verkehrsmittel", cells=[
            QuotaCell(code="a", name="Auto", target=20), QuotaCell(code="b", name="Bahn", target=20)]))
        db.session.commit()
        quota_id, (car, train) = survey.quotas[0].id, [c.id for c in survey.quotas[0].cells]
    test_boards._board(study, "empty")
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", f"quota:{quota_id}"), (f"view-quota:{quota_id}", "table")]))
    token = test_boards._link(study)
    _running(app)
    _answer(app, "1", 10, quota=(quota_id, car))
    _answer(app, "4", 3, quota=(quota_id, train))

    def done(state):
        return [r["done"] for r in state["tiles"][0]["rows"]]

    guest = test_boards._stranger(app)
    assert guest.get(f"/d/{token}/state").status_code == 200 and done(_state(app)) == [10, 3]
    _answer(app, "4", quota=(quota_id, train))                      # one more completes in the small cell
    assert done(_state(app)) == [10, 3] and done(_state(app, "en")) == [10, 3]
    page = guest.get(f"/d/{token}").data.decode()
    assert '<td class="b-num">3</td>' in page and '<td class="b-num">4</td>' not in page
    with app.app_context():                                         # the members see it at once (the counters)
        quotas.recount(db.session.get(Survey, 1))
        db.session.commit()
        assert done(boards.state(db.session.get(Board, 1), fresh=True, live=True)) == [10, 4]
    _answer(app, "1", 4, quota=(quota_id, car))
    assert done(_state(app, "en")) == [14, 4]


def _state(app, lang=None):
    with app.app_context():
        return boards.state(db.session.get(Board, 1), lang=lang, steps=True)


def test_the_page_of_quota_links_says_they_are_live(app, client, survey):
    test_quota_field.providers_survey(app, client)
    page = client.get("/surveys/1/flow/quotas/").data.decode()
    assert "Der Link zeigt die Zahlen live und je Teilquote" in page


# --- S10: the quota link -------------------------------------------------------------------------------------


def _quota_link(app, client, **data):
    test_quota_field.providers_survey(app, client)
    client.post(Q + "/link", data={"label": "Panel", "conds": "1", **data})
    with app.app_context():
        return db.session.scalars(db.select(QuotaShare).order_by(QuotaShare.id.desc())).first().token


def test_a_quota_link_of_an_archived_survey_looks_like_an_unknown_one(app, client, survey):
    token = _quota_link(app, client)
    visitor = app.test_client()
    assert visitor.get(f"/q/{token}").status_code == 200
    with app.app_context():
        db.session.get(Survey, 1).archived = True
        db.session.commit()
    assert visitor.get(f"/q/{token}").status_code == 404 and visitor.get(f"/q/{token}/state.csv").status_code == 404


def test_requests_for_quota_links_are_limited_per_sender(app, client, survey):
    token = _quota_link(app, client)
    app.config.update(BOARD_LIMIT=3, BOARD_WINDOW=600)
    visitor = app.test_client()
    codes = [visitor.get(f"/q/{token}", environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code for _ in range(3)]
    codes.append(visitor.get(f"/q/{token}/state.csv", environ_base={"REMOTE_ADDR": "203.0.113.7"}).status_code)
    assert codes == [200, 200, 200, 429]
    assert visitor.get(f"/q/{token}", environ_base={"REMOTE_ADDR": "203.0.113.8"}).status_code == 200


def test_the_count_of_a_provider_is_kept_a_few_seconds_for_its_link(app, client, survey, monkeypatch):
    token = _quota_link(app, client, qp="src=b")
    app.config["BALANCE_SECONDS"] = 60
    calls = []
    count = quotas._from_responses
    monkeypatch.setattr(quotas, "_from_responses", lambda *a, **kw: calls.append(1) or count(*a, **kw))
    visitor = app.test_client()
    for _ in range(3):
        assert "nur Teilnahmen mit src = b" in visitor.get(f"/q/{token}").data.decode()
    assert "Männer,m,gender: männlich,5,1,1,3,0,0,offen" in visitor.get(f"/q/{token}/state.csv").data.decode("utf-8-sig")
    assert len(calls) == 1
    client.get("/surveys/1/field?qp=src%3Db")                       # the researchers' own page counts afresh
    assert len(calls) == 2


# --- H4: the members' view --------------------------------------------------------------------------------


def test_the_members_view_is_fresh_only_right_after_saving(app, study, monkeypatch):
    test_boards._board(study)
    test_boards._fill(app)
    app.config["BOARD_CHECK_SECONDS"] = 60
    calls = []
    build = boards.build
    monkeypatch.setattr(boards.compute, "build", lambda board, lang=None, only=None, **kw: calls.append(1) or build(board, lang, only, **kw))
    assert study.get(f"{D}/1/state").get_json()["n"] == 12 and len(calls) == 1
    _answer(app, "1")
    for _ in range(3):                                              # looked at no more often than BOARD_CHECK_SECONDS
        assert study.get(f"{D}/1/view").status_code == 200 and study.get(f"{D}/1/state").get_json()["n"] == 12
    assert len(calls) == 1
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2")]))
    assert study.get(f"{D}/1/state").get_json()["n"] == 13 and len(calls) == 2  # the preview after saving
    _answer(app, "1")
    assert study.get(f"{D}/1/state").get_json()["n"] == 13 and len(calls) == 2  # once only


# --- H11: no frame for a lecture hall link ------------------------------------------------------------------


def test_a_lecture_hall_link_cannot_be_framed(app, study):
    test_boards._fill(app)
    test_boards._board(study)
    hall = test_boards._link(study, hall="1")
    plain = test_boards._link(study)
    guest = test_boards._stranger(app)
    assert guest.get(f"/d/{hall}/embed").status_code == 404
    assert guest.get(f"/d/{hall}").status_code == 200 and guest.get(f"/d/{plain}/embed").status_code == 200

