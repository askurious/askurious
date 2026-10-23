"""Regression cases for the 2026-10-13 privacy audit (AZ-1, AZ-60, TN-30, TN-34)."""
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Board, BoardShare, QuotaShare, Response, Survey, SurveyMember, utcnow
from tests.conftest import add, allow
from tests.dashboards.test_showcase import OTHER, _as


def test_viewer_cannot_filter_to_an_individual(app, client, survey):
    allow(app, OTHER)
    viewer = _as(app, OTHER, "Viewer")
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role="viewer"))
        db.session.commit()
    assert viewer.get("/surveys/1/results?nur_var=anything&nur=one&go=1").status_code == 403


def test_personal_quota_links_cannot_be_created_or_used(app, client, survey):
    from app import panel

    client.post("/surveys/1/flow/quotas/add")
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        value = next(iter(panel.value_labels(s)["w"]))
    response = client.post("/surveys/1/flow/quotas/link", data={"qp": f"w={value}"}, follow_redirects=True)
    assert "persönliche Links" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(QuotaShare.id))) == 0
        db.session.add(QuotaShare(survey_id=1, token="legacy-personal-link", param="w", value=value,
                                  expires_at=utcnow() + timedelta(days=1)))
        db.session.commit()
    outsider = app.test_client()
    assert outsider.get("/q/legacy-personal-link").status_code == 404
    assert outsider.get("/q/legacy-personal-link/state.csv").status_code == 404


def test_new_preview_links_contain_no_orcid(app, client, survey):
    import re

    from app.editor.preview import _share_serializer

    add(client, "single_choice")
    page = client.get("/surveys/1/editor/preview?device=mobile", follow_redirects=True).get_data(as_text=True)
    token = re.search(r"/p/([^\"/?]+)", page).group(1)
    with app.app_context():
        payload = _share_serializer().loads(token)
    assert payload["user"] == 1 and "u" not in payload


def test_diary_viewer_sees_no_enrolment_identifiers(app, client):
    from tests.conftest import experiments_on
    from tests.diary.test_diary import _enrol, _new
    from app.models import Enrolment

    code = _new(app, client)
    _enrol(app, code)
    allow(app, OTHER, keep=True)
    viewer = _as(app, OTHER, "Viewer")
    experiments_on(viewer, "diary", orcid=OTHER)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, role="viewer"))
        db.session.commit()
        pid = db.session.scalar(db.select(Enrolment.pid))
    # the persons are on "Datenerhebung" since T5 (concept "Befragungstypen im Vergleich" 2026-10-17)
    assert pid in client.get("/surveys/1/field").get_data(as_text=True)
    page = viewer.get("/surveys/1/field")
    assert page.status_code == 200 and pid not in page.get_data(as_text=True)


def test_interview_viewer_cannot_read_the_case_list(app, client):
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, sid = make(app, client)
    assert start(app, client, code, label="PrivateCase")
    assert "PrivateCase" in client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    allow(app, OTHER, keep=True)
    viewer = _as(app, OTHER, "Viewer")
    with app.app_context():
        db.session.add(SurveyMember(survey_id=sid, orcid=OTHER, role="viewer"))
        db.session.commit()
    response = viewer.get(f"/guide/{code}/interviews")
    assert response.status_code in (302, 403) and "PrivateCase" not in response.get_data(as_text=True)


def test_viewer_map_contains_only_coarse_cells_with_five_distinct_people():
    from app.results import for_viewers

    points = [{"lat": 50.123456, "lon": 6.123456, "p": i, "a": ["private"], "w": 1} for i in range(5)]
    points += [{"lat": 51.987654, "lon": 7.123456, "p": 99, "w": 1}] * 8
    data = {"items": [{"columns": [], "map": {"points": points}}]}
    shown = for_viewers(data)["items"][0]["map"]["points"]
    assert shown == [{"lat": 50.12, "lon": 6.12, "k": 1, "w": 5}]


def test_shared_links_are_not_stored_as_plaintext(app, client, survey):
    from app.shared_tokens import find

    with app.app_context():
        board = Board(survey_id=1, name="Board", tiles=[])
        db.session.add(board)
        db.session.flush()
        for model, args in ((BoardShare, {"board_id": board.id}), (QuotaShare, {"survey_id": 1})):
            token = "sensitive-public-link-token"
            share = model(token=token, expires_at=utcnow() + timedelta(days=1), **args)
            db.session.add(share)
            db.session.commit()
            assert share.token == token and find(model, token).id == share.id
            raw = db.session.execute(db.text(f"SELECT token, token_box FROM {model.__tablename__} WHERE id=:id"),
                                     {"id": share.id}).one()
            assert token not in raw and raw[0] != token and token not in raw[1]


@pytest.mark.sqlite_foreign_keys_off  # goes down and up again: SQLite's batch mode (tests/conftest.py, B8)
def test_migration_preserves_existing_share_urls(app, client, survey):
    from alembic import command
    from app import migrations
    from app.shared_tokens import find

    with app.app_context():
        board = Board(survey_id=1, name="Board", tiles=[])
        db.session.add(board)
        db.session.flush()
        share = BoardShare(board_id=board.id, token="existing-link-token", expires_at=utcnow() + timedelta(days=1))
        quota = QuotaShare(survey_id=1, token="existing-quota-token", expires_at=utcnow() + timedelta(days=1))
        db.session.add_all([share, quota])
        db.session.commit()
        migrations._run(command.downgrade, "0055")
        assert db.session.execute(db.text("SELECT token FROM board_share")).scalar() == "existing-link-token"
        migrations._run(command.upgrade)
        db.session.expire_all()
        assert find(BoardShare, "existing-link-token").token == "existing-link-token"
        assert find(QuotaShare, "existing-quota-token").token == "existing-quota-token"


def test_erasure_pauses_every_public_dashboard_until_a_new_batch(app, client, survey):
    from app import erasure
    from app.boards.compute import state

    add(client, "single_choice")
    app.config.update(BOARD_STEP_MARGIN=0, BOARD_CHECK_SECONDS=0, BOARD_PUBLIC_SECONDS=0)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        board = Board(survey_id=1, name="Board", tiles=[{"key": "item:2", "view": "bars"}])
        db.session.add(board)
        for i in range(20):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED,
                                    pid=f"private-{i}", is_preview=False, answers={"2": "1"},
                                    completed_at=utcnow() - timedelta(minutes=2)))
        db.session.commit()
        assert not state(board, steps=True).get("pending")
        victim = db.session.scalar(db.select(Response).filter_by(survey_id=1))
        erasure.erase(s, [victim], "request")
        db.session.commit()
        assert state(board, steps=True)["pending"]
        assert state(board, steps=True, lang="en")["tiles"] == []
        assert not state(board, live=True).get("pending")
        for i in range(6):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED,
                                    pid=f"new-{i}", is_preview=False, answers={"2": "1"},
                                    completed_at=utcnow() - timedelta(minutes=1)))
        db.session.commit()
        assert not state(board, steps=True).get("pending")
