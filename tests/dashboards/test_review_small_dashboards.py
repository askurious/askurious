"""Code review of 2026-10-02, the small findings of the dashboards (K1 to K6 of the report): percentages rounded as
on the results page, the first state and the state of a
link not computed by everyone, no small cells in the shares of conjoint levels, links that follow the field end,
day labels by language, and one basis for the figures of a provider's link."""
import os
import re
import time
from datetime import timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import boards
from app.extensions import db
from app.models import Board, BoardShare, Survey, utcnow
from tests.dashboards import test_boards
from tests.conftest import add
from tests.dashboards.test_boards import D, study  # noqa: F401  (the fixture)


# --- K1: percentages as on the results page ------------------------------------------------------------------


def test_percentages_are_rounded_half_up(app, study):
    with app.app_context():
        assert boards._pct(0.125) == "12,5 %" and boards._pct(0.225) == "22,5 %" and boards._pct(0.124) == "12,4 %"
        with boards.speaking("en"):
            assert boards._pct(0.125) == "12.5%" and boards._pct(0.1225) == "12.3%"  # half up, not to even
    test_boards._board(study, "empty")
    study.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "table"), ("n-item:2", "1"), ("pct-item:2", "1")]))
    test_boards._answer(app, {"2": "1"}, n=5)
    test_boards._answer(app, {"2": "2"}, n=35)
    page = study.get(f"{D}/1/view").data.decode()
    assert dict(re.findall(r'<td>(Auto|Rad)</td><td class="b-num">([^<]*)</td>', page)) == {"Auto": "12,5 %", "Rad": "87,5 %"}


# --- K2 ("Alle Fragen, auch künftige") is gone with the option (Ralf, 2026-10-03): tests/dashboards/test_boards.py ------


# --- K3: load -------------------------------------------------------------------------------------------------


def _counted(monkeypatch):
    calls = []
    build = boards.build
    monkeypatch.setattr(boards.compute, "build", lambda board, lang=None, only=None, **kw: calls.append(1) or build(board, lang, only, **kw))
    return calls


def test_the_first_state_is_waited_for_not_computed_twice(app, study, monkeypatch):
    test_boards._board(study)
    token = test_boards._link(study)
    test_boards._fill(app)
    calls = _counted(monkeypatch)
    with app.app_context():
        lock = boards._path(db.session.get(Board, 1)) + ".lock"
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    open(lock, "w").close()                                         # another request computes the first state
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:                                         # ... and is done after two looks
            os.remove(lock)
            boards.state(db.session.get(Board, 1), fresh=True)

    monkeypatch.setattr(boards.compute.time, "sleep", sleep)
    page = test_boards._stranger(app).get(f"/d/{token}").data.decode()
    assert "12</span> Teilnahmen" in page
    assert len(slept) == 2 and len(calls) == 1                      # the one computation of the other request


def test_a_first_state_that_does_not_come_shows_a_placeholder_after_a_short_wait(app, study, monkeypatch):
    """Since the performance audit of 2026-10-06 (P10) the waiting request does not compute the state itself: the
    page says it is being computed and asks again (board.js)."""
    test_boards._board(study)
    token = test_boards._link(study)
    test_boards._fill(app)
    calls = _counted(monkeypatch)
    with app.app_context():
        lock = boards._path(db.session.get(Board, 1)) + ".lock"
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    open(lock, "w").close()
    slept = []
    monkeypatch.setattr(boards.compute.time, "sleep", slept.append)
    page = test_boards._stranger(app).get(f"/d/{token}").data.decode()
    assert "Die Zahlen werden berechnet" in page and "12</span> Teilnahmen" not in page
    assert len(calls) == 0 and 1.5 <= sum(slept) <= 2.5 and max(slept) <= 0.5
    assert os.path.exists(lock)                                     # the other request's lock stays its own


