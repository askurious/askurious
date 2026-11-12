"""Participations deleted on the participants' side through participant_erase_response, which checks the proof
(app/participant_erase.py; access audit 2026-11-11, B2 stage 2). On PostgreSQL under a real participants' role
from schema_migrate.grant_participant, with DELETE on response taken away as stage 2b will: the ORM's deletion goes
through the function and succeeds with a fitting proof, deletes nothing without one, and the role may not call
DELETE itself."""
import hashlib
import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError

from app import participant_erase
from app.extensions import db
from tests.conftest import PG_SERVER, _config, _pg_admin, _pg_url

needs_pg = pytest.mark.skipif(not PG_SERVER, reason="needs a PostgreSQL server: TEST_PG_SERVER or TEST_DATABASE_URL")


def test_the_proof_kinds_are_those_the_function_knows():
    for kind in participant_erase.KINDS:
        assert f"p_kind = '{kind}'" in participant_erase._SQL
    assert "SECURITY DEFINER" in participant_erase._SQL and "SET search_path" in participant_erase._SQL
    from app.participant_grants import FUNCTIONS

    assert participant_erase.SIGNATURE in FUNCTIONS


def test_the_researchers_side_and_sqlite_delete_as_before(app, client, survey):
    """Without the participants' side on PostgreSQL nothing is redirected: prove() notes nothing."""
    with app.app_context():
        assert not app.extensions.get(participant_erase._KEY)
        participant_erase.prove("code", "ABC")
        assert participant_erase._KEY not in db.session.connection().info


def _rows(app):
    """A survey with a deletion code, a browser participation, a diary person with two entries, a declined
    participation, a further live word and a test run; returns what the proofs need."""
    from app import erasure
    from app.models import Enrolment, Response, Survey, User

    with app.app_context():
        user = User(orcid="0000-0001-2222-0001", name="Forschende")
        db.session.add(user)
        db.session.flush()
        survey = Survey(title="Löschen", created_by_id=user.id, code="ERASE1", erase_code=True)
        db.session.add(survey)
        db.session.flush()

        def response(pid, **kw):
            r = Response(survey_id=survey.id, answers={}, source="real", is_preview=False, pid=pid, **kw)
            db.session.add(r)
            return r

        code = "A" * 12
        coded = response("p-code", erase_hash=erasure.digest(code), status=Response.COMPLETED)
        browser = response("p-browser", status=Response.COMPLETED)
        secret = "diary-secret"
        person = Enrolment(survey_id=survey.id, secret_hash=hashlib.sha256(secret.encode()).hexdigest(), pid="p-person")
        db.session.add(person)
        db.session.flush()
        entries = [response(f"p-entry{n}", enrolment_id=person.id, status=Response.COMPLETED) for n in range(2)]
        declined = response("p-declined", status=Response.DECLINED)
        word = response("p-word", status=Response.COMPLETED, params={"live_extra": True})
        other = response("p-other", status=Response.COMPLETED)
        trial = Response(survey_id=survey.id, answers={}, source="test", is_preview=True, pid="p-trial")
        db.session.add(trial)
        db.session.commit()
        return {"survey": survey.id, "code": (coded.id, code), "browser": (browser.id, "p-browser"),
                "diary": ([e.id for e in entries], secret), "declined": declined.id, "word": word.id,
                "trial": trial.id, "other": other.id}


@pytest.fixture
def two_sides(tmp_path):
    """(owner app, participants' app) on one fresh PostgreSQL database; the participants' app runs with SIDE=participant
    under the restricted role, which has no DELETE on response."""
    if not PG_SERVER:
        pytest.skip("needs a PostgreSQL server: TEST_PG_SERVER or TEST_DATABASE_URL")
    from sqlalchemy.engine import make_url

    from app import create_app
    from app.schema_migrate import grant_participant

    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    name, role = f"askurious_erase_{worker}", f"askurious_erase_role_{worker}"
    _pg_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"DROP ROLE IF EXISTS {role}",
              f"CREATE DATABASE {name}", f"CREATE ROLE {role} LOGIN PASSWORD 'x'")
    url = _pg_url(name)
    owner = create_app(_config(tmp_path / "owner", url))
    with owner.app_context():
        grant_participant(role)
        db.session.execute(text(f'REVOKE DELETE ON public.response FROM "{role}"'))  # as stage 2b
        db.session.commit()
    rows = _rows(owner)
    participant_url = make_url(url).set(username=role, password="x").render_as_string(hide_password=False)
    config = _config(tmp_path / "participant", participant_url)
    config.SIDE, config.PARTICIPANT_BASE_URL = "participant", "https://ask.example.org"
    side = create_app(config)
    yield owner, side, rows
    for app in (side, owner):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    _pg_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"DROP ROLE IF EXISTS {role}")


