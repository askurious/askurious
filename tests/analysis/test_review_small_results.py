"""The small findings of the code review of 2026-10-02 on results, quality control and the participant side
(code-review-2026-10-02.md, K20 to K30)."""
import csv
import io
import os
import time
import warnings
import zipfile
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from app import export, methods, progress, quality, results, runtime
from app.extensions import db
from app.models import Item, Response, Survey, SurveyMember, utcnow
from tests.participation import test_participate
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
OTHER = "0000-0002-1825-0097"
KEPT, FRESH = "Datengrundlage unverändert", "eben neu berechnet"


def _answer(app, answers, n=1, status=Response.COMPLETED, **more):
    with app.app_context():
        now = utcnow()
        for _ in range(n):
            db.session.add(Response(survey_id=1, source="real", status=status, answers=answers,
                                    started_at=now - timedelta(minutes=5),
                                    completed_at=now if status == Response.COMPLETED else None, **more))
        db.session.commit()


def _start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    return f"/s/{test_participate.uid(app)}"


def _viewer(app):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.members.append(SurveyMember(orcid=OTHER, name="Kollegin", role="viewer", position=1))
        db.session.commit()
    viewer = app.test_client()
    viewer.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    viewer.post("/terms/", data={"accept": "1"})
    return viewer


def _real(app):
    return db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False))


# --- K20: the method section with one participant -------------------------------------------------------------


