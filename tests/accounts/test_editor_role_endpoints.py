"""Routes whose docstrings give the role "Bearbeiten" are open to it (security and code audit 2026-10-13, AZ-62): the
study plan and the estimations started from the results page fell to the default "admin" for a POST."""
import pytest

from app.extensions import db
from app.models import SurveyMember
from tests.editor.test_join_links import OTHER, _as

POSTS = ["/surveys/1/researchdesign", "/surveys/1/researchdesign/assign", "/surveys/1/researchdesign/goal",
         "/surveys/1/researchdesign/pilot", "/surveys/1/results/classes", "/surveys/1/results/sem-bootstrap",
         "/surveys/1/results/lc/1"]


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_editors_may_viewers_may_not(app, client, survey, role):
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="Mitarbeiterin", role=role, position=2))
        db.session.commit()
    other = app.test_client()
    _as(other, OTHER, "Mitarbeiterin")
    for path in POSTS:
        other.post(path, data={})
        with other.session_transaction() as sess:
            refused = any("fehlen die Rechte" in str(text) for _kind, text in sess.pop("_flashes", []))
        assert refused == (role == "viewer"), (path, role)
