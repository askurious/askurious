"""The results page computes only when what the results rest on has changed (app/results_store.py)."""
import os
from datetime import timedelta

import pytest

from app import results, results_store
from app.extensions import db
from app.models import Item, Response, Survey, SurveyMember, utcnow
from tests.items.test_other_option import _setup

E = "/surveys/1/editor"
OTHER = "0000-0002-1825-0097"
KEPT, FRESH = "Datengrundlage unverändert", "eben neu berechnet"


def _answer(app, answers, source="real", status=Response.COMPLETED, minutes=5, **more):
    with app.app_context():
        now = utcnow()
        db.session.add(Response(survey_id=1, source=source, is_preview=source != "real", status=status,
                                answers=answers, started_at=now - timedelta(minutes=minutes),
                                completed_at=now if status == Response.COMPLETED else None, **more))
        db.session.commit()


@pytest.fixture
def study(app, client, survey):
    """Single choice (item 2) with an option that has a text field, three completed real participations."""
    _setup(client)
    client.post("/surveys/1/pool", data={"source": "real"})   # a draft would pool its test runs too (UI audit, question 9)
    for text in ("E-Scooter", "e-scooter", "Tretroller"):
        _answer(app, {"2": {"v": "3", "t": {"3": text}}})
    return client


@pytest.fixture
def computed(monkeypatch):
    """How often the results were computed."""
    calls = []
    compute = results.compute
    monkeypatch.setattr(results, "compute", lambda *a, **k: calls.append(1) or compute(*a, **k))
    return calls


def _page(client, url="/surveys/1/results"):
    r = client.get(url)
    assert r.status_code == 200
    return r.data.decode()


def test_second_visit_shows_the_kept_results(app, study, computed):
    first = _page(study)
    assert FRESH in first and "Neu berechnen" not in first and len(computed) == 1
    second = _page(study)
    assert KEPT in second and FRESH not in second and len(computed) == 1
    # the same page, apart from the line that says when it was computed
    import re

    # Signed CSRF values contain a timestamp which may change between visits under load.
    def content(page):
        page = re.sub(r'(name="csrf_token" value=")[^"]+"', r'\1CSRF"', page)
        return page.split('id="basis-line"')[1].split("</section>")[1:]
    assert content(first) == content(second)
    assert "E-Scooter" in second and "Grundlage: <strong>3</strong>" in second


def _changes():
    def new_participation(app, client):
        _answer(app, {"2": "1"})

    def pool(app, client):
        client.post("/surveys/1/quality", data={"source": ["real", "dummy"]})

    def unfinished_too(app, client):
        client.post("/surveys/1/quality", data={"source": ["real"], "all": "1"})

    def threshold(app, client):
        client.post("/surveys/1/quality", data={"source": ["real"], "speeder": "30"})

    def marking_rule(app, client):
        client.post("/surveys/1/quality", data={"source": ["real"], "qc_flag": ["missing"], "qc_min": "1"})

    def question_text(app, client):
        with app.app_context():
            item = db.session.get(Item, 2)
            item.texts = {**item.texts, "de": {**item.texts["de"], "question": "Womit fährst du?"}}
            db.session.commit()

    def measurement_level(app, client):
        with app.app_context():
            item = db.session.get(Item, 2)
            item.config = {**item.config, "missing": ["1"]}
            db.session.commit()

    def answer_replaced(app, client):  # same number of participations, same ids: other content
        with app.app_context():
            r = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
            r.answers = {"2": {"v": "3", "t": {"3": "Lastenrad mit Anhänger"}}}
            db.session.commit()

    def removed_and_added(app, client):  # SQLite gives the id of a deleted last row to the next one
        with app.app_context():
            last = db.session.scalars(db.select(Response).order_by(Response.id.desc())).first()
            db.session.delete(last)
            db.session.commit()
        _answer(app, {"2": {"v": "3", "t": {"3": "Tretroller"}}}, minutes=9)

    def answer_of_the_same_length(app, client):  # nothing about sizes or times changes
        with app.app_context():
            r = db.session.scalars(db.select(Response).order_by(Response.id)).first()
            r.answers = {"2": {"v": "3", "t": {"3": "E-Scooper"}}}
            db.session.commit()

    def questionnaire_version_of_a_participation(app, client):
        with app.app_context():
            db.session.scalars(db.select(Response)).first().qversion = 2
            db.session.commit()

    def new_program(app, client):
        results_store._program = "another version"

    return [new_participation, pool, unfinished_too, threshold, marking_rule, question_text, measurement_level,
            answer_replaced, removed_and_added, answer_of_the_same_length,
            questionnaire_version_of_a_participation, new_program]


