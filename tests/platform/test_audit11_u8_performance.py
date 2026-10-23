"""Security and performance audit 2026-10-10, package U8 "Kleine Performance".

P7      the public status page sorts the results of a day into its 96 bars in one pass instead of 96 passes
P8      a paired comparison counts its answers once per build (analyse, model, parameters and the HB input)
Q-Rest  quality control keeps its page per process while nothing changed (quality.page, quality.stamp), reads the
        participations once for histogram, tempo calibration and indicators, keys the kept values by the row version
        instead of repr() of the whole participation; the methods section as .md/.docx reads the kept facts of the
        page "Methodenteil" instead of building the table and quality.summary anew
Every result is the same as computed the old way.
"""
import random
import time
from datetime import datetime

import pytest

from app import compare, export, methods_text, quality, results_store, status_node, tempo
from app.extensions import db
from app.models import Item, Response, Survey
from tests.analysis.test_paired import setup as paired_setup, simulate


# --- P7 ------------------------------------------------------------------------------------------------------------


def _old_bars(rows, now):
    """The bars the slow way (96 passes over the rows): on the quarter hours, one count per minute, failed when one
    round of that minute failed (2026-10-17)."""
    slot = status_node.SLOT
    first = (now // slot + 1 - status_node.SLOTS) * slot
    bars = []
    for i in range(status_node.SLOTS):
        lo = first + i * slot
        part = [r for r in rows if lo <= r[0] // 60 * 60 < lo + slot]
        minutes = {r[0] // 60 for r in part}
        bad = len({r[0] // 60 for r in part if not r[1]})
        state = status_node.bar_state(len(minutes), bad)
        bars.append({"state": state, "level": status_node.bar_level(len(minutes), bad), "at": datetime.fromtimestamp(lo, status_node.BERLIN).strftime("%H:%M"),
                     "to": datetime.fromtimestamp(lo + slot, status_node.BERLIN).strftime("%H:%M"),
                     "n": len(minutes), "bad": bad})
    return bars


def test_p7_status_bars_in_one_pass_the_same(tmp_path, monkeypatch):
    from flask import Flask

    store = status_node.Store(str(tmp_path / "status.db"))
    now = int(time.time()) // 60 * 60  # Store.add keeps KEEP_DAYS back from now
    first = (now // status_node.SLOT + 1 - status_node.SLOTS) * status_node.SLOT
    rng = random.Random(7)
    # every minute, gaps, whole bars down, and the edges of the bars and of the day
    times = [now - s * 60 for s in range(1500) if rng.random() > 0.1]
    times += [first, first + status_node.SLOT, first + status_node.SLOT - 1, now - 1, now, first - 1]
    rows = [(t, "web", int(rng.random() > (0.9 if 30 <= (t - first) // status_node.SLOT <= 33 else 0.05)), 20, "")
            for t in times]
    store.add(rows)
    monkeypatch.setattr(status_node, "current", lambda: "de")
    with Flask(__name__).test_request_context("/"):
        got = status_node.summary(store, "web", now)
    assert got["bars"] == _old_bars(store.since("web", first), now)
    assert {b["state"] for b in got["bars"]} >= {"ok"} and {b["state"] for b in got["bars"]} & {"minor", "partial"}


# --- P8 ------------------------------------------------------------------------------------------------------------


def _counting(monkeypatch):
    from app import paired_analysis as pa

    calls = []
    counts = pa.counts
    monkeypatch.setattr(pa, "counts", lambda *a, **k: calls.append(1) or counts(*a, **k))
    return calls, counts


@pytest.mark.parametrize("cats", [[-1, 1], [-2, -1, 0, 1, 2]])
def test_p8_model_with_the_counts_given_the_same(cats):
    from app import paired_analysis as pa
    from app.paired_design import generate

    persons = simulate([1.0, 0.4, 0.0, -0.5], generate(4, 6, 2, seed=2), 80, cats, seed=5, delta=0.2)
    assert pa.model(4, persons, list("ABCD"), cats, pa.counts(4, persons)) == pa.model(4, persons, list("ABCD"), cats)
    est = pa.parameters(4, persons, cats)[0]
    assert pa.parameters(4, persons, cats, pa.counts(4, persons)[0])[0] == est == sorted(pa.components(4, persons)[0])


def test_p8_counts_once_per_build_same_results(app, client, survey, monkeypatch):
    from app import paired_analysis as pa, paired_hb
    from app.items.base import plain

    paired_setup(client, answer="tie")
    with app.app_context():
        s = db.session.get(Survey, 1)
        pairs = db.session.get(Item, 2).config["design"]["versions"][0]
        rng = random.Random(4)
        for _ in range(30):
            answer = {"v": 0, **{f"t{t}": (1 if a < b else -1) if rng.random() < 0.8 else 0 for t, (a, b) in enumerate(pairs)}}
            s.responses.append(Response(answers={"2": answer}, layout={"cj": {"2": {"version": 0}}}, status=Response.COMPLETED))
        db.session.commit()
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        table = export.rows(s, False, ["real"], None, True, with_hb=False, with_quality=False)
        labels = [plain(x) for x in item.kind.labels(item, "de")]
        n = len(labels)
        calls, counts = _counting(monkeypatch)
        out = pa.analyse(item, table, labels)
        inp, note = paired_hb._prepare(item, table)
        assert len(calls) == 1                                    # before: four times (analyse, model, 2 x parameters)
        persons = pa.persons_from_table(item, table)
        cats = pa.categories("tie", 5)
        monkeypatch.setattr(pa, "counts", counts)
        assert out["model"] == pa.model(n, persons, labels, cats)  # as computed before, counting anew
        rows = [p for p in persons if p[2]]
        assert inp["n_items"] == n and note is None and pa.parameters(n, rows, cats)[0] == list(range(n))


# --- Q-Rest: quality control ---------------------------------------------------------------------------------------


def _timed_dummies(app, client, n=40):
    """A Likert scale and an open question with dummies that have page times (speed index, tempo calibration)."""
    from app import dummy
    from tests.conftest import add

    add(client, "likert")
    add(client, "text_input")
    client.post("/surveys/1/pool", data={"source": ["real", "dummy"]})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.page_timing = True
        db.session.commit()
        dummy.generate(s, n, seed=3)
        db.session.commit()


def _pool(s):
    from app.surveys import stored_pool

    return stored_pool(s)


def test_q_quality_page_kept_while_nothing_changed(app, client, survey, monkeypatch):
    _timed_dummies(app, client)
    reads = []
    responses = export._responses
    monkeypatch.setattr(export, "_responses", lambda *a, **k: reads.append(1) or responses(*a, **k))
    first = client.get("/surveys/1/quality")
    assert first.status_code == 200 and len(reads) == 1           # one read for histogram, calibration, indicators
    again = client.get("/surveys/1/quality")
    assert again.status_code == 200 and len(reads) == 1           # nothing changed: nothing read
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        pool = _pool(s)
        kept = quality.page(s, pool)
        assert len(reads) == 1
        # the same as the three computed the old way, each reading the participations itself
        assert kept["speed"] == export.speed_data(s, pool["sources"])
        assert kept["tempo_pages"] == tempo.calibration(s, pool["sources"]) and kept["tempo_pages"]
        assert kept["patterns"] == quality.summary(s, pool["sources"], pool["all"])
        assert kept["speed"]["rsi"] and kept["speed"]["rsi"]["values"]
    # a changed answer of one participation: computed anew, and again the same as the old way
    with app.app_context():
        r = db.session.scalars(db.select(Response).order_by(Response.id)).first()
        r.answers = {**r.answers, "3": "ein ganz anderer Text"}
        db.session.commit()
    before = len(reads)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        pool = _pool(s)
        fresh = quality.page(s, pool)
        assert len(reads) == before + 1
        assert fresh["patterns"] == quality.summary(s, pool["sources"], pool["all"])
    # another marking rule: another stamp
    with app.app_context():
        db.session.get(Survey, 1).quality_rules = {"flags": ["bot"], "min": 1}
        db.session.commit()
    before = len(reads)
    client.get("/surveys/1/quality")
    assert len(reads) == before + 1


def test_q_state_by_version_and_unstored_not_kept(app, client, survey, monkeypatch):
    _timed_dummies(app, client, n=6)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        r = db.session.scalars(db.select(Response).order_by(Response.id)).first()
        ctx = quality.context(s, export._items(s))
        quality._KEPT.clear()
        old = quality.state(r)
        r.answers = {**r.answers, "3": "neu"}
        assert quality._stored(r) is False                        # changed, not yet stored: computed, not kept
        quality.request_values(s, r, ctx)
        assert not quality._KEPT
        db.session.commit()
        r = db.session.get(Response, r.id)
        assert quality.state(r) != old and quality._stored(r)
        quality.request_values(s, r, ctx)
        assert len(quality._KEPT) == 1


# --- Q-Rest: methods section as Markdown -------------------------------------------------------------------------


def _old_markdown(app, lang):
    """The draft as export_methods made it before: the table and quality.summary built anew."""
    from app import surveys

    with app.test_request_context():
        s = db.session.get(Survey, 1)
        f = {**surveys.stored_pool(s, drop=True), "speed_real": True}
        data = results_store.load(s, results_store.fingerprint(s, f))[0]
        table = compare.table_of(s, {**f, "drop": False}).keep(lambda r: r.get("qc_marked") != 1)
        counts = surveys.pool_counts(s)
        parts = methods_text.sections(s, data, table, quality.summary(s, f["sources"], f["all"]), lang,
                                      n=sum(counts.get(x, 0) for x in f["sources"]))
        return methods_text.markdown(s, parts, lang)


def test_q_methods_markdown_from_the_kept_facts_the_same(app, client, survey, monkeypatch):
    from app import dummy

    paired_setup(client, answer="choice")
    from tests.conftest import add

    add(client, "likert")
    client.post("/surveys/1/pool", data={"source": ["real", "dummy"]})
    with app.app_context():
        dummy.generate(db.session.get(Survey, 1), 40, seed=2)
        db.session.commit()
    md = client.get("/surveys/1/export/methods-section.md").data.decode()
    en = client.get("/surveys/1/export/methods-section.md?lang=en").data.decode()
    assert "## Datenbereinigung" in md and "Paarvergleich" in md and "## Sample" in en
    assert md == _old_markdown(app, "de") and en == _old_markdown(app, "en")
    # downloaded again: no participation read, no table built
    reads = []
    responses = export._responses
    monkeypatch.setattr(export, "_responses", lambda *a, **k: reads.append(1) or responses(*a, **k))
    assert client.get("/surveys/1/export/methods-section.md").data.decode() == md
    assert not reads
