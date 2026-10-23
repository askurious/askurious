import csv
import io
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Response, Survey


def _responses(app, seconds):
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10, 0)
        for sec in seconds:
            s.responses.append(Response(started_at=t0, completed_at=t0 + timedelta(seconds=sec), status=Response.COMPLETED))
        s.responses.append(Response(started_at=t0, status=Response.ACTIVE))        # unfinished: never flagged
        db.session.commit()


def _csv(client, **args):
    body = client.get("/surveys/1/export/data.csv", query_string={"all": "1", **args}).data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(body)))


def test_speeders_flagged_not_removed(app, client, survey):
    _responses(app, [100, 200, 300, 400, 500])                    # median 300 s
    rows = _csv(client)
    assert len(rows) == 6                                          # nothing removed
    assert [r["speeder"] for r in rows] == ["1", "0", "0", "0", "0", ""]   # 100 < 150 (50 %)
    assert rows[0]["duration_rel"] == "0.333" and rows[2]["duration_rel"] == "1"
    assert "nur markiert, nichts entfernt" in client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    # the threshold belongs to the survey (page "Qualitätssicherung"); a value in the address changes nothing
    assert [r["speeder"] for r in _csv(client, speeder="70")][:5] == ["1", "0", "0", "0", "0"]
    r = client.post("/surveys/1/quality", data={"speeder": "70"}, follow_redirects=True)
    assert "Qualitätssicherung gespeichert" in r.data.decode() and "70 % der Median-Zeit" in r.data.decode()
    rows = _csv(client)                                            # 210 s: 100 and 200 are speeders
    assert [r["speeder"] for r in rows][:5] == ["1", "1", "0", "0", "0"]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "Schwelle: 70 % des Medians = 210 s" in book and "Leiner" in book
    with app.app_context():
        assert db.session.get(Survey, 1).speeder_pct == 70
        from app import methods

        text = methods.draft(methods.facts(db.session.get(Survey, 1)))   # X-8: Leiner measures speed per page
        assert "Die Grenze ist eine eigene Festlegung" in text and "vgl. Leiner (2019)" in text
        assert "own threshold" in methods.facts_text(methods.facts(db.session.get(Survey, 1)))
    client.post("/surveys/1/quality", data={"speeder": "abc"})    # nonsense: back to the default
    with app.app_context():
        assert db.session.get(Survey, 1).speeder_pct == 50


def test_speeders_left_out_on_request(app, client, survey, tmp_path):
    """Data pool: "ausschließen" removes the marked participations from every data file; resp_id stays."""
    _responses(app, [100, 200, 300, 400, 500])
    client.post("/surveys/1/quality", data={"speeder": "70"})
    rows = _csv(client, drop="1")
    assert [r["resp_id"] for r in rows] == ["3", "4", "5", "6"] and "1" not in [r["speeder"] for r in rows]
    assert len(_csv(client, drop="0")) == 6
    book = client.get("/surveys/1/export/codebook.csv", query_string={"drop": "1"}).data.decode("utf-8-sig")
    assert "markierte Teilnahmen (qc_marked) sind ausgeschlossen" in book and "nur markiert" not in book
    import pyreadstat

    path = tmp_path / "data.sav"
    path.write_bytes(client.get("/surveys/1/export/data.sav", query_string={"all": "1", "drop": "1"}).data)
    assert len(pyreadstat.read_sav(str(path))[0]) == 4
    # a copy and a survey file carry the threshold along
    from app import duplicate, transfer

    with app.app_context():
        s = db.session.get(Survey, 1)
        assert transfer.dump(s, with_files=False)["survey"]["speeder_pct"] == 70
        assert duplicate.SETTINGS.count("speeder_pct") == 1


