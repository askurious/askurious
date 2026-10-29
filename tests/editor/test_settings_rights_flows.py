"""Rights and ways on the settings page, the diary schedule, the editor and the live presentation (UI/UX audit
2026-10-29, WQ-01, WQ-02, WQ-03, WQ-09, WQ-12)."""
import re
from pathlib import Path

from app.extensions import db
from app.models import EditStep, Survey, SurveyMember, User
from tests.conftest import fill_consent

OTHER = "0000-0002-1825-0097"


def _colleague(app, role):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role=role, position=1))
        db.session.commit()
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    c.post("/terms/", data={"accept": "1"})
    return c


def _consent_form(page):
    start = page.index('id="consent-facts"')
    return page[start:page.index("</form>", start)]


def test_consent_key_facts_read_only_without_admin_right(app, client, survey):
    fill_consent(client)
    shut = re.compile(r'<fieldset class="bare" +disabled>')
    assert not shut.search(_consent_form(client.get("/surveys/1").data.decode()))  # the admin edits
    other = _colleague(app, "editor")
    assert shut.search(_consent_form(other.get("/surveys/1").data.decode()))
    with app.app_context():
        db.session.scalar(db.select(SurveyMember).filter_by(survey_id=1, orcid=OTHER)).role = "viewer"
        db.session.commit()
    assert shut.search(_consent_form(other.get("/surveys/1").data.decode()))


def _ai_off(app):
    with app.app_context():
        for u in db.session.scalars(db.select(User)):
            u.ai_enabled = False
        db.session.commit()


def test_construct_box_without_ai_and_named_ai_dialog(app, client, survey):
    page = client.get("/surveys/1/editor/").data.decode()
    assert "data-box-open" in page and "data-construct-box" in page
    assert 'data-ai-dialog aria-labelledby="ai-dialog-title"' in page and 'id="ai-dialog-title"' in page
    _ai_off(app)
    page = client.get("/surveys/1/editor/").data.decode()
    assert "data-box-open" in page and "data-construct-box" in page  # the opener has its dialog
    assert "data-ai-dialog" not in page


def test_diary_schedule_has_undo_and_undo_returns_there(app, client):
    client.post("/diary/new", data={"title": "Tagebuch", "language": "de", "description": "x"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Tagebuch"))
        sid, code = s.id, s.code
    page = client.get(f"/diary/{code}/schedule").data.decode()
    assert "data-undo" in page and "undo-reserved" not in page
    form = page[page.index('id="schedule-form"'):]
    form = form[:form.index("</form>")]
    data = {}
    for name, value in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form):
        data.setdefault(name, value)
    times = [k for k in data if k.endswith("times")]
    for k in times:
        data[k] = "10:00 15:00 20:00"
    client.post(f"/diary/{code}/schedule", data=data)
    with app.app_context():
        steps = db.session.scalars(db.select(EditStep).filter_by(survey_id=sid)).all()
    assert steps and steps[-1].data.get("node") == "diary"
    r = client.post(f"/surveys/{sid}/editor/undo")
    assert r.status_code == 302 and "/schedule" in r.headers["Location"]


def test_second_screen_asks_for_the_screens_before_starting():
    js = (Path(__file__).resolve().parents[2] / "app/static/live.js").read_text(encoding="utf-8")
    handler = js[js.index('two.addEventListener("click"'):]
    handler = handler[:handler.index("two.disabled = false")]
    assert handler.index("getScreenDetails") < handler.index("fetch(two.dataset.control")
