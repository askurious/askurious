"""A further word of a live vote (params["live_extra"]) is no participation of its own: the admins' and the
organisation's lists of surveys, the dashboard and the survey's own count leave it out, as the live view and the
export do (Ralf, 2026-10-17: cry94d showed 94 in Administration > Befragungen, 40 phones had taken part)."""
import re
from datetime import timedelta

from app import admin_stats
from app.extensions import db
from app.models import Response, Survey, utcnow


def _live_vote(app, people=3, extra_words=5):
    with app.app_context():
        s = Survey(title="Wortwolke", status=Survey.RUNNING, live={"run": {"id": 1, "name": "Durchgang"}},
                   opened_at=utcnow() - timedelta(days=3))
        db.session.add(s)
        db.session.flush()
        t = utcnow() - timedelta(days=1)
        for i in range(people + extra_words):
            params = {"live_run": 1, "live_device": f"d{i % people}"} if i < people else \
                {"live_run": 1, "live_extra": True, "live_main": f"p{i % people}"}
            db.session.add(Response(survey_id=s.id, source="real", status=Response.COMPLETED, started_at=t,
                                    completed_at=t, last_active_at=t, params=params))
        db.session.commit()
        return s.id


def test_further_words_are_no_participations(app, client):
    sid = _live_vote(app)
    with app.app_context():
        assert db.session.get(Survey, sid).completed_count() == 3
        assert admin_stats.compute("30")["parts"]["completed"] == 3
        assert db.session.scalar(db.select(db.func.count(Response.id)).where(
            Response.survey_id == sid, Response.not_live_extra())) == 3
    page = client.get("/admin/surveys").data.decode().split('id="befragungen"', 1)[1]
    row = page.split("Wortwolke", 1)[1].split("</tr>", 1)[0]
    assert re.findall(r"<td>(?:<strong>)?(\d+)(?:</strong>)?</td>", row)[:3] == ["3", "3", "3"]


def test_a_participation_without_params_still_counts(app, client):
    with app.app_context():
        s = Survey(title="Fragebogen")
        db.session.add(s)
        db.session.flush()
        db.session.add(Response(survey_id=s.id, source="real", status=Response.COMPLETED, completed_at=utcnow()))
        db.session.add(Response(survey_id=s.id, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                params={"x": "1"}))
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(Response.id)).where(
            Response.survey_id == s.id, Response.not_live_extra())) == 2


def test_cards_tabs_and_deleting_count_no_further_words(app):
    """Ralf, 2026-10-18: the card on "Meine Befragungen" still said 94 completed; so did the tabs of the survey
    (tally), the dashboard tiles of a step and the plan of deleting an account."""
    from app import accounts, dashboard

    sid = _live_vote(app)
    with app.app_context():
        s = db.session.get(Survey, sid)
        assert dashboard.completed_counts([sid]) == {sid: 3}
        assert sum(n for (source, preview, status), n in s.tally().items() if status == Response.COMPLETED) == 3
        assert accounts._real_count(s) == 3