def test_quality_page_shows_times_and_slider(app, client, survey):
    html = client.get("/surveys/1/quality", follow_redirects=True).data.decode()
    assert "Noch keine abgeschlossenen Teilnahmen" in html
    nav = html.split('class="survey-tabs"')[1].split("</nav>")[0]
    assert nav.index("Datenerhebung") < nav.index("Qualitätssicherung") < nav.index("Ergebnisse")
    assert "Qualitätssicherung" in nav.split('aria-current="page"')[1].split("</a>")[0]
    _responses(app, [120, 240, 360])
    html = client.get("/surveys/1/quality", follow_redirects=True).data.decode()
    assert "data-speed=" in html and 'name="speeder" form="quality-form"' in html and 'value="50"' in html
    assert ">Speichern</button>" in html and 'id="pool"' in html       # "Grundlage der Auswertung" on top (UI audit G3)
    assert 'name="drop"' not in html                               # marking or leaving out is a choice of the export
    field = client.get("/surveys/1/field").data.decode()
    assert 'id="pool"' not in field and 'id="testing"' in field      # the pool left Datenerhebung (UI audit G3)
    # the pool is set on Ergebnisse; here one line says what it holds and leads there (UI audit 2026-10-12, E6)
    line = html.split('id="pool"')[1].split("</p>")[0]
    assert "Echte Teilnahmen (3)" in line and 'href="/surveys/1/results#pool"' in line and 'name="all"' not in html
    # export: no slider any more, the choice in its data pool; results: no pool to tick at all
    html = client.get("/surveys/1/data", follow_redirects=True).data.decode()
    assert "data-speed=" not in html and 'name="speeder"' not in html
    assert 'name="drop" value="0" checked' in html and 'name="drop" value="1"' in html and "/surveys/1/quality" in html
    assert 'name="drop" value="1" checked' in client.get("/surveys/1/data?drop=1").data.decode()
    html = client.get("/surveys/1/results", follow_redirects=True).data.decode()
    assert "data-speed=" not in html and 'type="radio" name="drop"' not in html
    assert 'type="checkbox" name="source"' in html.split('class="panel basis')[1].split("</section>")[0]   # only in the basis box
    # one panel "Grundlage und Ansicht" (UI/UX audit 2026-10-07, S6): no link from one panel to the other any more
    assert "Datenpool: Echte Teilnahmen" in html and "Grundlage ändern" not in html and 'class="panel basis res-basis"' in html


def test_saved_pool_holds_for_results_and_starts_the_export(app, client, survey):
    """The data pool saved on the page "Qualitätssicherung" is the one of the results and the first choice on
    the export page; the export page may differ for its downloads."""
    _responses(app, [100, 200, 300, 400, 500])                    # real: 5 completed, 1 unfinished; one speeder at 50 %
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10, 0)
        for sec in (300, 300):
            s.responses.append(Response(source="dummy", started_at=t0, completed_at=t0 + timedelta(seconds=sec),
                                        status=Response.COMPLETED))
        db.session.commit()
    client.post("/surveys/1/pool", data={"source": "real"})        # a draft would pool its dummies (UI audit, question 9)
    assert "Grundlage: <strong>4</strong>" in client.get("/surveys/1/results").data.decode()   # the results leave speeders out
    from werkzeug.datastructures import MultiDict

    client.post("/surveys/1/pool", data=MultiDict([("source", "real"), ("source", "dummy"), ("all", "1"), ("drop", "1")]))
    with app.app_context():
        assert db.session.get(Survey, 1).data_pool == {"sources": ["real", "dummy"], "all": True}   # no speeder choice here
    html = client.get("/surveys/1/results").data.decode()         # 5 + 1 unfinished + 2 dummy - 1 speeder
    assert "Grundlage: <strong>7</strong>" in html and "ausgeschlossen" in html
    assert "Datenpool: Echte Teilnahmen, Dummy-Daten, auch abgebrochene" in html
    line = client.get("/surveys/1/quality").data.decode().split('id="pool"')[1].split("</p>")[0]
    assert "Echte Teilnahmen" in line and "Dummy-Daten" in line and "auch abgebrochene" in line   # one line (E6)
    assert "Testdurchläufe" not in line
    html = client.get("/surveys/1/data").data.decode()             # offered first on the export page
    assert 'value="dummy" checked' in html and 'name="drop" value="0" checked' in html   # files: speeders only marked
    assert len(_csv(client)) == 6                                  # all=1 in the address: an own choice (real only)
    body = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    assert len(list(csv.DictReader(io.StringIO(body)))) == 8       # nothing chosen: the saved pool, speeders kept


