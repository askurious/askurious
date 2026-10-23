"""Audit "Daten und Berechnungen" 2026-10-10, package B (Siegelkette und Pakete): L-1, L-5, L-15, E-4, E-6 to E-10."""
import csv
import io
import json
from datetime import timedelta

from app import seal, seal_verify
from app.extensions import db
from app.models import Response, SealChain, SealEntry, Survey, utcnow
from tests.privacy.test_erasure import take
from tests.exports.test_seal import sealed_survey, tsa  # noqa: F401  (fixture)


def _cite(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        seal.anchor(s.uid)
        return s.uid, seal.summary(s)["cite"]


# --- L-1: the chain of a deleted survey is still found by its check code ------------------------------------------

def test_verify_finds_the_chain_of_a_survey_the_platform_deleted(app, client, survey, tsa):  # noqa: F811
    from app import platform_erase

    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "eins")
    survey_uid, code = _cite(app)
    with app.app_context():
        row = db.session.get(SealChain, survey_uid)
        assert (row.survey_id, row.title) == (1, db.session.get(Survey, 1).title)  # written with the first entry
        platform_erase.take_down(db.session.get(Survey, 1), "Verstoß gegen den Datenschutz laut Anordnung")
        assert db.session.get(Survey, 1) is None and db.session.get(SealChain, survey_uid) is not None
    guest = app.test_client()
    r = guest.get(f"/verify/{code}")
    page = r.data.decode()
    assert r.status_code == 200 and "Kein solcher Stand" not in page
    assert "von der Plattform gelöscht" in page and "Verstoß gegen den Datenschutz laut Anordnung" in page
    chain = json.loads(guest.get(f"/verify/{code}/seal.json").data)
    assert chain["survey"] == survey_uid and chain["entries"][-1]["kind"] == "closed"
    assert seal_verify.check_chain(chain)["problems"] == []


def test_verify_after_the_survey_was_deleted_with_the_account(app, client, survey, tsa):  # noqa: F811
    from app import lifecycle

    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "eins")
    _, code = _cite(app)
    with app.app_context():
        lifecycle.delete(db.session.get(Survey, 1), force=True)
        db.session.commit()
    page = app.test_client().get(f"/verify/{code}").data.decode()
    assert "Die Befragung wurde am" in page and "gelöscht." in page and "von der Plattform" not in page


def test_a_chain_begun_without_its_row_is_found_and_gets_it_at_the_end(app, client, survey, tsa):  # noqa: F811
    """During the rolling deploy the old code writes no seal_chain row: the live survey still answers, and the close
    of the chain writes the row before the survey goes."""
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "eins")
    survey_uid, code = _cite(app)
    with app.app_context():
        db.session.execute(db.delete(SealChain))
        db.session.commit()
    assert app.test_client().get(f"/verify/{code}").status_code == 200
    from app import lifecycle

    with app.app_context():
        lifecycle.delete(db.session.get(Survey, 1), force=True)
        db.session.commit()
        assert db.session.get(SealChain, survey_uid).survey_id == 1
    assert app.test_client().get(f"/verify/{code}").status_code == 200


# --- L-5: the period "answers" writes one record, not one a minute ------------------------------------------------

def test_the_period_answers_records_purged_once(app, client, survey, monkeypatch):
    from app import lifecycle, retention

    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    with app.app_context():
        s = db.session.get(Survey, 1)
        lifecycle.close(s)
        s.closed_at = utcnow() - timedelta(days=400)
        s.retention = {"personal": None, "answers": 12}
        db.session.commit()
        survey_uid = s.uid
    with app.app_context():
        lifecycle.close_due_surveys()
    calls = []
    real = retention.purge
    monkeypatch.setattr(retention, "purge", lambda s: calls.append(s.id) or real(s))
    for _ in range(4):  # further rounds of the housekeeping (else once a minute per process)
        with app.app_context():
            lifecycle.close_due_surveys()
    with app.app_context():
        kinds = [e.kind for e in seal.entries(survey_uid)]
        assert kinds.count("purged") == 1
        assert calls == []                                       # nothing left: purge is not called again
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        seal.purged(db.session.get(Survey, 1))                  # and a call anyway adds nothing
        assert [e.kind for e in seal.entries(survey_uid)].count("purged") == 1