def test_the_state_of_a_link_has_a_minimum_age(app, study, monkeypatch):
    app.config["BOARD_PUBLIC_SECONDS"] = 30
    test_boards._board(study)
    token = test_boards._link(study)
    test_boards._fill(app)
    calls = _counted(monkeypatch)
    guest = test_boards._stranger(app)
    assert "12</span> Teilnahmen" in guest.get(f"/d/{token}").data.decode() and len(calls) == 1
    test_boards._answer(app, {"2": "1"})
    assert "12</span> Teilnahmen" in guest.get(f"/d/{token}").data.decode()          # young: shown as it is
    assert guest.get(f"/d/{token}/state").get_json()["n"] == 12 and len(calls) == 1
    with app.app_context():
        path = boards._path(db.session.get(Board, 1))
    os.utime(path, (time.time() - 60, time.time() - 60))            # half a minute later
    assert "13</span> Teilnahmen" in guest.get(f"/d/{token}").data.decode() and len(calls) == 2
    # the members of the survey and the designer see what is there now
    test_boards._answer(app, {"2": "1"})
    assert "14</span> Teilnahmen" in study.get(f"{D}/1/view").data.decode()
    assert study.get(f"{D}/1/state").get_json()["n"] == 14


# --- K5: no small cells in the shares of conjoint levels ------------------------------------------------------


def _levels(counts, n, whole):
    data = {"shape": "conjoint", "n": n, "basis": "", "_whole": whole,
            "attributes": [{"label": "Farbe", "levels": [{"label": f"L{i}", "_share": c / n, "_count": c} for i, c in enumerate(counts)]}]}
    return boards._reduce(data)["attributes"][0]


def test_conjoint_shares_of_few_choices_or_people_are_not_shown():
    # choice-based: the share of a level chosen one to four times is not named
    found = _levels([3, 40, 50, 0], 30, whole=False)
    assert [lv["share"] is None for lv in found["levels"]] == [True, False, False, False]
    assert found["levels"][0]["small"] and "small" not in found["levels"][1] and found["levels"][3]["share"] == 0
    assert all("_count" not in lv and "_share" not in lv for lv in found["levels"])
    # adaptive: the shares of an attribute add up to one, so one hidden share alone would follow from the others
    found = _levels([2, 8, 20], 30, whole=True)
    assert [lv["share"] is None for lv in found["levels"]] == [True, True, False]
    found = _levels([2, 3, 25], 30, whole=True)                     # two small ones together are five: enough
    assert [lv["share"] is None for lv in found["levels"]] == [True, True, False]
    found = _levels([10, 8, 12], 30, whole=True)
    # the importance comes from the model of the results page (boards._importances), not from these shares
    assert [round(lv["share"] * 30) for lv in found["levels"]] == [10, 8, 12] and "importance" not in found
    # too few people at all: nothing, as before
    assert all("share" not in lv for lv in _levels([2, 3, 4], 9, whole=True)["levels"])


def test_adaptive_conjoint_tile_hides_levels_few_people_prefer(app, client, survey):
    from tests.analysis.test_acbc import setup

    tile = test_boards._modelled(app, client, setup, n=24)
    levels = [lv for a in tile["attributes"] for lv in a["levels"]]
    assert any(lv.get("small") for lv in levels)                    # the example data have such levels
    for lv in levels:  # no share that stands for one to four people
        assert lv["share"] is None if lv.get("small") else not 0 < round(lv["share"] * tile["n"]) < boards.MIN_CELL
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "levels"), ("n-item:2", "1")]))
    assert '<span class="b-value">unter 5</span>' in client.get(f"{D}/1/view").data.decode()
    client.post(f"{D}/1", data=MultiDict([("name", "A"), ("pick", "item:2"), ("view-item:2", "table"), ("n-item:2", "1")]))
    assert '<td class="b-num">unter 5</td>' in client.get(f"{D}/1/view").data.decode()


# --- K6: links follow the field end, day labels, the figures of a provider's link ----------------------------


def _field_end(app, days):
    with app.app_context():
        db.session.get(Survey, 1).field_end = utcnow() + timedelta(days=days)
        db.session.commit()


def _days_left(app):
    with app.app_context():
        return [round((boards.expiry(s) - utcnow()).total_seconds() / 86400) for s in db.session.scalars(db.select(BoardShare).order_by(BoardShare.id))]