def test_method_section_with_one_person_has_no_empty_bracket_and_no_missing_sd(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                                               # 2
    client.post(E + "/items/2", data={"code": "age", "validation": "integer", "question": "Alter", "answer_mode": "optional"})
    client.post(E + "/blocks/2/items", data={"type": "construct:asku"})                     # 3
    with app.app_context():
        sids = [s["id"] for s in db.session.get(Item, 3).config["statements"]]
    _answer(app, {"2": 30, "3": {sid: 4 for sid in sids}})
    with app.app_context():
        f = methods.facts(db.session.get(Survey, 1))
        assert f["age"]["n"] == 1 and f["age"]["sd"] is None
        text = methods.draft(f)
        assert "im Mittel 30,0 Jahre alt (30 bis 30 Jahre)." in text and "(, " not in text and "None" not in text
        facts = methods.facts_text(f)
        assert "M = 30.0, range 30 to 30" in facts and "None" not in facts
    _answer(app, {"2": 40, "3": {sid: 2 for sid in sids}})                                  # two persons: with SD
    with app.app_context():
        f = methods.facts(db.session.get(Survey, 1))
        assert "(SD = 7,1, 30 bis 40 Jahre)." in methods.draft(f)
        assert "M = 35.0, SD = 7.1, range 30 to 40" in methods.facts_text(f)


# --- K21: a multiple choice sent without a tick is answered ---------------------------------------------------


def test_multiple_choice_without_a_tick_is_no_missing_answer(app, client, survey):
    fill_consent(client)
    for n in (2, 3):
        add(client, "multiple_choice")
        client.post(E + f"/items/{n}", data=MultiDict([
            ("code", f"mc{n}"), ("question", "Was trifft zu?"), ("answer_mode", "optional"),
            ("option_id", "a"), ("option_code", "1"), ("option_label", "A"),
            ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]))
    url = _start(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert _real(app).status == Response.COMPLETED and _real(app).answers["2"] == []
        row = export.rows(s)[0]
        assert row["mc2_1"] == 0 and row["qc_missing"] == 0.0                       # as the export: seen, nothing ticked
        item = db.session.get(Item, 2)
        assert item.kind.is_missing(item, [])                                       # the nudge still asks
        assert not quality.left_out(item, []) and quality.left_out(item, None)
        item.config = {**item.config, "min": 1}                                     # at least one tick is asked for
        assert quality.left_out(item, [])
        db.session.commit()
        assert export.rows(db.session.get(Survey, 1))[0]["qc_missing"] == 0.5


# --- K22: the page a person has open is not yet answered ------------------------------------------------------


def test_attention_check_on_the_open_page_counts_once_the_page_was_sent(app, client, survey):
    fill_consent(client)
    add(client, "likert")                                                                   # 2
    client.post(E + "/items/2", data=MultiDict([
        ("code", "m"), ("points", "5"), ("labeling", "full"), ("answer_mode", "optional"), ("level", "metric"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"),
        ("stmt_id", "s3"), ("stmt_code", ""), ("stmt_text", ""), ("stmt_check_s3", "4")]))
    add(client, "page_break")                                                               # 3
    add(client, "text_input")                                                               # 4
    client.post(E + "/items/4", data={"code": "frei", "question": "Frei", "answer_mode": "optional"})
    url = _start(app, client)
    client.post("/surveys/1/quality", data={"source": ["real"], "all": "1", "qc_flag": ["attention", "missing"], "qc_min": "1"})
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)                                    # the matrix is on the screen, nothing sent

    def row():
        with app.app_context():
            return export.rows(db.session.get(Survey, 1), include_incomplete=True)[0]

    open_page = row()
    assert open_page["status"] == "active"
    assert open_page.get("attention_n") is None and open_page.get("attention_failed") is None
    assert open_page.get("qc_missing") is None and open_page["qc_flags"] == 0 and open_page["qc_marked"] != 1
    with app.app_context():
        summary = {r["column"]: r["notable"] for r in quality.summary(db.session.get(Survey, 1), ("real",), True)["rows"]}
        assert summary["attention_failed"] == 0 and summary["qc_missing"] == 0
    p.post(url, data={"nav": "next", "i2_s1": "2", "i2_s2": "2", "i2_s3": "1"})     # sent, the check failed
    sent = row()
    assert sent["status"] == "active" and sent["attention_n"] == 1 and sent["attention_failed"] == 1
    assert sent["qc_missing"] == 0.0 and sent["qc_flags"] == 1                       # the open text page: not counted yet
    p.post(url, data={"nav": "next"})                                                # completed: every page counts
    done = row()
    assert done["status"] == "completed" and done["qc_missing"] == 0.5 and done["attention_failed"] == 1


# --- K23: an estimation that failed does not switch the results store off -------------------------------------


def test_results_stay_kept_next_to_a_failed_estimation(app, client, survey, monkeypatch):
    from app import dummy, maxdiff_hb
    from tests.analysis.test_maxdiff import setup

    calls = []
    compute = results.compute
    monkeypatch.setattr(results, "compute", lambda *a, **k: calls.append(1) or compute(*a, **k))
    setup(client)
    with app.app_context():
        dummy.generate(db.session.get(Survey, 1), 6, seed=2)
        db.session.execute(db.update(Response).values(source="real", is_preview=False))
        db.session.commit()
        item = db.session.scalar(db.select(Item).filter_by(type="maxdiff"))
        _, marker = maxdiff_hb._paths(item)
        started = maxdiff_hb._now().isoformat()
        maxdiff_hb._write(marker, {"state": "error", "started": started, "message": "ValueError: kaputt"})

    def page():
        return client.get("/surveys/1/results").data.decode()

    assert FRESH in page() and len(calls) == 1
    # an estimation that failed, failed again, died or runs: the kept results stay, only its view is read anew
    # (results.refresh_estimates, Ralf 2026-10-03: a click on the HB estimation must not compute the page again)
    assert KEPT in page() and "kaputt" in page() and len(calls) == 1
    with app.app_context():
        maxdiff_hb._write(marker, {"state": "error", "started": started, "message": "ValueError: anders"})
    assert KEPT in page() and "anders" in page() and len(calls) == 1
    with app.app_context():                                                       # one that died: no heartbeat for long
        old = (maxdiff_hb._now() - timedelta(seconds=maxdiff_hb.STALE_HEARTBEAT + 60)).isoformat()
        maxdiff_hb._write(marker, {"state": "running", "started": old, "beat": old, "progress": 0.3})
        assert maxdiff_hb.running(item)["state"] == "aborted"
    assert KEPT in page() and len(calls) == 1
    with app.app_context():                                                       # really running: still kept, its bar shows
        now = maxdiff_hb._now().isoformat()
        maxdiff_hb._write(marker, {"state": "running", "started": now, "beat": now, "progress": 0.3})
    html = page()
    assert KEPT in html and "data-hb-running" in html and len(calls) == 1


# --- K24: the panel on gender ---------------------------------------------------------------------------------


def test_gender_panel_is_sorted_and_shows_the_typed_texts_to_editors_only(app, client, survey):
    add(client, "single_choice")                                                            # 2
    client.post(E + "/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "anderes, und zwar:"),
        ("option_text", "c")]))
    for answer in ("2", "2", "2", "1", {"v": "3", "t": {"3": "nichtbinär-xyz"}}):
        _answer(app, {"2": answer})

    def panel(page):
        part = page[page.index("<h2>Geschlecht"):]
        return part[:part.index("</section>")]

    page = client.get("/surveys/1/results").data.decode()
    part = panel(page)
    assert part.index("männlich") < part.index("weiblich")                         # the most frequent first
    assert "nichtbinär-xyz" in part and page.count("nichtbinär-xyz") == 1
    seen = _viewer(app).get("/surveys/1/results").data.decode()                     # "Ansehen": no single answers
    assert "nichtbinär-xyz" not in seen and "männlich" in panel(seen)
    with app.app_context():                                                       # an entry of its own is reduced too
        data = results.compute(db.session.get(Survey, 1))
        data["gender"] = {**data["gender"], "columns": [dict(c) for c in data["gender"]["columns"]]}
        assert any("texts" in c for c in data["gender"]["columns"])
        assert not any("texts" in c for c in results.for_viewers(data)["gender"]["columns"])