# --- L-15: the values of external sections and web services are sealed ----------------------------------------------

EXT = {"n9": {"k": "x", "out": "2026-10-01T10:00:00", "n": 1, "back": "2026-10-01T10:01:00", "s": 60.0, "v": {"score": 7}},
       "_ws": {"n8": {"ok": 1, "v": {"temp": 13.8}, "ms": 120}}}


def _external(app, value):
    with app.app_context():
        db.session.execute(db.update(Response).where(Response.survey_id == 1).values(external=value))
        db.session.commit()


def _problems(app):
    with app.app_context():
        return seal.check_quick(db.session.get(Survey, 1)).problems


def test_returned_values_are_part_of_the_hash(app, client, survey):
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    with app.app_context():  # through the session (as a return or a web service in the request): a new entry
        r = db.session.scalars(db.select(Response)).one()
        r.external = EXT
        db.session.commit()
        entries = db.session.scalars(db.select(SealEntry).where(SealEntry.kind.in_(("sealed", "amended")))
                                     .order_by(SealEntry.seq)).all()
        assert [(e.kind, e.rec) for e in entries] == [("sealed", 2), ("amended", 2)] and seal.RECORD == 2
    assert _problems(app) == []
    changed = json.loads(json.dumps(EXT))
    changed["n9"]["v"]["score"] = 8                              # a value handed back, changed past the app
    _external(app, changed)
    assert [p[0] for p in _problems(app)] == ["changed"]
    changed = json.loads(json.dumps(EXT))
    changed["_ws"]["n8"]["v"]["temp"] = 30                       # a web service value, changed past the app
    _external(app, changed)
    assert [p[0] for p in _problems(app)] == ["changed"]
    paradata = json.loads(json.dumps(EXT))
    paradata["n9"]["s"], paradata["_ws"]["n8"]["ms"] = 1.0, 9    # time outside, duration: not covered
    _external(app, paradata)
    assert _problems(app) == []


def test_older_entries_keep_what_their_hash_covered(app, client, survey, monkeypatch):
    """Entries from before 2 hold rec empty (1): checked as then, so every chain there is still holds."""
    uid = sealed_survey(app, client)
    monkeypatch.setattr(seal, "RECORD", None)  # as the code before: no rec, the record without external values
    take(app.test_client(), uid, "a")
    monkeypatch.undo()
    with app.app_context():
        assert db.session.scalars(db.select(SealEntry.rec).where(SealEntry.kind == "sealed")).one() is None
    _external(app, EXT)
    assert _problems(app) == []
    with app.app_context():  # a change through the session: still compared by the old record, no new entry
        r = db.session.scalars(db.select(Response)).one()
        r.external = {**EXT, "_params": {"a": "b"}}
        db.session.commit()
        assert [e.kind for e in seal.entries(db.session.get(Survey, 1).uid)].count("amended") == 0


def test_a_web_service_call_after_the_end_gets_an_entry(app, client, survey):
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    _external(app, {"_ws": {"n8": {"ok": 1, "v": {"temp": 13.8}}}})  # as webservice.flush writes it after the request
    assert [p[0] for p in _problems(app)] == ["changed"]
    with app.app_context():
        rid = db.session.scalars(db.select(Response.id)).one()
        assert seal.amend_written(rid, "webservice")
        db.session.commit()
        assert seal.amend_written(rid, "webservice") is None  # nothing changed since
    assert _problems(app) == []


# --- E-4: the public package holds completes only -----------------------------------------------------------------

def _public_package(app, client):
    from app import zenodo

    uid = sealed_survey(app, client)
    for n in range(3):
        take(app.test_client(), uid, f"a{n}")
    with app.app_context():
        r = db.session.scalars(db.select(Response).order_by(Response.id)).first()
        r.status = Response.SCREENOUT
        db.session.commit()
        return dict(zenodo.package(db.session.get(Survey, 1)))