def _delete(app, ids, kind=None, proof=""):
    """Deletes these participations with the ORM on the participants' side (one flush), with a proof."""
    from app.models import Response

    with app.test_request_context():
        if kind:
            participant_erase.prove(kind, proof)
        for row in db.session.scalars(db.select(Response).where(Response.id.in_(ids))).all():
            db.session.delete(row)
        try:
            db.session.commit()
        except StaleDataError:
            db.session.rollback()
            return False
        return True


def _left(app, ids):
    from app.models import Response

    with app.app_context():
        return set(db.session.scalars(db.select(Response.id).where(Response.id.in_(ids))))


@needs_pg
def test_participations_go_only_with_their_proof(two_sides):
    owner, side, rows = two_sides
    assert side.extensions.get(participant_erase._KEY), "the participants' side deletes through the function"
    coded, code = rows["code"]
    browser, pid = rows["browser"]
    entries, secret = rows["diary"]
    # without a proof, with a wrong one, or with the proof of another participation: nothing goes
    assert not _delete(side, [coded])
    assert not _delete(side, [coded], "code", "B" * 12)
    assert not _delete(side, [rows["other"]], "code", code)
    assert not _delete(side, [rows["other"]], "browser", pid)
    assert not _delete(side, [rows["other"]], "declined")
    assert not _delete(side, [rows["other"]], "live_word")
    assert not _delete(side, [rows["other"]], "test_run")
    assert not _delete(side, entries, "diary", "wrong")
    assert _left(owner, [coded, browser, rows["other"], *entries]) == {coded, browser, rows["other"], *entries}
    # with the fitting proof it goes, the ORM's hooks included
    assert _delete(side, [coded], "code", code)
    assert _delete(side, [browser], "browser", pid)
    assert _delete(side, entries, "diary", secret)
    assert _delete(side, [rows["declined"]], "declined")
    assert _delete(side, [rows["word"]], "live_word")
    assert _delete(side, [rows["trial"]], "test_run")
    assert _left(owner, [coded, browser, rows["declined"], rows["word"], rows["trial"], *entries]) == set()
    assert _left(owner, [rows["other"]]) == {rows["other"]}


@needs_pg
def test_a_diary_person_goes_whole_with_the_code_of_one_entry(two_sides):
    from app import erasure
    from app.models import Response

    owner, side, rows = two_sides
    entries, _ = rows["diary"]
    with owner.app_context():
        db.session.execute(db.update(Response).where(Response.id == entries[0]).values(erase_hash=erasure.digest("C" * 12)))
        db.session.commit()
    assert _delete(side, entries, "code", "C" * 12)
    assert _left(owner, entries) == set()


@needs_pg
def test_the_role_may_neither_delete_itself_nor_hand_the_function_on(two_sides):
    from sqlalchemy.exc import ProgrammingError

    owner, side, rows = two_sides
    with side.app_context():
        with pytest.raises(ProgrammingError, match="permission denied"):
            db.session.execute(text("DELETE FROM response WHERE id = :i"), {"i": rows["other"]})
        db.session.rollback()
        public = db.session.scalar(text("SELECT has_function_privilege('public', :f, 'EXECUTE')"),
                                   {"f": f"public.{participant_erase.SIGNATURE}"})
        assert public is False
        from app.participant_grants import role_problem

        with db.engine.connect() as connection:
            assert role_problem(connection) is None, "the start check passes the restricted role"
    assert _left(owner, [rows["other"]]) == {rows["other"]}