@pytest.mark.parametrize("change", _changes(), ids=lambda f: f.__name__)
def test_a_changed_basis_is_computed_anew(app, study, computed, change, monkeypatch):
    monkeypatch.setattr(results_store, "_program", results_store.program())
    assert FRESH in _page(study) and KEPT in _page(study) and len(computed) == 1
    change(app, study)
    assert FRESH in _page(study) and len(computed) == 2
    assert KEPT in _page(study) and len(computed) == 2


def _unchanged():
    def someone_under_way(app, client):  # the pool holds completed participations only
        _answer(app, {"2": "1"}, status=Response.ACTIVE, last_active_at=utcnow())

    def a_test_run(app, client):  # another source than the pool's
        _answer(app, {"2": "1"}, source="test")

    def pool_saved_as_it_was(app, client):  # the form of the page "Qualitätssicherung", sent without a change
        from app import quality

        client.post("/surveys/1/quality", data={"source": ["real"], "speeder": "50", "qc_min": str(quality.DEFAULT_MIN),
                                                "qc_flag": [key for key, _, _ in quality.FLAGS if key not in quality.OPT_IN]})

    def another_page_visited(app, client):
        client.get("/surveys/1/editor/")
        client.get("/surveys/1/data")

    return [someone_under_way, a_test_run, pool_saved_as_it_was, another_page_visited]


@pytest.mark.parametrize("event", _unchanged(), ids=lambda f: f.__name__)
def test_what_leaves_the_basis_alone_computes_nothing(app, study, computed, event):
    _page(study)
    event(app, study)
    assert KEPT in _page(study) and len(computed) == 1


def test_another_surveys_participations_do_not_matter(app, study, computed):
    _page(study)
    study.post("/surveys/new", data={"title": "Zweite", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        db.session.add(Response(survey_id=2, source="real", status=Response.COMPLETED, answers={},
                                completed_at=utcnow()))
        db.session.commit()
    assert KEPT in _page(study) and len(computed) == 1


def test_the_role_ansehen_gets_the_kept_results_without_single_answers(app, study, computed):
    assert "E-Scooter" in _page(study)                                       # an admin computes and keeps them
    with app.app_context():
        survey = db.session.get(Survey, 1)
        survey.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="viewer", position=1))
        db.session.commit()
    viewer = app.test_client()
    viewer.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    viewer.post("/terms/", data={"accept": "1"})
    page = _page(viewer)
    assert KEPT in page and len(computed) == 1                               # from the store ...
    assert "E-Scooter" not in page and "Tretroller" not in page             # ... without the typed texts
    assert "E-Scooter" in _page(study) and len(computed) == 1               # the store itself is as it was
    # and the other way round: a viewer computes, an editor reads the kept results in full
    _answer(app, {"2": {"v": "3", "t": {"3": "Einrad"}}})
    assert "Einrad" not in _page(viewer) and len(computed) == 2
    page = _page(study)
    assert "Einrad" in page and KEPT in page and len(computed) == 2


def test_kept_results_are_only_for_people_with_access(app, study):
    _page(study)
    stranger = app.test_client()
    stranger.get("/auth/dev-login?orcid=0000-0000-0000-0124&name=Fremde")
    stranger.post("/terms/", data={"accept": "1"})
    assert stranger.get("/surveys/1/results").status_code == 404
    assert app.test_client().get("/surveys/1/results").status_code in (302, 401)
    with app.app_context():
        path = results_store._path(db.session.get(Survey, 1))
        assert os.path.isfile(path) and os.sep + "hb" + os.sep in path
    # the file is not served: /media only sends images of a survey's own folder
    with app.app_context():
        uid = db.session.get(Survey, 1).uid
    for url in (f"/media/hb/{uid}/results.bin", f"/media/{uid}/results.bin", "/media/hb/results.bin"):
        assert study.get(url).status_code == 404


