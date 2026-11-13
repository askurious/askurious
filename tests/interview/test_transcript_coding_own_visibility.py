"""Interviews kodieren with the visibility "jede Person ihre eigenen" (CodingProject.OWN): a member who is not of the
team opens the analysis and its pages (transcript_coding.scope). Before, scope() read Actor.link, which the actor has
not had since the coding links went (2026-10-27), and every page of such a member ended in a server error."""
from app.extensions import db
from app.models import CodingProject
from tests.interview.test_transcript_coding_review import _setup


def test_a_member_opens_an_analysis_with_own_interviews_only(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    with app.app_context():
        db.session.get(CodingProject, pid).visibility = CodingProject.OWN
        db.session.commit()
    assert bea.get(base).status_code == 200                     # the analysis: no interview of hers to see
    assert bea.get(f"{base}/i/{rid}").status_code == 404          # someone else's interview stays closed
    assert bea.get(f"{base}/overview").status_code == 200
