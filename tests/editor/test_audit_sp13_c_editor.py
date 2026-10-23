"""SP13 AZ-12: example-only interactive previews never write owner Response rows or call their services."""
from app.extensions import db
from app.models import BrowserReceipt, Response, Survey
from tests.conftest import add, allow, fill_consent
from tests.dashboards.test_showcase import OTHER, _as, _showcase


def test_example_preview_keeps_answers_separate_and_transient(app, client, survey, monkeypatch):
    from app import webservice

    from tests.editor.test_webservice import build
    from tests.conftest import CONSENT_OK

    build(app, client)
    allow(app, OTHER)
    other = _as(app, OTHER, "Other")
    _showcase(client)
    calls = []
    monkeypatch.setattr(webservice, "run", lambda *args, **kwargs: calls.append(args))
    for path in ["", "?restart=1", "?device=mobile", "?device=desktop"]:
        assert other.get("/surveys/1/editor/preview"+path).status_code == 200
    other.post("/surveys/1/editor/preview", data=CONSENT_OK)
    other.post("/surveys/1/editor/preview", data={"nav": "next", "i2": "Example answer"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        saved = db.session.scalar(db.select(BrowserReceipt).filter_by(scope="example-preview"))
        assert saved is not None
        assert saved.expires_at is not None
        assert saved.data["answers"].get("2") == "Example answer"
    assert calls == []
    assert client.get("/surveys/1/editor/preview").status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1
