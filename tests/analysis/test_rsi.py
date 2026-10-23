"""Relative speed index (Leiner 2019) as the speeder rule of surveys with page times (app/export.py: Speed)."""
import csv
import io
from datetime import datetime, timedelta

from app import export
from app.extensions import db
from app.models import Block, Response, Survey
from tests.conftest import add, fill_consent

# Pages: "2", "4", "6" questions; "8" an info text; "9" a question in a block with a minimum time.
PAGE2 = [10] * 10 + [2, 1]        # median 10
PAGE4 = [20] * 10 + [5, 40]       # median 20
PAGE6 = [10] * 8 + [1]            # only 9 persons: does not count
INFO8 = [0.1] + [30] * 11         # info text: does not count
HELD9 = [30, 0.1] + [30] * 10     # minimum time: does not count


def setup(app, client, timing=True):
    fill_consent(client)
    for n, kind in enumerate(["text_input", "page_break", "text_input", "page_break", "text_input", "page_break", "info"]):
        add(client, kind)                                           # items 2..8
    client.post("/surveys/1/editor/blocks")                         # block 3
    add(client, "text_input", block=3)                              # item 9
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.page_timing = timing
        db.session.get(Block, 3).settings = {"min_seconds": 10}
        t0 = datetime(2026, 9, 1, 10, 0)
        for n in range(12):
            times = {"2": PAGE2[n], "4": PAGE4[n], "8": INFO8[n], "9": HELD9[n]}
            if n < 9:
                times["6"] = PAGE6[n]
            s.responses.append(Response(started_at=t0, completed_at=t0 + timedelta(seconds=300 + n),
                                        status=Response.COMPLETED, paradata={"_t": times}))
        s.responses.append(Response(started_at=t0, status=Response.ACTIVE, paradata={"_t": {"2": 0.5}}))
        db.session.commit()


def _csv(client, path="/surveys/1/export/data.csv", **args):
    body = client.get(path, query_string={"all": "1", **args}).data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(body)))


def test_index_as_computed_by_hand():
    medians = {"a": 10, "b": 20}
    assert export.rsi({"a": 10, "b": 20}, medians) == 1
    assert export.rsi({"a": 5, "b": 20}, medians) == 1.5                    # (2 + 1) / 2
    assert export.rsi({"a": 1, "b": 40}, medians) == (3 + 0.5) / 2          # 10 is clipped at 3
    assert export.rsi({"a": 0, "b": 20}, medians) == 2                      # no time at all: the cap
    assert export.rsi({"a": 5}, medians) == 2                               # only the pages seen
    assert export.rsi({"c": 5}, medians) is None and export.rsi({}, medians) is None
    assert [export.rsi_tenths(v) for v in ("2.0", "2,5", "0.2", "9", "abc", None, "nan")] == [20, 25, 10, 30, 20, 20, 20]


def test_pages_counted_and_export(app, client, survey):
    setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert export.rsi_pages(s) == {"2", "4", "6"}                  # no info page, no page with a minimum time
        assert export.page_medians(s, export.completed_times(s)) == {"2": 10, "4": 20}   # "6": fewer than 10
        assert export.speeder_rule(s) == "rsi" and export.rsi_limit(s) == 2.0   # with page times the default
    rows = _csv(client)
    assert [r["qc_rsi"] for r in rows] == ["1"] * 10 + ["3", "1.75", ""]
    assert [r["speeder"] for r in rows] == ["0"] * 10 + ["1", "0", ""]
    cols = list(rows[0])
    assert cols.index("qc_rsi") == cols.index("speeder") + 1
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "Regel: Relative Speed Index (qc_rsi) über 2,0" in book and "höchstens 3 (Leiner, 2019)" in book
    assert "mindestens 10 Personen" in book and "zwei Nachkommastellen" in book
    assert len(_csv(client, drop="1")) == 12                           # the one speeder left out
    with app.app_context():
        s = db.session.get(Survey, 1)
        fast = db.session.scalars(db.select(Response).filter_by(survey_id=1).order_by(Response.id)).all()
        assert export.marked_ids(s) == {fast[10].id}
        assert export.marked_at_end(s, fast[10]) and not export.marked_at_end(s, fast[11])
        from app import methods, quality

        assert quality.summary(s)["speeders"] == 1
        f = methods.facts(s)
        assert f["speeders"] == 1 and f["final"] == 11
        assert "Relative Speed Index über 2,0" in methods.draft(f)
        assert "relative speed index above 2.0" in methods.facts_text(f)
    # a lower limit: 1.75 is above 1.5
    client.post("/surveys/1/quality", data={"speeder_rsi": "1.5"})
    assert [r["speeder"] for r in _csv(client)][10:12] == ["1", "1"]
    # the duration rule again: nobody is that fast in total
    r = client.post("/surveys/1/quality", data={"speeder_rule": "duration", "speeder": "50"}, follow_redirects=True)
    assert "50 % der Median-Zeit" in r.data.decode()
    rows = _csv(client)
    assert [r["speeder"] for r in rows][:12] == ["0"] * 12 and rows[10]["qc_rsi"] == "3"   # the index stays as a column
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert (s.speeder_rule, s.speeder_rsi) == ("duration", 15)
        from app import duplicate, transfer

        dumped = transfer.dump(s, with_files=False)["survey"]
        assert dumped["speeder_rule"] == "duration" and dumped["speeder_rsi"] == 15
        assert "speeder_rule" in duplicate.SETTINGS and "speeder_rsi" in duplicate.SETTINGS


