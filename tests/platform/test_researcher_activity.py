""""Wann Forschende arbeiten" on Administration > Befragungen (app/researcher_activity.py; Ralf 2026-11-24): per hour
only the number of sign-in sessions used in it, no account and no session; summed by local weekday and hour, cells
under five light, only for the whole platform, deleted after a year and a bit."""
from datetime import datetime, timedelta

import app.models as models
from app import admin_stats, researcher_activity, sessions
from app.extensions import db
from app.models import LoginSession, ResearcherActivity, User, utcnow


def _hours(app):
    with app.app_context():
        return {r.hour: r.n for r in db.session.scalars(db.select(ResearcherActivity))}


def test_the_table_holds_nothing_about_people():
    assert set(ResearcherActivity.__table__.columns.keys()) == {"hour", "n"}
    assert not ResearcherActivity.__table__.foreign_keys


def test_a_session_counts_once_per_hour(app, client, monkeypatch):
    # the sign-in of the client fixture counted its hour
    assert sum(_hours(app).values()) >= 1
    with app.app_context():
        db.session.execute(db.delete(ResearcherActivity))
        user = db.session.scalar(db.select(User).where(User.orcid == "0000-0000-0000-001X"))
        row = db.session.scalar(db.select(LoginSession).where(LoginSession.user_id == user.id))
        base = datetime(2026, 11, 23, 9, 50)
        row.last_seen_at = base
        row.created_at = base
        db.session.commit()
        sid, uid = row.id, user.id
    for minute in (56, 58, 63, 75, 140):  # 9:56 and 9:58 in the same hour, then 10:03, 10:15, 12:00 (10:58 skipped)
        monkeypatch.setattr(models, "utcnow", lambda m=minute: base.replace(minute=0) + timedelta(minutes=m))
        with app.test_request_context("/"):
            assert sessions.open_row(uid, sid) is not None
    assert _hours(app) == {datetime(2026, 11, 23, 10): 1, datetime(2026, 11, 23, 11): 1}


def test_the_heatmap_sums_local_hours_and_stays_light_below_five(app, client):
    now = utcnow()
    monday = (now - timedelta(days=now.weekday() + 7)).replace(hour=8, minute=0, second=0, microsecond=0)
    with app.app_context():
        db.session.execute(db.delete(ResearcherActivity))
        db.session.add(ResearcherActivity(hour=monday, n=7))                         # 9:00 local (CET)
        db.session.add(ResearcherActivity(hour=monday + timedelta(hours=14), n=2))   # 23:00 local: under five
        db.session.add(ResearcherActivity(hour=now - timedelta(days=60), n=50))      # outside 30 days
        db.session.commit()
        app.extensions.get("admin_stats", {}).clear()
        work = admin_stats.compute("30")["work"]
    local = admin_stats.to_local(monday)
    assert work[local.weekday()][local.hour] == 7 and sum(map(sum, work)) == 9
    page = client.get("/admin/surveys").data.decode()
    card = page.split('id="forschende-wann"', 1)[1].split("</section>", 1)[0]
    assert "Wann Forschende arbeiten" in card
    assert card.count('<td class="adash-h4"') == 1 and card.count('<td class="adash-h0"') == 167
    assert "· &lt; 5" in card                                                    # the late hour: "< 5", light
    assert "Felder mit weniger als 5 Anmeldungen bleiben hell." in card


def test_only_the_platform_sees_it(app):
    with app.app_context():
        assert admin_stats.compute("30", org_id=12345)["work"] is None


def test_old_hours_go(app):
    now = utcnow()
    with app.app_context():
        db.session.execute(db.delete(ResearcherActivity))
        db.session.add(ResearcherActivity(hour=researcher_activity.hour_of(now - timedelta(days=401)), n=3))
        db.session.add(ResearcherActivity(hour=researcher_activity.hour_of(now - timedelta(days=399)), n=4))
        db.session.commit()
        assert researcher_activity.sweep(now) == 1
    assert list(_hours(app).values()) == [4]


def test_privacy_concept_names_it(app):
    from app import privacy_concept

    with app.test_request_context("/"):
        text = str(privacy_concept.build())
    assert "Wann Forschende arbeiten" in text and "400 Tagen" in text
