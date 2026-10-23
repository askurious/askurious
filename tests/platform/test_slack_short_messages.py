"""What goes to Slack (app/notify.py): short messages by default, without names, organisations, survey titles or
excerpts, as long as there is no processing agreement with Slack; a site admin switches to detailed messages on
Administration > Server (Ralf, 2026-10-21). The privacy texts follow the choice."""
from types import SimpleNamespace

from app import notify
from app.extensions import db
from app.models import Feedback, SiteSetting, User


def _me(app):
    with app.app_context():
        return db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))


def test_short_by_default_and_detailed_once_an_admin_chooses_it(app, client, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: sent.append((text, blocks)))
    survey = SimpleNamespace(code="ABC123", title="Studie über Frau Muster", status="draft")
    with app.test_request_context():
        me = db.session.merge(_me(app))
        name = me.name
        assert name and not notify.full()
        notify.new_user(me)
        notify.reset_request(me)
        assert notify.survey_ref(survey) == "`ABC123`"
        item = SimpleNamespace(severity=Feedback.COMPLAINT, tag="Urheberrecht", survey=survey, id=1, status="new",
                               message="Befragung ABC123\n\nFrau Muster hat das Bild gemacht.", page="")
        assert notify.excerpt(item) == "" and "Muster" not in notify.headline(item)
    assert sent and all(name not in text and f"Konto Nr. {me.number}" in text for text, _ in sent)

    page = client.get("/admin/database").get_data(as_text=True)
    assert 'id="slack"' in page and 'name="slack_content" value="short" checked' in page
    assert "Gewählt: knapp." in client.get("/admin/privacy").get_data(as_text=True)
    client.post("/admin/database", data={"slack_content": "full"})
    with app.app_context():
        assert db.session.get(SiteSetting, notify.CONTENT_KEY).value == "full"
    sent.clear()
    with app.test_request_context():
        assert notify.full()
        notify.reset_request(db.session.merge(_me(app)))
        assert "Frau Muster" in notify.survey_ref(survey) and "Frau Muster" in notify.excerpt(item)
    assert name in sent[0][0]
    assert "Gewählt: ausführlich." in client.get("/admin/privacy").get_data(as_text=True)


def test_privacy_texts_follow_the_choice(app, client):
    app.config["SLACK_WEBHOOK_URL"] = "https://hooks.slack.com/services/x"
    page = client.get("/imprint").get_data(as_text=True)
    assert "keine Namen, Organisationen, Titel von Befragungen" in page and "die ersten 500 Zeichen" not in page
    client.post("/admin/database", data={"slack_content": "full"})
    page = client.get("/imprint").get_data(as_text=True)
    assert "die ersten 500 Zeichen" in page and "keine Namen, Organisationen" not in page


def test_complaints_of_participants_stay_short_even_when_detailed(app, client, survey, monkeypatch):
    """The participants' side never reads the setting (its database role may not read site_setting)."""
    import threading

    from tests.participation.test_participate import uid
    from tests.platform.test_complaints import _running

    _running(client)
    client.post("/admin/database", data={"slack_content": "full"})
    sent = []
    monkeypatch.setattr(notify, "_post", lambda url, payload, logger: sent.append(str(payload)))
    app.config["SLACK_WEBHOOK_URL"] = "https://hooks.slack.com/services/x"
    p = app.test_client()
    page = p.get(f"/s/{uid(app)}/complaint").data.decode()
    assert "nicht Ihre Beschreibung" in page
    p.post(f"/s/{uid(app)}/complaint", data={"kind": "copyright", "message": "Frau Muster hat das Bild gemacht.",
                                             "good_faith": "1"})
    for thread in threading.enumerate():
        if thread.daemon and thread is not threading.current_thread():
            thread.join(2)
    assert sent and "Beschwerde" in sent[0] and "Muster" not in sent[0]
