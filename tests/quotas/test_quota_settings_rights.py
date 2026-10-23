"""Who may change the page "Quoten"'s survey-wide settings (code review 2026-10-15, L8: the routes quota_weighting
and quota_marked had happy paths in tests/analysis/test_weights.py and test_quota_marked.py, but no test of rights):
the weighting needs "Bearbeiten" in a draft and in the field; the setting for marked participations needs
"Bearbeiten" in a draft and "Verwalten" in the field."""
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Survey, SurveyMember
from tests.quotas.test_quotas import Q, build, start

OTHER = "0000-0002-1825-0097"


def member(app, role):
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="Zweite", role=role, position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Zweite")
    other.post("/terms/", data={"accept": "1"})
    return other


def setting(app, name):
    with app.app_context():
        return getattr(db.session.get(Survey, 1), name)


def test_a_viewer_changes_no_weighting_an_editor_does(app, client, survey):
    build(app, client)
    viewer = member(app, "viewer")
    assert viewer.post(Q + "/weighting", data=MultiDict([("kind", "quotas"), ("w_quota", "m")])).status_code == 302
    assert setting(app, "weighting") is None
    with app.app_context():
        db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER)).role = "editor"
        db.session.commit()
    start(app, client)                                                     # in the field too
    viewer.post(Q + "/weighting", data=MultiDict([("kind", "quotas"), ("w_quota", "m")]))
    assert setting(app, "weighting") == {"kind": "quotas", "quotas": ["m"], "census": []}


def test_marked_participations_in_the_field_only_by_admins(app, client, survey):
    build(app, client)
    editor = member(app, "editor")
    editor.post(Q + "/quality", data={"drop_marked": "1"})                # in a draft: "Bearbeiten" is enough
    assert setting(app, "quota_drop_marked") is True
    start(app, client)
    assert editor.post(Q + "/quality", data={}).status_code == 302
    assert setting(app, "quota_drop_marked") is True                       # in the field: refused
    client.post(Q + "/quality", data={})                                    # the admin
    assert setting(app, "quota_drop_marked") is False