def test_a_file_that_was_changed_is_not_read(app, study, computed, monkeypatch):
    _page(study)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        path = results_store._path(survey)
    raw = open(path, "rb").read()
    loads = []
    load = results_store._Unpickler.load
    # only the results themselves: the page keeps the structural equation model in a file of its own (for everyone
    # since 2026-10-11), which this test does not change
    plain = results_store.load

    def counted(*a, **k):
        if k.get("name", "results") != "results":
            monkeypatch.setattr(results_store._Unpickler, "load", load)
            try:
                return plain(*a, **k)
            finally:
                monkeypatch.setattr(results_store._Unpickler, "load", lambda self: loads.append(1) or load(self))
        return plain(*a, **k)
    monkeypatch.setattr(results_store, "load", counted)
    monkeypatch.setattr(results_store._Unpickler, "load", lambda self: loads.append(1) or load(self))
    assert KEPT in _page(study) and len(loads) == 1 and len(computed) == 1
    for bad in (raw[:40] + bytes([raw[40] ^ 1]) + raw[41:],            # one bit of the content
                bytes(32) + raw[32:],                                   # no signature
                raw[:32],                                               # nothing but a signature
                b"cos\nsystem\n(S'echo'\ntR."):                         # a pickle from elsewhere
        open(path, "wb").write(bad)
        assert FRESH in _page(study)                                    # computed anew, and kept again
        assert open(path, "rb").read() != bad
    assert len(loads) == 1 and len(computed) == 5                       # none of the four was unpickled
    # signed with another SECRET_KEY (a file copied from another installation)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        key = results_store.fingerprint(survey, {"sources": ["real"], "all": False, "drop": True, "speeder": 50})
        assert results_store.load(survey, key) is not None and len(loads) == 2
        app.config["SECRET_KEY"] = "another key"
        assert results_store.load(survey, key) is None and len(loads) == 2   # not unpickled


def test_only_the_surveys_own_pool_is_kept(app, study, computed):
    _answer(app, {"2": "1"}, source="dummy")
    _page(study)
    page = _page(study, "/surveys/1/results?source=dummy&drop=1")       # e.g. back from starting an HB estimation
    assert "Grundlage: <strong>1</strong>" in page and len(computed) == 2
    assert _page(study, "/surveys/1/results?source=dummy&drop=1") and len(computed) == 3   # not kept
    page = _page(study)
    assert KEPT in page and "Grundlage: <strong>3</strong>" in page and len(computed) == 3  # and not overwritten


def test_the_page_opens_at_once_and_then_asks_for_the_results(app, study, computed):
    app.config["RESULTS_WAIT_PAGE"] = True                              # as on a server
    page = _page(study)
    assert "data-auto" in page and 'name="go" value="1"' in page and "data-results-progress" in page and not computed
    assert "<noscript>" in page                                          # without JavaScript: a button
    page = _page(study, "/surveys/1/results?go=1")                      # what the form asks for
    assert FRESH in page and len(computed) == 1
    for url in ("/surveys/1/results", "/surveys/1/results?go=1"):       # kept: no waiting page, nothing computed
        assert KEPT in _page(study, url) and len(computed) == 1
    _answer(app, {"2": "1"})                                            # a new participation: the bar again
    assert "data-auto" in _page(study) and len(computed) == 1
    # an own pool in the address travels with the form; nothing of it is kept
    page = _page(study, "/surveys/1/results?source=dummy&drop=1")
    assert "data-auto" in page and 'name="source" value="dummy"' in page and 'name="drop" value="1"' in page
    script = open("app/static/app.js", encoding="utf-8").read()
    assert 'hasAttribute("data-auto")' in script and "data-results-stale" not in script and "window.location.hash" in script
    # a survey without any participation: the same way
    study.post("/surveys/new", data={"title": "Leer", "languages": ["de"], "default_language": "de"})
    assert "data-auto" in _page(study, "/surveys/2/results") and FRESH in _page(study, "/surveys/2/results?go=1")
    assert KEPT in _page(study, "/surveys/2/results")