# --- K25: the CSV of the SPSS package says what it is for -----------------------------------------------------


def test_spss_syntax_says_that_its_csv_is_not_for_spreadsheets(app, client, survey):
    add(client, "text_input")                                                               # 2
    client.post(E + "/items/2", data={"code": "frei", "question": "Frei", "answer_mode": "optional"})
    _answer(app, {"2": "=1+1"})
    z = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip").data))
    syntax = next(z.read(n).decode("utf-8-sig") for n in z.namelist() if n.endswith(".sps"))
    head = syntax.split("SET DECIMAL")[0]
    assert "für SPSS gedacht" in head and "als Formeln" in head and "Datenexport" in head
    assert all(line.startswith("* ") and line.endswith(".") for line in head.strip().splitlines())  # comments, each closed
    data = next(z.read(n).decode("utf-8-sig") for n in z.namelist() if n.endswith(".dat"))
    assert [r["frei"] for r in csv.DictReader(io.StringIO(data))] == ["=1+1"]       # the file itself: as it was


# --- K26: small notes -----------------------------------------------------------------------------------------


def test_ordinal_single_choice_with_letter_codes_keeps_the_order_of_its_options(app, client, survey):
    add(client, "single_choice")                                                            # 2
    client.post(E + "/items/2", data=MultiDict([
        ("code", "stufe"), ("question", "Wie viel?"), ("answer_mode", "optional"), ("level", "ordinal"),
        ("option_id", "a"), ("option_code", "n"), ("option_label", "niedrig"),
        ("option_id", "b"), ("option_code", "m"), ("option_label", "mittel"),
        ("option_id", "c"), ("option_code", "h"), ("option_label", "hoch")]))
    for code, n in (("n", 1), ("m", 2), ("h", 4)):
        _answer(app, {"2": code}, n=n)
    with app.app_context():
        assert db.session.get(Item, 2).kind.level(db.session.get(Item, 2)) == "ordinal"
        col = results.compute(db.session.get(Survey, 1))["items"][0]["columns"][0]
        assert col["type"] == "ord" and [r["value"] for r in col["rows"]] == ["n", "m", "h"]
        assert col["median"]["value"] == "h" and col["min"]["value"] == "n" and round(col["rows"][1]["cum"], 3) == 0.429
    page = client.get("/surveys/1/results").data.decode()
    assert page.index("n · niedrig") < page.index("m · mittel") < page.index("h · hoch")


