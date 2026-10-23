"""Speeders on different paths through the questionnaire: the speed index per version of a page seen (paradata
"_v"), the note on the completion time rule when paths differ, and the participations without an index."""
from datetime import datetime, timedelta

from werkzeug.datastructures import MultiDict

from app import export
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add, fill_consent
from tests.analysis.test_rsi import setup as rsi_setup


def test_a_page_is_measured_against_the_version_seen():
    medians = {"a": 10, "a|v1": 3, "a|": 12, "b": 20}
    short = {"a": 3, "b": 20, "_v": {"a": "v1"}}
    assert export.rsi(short, medians) == 1                                   # (3/3 + 20/20) / 2
    assert export.rsi({"a": 3, "b": 20}, medians) == (3 + 1) / 2             # the whole page: 12/3, clipped at 3
    assert export.rsi({"a": 3, "b": 20, "_v": {"a": "v9"}}, medians) == (3 + 1) / 2   # no median of its own: the page's
    assert export.rsi_times({"_t": {"a": 3}, "_v": {"a": "v1"}}) == {"a": 3, "_v": {"a": "v1"}}
    assert export.rsi_times({"_t": {"a": 3}}) == {"a": 3} and export.rsi_times(None) == {}


def test_version_medians_from_the_pool(app, client, survey):
    rsi_setup(app, client)                                                   # pages "2", "4" with 12 people each
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10, 0)
        for _ in range(10):  # saw only part of page "2" (3 s, as is usual for that version), page "4" in full
            s.responses.append(Response(started_at=t0, completed_at=t0 + timedelta(seconds=300), status=Response.COMPLETED,
                                        paradata={"_t": {"2": 3, "4": 20}, "_v": {"2": "abcd1234"}}))
        db.session.commit()
        medians = export.page_medians(s, export.completed_times(s))
        assert medians["2|abcd1234"] == 3 and medians["2|"] == 10 and "4|" not in medians   # "4" has one version
        assert export.speed_data(s, ("real",))["rsi"]["pages"] == 2                        # versions are no pages
        everyone = db.session.scalars(db.select(Response).filter_by(survey_id=1).order_by(Response.id)).all()
        assert export.Speed(s).rsi(everyone[-1]) == 1                        # not a speeder for the short version
        assert export.marked_ids(s) == {everyone[10].id}


def test_the_version_seen_is_kept_while_answering(app, client, survey):
    from tests.editor.test_row_conditions import build, start

    build(app, client)
    with app.app_context():
        db.session.get(Survey, 1).page_timing = True
        db.session.commit()
    p, uid = start(app, client)
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2", "1")]))           # a phone only: no tablet statement
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data={"nav": "next", "i4_s1": "4", "i4_s3": "2"})
    q, _ = start(app, client)
    q.get(f"/s/{uid}")
    q.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2", "1"), ("i2", "2")]))  # sees every row of page 2
    q.get(f"/s/{uid}")
    q.post(f"/s/{uid}", data={"nav": "next", "i4_s1": "4", "i4_s2": "3", "i4_s3": "2"})
    with app.app_context():
        first, second = db.session.scalars(db.select(Response).filter_by(is_preview=False).order_by(Response.id)).all()
        assert first.status == second.status == Response.COMPLETED
        assert set(first.paradata["_v"]) == {"4"} and len(first.paradata["_v"]["4"]) == 8
        assert "4" in first.paradata["_t"]
        assert "_v" not in second.paradata                                   # saw all of it: nothing kept
        from app import paradata
        from app.models import Item

        items = [db.session.get(Item, 4), db.session.get(Item, 5)]
        phone, both = {2: ["1"]}, {2: ["1", "2"]}
        assert paradata.shown_version(items, items, both) is None
        assert paradata.shown_version(items, items, phone) == first.paradata["_v"]["4"]
        assert paradata.shown_version(items, items, {2: ["2"]}) not in (None, first.paradata["_v"]["4"])


def test_no_note_when_everybody_goes_the_same_way(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    with app.app_context():
        assert not export.paths_differ(db.session.get(Survey, 1))
    assert "Nicht alle gehen denselben Weg" not in client.get("/surveys/1/quality").data.decode()


def test_note_shown_with_rows_only_some_see(app, client, survey):
    from tests.editor.test_row_conditions import build

    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert export.paths_differ(s)
        from app import methods

        assert "dieselbe Fassung" not in methods.draft(methods.facts(s))  # duration rule: no word on versions
    html = client.get("/surveys/1/quality").data.decode()
    assert "Nicht alle gehen denselben Weg" in html and "braucht Seitenzeiten" in html


def test_a_branch_makes_paths_differ(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [*s.flow["children"], {"t": "branch", "id": "b1", "cond": {}, "children": []}]} \
            if s.flow else {"t": "root", "children": [{"t": "branch", "id": "b1", "cond": {}, "children": []}]}
        assert export.paths_differ(s)


def test_participations_without_an_index_are_counted(app, client, survey):
    rsi_setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10, 0)
        s.responses.append(Response(started_at=t0, completed_at=t0 + timedelta(seconds=300), status=Response.COMPLETED,
                                    paradata={"_t": {"8": 5}}))                # only the info page, which does not count
        db.session.commit()
        assert export.speed_data(s, ("real",))["rsi"]["without"] == 1
    html = client.get("/surveys/1/quality").data.decode()
    assert "1 ohne Index" in html and "1 abgeschlossene Teilnahme hat keinen Index" in html
