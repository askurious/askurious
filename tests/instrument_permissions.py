"""Explicit documentary permission fixtures for tests that exercise authorised private instrument uses."""
from app.extensions import db
from app.models import Survey, User


def grant_instrument(app, key, survey_id=1, *, orcid="0000-0000-0000-001X", languages=None):
    with app.app_context():
        survey = db.session.get(Survey, survey_id)
        grant = {"key": key, "survey_uid": survey.uid, "orcid": orcid,
                 "languages": languages if languages is not None else survey.languages,
                 "evidence": "test-only permission fixture; not a production rights document"}
        old = app.config.get("INSTRUMENT_WORDING_PERMISSIONS", [])
        app.config["INSTRUMENT_WORDING_PERMISSIONS"] = (old if isinstance(old, list) else []) + [grant]
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        # Load fields before the context closes, for direct template-factory tests.
        if user:
            user.orcid
        return user