def test_pool_label_names_the_participations_excluded_by_the_flow(app, client, survey):
    for url in ("/surveys/1/results", "/surveys/1/data"):
        assert "auch abgebrochene, Screen-outs, „Quote voll“ und im Ablauf ausgeschlossene" in client.get(url).data.decode()


def test_text_module_compiles_without_a_warning():
    from app.items import text

    with open(text.__file__, encoding="utf-8") as f:
        source = f.read()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(source, text.__file__, "exec")


def test_progress_is_reported_although_an_old_note_vanished_while_cleaning_up(app, monkeypatch):
    with app.app_context():
        folder = os.path.join(app.config["UPLOAD_FOLDER"], "progress")
        os.makedirs(folder, exist_ok=True)
        old = os.path.join(folder, "7_oldoldold1.json")
        with open(old, "w", encoding="utf-8") as f:
            f.write("{}")
        long_ago = time.time() - progress.MAX_AGE - 60
        os.utime(old, (long_ago, long_ago))
        remove = os.remove

        def gone(path):  # another request removed the note between the listing and this one's turn
            remove(path)
            raise FileNotFoundError(path)

        monkeypatch.setattr(progress.os, "remove", gone)
        report = progress.reporter("abcdefgh12", 7)
        monkeypatch.undo()
        assert report is not None and not os.path.exists(old)
        report(0.5, "halb")
        assert progress.read("abcdefgh12", 7) == {"share": 0.5, "text": "halb"}


# --- K27: ?lang= decides the language at the start only, where participants may not switch --------------------


def test_lang_in_the_link_starts_a_participation_but_does_not_switch_it(app, client, survey):
    test_participate.build(client)
    url = _start(app, client)
    with app.app_context():
        db.session.get(Survey, 1).language_choice = False
        db.session.commit()
    p = app.test_client()
    assert 'lang="en"' in p.get(url + "?lang=en", headers={"Accept-Language": "de"}).data.decode()   # the link decides
    assert 'lang="de"' in p.get(url + "?lang=de").data.decode()                    # nothing stored yet: still the link
    p.get(url + "?lang=en")
    p.post(url, data=CONSENT_OK)
    with app.app_context():
        assert _real(app).language == "en"
    page = p.get(url + "?lang=de").data.decode()                                    # typed into the address on the way
    # the page is English; only a question not translated yet says it is German (W45)
    assert '<html lang="en"' in page and '<html lang="de"' not in page
    p.post(url + "?lang=de", data={"nav": "next", "i2_s1": "1", "i2_s2": "1"})
    with app.app_context():
        assert _real(app).language == "en" and _real(app).position == 2
        db.session.get(Survey, 1).language_choice = True                            # with the choice: as before
        db.session.commit()
    assert 'lang="de"' in p.get(url + "?lang=de").data.decode()
    with app.app_context():
        assert _real(app).language == "de"


# --- K28: the placement is computed on the server -------------------------------------------------------------


