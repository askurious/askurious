"""Cost and load limits of the AI (security audit 2026-10-13, KR-2): the daily limits held per account only. Now no
call without a signed-in person, site admins with AI_ADMIN_FACTOR times the limit instead of none, a daily budget of
the platform over the installation's key (Slack hears of it once a day), a limit of calls at the same time per process
and per person, and max_output_tokens on the Responses API. No real AI call: the client is a fake."""
import os
import threading

import pytest
from flask_login import login_user

from app import ai, notify
from app.extensions import db
from app.models import AiUsage, User
from tests.accounts.test_own_ai_key import FakeClient


@pytest.fixture
def fake(app, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(ai, "_client", lambda: client)
    app.config["OPENAI_API_KEY"] = "sk-platform"
    return client


def _user(app, orcid="0000-0002-1825-0097", admin=False):
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        if user is None:
            user = User(orcid=orcid, name="Kollegin")
            db.session.add(user)
        user.is_admin = admin
        db.session.commit()
        return user.id


def test_no_call_without_a_signed_in_person(app, fake):
    with app.test_request_context():
        with pytest.raises(ai.AIError, match="nur für angemeldete Personen"):
            ai.generate_text("i", "p")
    assert fake.calls == []


def test_site_admins_have_a_finite_limit(app, fake):
    app.config.update(AI_TEXTS_PER_DAY=1, AI_ADMIN_FACTOR=3)
    uid = _user(app, admin=True)
    with app.test_request_context():
        login_user(db.session.get(User, uid))
        for _ in range(3):
            ai.generate_text("i", "p")
        with pytest.raises(ai.AIError, match="Tageslimit von 3"):
            ai.generate_text("i", "p")
    assert len(fake.calls) == 3


def test_the_platform_budget_stops_everybody_and_tells_slack_once(app, fake, monkeypatch):
    told = []
    monkeypatch.setattr(notify, "slack", lambda text: told.append(text))
    app.config.update(AI_PLATFORM_TEXTS_PER_DAY=2, AI_TEXTS_PER_DAY=10)
    one, two = _user(app), _user(app, "0000-0000-0000-0124")
    with app.test_request_context():
        login_user(db.session.get(User, one))
        ai.generate_text("i", "p")
        ai.generate_text("i", "p")
    with app.test_request_context():
        login_user(db.session.get(User, two))
        for _ in range(2):
            with pytest.raises(ai.AIError, match="Tagesbudget der Plattform"):
                ai.generate_text("i", "p")
        assert db.session.scalar(db.select(AiUsage.count).filter_by(user_id=two)) in (None, 0)
    assert len(fake.calls) == 2 and len(told) == 1
    assert os.path.isdir(os.path.join(app.config["UPLOAD_FOLDER"], "ai-budget"))


def test_calls_at_the_same_time_are_limited(app, fake, monkeypatch):
    app.config.update(AI_PARALLEL=1, AI_PARALLEL_PER_ACCOUNT=1)
    uid = _user(app)
    entered, release = threading.Event(), threading.Event()

    def slow(kw):
        entered.set()
        release.wait(5)
        return FakeClient().responses.create(**kw)

    slow_client = FakeClient(responses=slow)
    monkeypatch.setattr(ai, "_client", lambda: slow_client)
    results = []

    def first():
        with app.test_request_context():
            login_user(db.session.get(User, uid))
            results.append(ai.generate_text("i", "p"))

    t = threading.Thread(target=first)
    t.start()
    assert entered.wait(5)
    with app.test_request_context():
        login_user(db.session.get(User, uid))
        with pytest.raises(ai.AIError, match="zu viele KI-Aufrufe gleichzeitig"):
            ai.generate_text("i", "p")
    release.set()
    t.join(5)
    assert results and ai._running == {"all": 0}
    with app.test_request_context():  # the slot is free again, and the refused call was not counted
        login_user(db.session.get(User, uid))
        ai.generate_text("i", "p")
        assert db.session.scalar(db.select(AiUsage.count).filter_by(user_id=uid, kind="text")) == 2


def test_the_responses_api_gets_an_output_limit(app, fake):
    app.config["AI_MAX_OUTPUT_TOKENS"] = 12000
    uid = _user(app)
    with app.test_request_context():
        login_user(db.session.get(User, uid))
        ai.generate_text("i", "p")
    assert fake.calls[-1][0] == "responses" and fake.calls[-1][1]["max_output_tokens"] == 12000