def test_results_stay_kept_while_an_estimation_runs(app, client, survey, computed):
    from app import maxdiff_hb
    from tests.analysis.test_maxdiff import setup

    setup(client)
    with app.app_context():
        from app import dummy

        dummy.generate(db.session.get(Survey, 1), 6, seed=2)
        db.session.execute(db.update(Response).values(source="real", is_preview=False))
        db.session.commit()
    _page(client)
    assert KEPT in _page(client) and len(computed) == 1
    with app.app_context():
        item = db.session.scalar(db.select(Item).filter_by(type="maxdiff"))
        result_path, marker = maxdiff_hb._paths(item)
        maxdiff_hb._write(marker, {"state": "running", "started": maxdiff_hb._now().isoformat(), "progress": 0.2})
    # starting and running: the kept results stay, only the view of the estimate is read anew (Ralf, 2026-10-03)
    assert KEPT in _page(client) and KEPT in _page(client) and len(computed) == 1
    os.remove(marker)
    assert KEPT in _page(client) and len(computed) == 1
    with app.app_context():                                             # a stored estimate: other results
        maxdiff_hb._write(result_path, {"format": maxdiff_hb.FORMAT, "key": "eine andere Schätzung"})
    assert FRESH in _page(client) and len(computed) == 2                # an estimate of other settings: computed again


def test_results_with_a_database_row_inside_are_not_kept(app, study):
    with app.app_context():
        survey = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        assert not results_store.save(survey, "k", {"items": [{"item": item, "who": survey.members[0]}]})
        assert results_store.save(survey, "k", {"items": [{"item": item, "fmt": results._Price(item, 2)}]})
        data, at = results_store.load(survey, "k")
        assert data["items"][0]["item"] is item and data["items"][0]["fmt"].item is item and at
        assert results_store.load(survey, "another key") is None
        db.session.delete(item)                                          # the question is gone: nothing to show
        db.session.commit()
        assert results_store.load(db.session.get(Survey, 1), "k") is None
        results_store.forget(survey)
        assert not os.path.exists(results_store._path(survey))


def test_a_deletion_while_computing_leaves_no_kept_results(app, study, monkeypatch):
    """Code review 2026-10-15, D1: a participation deleted (on request, by a retention period) while the results are
    computed would be back in the kept file, after the deletion had removed the files: not kept."""
    compute = results.compute

    def deleting(*a, **k):
        found = compute(*a, **k)
        db.session.execute(db.delete(Response).where(Response.id == db.session.scalar(db.select(db.func.min(Response.id)))))
        db.session.commit()
        return found

    monkeypatch.setattr(results, "compute", deleting)
    assert FRESH in _page(study)
    with app.app_context():
        assert not os.path.exists(results_store._path(db.session.get(Survey, 1)))
    monkeypatch.setattr(results, "compute", compute)
    assert FRESH in _page(study) and KEPT in _page(study)               # without a deletion: kept as before


def test_every_save_in_the_application_names_its_basis():
    """D1: results_store.save checks after writing whether what the results were computed under is still current;
    every caller names it (basis=), so a new kept file cannot slip past the check."""
    import pathlib
    import re

    root = pathlib.Path(results_store.__file__).parent
    missing = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"results_store\.save\(", text):
            depth, i = 1, m.end()
            while depth and i < len(text):
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            if "basis=" not in text[m.end():i]:
                missing.append(f"{path.relative_to(root)}:{text[:m.start()].count(chr(10)) + 1}")
    assert not missing


def test_the_blocks_of_a_survey_page_load_in_two_queries(app, client, survey):
    """Audit 2026-10-22, G-04: every page of a survey loaded the items of each block on its own (13 queries for 13
    blocks, 26 in the editor). get_survey loads the blocks and their items in two queries, however many blocks."""
    from sqlalchemy import event

    from app.extensions import db
    from tests.conftest import add

    for _ in range(4):
        client.post("/surveys/1/editor/blocks", data={})
    add(client, "text_input")
    statements = []

    def count(conn, cursor, statement, *args):
        if "from item" in " ".join(statement.split()).lower() and "item.block_id" in statement.lower():
            statements.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert client.get("/surveys/1/results").status_code in (200, 202)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(statements) <= 2, len(statements)