def test_score_band_stores_what_the_server_computes_not_what_the_browser_sends(app, client, survey):
    fill_consent(client)
    client.post(E + "/blocks/2/items", data={"type": "construct:asku"})                     # 2, scale 1 to 5
    add(client, "page_break")                                                               # 3
    add(client, "score_band")                                                               # 4
    form = MultiDict([("code", "band"), ("question", "Ihre Einordnung"), ("source", "2"), ("basis", "value")])
    for upper, label in (("2,5", "niedrig"), ("3,5", "mittel"), ("", "hoch"), ("", "")):
        form.add("band_id", ""); form.add("band_upper", upper); form.add("band_label", label); form.add("band_text", "")
    client.post(E + "/items/4", data=form)
    add(client, "page_break")                                                               # 5
    add(client, "text_input")                                                               # 6
    client.post(E + "/items/6", data={"code": "frei", "question": "Frei", "answer_mode": "optional"})
    with app.app_context():
        sids = [s["id"] for s in db.session.get(Item, 2).config["statements"]]
    url = _start(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", **{f"i2_{sid}": "4" for sid in sids}})
    page = p.get(url).data.decode()
    assert 'name="i4_value" value="4.000"' in page and 'name="i4_band" value="3"' in page
    p.post(url, data={"nav": "next", "i4_value": "1.000", "i4_band": "1"})          # a browser that sends something else
    with app.app_context():
        assert _real(app).position == 3 and _real(app).answers["4"] == {"value": 4.0, "band": 3}
    q = app.test_client()                                                           # nothing placed on the page: nothing stored
    q.get(url)
    q.post(url, data=CONSENT_OK)
    q.post(url, data={"nav": "next", "nudge_ack": "1"})
    assert 'name="i4_value"' not in q.get(url).data.decode()
    q.post(url, data={"nav": "next", "i4_value": "5.000", "i4_band": "3"})
    with app.app_context():
        other = db.session.scalars(db.select(Response).filter_by(survey_id=1, is_preview=False).order_by(Response.id)).all()[1]
        assert other.position == 3 and other.answers["4"] is None
        item = db.session.get(Item, 4)                                              # outside a participation: as sent
        assert item.kind.parse_answer(item, MultiDict([("i4_value", "2.000"), ("i4_band", "1")])) == {"value": 2.0, "band": 1}


# --- K29: the cookie names a participation by its id and its pid ----------------------------------------------


def test_a_row_id_given_to_another_participation_is_not_this_browsers(app, client, survey):
    test_participate.build(client)
    url = _start(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    with app.app_context():
        mine = _real(app)
        rid, pid = mine.id, mine.pid
    with p.session_transaction() as s:
        assert s["r1"] == rid and s["rp1"] == pid
    assert 'name="i2_s1"' in p.get(url).data.decode()                               # the own participation, page 2
    legacy = app.test_client()                                                      # a cookie from before: the id alone
    with legacy.session_transaction() as s:
        s["r1"] = rid
    assert 'name="i2_s1"' in legacy.get(url).data.decode()
    with app.app_context():                                                         # the row goes, its id comes again
        db.session.delete(db.session.get(Response, rid))
        db.session.commit()
        db.session.add(Response(id=rid, survey_id=1, source="real", status=Response.COMPLETED, pid="someone-else",
                                answers={"1": "1", "3": "44"}, started_at=utcnow(), completed_at=utcnow()))
        db.session.commit()
    page = p.get(url).data.decode()
    assert 'name="i1_c_voluntary"' in page and "Fast fertig" not in page            # a fresh start, not the other's end
    p.post(url, data=CONSENT_OK)
    with app.app_context():
        other = db.session.get(Response, rid)
        assert other.pid == "someone-else" and other.status == Response.COMPLETED and other.answers["3"] == "44"
        new = db.session.scalar(db.select(Response).filter(Response.id != rid, Response.survey_id == 1,
                                                           Response.is_preview.is_(False)))
    with p.session_transaction() as s:
        assert s["r1"] == new.id and s["rp1"] == new.pid


# --- K30: nothing a participant typed reaches the server log --------------------------------------------------


def test_unreadable_answer_is_logged_without_its_message(app, client, survey, caplog):
    add(client, "text_input")                                                               # 2
    with app.app_context(), caplog.at_level("WARNING"):
        app.logger.propagate = True
        item = db.session.get(Item, 2)
        runtime._log_unreadable(item, ValueError("invalid literal for int() with base 10: 'mein Geheimnis'"))
    assert "Unreadable answer for item 2 (text_input): ValueError" in caplog.text
    assert "Geheimnis" not in caplog.text and "invalid literal" not in caplog.text