def test_a_link_until_after_the_field_end_follows_the_field_end(app, study):
    test_boards._board(study)
    _field_end(app, 10)
    study.post(f"{D}/1/link", data={"valid": "field"})
    study.post(f"{D}/1/link", data={"valid": "30"})
    assert _days_left(app) == [24, 30]
    _field_end(app, 40)                                             # the fieldwork takes longer
    assert _days_left(app) == [54, 30]
    with app.app_context():
        first, second = db.session.scalars(db.select(BoardShare).order_by(BoardShare.id)).all()
        assert first.valid == "field" and second.valid is None
        tokens = first.token, second.token
        # the day both would have run out by the date they got when they were made
        first.expires_at = second.expires_at = utcnow() - timedelta(hours=1)
        db.session.commit()
    guest = test_boards._stranger(app)
    page = guest.get(f"/d/{tokens[0]}")
    assert page.status_code == 200 and guest.get(f"/d/{tokens[0]}/state").status_code == 200
    assert guest.get(f"/d/{tokens[1]}").status_code == 404          # a fixed end stays fixed
    with app.app_context():
        until = boards.expiry(db.session.get(BoardShare, 1))
        from app.timeutil import fmt

        assert f"Der Link gilt bis {fmt(until)}" in page.data.decode()
    designer = study.get(f"{D}/1").data.decode()
    assert designer.count("Link zum Dashboard") == 1 and "folgt dem Feldende" in designer
    # the field end moved to an earlier day: the link ends with it
    with app.app_context():
        db.session.get(BoardShare, 1).created_at = utcnow() - timedelta(days=60)
        db.session.commit()
    _field_end(app, -20)
    assert guest.get(f"/d/{tokens[0]}").status_code == 404 and guest.get(f"/d/{tokens[0]}/state").status_code == 404


def test_a_field_end_link_without_a_field_end_keeps_its_thirty_days(app, study):
    test_boards._board(study)
    study.post(f"{D}/1/link", data={"valid": "field"})              # no field end: 30 days
    assert _days_left(app) == [30]
    _field_end(app, -3)                                             # an end that had passed before the link was made
    study.post(f"{D}/1/link", data={"valid": "field"})
    assert _days_left(app) == [30, 30]
    _field_end(app, 50)                                             # set later: both follow it
    assert _days_left(app) == [64, 64]


def test_day_labels_speak_the_language_of_the_dashboard(app, study):
    from app.timeutil import to_local

    test_boards._board(study, "field")
    test_boards._fill(app)
    today = to_local(utcnow()).date()
    with app.app_context():
        board = db.session.get(Board, 1)
        days = {lang: next(t for t in boards.build(board, lang=lang)["tiles"] if t.get("name") == "days") for lang in ("de", "en")}
    assert [r["label"] for r in days["de"]["rows"]] == [f"{today.day}.{today.month}."]
    assert [r["label"] for r in days["en"]["rows"]] == [today.strftime("%m-%d")]


def test_a_provider_link_counts_real_participations_whatever_the_data_pool(app, study):
    test_boards._providers(app, study)
    study.post("/surveys/1/quality", data={"source": ["dummy"]})    # the survey is analysed with example data only
    with app.app_context():
        assert boards.pool_of(db.session.get(Survey, 1))["sources"] == ["dummy"]
    own, figures = test_boards._figures(app, ("src", "a"))
    assert own["n"] == 12 and not own["mixed"]
    assert figures["count"]["value"] == "12" and figures["count"]["sub"] == "16 begonnen · 12 abgeschlossen"
    assert figures["rate"]["value"] == "75,0 %" and "12 von 16" in figures["rate"]["sub"]
    assert sum(r["count"] for r in figures["days"]["rows"]) == 12
    # the link for everyone still rests on the data pool
    everyone, figures = test_boards._figures(app)
    assert everyone["mixed"] and figures["count"]["value"] == "0"


# --- K4: the address of a link only for who may give it out ----------------------------------------------------


def _colleague(app, role):
    from app.models import SurveyMember

    orcid = "0000-0002-1825-0097"
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=orcid, name="Vera", role=role, position=1))
        db.session.commit()
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name=Vera")
    c.post("/terms/", data={"accept": "1"})
    return c


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_only_admins_see_the_address_of_a_link(app, study, role):
    """Ralf, 2026-10-02: whoever has the address can pass the dashboard on; that is for the admins of the survey."""
    test_boards._board(study, "field")
    token = test_boards._link(study, label="Auftraggeber")
    page = _colleague(app, role).get(f"{D}/1").data.decode()
    assert "Auftraggeber" in page and token not in page             # that there is a link, not where it leads
    assert "weitergeben und widerrufen können Personen mit der Rolle „Admin“" in page
    assert f"/d/{token}" in study.get(f"{D}/1").data.decode()       # the admin