def test_quality_page_offers_the_index_with_page_times(app, client, survey):
    setup(app, client)
    html = client.get("/surveys/1/quality").data.decode()
    assert 'name="speeder_rule" value="rsi" form="quality-form" checked' in html
    assert 'data-rsi=\'{"values": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.75, 3.0]}\'' in html
    assert 'name="speeder_rsi"' in html and "2 Seiten gezählt" in html and "Festlegung von Askurious" in html
    assert 'data-rule-box="duration" hidden' in html
    assert "Relative Speed Index, je Seite Median der Seite" in html          # the source box names the rule
    r = client.post("/surveys/1/quality", data={"speeder_rsi": "2,5"}, follow_redirects=True)
    assert "Relative Speed Index über 2,5" in r.data.decode()
    assert "Relative Speed Index über 2,5" in client.get("/surveys/1/data").data.decode()


def test_without_page_times_the_duration_rule_holds(app, client, survey):
    setup(app, client, timing=False)
    client.post("/surveys/1/quality", data={"speeder_rule": "rsi"})       # not offered: changes nothing
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.speeder_rule == "" and export.speeder_rule(s) == "duration"
    rows = _csv(client)
    assert "qc_rsi" not in rows[0] and [r["speeder"] for r in rows][:12] == ["0"] * 12
    html = client.get("/surveys/1/quality").data.decode()
    assert 'value="rsi" form="quality-form"  disabled' in html and "Braucht Seitenzeiten." in html
    assert "data-rsi=" not in html


def test_without_page_times_the_way_to_the_index(app, client, survey):
    """Pre-test 2026-10-20, 27: under the disabled index a sentence says what it needs; in a draft its link goes to
    the setting (#page-timing, which loads the page anew once saved) and a button switches it on; after the start
    the page says the index is not possible for this survey."""
    setup(app, client, timing=False)
    html = client.get("/surveys/1/quality").data.decode()
    assert 'id="rsi-need"' in html and 'href="#page-timing"' in html and 'data-switch-setting="page-timing"' in html
    assert 'id="page-timing"' in html and 'name="page_timing" value="1" data-reload-saved' in html
    assert "Gesamtdauer: Schneller als" in html and "Relative Speed Index: Seiten schneller" in html
    with app.app_context():
        db.session.get(Survey, 1).status = Survey.RUNNING
        db.session.commit()
    html = client.get("/surveys/1/quality").data.decode()
    assert "Für diese Befragung nicht möglich" in html and "data-switch-setting" not in html
    with app.app_context():  # with page times the sentence is gone
        db.session.get(Survey, 1).page_timing = True
        db.session.commit()
    assert 'id="rsi-need"' not in client.get("/surveys/1/quality").data.decode()


def test_too_few_persons_no_index(app, client, survey):
    """Fewer than 10 completed participations: no page counts, nobody is a speeder by the index."""
    fill_consent(client)
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.page_timing = True
        t0 = datetime(2026, 9, 1, 10, 0)
        for seconds in [10] * 8 + [0.5]:
            s.responses.append(Response(started_at=t0, completed_at=t0 + timedelta(seconds=300), status=Response.COMPLETED,
                                        paradata={"_t": {"2": seconds}}))
        db.session.commit()
    rows = _csv(client)
    assert [r["qc_rsi"] for r in rows] == [""] * 9 and [r["speeder"] for r in rows] == ["0"] * 9
    assert "Noch kein Relative Speed Index" in client.get("/surveys/1/quality").data.decode()


def test_loop_passes_are_pages_of_their_own(app, client, survey):
    from tests.editor.test_loops import setup as loop_setup

    loop_setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.page_timing = True
        from app import loops

        rows = [r["id"] for r in loops.config(db.session.get(Block, 2))["rows"]]
        assert export.rsi_pages(s) == {f"2#{rid}" for rid in rows}
        t0 = datetime(2026, 9, 1, 10, 0)
        for n in range(10):  # pass 1 takes 10 s, pass 2 40 s
            s.responses.append(Response(started_at=t0, completed_at=t0 + timedelta(seconds=300), status=Response.COMPLETED,
                                        paradata={"_t": {f"2#{rows[0]}": 10, f"2#{rows[1]}": 40}}))
        db.session.commit()
        assert export.page_medians(s, export.completed_times(s)) == {f"2#{rows[0]}": 10, f"2#{rows[1]}": 40}
        # 10 s in both passes: 1 and 4 (clipped to 3), mean 2.0, not above the limit
        assert export.rsi({f"2#{rows[0]}": 10, f"2#{rows[1]}": 10}, {f"2#{rows[0]}": 10, f"2#{rows[1]}": 40}) == 2
