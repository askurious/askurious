"""Versions of an interview guide and the category system under two requests at once (audit Datenspeicherung und
Berechnungen 2026-10-21, F3 and F10)."""
import threading

import pytest

from app.extensions import db
from app.models import Survey
from tests.interview.test_interview import make


def test_two_versions_at_once_keep_both_entries(app, client):
    """F3: request A has read the survey, request B records version 1 and commits, then A records its version. Before,
    A wrote its list read before B's commit: only [2] was left and fassung-1.zip was an orphan."""
    from app import interview_versions

    code, sid = make(app, client)
    with app.test_request_context():
        survey = db.session.get(Survey, sid)
        assert survey.interview.get("versions", []) == []  # A's read, before B
        answers = []  # B in a thread of its own: its own app context and database session
        other = threading.Thread(target=lambda: answers.append(
            client.post(f"/guide/{code}/versions", data={"note": "erste"}).status_code))
        other.start()
        other.join(120)
        assert answers == [302]
        entry = interview_versions.freeze(survey, "ethik", "", "Person A")
        db.session.commit()
        assert entry["n"] == 2
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert [v["n"] for v in survey.interview["versions"]] == [1, 2]
        assert [v["name"] for v in interview_versions.listing(survey)] == ["fassung-2.zip", "fassung-1.zip"]


def test_category_check_reads_the_row_for_update(app, client, monkeypatch):
    """F10: the version check of the category system reads the survey row with FOR UPDATE, so a second request at the
    same moment waits for the first commit instead of passing the check too (SQLite ignores the clause; the test
    looks at the statement)."""
    from app import coding

    code, sid = make(app, client)
    seen = []
    execute = db.session.execute

    def spy(statement, *args, **kwargs):
        seen.append(statement)
        return execute(statement, *args, **kwargs)

    with app.app_context():
        survey = db.session.get(Survey, sid)
        monkeypatch.setattr(db.session, "execute", spy)
        coding._store(survey, "q1", {"categories": []})
        monkeypatch.undo()
        assert any(getattr(s, "_for_update_arg", None) is not None for s in seen)
        assert coding.version(survey.codings) == 1
        # a request that read the old version still gets the conflict
        db.session.commit()
        stale = dict(survey.codings)
        stale[coding.VERSION] = 0
        with db.session.no_autoflush:
            survey.codings = stale
            with pytest.raises(coding.Conflict):
                coding._store(survey, "q2", {"categories": []})