def test_median_comes_from_the_chosen_pool(app, client, survey):
    _responses(app, [100, 200, 300])                               # real, median 200
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10, 0)
        for sec in (1000, 1000, 1000, 1000):
            s.responses.append(Response(source="dummy", started_at=t0, completed_at=t0 + timedelta(seconds=sec),
                                        status=Response.COMPLETED))
        db.session.commit()
    real = _csv(client)
    assert real[0]["duration_rel"] == "0.5" and real[0]["speeder"] == "0"      # 100 is not < 50 % of 200
    from werkzeug.datastructures import MultiDict

    body = client.get("/surveys/1/export/data.csv", query_string=MultiDict([("source", "real"), ("source", "dummy"),
                                                                             ("all", "1")])).data.decode("utf-8-sig")
    mixed = list(csv.DictReader(io.StringIO(body)))                 # pool real + dummy: median of 7 values = 1000
    assert mixed[0]["duration_rel"] == "0.1" and mixed[1]["speeder"] == "1"
    book = client.get("/surveys/1/export/codebook.csv", query_string=MultiDict([("source", "real"), ("source", "dummy")]))
    assert "echte Teilnahmen + Dummy-Daten" in book.data.decode("utf-8-sig")
    html = client.get("/surveys/1/data?source=real&source=dummy", follow_redirects=True).data.decode()
    assert "Datenpool" in html and "Basis nur echte Daten" not in html
    assert html.index('id="pool"') < html.index('id="pool-speeders"') < html.index("Export der Antworten")


def test_a_negative_duration_is_no_speeder(app, client, survey):
    """Mathe-Audit Q-14: a participation that ends before it starts (a clock error) is no speeder and has no
    relative duration; the median leaves it out already."""
    from app import export

    _responses(app, [100, 200, 300, 400, 500, -60])
    rows = _csv(client)
    assert rows[5]["duration_s"] == "-60" and rows[5]["duration_rel"] == "" and rows[5]["speeder"] == ""
    assert [r["speeder"] for r in rows][:5] == ["1", "0", "0", "0", "0"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        late = next(r for r in s.responses if r.completed_at and r.completed_at < r.started_at)
        assert not export.is_speeder(late, 300, 50) and late.id not in export.marked_ids(s)


def test_histogram_colours_the_speeders_of_a_bar_the_limit_cuts(app):
    """Mathe-Audit Q-16: a bar the limit cuts was neutral although its fast participations count as speeders; now
    each bar has a red part as high as its speeders."""
    js = open(app.root_path + "/static/app.js", encoding="utf-8").read()
    assert "(i + 1) * bw <= limit" not in js
    assert "binned[i].filter(function (t) { return t < limit; }).length / ymax" in js


def test_a_draft_pools_its_tests_and_the_start_turns_to_real_ones(app, client, survey):
    # UI audit 2026-10-04, question 9 and G3: the basis box of Ergebnisse saves the pool and leads back there
    from app.surveys import stored_pool
    from tests.conftest import fill_consent, add

    with app.app_context():
        assert stored_pool(db.session.get(Survey, 1))["sources"] == ["real", "test", "dummy"]
    r = client.post("/surveys/1/pool", data={"source": "dummy", "back": "/surveys/1/results"})
    assert r.headers["Location"].endswith("/surveys/1/results#pool")
    assert client.post("/surveys/1/pool", data={"source": "dummy", "back": "https://example.org/"}).headers["Location"].endswith("/surveys/1/quality#pool")
    fill_consent(client)
    add(client, "info")
    client.post("/surveys/1/start", data={})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING and stored_pool(s)["sources"] == ["real"]