def test_seal_verify_has_a_mode_for_files_of_completes(app, client, survey, tsa, tmp_path):  # noqa: F811
    files = _public_package(app, client)
    chain = json.loads(files["seal.json"])
    rows = list(csv.DictReader(io.StringIO(files["data.csv"].decode("utf-8-sig"))))
    assert len(rows) == 2
    lines, ok = seal_verify.report(chain, rows)
    assert not ok and any("MISSING" in line and "screenout" in line for line in lines)
    lines, ok = seal_verify.report(chain, rows, completed=True)
    assert ok and lines[-1].startswith(seal_verify.COMPLETED_ONLY)
    assert any("screenout 1" in line for line in lines)
    readme = files["README.md"].decode()
    assert "python seal_verify.py --completed seal.json data.csv" in readme and "holds for the cases in the file" in readme
    (tmp_path / "seal.json").write_bytes(files["seal.json"])
    (tmp_path / "data.csv").write_bytes(files["data.csv"])
    argv = ["seal_verify.py", "--completed", str(tmp_path / "seal.json"), str(tmp_path / "data.csv")]
    assert seal_verify.main(argv) == 0
    # a completed case missing is still missing with --completed
    lines, ok = seal_verify.report(chain, rows[1:], completed=True)
    assert not ok and any("MISSING" in line and "completed" in line for line in lines)


# --- E-6 to E-10: README, codebook, matching report ---------------------------------------------------------------

def test_readme_names_what_was_stored(app, client, survey):
    from app import export

    with app.app_context():
        s = db.session.get(Survey, 1)
        text = export.readme_md(s)
        assert "no IP addresses were stored. No identifiers from the link were stored." in text
        assert "no IP addresses or identifiers were stored" not in text
        s.panel = {"params": [{"name": "PROLIFIC_PID", "mode": "store"}]}
        db.session.commit()
        text = export.readme_md(s)
        assert "Identifiers from the link were stored" in text and "`PROLIFIC_PID`" in text


def test_readme_instrument_without_a_source(app, client, survey, monkeypatch):
    from app import constructs, export
    from tests.conftest import add

    add(client, "text_input")
    monkeypatch.setattr(constructs, "info", lambda item: {"citation": "", "label": "Alter", "modifications": []})
    with app.app_context():
        text = export.readme_md(db.session.get(Survey, 1))
    assert "-  Variables" not in text and "- Alter (no published source). Variables: `" in text


def test_readme_names_the_coding_guide_and_the_category_columns(app, client, survey):
    from app import export
    from tests.interview.test_coding import _add, _survey

    _survey(app, client)
    _add(client, "Lieferzeit")
    with app.app_context():
        text = export.readme_md(db.session.get(Survey, 1))
    assert "- `kodierleitfaden.md`:" in text and "`<column>_k<n>`" in text and "`<column>_kat`" in text


def test_seal_columns_are_not_experimental_in_the_codebook(app, client, survey):
    from app import export

    sealed_survey(app, client)
    with app.app_context():
        rows = [r for r in export.codebook(db.session.get(Survey, 1)) if r[0] in ("seal_seq", "seal_hash")]
    assert len(rows) == 2 and all("experiment" not in " ".join(map(str, r)) for r in rows)


def test_matching_report_has_no_formulas():
    from types import SimpleNamespace

    from app import waves

    rep = {"waves": [{"rows": 1, "exact": 0, "tolerant": 0, "partial": 0, "unmatched": 1, "duplicate": 0, "no_code": 0}],
           "persons": 1, "linked": 0, "tolerant": 0, "partial": 0, "complete": 0, "unmatched": 1, "duplicate": 0,
           "no_code": 0}
    text = waves.report_csv(rep, [{"label": "=HYPERLINK(1)", "survey": SimpleNamespace(title="+Titel")}], False)
    assert "'=HYPERLINK(1)" in text and "'+Titel" in text
