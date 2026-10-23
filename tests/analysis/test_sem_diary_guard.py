"""No structural equation model over the entries of a diary study (audit 2026-10-17, M1): the results page leaves it
out, and the routes of the bootstrap and the downloads, which estimate the model of the page, do so too."""
from app import sem_model
from app.extensions import db
from app.models import Survey
from tests.diary.test_diary import _new


def test_no_model_over_diary_entries_through_the_bootstrap(app, client, monkeypatch):
    _new(app, client)

    def estimate(*args, **kwargs):
        raise AssertionError("a model over the entries of a diary study")

    monkeypatch.setattr(sem_model, "estimate", estimate)
    monkeypatch.setattr(sem_model, "prepare", estimate)
    page = client.post("/surveys/1/results/sem-bootstrap")
    assert page.status_code == 302
    with app.app_context():
        assert db.session.get(Survey, 1).sampling is not None
    assert client.get("/surveys/1/results/sem-bootstrap/status").status_code in (200, 404)
