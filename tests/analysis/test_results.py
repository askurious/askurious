from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import results
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def test_statistics_known_values():
    assert results.gini([2, 2, 2, 2]) == 0
    assert abs(results.gini([0, 0, 0, 4]) - 0.75) < 1e-9
    assert abs(results.cronbach_alpha([[1, 1], [2, 2], [3, 3]]) - 1) < 1e-9           # perfectly consistent
    assert results.cronbach_alpha([[1, 4], [4, 2], [2, 3], [5, 1]]) < 0              # contradicting items
    assert abs(results.gini_simpson([5, 5]) - 0.5) < 1e-9 and results.gini_simpson([10]) == 0


def test_page_sources_only_for_what_the_page_shows():
    # D-25, P-4, C-4: one group per procedure the results page shows, nothing for what it does not show
    def heads(data):
        return [h for h, _ in results.page_sources(data)]

    assert heads({"items": []}) == []
    data = {"speeders": 2, "items": [
        {"columns": [{"type": "ord"}, {"type": "freq", "gini_simpson": 0.5}, {"type": "num", "ratio": True, "gini": 0.2},
                     {"type": "num", "unit": "m", "n": 3}]},
        {"columns": [], "scale": {"partial": {"n": 4}}},
        {"columns": [], "psm": {"nms": {"n": 5}}},
        {"columns": [], "maxdiff": {"persons": 3, "anchor_n": 0, "mnl": {"disconnected": True}, "hb": {"result": None}}},
        {"columns": [], "cbc": {"model": {"wtp_ok": True}, "hb": {"result": {"x": 1}}}}],
        "overview": {"rows": [{}, {}]}}
    groups = dict(results.page_sources(data))
    cited = " ".join(" ".join(c) for c in groups.values())
    for author in ("Hyndman", "Gini, C.", "Simpson, E. H.", "Moritz", "Cronbach", "Mazza", "Pearson, K.", "Fisher, R. A.",
                   "Van Westendorp", "Newton, D.", "Louviere", "McFadden", "Cameron", "Hole, A. R.",
                   "Rossi, P. E.", "Leiner, D. J."):
        assert author in cited, author
    assert "Lattery" not in cited and "ACBC" not in cited                     # no anchoring, no adaptive conjoint
    assert len(groups) == len(results.page_sources(data))                     # every procedure once
    only_scale = heads({"items": [{"columns": [{"type": "num", "ratio": False, "gini": None}], "subscales": [{}]}],
                        "overview": {"rows": [{}]}})
    assert only_scale == ["Reliabilität: Cronbachs α und McDonalds ω (Ein-Faktor-Modell, Maximum Likelihood)"]


def test_gini_needs_non_negative_values():
    assert results.gini([-50, -50, 50, 50]) is None                                   # was 0: negatives dropped
    assert results.gini([-10, 10, 20]) is None                                        # was the Gini of [10, 20]
    assert abs(results.gini([1, 2, 3, 4, 5]) - 0.2667) < 1e-4 and results.gini([0, 0, 3, None]) is not None


def _answer(s, answers):
    t0 = datetime(2026, 9, 1, 10)
    s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=300),
                                answers=answers))


def test_multiple_choice_never_seen_is_missing_not_unticked(app, client, survey):
    from app import export

    add(client, "multiple_choice")                                  # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "mc"), ("question", "Welche?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "A"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "B")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        _answer(s, {"2": ["1"]})
        _answer(s, {"2": ["1", "2"]})
        _answer(s, {"2": []})                                       # seen, nothing ticked: 0
        _answer(s, {})                                              # skipped by a condition: missing
        db.session.commit()
        rows = [{k: v for k, v in r.items() if k.startswith("mc_")} for r in export.rows(s)]
        assert {"mc_1": 0, "mc_2": 0} in rows and {"mc_1": None, "mc_2": None} in rows
        e = results.compute(s)["items"][0]
        assert e["answered"] == 3
        # one list of the options (D-9 of the maths audit 2026-10-03), no 0/1 column per option
        assert e["columns"] == []
        (m,) = e["multi"]
        assert m["n"] == 3 and [(r["label"], r["count"], r["n"]) for r in m["rows"]] == [("A", 2, 3), ("B", 1, 3)]


def test_reversed_items_carry_flipped_labels(app, client, survey):
    from app import export
    from app.models import Item

    add(client, "likert")                                           # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "TA"), ("points", "5"), ("labeling", "full"), ("level", "ordinal"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"), ("stmt_reverse", "s2")]))
    add(client, "semantic")                                         # 3
    with app.app_context():
        sd = db.session.get(Item, 3)
        pid = sd.config["pairs"][0]["id"]
        sd.code = "SD"
        sd.config = {**sd.config, "pairs": [{**sd.config["pairs"][0], "reverse": True}]}
        sd.texts = {**sd.texts, "de": {**sd.texts["de"], "pairs": {pid: {"left": "kalt", "right": "warm"}}}}
        s = db.session.get(Survey, 1)
        for _ in range(4):
            _answer(s, {"2": {"s1": 5, "s2": 5}, "3": {pid: 1}})      # "voll und ganz" to B; "kalt" on the pair
        db.session.commit()
        items = {e["item"].code: e for e in results.compute(s)["items"]}
        plain, flipped = items["TA"]["columns"]
        assert plain["median"]["label"] == flipped["median"]["label"] != ""   # same answer, same label
        assert flipped["median"]["value"] == "1"                               # recoded value
        col = items["SD"]["columns"][0]
        top = {r["value"]: r["label"] for r in col["rows"]}
        points = sd.config["points"]
        assert top[str(points)] == "kalt" and top["1"] == "warm"               # recoded: 1 + points - 1
        book = {b[0]: b[3] for b in export.codebook(s)}
        assert book[col["code"]] == f"1 = warm; {points} = kalt"


def setup(app, client, level="metric"):
    add(client, "single_choice")                                    # 2 gender
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich")]))
    add(client, "likert")                                           # 3 scale with two statements, one reversed
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "TA"), ("points", "5"), ("labeling", "full"), ("level", level),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"), ("stmt_reverse", "s2")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        # (gender, s1, s2 raw, seconds): s2 is reversed, so recoded s2 = 6 - raw equals s1 -> alpha 1
        for g, a, b, sec in (("1", 1, 5, 300), ("1", 2, 4, 320), ("2", 4, 2, 310), ("2", 5, 1, 30), ("1", 3, 3, 330)):
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=sec),
                                        answers={"2": g, "3": {"s1": a, "s2": b}}))
        db.session.commit()


def test_results_page_filter_and_stats(app, client, survey):
    setup(app, client)
    # Q-9: an address without drop=1 (or with a source) does not bring the marked participations back
    for address in ("/surveys/1/results?drop=0", "/surveys/1/results?source=real"):
        html = client.get(address).data.decode()
        assert "Grundlage: <strong>4</strong>" in html and "ohne 1 Markierte <span class=\"hint\">(davon 1 Speeder)" in html
    assert "badge-wip" not in html and "Neu berechnen" not in html  # no "in Arbeit" on the tab; computed by itself (tests/analysis/test_results_store.py)
    assert "Cronbachs α = 1,00" in html and "Geschlecht" in html and "weiblich" in html
    with app.app_context():
        r = results.compute(db.session.get(Survey, 1))
        ta = next(e for e in r["items"] if e["item"].code == "TA")
        assert ta["scale"]["alpha"] > 0.999 and ta["columns"][0]["mean"] == 3.0
        assert r["gender"]["columns"][0]["rows"][0] == {"value": "1", "label": "weiblich", "count": 3, "share": 0.6,
                                                       "ci": None}  # 95 % interval from n = 10 on
    html = client.get("/surveys/1/results").data.decode()                       # the page itself: the 30-second case goes
    assert "Grundlage: <strong>4</strong>" in html and "ohne 1 Markierte <span class=\"hint\">(davon 1 Speeder)" in html


def test_gender_shown_once(app, client, survey):
    setup(app, client)
    html = client.get("/surveys/1/results").data.decode()
    assert html.count("<h2>Geschlecht") == 1 and "<h2>gender " not in html


def test_every_item_folds_and_starts_open(app, client, survey):
    setup(app, client)
    html = client.get("/surveys/1/results").data.decode()
    panels = html.count('<section class="panel res-item"') + html.count("<h2>Geschlecht") + html.count('id="scales"')
    assert panels and html.count('<details class="res-fold" open>') == panels
    # every item is open, so the one fold button starts as "Alle zuklappen"; app.js switches it (U10)
    assert html.count("data-fold-all ") == 1 and 'data-label-open="Alle aufklappen"' in html
    assert 'aria-expanded="true"' in html and '>Alle zuklappen</button>' in html


def test_assignment_answers_and_reaction_times_apart(app, client, survey):
    add(client, "assign")                                           # 2
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, 2)
        item.code = "iat"
        cats = [c["code"] for c in item.config["categories"]]
        els = item.config["elements"]
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for n in range(4):
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0, completed_at=t0 + timedelta(seconds=200),
                                        answers={"2": [{"s": e["id"], "c": cats[(n + k) % 2], "rt": 500 + 100 * n}
                                                       for k, e in enumerate(els)]}))
        db.session.commit()
        e0 = els[0]["code"]
    html = client.get("/surveys/1/results").data.decode()
    assert "Reaktionszeiten (ms)" in html and "650 ms" in html                 # mean of 500..800
    assert f"iat_{e0}_rt" in html and "iat_order" not in html
    section = html.split("Reaktionszeiten (ms)")[0].split('<span class="hint">iat · ')[1]
    assert "res-bars" in section                                              # the answers as frequencies above


def test_loop_scale_uses_every_pass(app, client, survey):
    from app import results
    from app.models import Survey

    from tests.editor.test_loops import setup
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        r = results.compute(s, sources=("real", "test", "dummy"), include_incomplete=True)
        e = next(x for x in r["items"] if x["item"].code == "accept")
        assert e["passes"] == 4 and [c["code"] for c in e["columns"]] == [f"accept_{n}" for n in range(1, 5)]
        assert e["answered"] == 0


def test_data_pool_is_the_same_on_export_and_results(app, client, survey):
    import re

    with app.app_context():
        s = db.session.get(Survey, 1)
        for src in ("real", "real", "dummy"):
            s.responses.append(Response(status=Response.COMPLETED, source=src, is_preview=src != "real", answers={},
                                        started_at=datetime(2026, 9, 1), completed_at=datetime(2026, 9, 1, 0, 5)))
        db.session.commit()

    def pool(url):
        html = client.get(url).data.decode()
        box = html.split('id="pool"')[1].split("</form>")[0]
        box = re.sub(r'<summary>.*?</summary>|<div class="settings-body">|<h2>.*?</h2>|<span class="hint">.*?</span>|<input type="hidden"[^>]*>|method="[a-z]+"|id="[a-z-]+-form"|action="[^"]*"| data-autosave-form',
                     "", box)                                                 # the same fields; heading, hints and target differ
        box = re.sub(r'^[^>]*>', "", box)                                     # the tag itself: section or details
        box = re.sub(r'<div class="form-actions autosave-bar".*?</div>', "", box, flags=re.S)  # the saved pool saves by itself
        box = re.sub(r'<div class="row-fields source-choice" id="pool-speeders">.*?</div>', "", box, flags=re.S)  # export only
        box = re.sub(r'<details class="pool-fold"[^>]*>\s*<summary>.*?</summary>|</details>', "", box, flags=re.S)  # export: folded
        box = re.sub(r'<p class="muted small">Im Entwurf.*?</p>', "", box, flags=re.S)  # the basis box says what a draft pools
        return re.sub(r"\s+", " ", box)

    def ticked(box):
        return re.findall(r'name="source" value="([a-z]+)" checked', box)

    client.post("/surveys/1/pool", data={"source": ["real", "dummy"]})
    # the export starts from the saved pool, set on the results page since the UI audit of 2026-10-12 (E6)
    export_pool, results_pool = pool("/surveys/1/data"), pool("/surveys/1/results")
    assert ticked(export_pool) == ticked(results_pool) == ["real", "dummy"]
    assert "echte Teilnahmen (2)" in export_pool and "Dummy-Daten (1)" in export_pool and "Testdurchläufe (0)" in export_pool


def test_method_section_draft_and_ai(app, client, survey, monkeypatch):
    from datetime import timedelta

    from app import ai

    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:age"})     # item 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status, s.closed_at = Survey.CLOSED, datetime(2026, 9, 20, 12)
        s.status_log = [{"at": "2026-09-01T08:00:00", "event": "start"}, {"at": "2026-09-05T08:00:00", "event": "close"},
                        {"at": "2026-09-06T08:00:00", "event": "start"}, {"at": "2026-09-20T12:00:00", "event": "close"}]
        s.declined_count = 2
        t0 = datetime(2026, 9, 2, 10)
        for n, (age, secs) in enumerate([(30, 600), (40, 620), (50, 580), (20, 60)]):  # the last one is a speeder
            s.responses.append(Response(status=Response.COMPLETED, source="real", answers={"2": str(age)},
                                        started_at=t0, completed_at=t0 + timedelta(seconds=secs)))
        s.responses.append(Response(status=Response.ACTIVE, source="real", answers={}, started_at=t0))
        s.responses.append(Response(status=Response.COMPLETED, source="test", answers={}, started_at=t0, completed_at=t0))
        db.session.commit()
    html = client.get("/surveys/1/methods?speeder=50").data.decode()
    for fact in ("01.09.2026", "20.09.2026", "20 Tage", "<dd>7</dd>", "<dd>2</dd>", "N = 3", "9:50 Min.", "4:55 Min.",
                 "unter 50 % des Medians", "M = 40,0, SD = 10,0; 30 bis 50 Jahre"):
        assert fact in html, fact                                              # the facts in columns, the pause left out
    assert 'name="lang" value="de"' in html and 'name="lang" value="en"' in html and 'value="medium" checked' in html
    assert "methods-text" not in html                                          # no text before a button is pressed
    seen = {}

    def fake(instr, prompt, **kw):
        seen.update(instr=instr, prompt=prompt, kw=kw)
        return "Data were collected ..."

    monkeypatch.setattr(ai, "generate_text", fake)
    html = client.post("/surveys/1/methods", data={"speeder": "50", "lang": "en", "length": "short"}).data.decode()
    assert "Data were collected ..." in html and "Englisch, kurz" in html
    assert "Language: English" in seen["instr"] and "short" in seen["instr"] and seen["kw"]["purpose"] == "methods"
    assert "Data were collected from September 1, 2026 to September 20, 2026 (20 days) as an online survey on Askurious.de." in seen["instr"]
    for fact in ("Platform: Askurious.de", "Started: 7", "Did not consent: 2", "Did not finish: 1", "Completed: 4",
                 "Median completion time: 9.8 minutes", "below 50 % of the median, i.e. below 4.9 minutes", "excluded: 1",
                 "Final sample: N = 3", "Age (years, n = 3): M = 40.0, SD = 10.0, range 30 to 50"):
        assert fact in seen["prompt"], fact                                    # every fact goes to the AI
    client.post("/surveys/1/methods", data={"lang": "de", "length": "long"})
    assert "Language: German" in seen["instr"] and "Die Datenerhebung erfolgte im Zeitraum vom 01.09.2026 bis 20.09.2026" in seen["instr"]

    def broken(*a, **kw):
        raise ai.AIError("Kein Schlüssel.")

    monkeypatch.setattr(ai, "generate_text", broken)                          # no AI: the German draft instead
    html = client.post("/surveys/1/methods", data={"lang": "en"}).data.decode()
    assert "Entwurf ohne KI" in html and "Die Datenerhebung erfolgte im Zeitraum vom 01.09.2026" in html
    data_page = client.get("/surveys/1/data").data.decode()
    assert 'href="/surveys/1/methods">Methodenteil öffnen' in data_page  # one methods section, Word and AI there (V29)
    assert data_page.index('id="documentation"') < data_page.index('href="/surveys/1/methods"')  # a row of Dokumentation (E5)


def test_field_days_count_first_and_last_day(app):
    from datetime import time

    from app import lifecycle, methods
    from app.timeutil import last_day, to_local

    with app.app_context():
        s = Survey(title="x", status=Survey.CLOSED, status_log=[{"at": "2026-09-30T08:00:00", "event": "start"}],
                   closed_at=datetime(2026, 9, 30, 15))
        assert methods.field_period(s)[:2] == (datetime(2026, 9, 30, 8), datetime(2026, 9, 30, 15))
        start, end, _ = methods.field_period(s)
        assert (to_local(end).date() - to_local(start).date()).days + 1 == 1       # today to today: 1 day
        today = to_local(lifecycle.utcnow()).date()
        # the default field end is exclusive, midnight at the end of the 30th day (audit 2026-10-18, FUNC-06)
        end = lifecycle.default_field_end()
        assert to_local(end).time() == time(0) and (last_day(end) - today).days + 1 == 30   # 30 days incl. today
        # closed by that field end (the next run after midnight, or a paused one at the field end itself): still
        # 30 field days in the methods section, not 31 for the day the closing ran on
        for closed_at in (end + timedelta(seconds=40), end):
            s = Survey(title="y", status=Survey.CLOSED, close_reason="time", field_end=end, closed_at=closed_at,
                       status_log=[{"at": lifecycle.utcnow().isoformat(), "event": "start"}])
            start, stop, _ = methods.field_period(s)
            assert (to_local(stop).date() - to_local(start).date()).days + 1 == 30
            assert to_local(stop).date() == last_day(end)


def test_ordinal_likert_gets_median_and_quartiles_not_mean(app, client, survey):
    setup(app, client, level="ordinal")
    with app.app_context():
        r = results.compute(db.session.get(Survey, 1))
        ta = next(e for e in r["items"] if e["item"].code == "TA")
        assert ta["scale"] is None and ta["scale_hidden"] and ta["level_label"] == "ordinal"
        col = ta["columns"][0]                                       # s1: 1, 2, 4, 5, 3
        assert col["type"] == "ord" and "mean" not in col and "sd" not in col
        assert col["median"]["value"] == "3" and col["q1"]["value"] == "2" and col["q3"]["value"] == "4"
        assert [r["value"] for r in col["rows"]] == ["1", "2", "3", "4", "5"]   # scale order, all points labelled
        assert abs(col["rows"][-1]["cum"] - 1) < 1e-9 and col["rows"][0]["label"] == "stimme überhaupt nicht zu"
    html = client.get("/surveys/1/results").data.decode()                       # the 30-second case (s1 = 5) goes
    assert "Cronbachs α =" not in html and "Md = 2–3" in html and "Q1–Q3: 1–3" in html
    assert "Likert-Skala / Matrix · ordinal" in html


def test_level_defaults_and_editor_switch(app, client, survey):
    from app.models import Item

    add(client, "likert")                                           # 2
    add(client, "rating")                                           # 3
    add(client, "slider")                                           # 4
    add(client, "single_choice")                                    # 5
    add(client, "multiple_choice")                                  # 6
    with app.app_context():
        levels = {i: db.session.get(Item, i).kind.level(db.session.get(Item, i)) for i in range(2, 7)}
        assert levels == {2: "ordinal", 3: "ordinal", 4: "metric", 5: "nominal", 6: "nominal"}
        rating = db.session.get(Item, 3)
        rating.config = {**rating.config, "style": "slider"}
        assert rating.kind.level(rating) == "metric"                   # a rating slider counts as metric
        db.session.commit()
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="level" value="ordinal" checked' in html and 'name="level" value="metric"' in html
    assert 'name="level"' not in client.get("/surveys/1/editor/items/6").data.decode()   # nothing to choose
    client.post("/surveys/1/editor/items/2", data={"code": "L", "level": "metric"})
    client.post("/surveys/1/editor/items/6", data={"code": "M", "level": "metric"})   # not offered: ignored
    with app.app_context():
        assert db.session.get(Item, 2).config["level"] == "metric"
        m = db.session.get(Item, 6)
        assert "level" not in m.config and m.kind.level(m) == "nominal"


def test_constructs_default_to_metric():
    from types import SimpleNamespace

    from app.items import get

    likert = get("likert")
    assert likert.level(SimpleNamespace(config={"construct": {"key": "bfi"}})) == "metric"
    assert likert.level(SimpleNamespace(config={"construct": {"key": "bfi"}, "level": "ordinal"})) == "ordinal"
    assert likert.level(SimpleNamespace(config={"level": "nonsense"})) == "ordinal"


def test_stored_nan_does_not_break_the_results_page(app, client, survey):
    add(client, "text_input")                                       # 2
    client.post("/surveys/1/editor/items/2", data={"code": "amount", "question": "Betrag?", "validation": "decimal"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        for v in (2.5, float("nan"), 4.0):  # nan: stored before the input check rejected it
            s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2),
                                        started_at=datetime(2026, 9, 2), answers={"2": v}))
        db.session.commit()
    assert client.get("/surveys/1/results").status_code == 200


def test_letter_codes_keep_labels_order_and_empty_categories(app, client, survey):
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "sex"), ("question", "Geschlecht?"),
        ("option_id", "a"), ("option_code", "w"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "m"), ("option_label", "männlich"),
        ("option_id", "c"), ("option_code", "d"), ("option_label", "divers; anderes")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for v in ("w", "w", "m"):
            _answer(s, {"2": v})
        db.session.commit()
        rows = results.compute(s)["gender"]["columns"][0]["rows"]
        assert [(r["value"], r["label"], r["count"]) for r in rows] == \
            [("w", "weiblich", 2), ("m", "männlich", 1), ("d", "divers; anderes", 0)]   # was m, w without labels


def test_multiple_choice_is_no_gender_question(app, client, survey):
    add(client, "multiple_choice")                                  # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        _answer(s, {"2": ["1"]})
        db.session.commit()
        assert results.compute(s)["gender"] is None


def test_mixed_questionnaire_versions_are_flagged(app, client, survey):
    setup(app, client)
    assert "Fragebogen-Versionen" not in client.get("/surveys/1/results").data.decode()
    with app.app_context():
        r = db.session.get(Survey, 1).responses[0]
        r.qversion = 2
        db.session.commit()
    html = client.get("/surveys/1/results").data.decode()
    assert "2 Fragebogen-Fassungen (1, 2)" in html and "qversion" in html


def _know(client, level="ordinal"):
    """Single choice 1/2/3 plus 99 = "weiß nicht", ticked as missing."""
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "wissen"), ("question", "Wie gut?"), ("level", level),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "schlecht"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "mittel"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "gut"),
        ("option_id", "d"), ("option_code", "99"), ("option_label", "weiß nicht"), ("option_missing", "d")]))


def test_missing_option_stays_out_of_the_statistics(app, client, survey):
    from app import export
    from app.models import Item

    _know(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["options"][3].get("missing") and item.kind.missing_codes(item, "wissen") == ("99",)
        assert item.kind.validate(item) == []
        s = db.session.get(Survey, 1)
        for v in ("1", "2", "99", "99", "99"):
            _answer(s, {"2": v})
        db.session.commit()
        c = results.compute(s)["items"][0]["columns"][0]
        assert c["type"] == "ord" and c["n"] == 2 and c["median"]["value"] == "1"       # was n = 5, Md = 99
        assert c["max"]["value"] == "2" and [r["value"] for r in c["rows"]] == ["1", "2", "3"]
        assert c["missing"] == [{"value": "99", "label": "weiß nicht", "count": 3}]
        assert results.compute(s)["items"][0]["answered"] == 5                         # they did answer
        assert [r["wissen"] for r in export.rows(s)].count("99") == 3                  # the export keeps the code
        book = {b[0]: b for b in export.codebook(s)}
        assert "als fehlend gewertet: 99" in book["wissen"][4]
    html = client.get("/surveys/1/results").data.decode()
    assert "als fehlend gewertet: weiß nicht (3)" in html
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="option_missing" value="d" aria-label="zählt als fehlend" checked' in page


def test_missing_option_needs_two_valid_ones_and_is_single_choice_only(app, client, survey):
    from app.models import Item

    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "x"), ("question", "?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "ja"),
        ("option_id", "d"), ("option_code", "99"), ("option_label", "weiß nicht"), ("option_missing", "d")]))
    add(client, "multiple_choice")                                  # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "m"), ("question", "?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "A"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "B"), ("option_missing", "b")]))
    with app.app_context():
        assert any("nicht als fehlend" in p for p in db.session.get(Item, 2).kind.validate(db.session.get(Item, 2)))
        assert not any(o.get("missing") for o in db.session.get(Item, 3).config["options"])
    assert 'name="option_missing"' not in client.get("/surveys/1/editor/items/3").data.decode()


def test_percentages_round_half_up():
    from app import units

    assert [units.percent(x) for x in (0.125, 0.025, 0.345, 1 / 3, 0, 1)] == [13, 3, 35, 33, 0, 100]   # round(): 12, 2, 34


def test_every_scale_point_shows_even_unlabelled_and_unchosen(app, client, survey):
    from app.models import Item

    add(client, "likert")                                           # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "TA"), ("points", "7"), ("labeling", "endpoints"), ("level", "ordinal"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "A"),
        ("stmt_id", "s2"), ("stmt_code", ""), ("stmt_text", "B"), ("stmt_reverse", "s2")]))
    add(client, "semantic")                                         # 3
    with app.app_context():
        sd = db.session.get(Item, 3)
        pid = sd.config["pairs"][0]["id"]
        sd.code = "SD"
        sd.texts = {**sd.texts, "de": {**sd.texts["de"], "pairs": {pid: {"left": "kalt", "right": "warm"}}}}
        s = db.session.get(Survey, 1)
        for v in (1, 1, 7):
            _answer(s, {"2": {"s1": v, "s2": v}, "3": {pid: v if v <= sd.config["points"] else sd.config["points"]}})
        db.session.commit()
        items = {e["item"].code: e for e in results.compute(s)["items"]}
        plain, flipped = items["TA"]["columns"]
        assert [r["value"] for r in plain["rows"]] == [str(n) for n in range(1, 8)]          # was only 1 and 7
        assert plain["rows"][0]["label"] and plain["rows"][3]["label"] == "" and plain["rows"][0]["count"] == 2
        assert flipped["rows"][0]["label"] == plain["rows"][6]["label"] and flipped["rows"][6]["count"] == 2
        col = items["SD"]["columns"][0]
        points = sd.config["points"]
        assert len(col["rows"]) == points and col["rows"][0]["label"] == "kalt" and col["rows"][-1]["label"] == "warm"
    html = client.get("/surveys/1/results").data.decode()
    assert "Median als Kategorie" in html


def test_median_between_two_levels_names_both(app, client, survey):
    from app.items import get

    rows = [{"value": "1", "count": 2}, {"value": "2", "count": 0}, {"value": "3", "count": 2}]
    assert results._median_upper(rows, 4, rows[0]) is rows[2] and results._median_upper(rows, 4, rows[2]) is None
    labels = {"1": "a", "2": "b", "3": "c"}
    even, odd = results.ordinal_stats([1, 1, 2, 2], labels), results.ordinal_stats([1, 2, 2], labels)
    assert (even["median"]["value"], even["median_hi"]["value"]) == ("1", "2")            # was a silent 1
    assert odd["median"]["value"] == "2" and odd["median_hi"] is None
    kind = get("sample_compare")                                                           # the live comparison agrees
    assert kind._ordinal("x", ["a", "b", "c"], [1, 1, 2, 2], None, None)["median_label"] == "1–2"   # was 1,5
    assert kind._ordinal("x", ["a", "b", "c"], [1, 2, 2], None, None)["median_label"] == "2"
    _know(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for v in ("1", "1", "2", "2", "99"):
            _answer(s, {"2": v})
        db.session.commit()
    assert "Md = 1–2</strong>" in client.get("/surveys/1/results").data.decode()


def test_speeder_flag_uses_the_exported_whole_seconds(app, client, survey):
    from app import export

    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 1, 10)
        for sec in (301, 301, 150.7):                                # median 301 s, 50 % = 150.5 s
            s.responses.append(Response(status=Response.COMPLETED, started_at=t0,
                                        completed_at=t0 + timedelta(seconds=sec), answers={}))
        db.session.commit()
        row = export.rows(s, speeder=50)[2]
        assert row["duration_s"] == 150 and row["speeder"] == 1      # 150 s < 150.5 s, as the slider counts it
        assert row["duration_rel"] == round(150 / 301, 3)


def test_results_report_progress(app, client, survey):
    """"Neu berechnen" waits with a bar: the computation says how far it is, the waiting page reads it via
    its cookie, and the note is gone once the page is there."""
    from app import dummy, progress
    from tests.conftest import fill_consent

    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "name", "question": "Name?"})
    seen = []
    with app.app_context():
        s = db.session.get(Survey, 1)
        dummy.generate(s, 45, seed=1)
        results.compute(s, ["dummy"], progress=lambda share, text: seen.append((share, text)))
    shares = [share for share, _ in seen]
    assert shares == sorted(shares) and 0 < shares[0] < 0.5 and shares[-1] == 0.95
    assert seen[0][1] == "Antworten lesen: Teilnahme 1 von 45" and "Element" in seen[-2][1]
    page = client.get("/surveys/1/results").data.decode()
    assert "data-results-progress" not in page and "Neu berechnen" not in page  # few participations: computed at once

    assert client.get("/surveys/1/results/progress").get_json() == {}          # no token: nothing to tell
    client.set_cookie("results_progress", "abcdefgh12345678", path="/surveys/1/results")
    with app.test_request_context():
        progress.reporter("abcdefgh12345678", 1)(0.25, "Antworten lesen")
        assert progress.reporter("../../etc", 1) is None and progress.read("../../etc", 1) == {}
        assert progress.read("abcdefgh12345678", 2) == {}                         # another user's note
    r = client.get("/surveys/1/results/progress")
    assert r.get_json() == {"share": 0.25, "text": "Antworten lesen"} and r.headers["Cache-Control"] == "no-store"
    assert client.get("/surveys/1/results?source=dummy").status_code == 200
    assert client.get("/surveys/1/results/progress").get_json() == {}          # the page arrived


def test_method_section_without_or_with_own_demographics(app, client, survey, monkeypatch):
    from app import methods
    from tests.conftest import add

    html = client.get("/surveys/1/methods").data.decode()                      # no demographics at all: a note
    assert "Keine Angaben: Der Fragebogen enthält keine Fragen zu Alter oder Geschlecht" in html
    with app.app_context():
        assert "Demographics: none recorded" in methods.facts_text(methods.facts(db.session.get(Survey, 1)))
    add(client, "text_input")                                                  # own age question (item 2)
    client.post("/surveys/1/editor/items/2", data={"code": "alter", "question": "Alter?", "validation": "integer"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 2, 10)
        for age in (20, 40):
            s.responses.append(Response(status=Response.COMPLETED, source="real", answers={"2": str(age)},
                                        started_at=t0, completed_at=t0 + timedelta(minutes=8)))
        db.session.commit()
        f = methods.facts(s)
        assert f["age"]["mean"] == 30 and "Demographics: none" not in methods.facts_text(f)
    html = client.get("/surveys/1/methods").data.decode()
    assert "M = 30,0" in html and 'class="button button-ai"' in html and "data-ai-writing-box" in html
    assert "methods-text ai-text" not in html                                  # the result itself looks normal


def test_nominal_frequencies_are_drawn_most_frequent_first(app, client, survey):
    def options(*extra):
        return MultiDict([("code", "weg"), ("question", "Womit?"), *extra,
                          ("option_id", "a"), ("option_code", "1"), ("option_label", "Auto"),
                          ("option_id", "b"), ("option_code", "2"), ("option_label", "Rad"),
                          ("option_id", "c"), ("option_code", "3"), ("option_label", "Bahn"),
                          ("option_id", "d"), ("option_code", "4"), ("option_label", "Schiff")])

    def labels():
        page = client.get("/surveys/1/results").data.decode()
        bars = page[page.index('class="plain res-bars'):]
        return sorted(("Auto", "Rad", "Bahn", "Schiff"), key=bars.index)

    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=options())
    with app.app_context():
        s = db.session.get(Survey, 1)
        for v in ("2", "2", "2", "3", "3", "1"):
            _answer(s, {"2": v})
        db.session.commit()
        # what is computed keeps the order of the questionnaire (method section, export)
        assert [r["label"] for r in results.compute(s)["items"][0]["columns"][0]["rows"]] == ["Auto", "Rad", "Bahn", "Schiff"]
    assert labels() == ["Rad", "Bahn", "Auto", "Schiff"]          # the figure: most frequent first, none last
    client.post("/surveys/1/editor/items/2", data=options(("level", "ordinal")))
    assert labels() == ["Auto", "Rad", "Bahn", "Schiff"]          # ordered answers keep their order


def test_numbers_of_one_kind_have_the_same_decimals(app, client, survey):
    from app import units
    from app.models import Item

    # the ticks of one axis
    assert units.tick_labels([0, 0.5, 1]) == ["0,0", "0,5", "1,0"] and units.tick_labels([0, 10, 20]) == ["0", "10", "20"]
    assert units.tick_labels([-1, -0.5, 0, 0.5, 1], signed=True) == ["−1,0", "−0,5", "0,0", "+0,5", "+1,0"]
    # lowest and highest answer of a number: both whole, or both with decimals
    add(client, "text_input")                                       # 2
    client.post("/surveys/1/editor/items/2", data={"code": "betrag", "question": "Betrag?", "validation": "decimal"})
    add(client, "text_input")                                       # 3
    client.post("/surveys/1/editor/items/3", data={"code": "anzahl", "question": "Anzahl?", "validation": "integer"})
    add(client, "psm")                                              # 4
    client.post("/surveys/1/editor/items/4", data={"code": "wtp", "question": "Preis?", "product": "Ein Apfel", "currency": "EUR", "decimals": "2"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        for a, b in ((1, 2), (4.5, 7), (3, 9)):
            _answer(s, {"2": a, "3": b})
        db.session.commit()
        price = results._Price(db.session.get(Item, 4), 2)
        assert [price(v) for v in (84.5, 137, 92.666, None)] == ["84,50 €", "137,00 €", "92,67 €", "–"]   # not 84,5 € and 137 €
    page = client.get("/surveys/1/results").data.decode()
    assert "1,00–4,50" in page and "2–9" in page and "1–4,50" not in page


def test_residual_answers_come_last_in_sorted_frequencies():
    """Ralf, 2026-10-03: nominal frequencies stay sorted by count, but "keine Angabe", "weiß nicht" and "Anderes"
    come last whatever their count; equal counts keep the questionnaire's order."""
    from app.results import by_count, residual

    rows = [{"label": "weiblich", "count": 13}, {"label": "keine Angabe", "count": 16}, {"label": "männlich", "count": 16},
            {"label": "Anderes, und zwar", "count": 20}, {"label": "divers", "count": 12}, {"label": "Weiß nicht.", "count": 2}]
    assert [r["label"] for r in by_count(rows)] == ["männlich", "weiblich", "divers", "Anderes, und zwar", "keine Angabe",
                                                    "Weiß nicht."]
    assert residual("Prefer not to say") and residual("<b>keine Angabe</b>") and not residual("Andersrum")


def test_residual_answers_are_found_in_more_wordings():
    """D-15 of the maths audit 2026-10-03: further wordings of "no answer" and "something else" come last."""
    from app.results import residual

    for label in ("Weiß nicht / keine Angabe", "Möchte ich nicht angeben", "k.A.", "Ich weiß es nicht", "Kann ich nicht einschätzen",
                  "Keine der genannten", "Etwas anderes", "Keine Angabe (weiß nicht)"):
        assert residual(label), label
    for label in ("Andersrum", "Abitur", "Ja oder nein", "Kanada"):
        assert not residual(label), label


def test_ordinal_single_choice_keeps_the_order_of_its_options(app, client, survey):
    """D-5 of the maths audit 2026-10-03: codes that do not follow the order of the options (an option put in
    between) do not reorder the scale; the median is a level in the order of the question."""
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "bildung"), ("question", "Abschluss?"), ("level", "ordinal"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Hauptschule"),
        ("option_id", "b"), ("option_code", "3"), ("option_label", "Realschule"),
        ("option_id", "c"), ("option_code", "2"), ("option_label", "Abitur")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for v in ("1", "3", "3", "2", "2"):
            _answer(s, {"2": v})
        db.session.commit()
        col = results.compute(s)["items"][0]["columns"][0]
        assert col["type"] == "ord"
        assert [r["label"] for r in col["rows"]] == ["Hauptschule", "Realschule", "Abitur"]
        assert col["median"]["label"] == "Realschule" and col["median_hi"] is None
        assert [round(r["cum"], 2) for r in col["rows"]] == [0.2, 0.6, 1.0]


def test_multiple_choice_is_one_list_sorted_by_frequency(app, client, survey):
    """D-9 of the maths audit 2026-10-03: one list of the options, per option the share of those who saw it and
    answered the question, the most frequent first and "weiß nicht" last; no 0/1 block per option."""
    add(client, "multiple_choice")                                  # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "obst"), ("question", "Welches Obst?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Apfel"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Birne"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Kiwi"),
        ("option_id", "d"), ("option_code", "9"), ("option_label", "Weiß nicht / keine Angabe")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        for _ in range(8):                                          # Kiwi seen and ticked
            _answer(s, {"2": ["1", "3", "9"]})
        for _ in range(2):                                          # Kiwi seen, not ticked
            _answer(s, {"2": ["1", "9"]})
        for i in range(10):                                         # Kiwi not shown (-77)
            _answer(s, {"2": {"v": ["1", "2"] if i < 5 else ["1", "9"], "h": ["3"]}})
        db.session.commit()
        e = results.compute(s)["items"][0]
        (m,) = e["multi"]
        assert e["columns"] == [] and m["n"] == 20 and m["partial"]
        rows = [(r["label"], r["count"], r["n"]) for r in m["rows"]]
        # Kiwi: 8 of the 10 who saw it (80 %, not 40 %); "weiß nicht" last although 15 ticked it
        assert rows == [("Apfel", 20, 20), ("Kiwi", 8, 10), ("Birne", 5, 20), ("Weiß nicht / keine Angabe", 15, 20)]
    page = client.get("/surveys/1/results").data.decode()
    assert "80 % (8)" in page and "die Anteile ergeben zusammen nicht 100 %" in page and "Gini-Simpson" not in page
    assert page.index(">Apfel<") < page.index(">Kiwi<") < page.index(">Birne<") < page.index(">Weiß nicht / keine Angabe<")


def test_method_section_takes_no_age_from_a_year(app, client, survey):
    """Mathe-Audit X-4: a year of birth under the code "alter" (element Datum / Uhrzeit) is no age."""
    from app import methods
    from app.models import Item

    add(client, "date_time")                                                   # 2
    with app.app_context():
        item = db.session.get(Item, 2)
        item.code, item.config = "alter", {**item.config, "parts": "year"}
        s = db.session.get(Survey, 1)
        t0 = datetime(2026, 9, 2, 10)
        for year in (1980, 1990):
            s.responses.append(Response(status=Response.COMPLETED, source="real", answers={"2": year},
                                        started_at=t0, completed_at=t0 + timedelta(minutes=8)))
        db.session.commit()
        f = methods.facts(s)
        assert f["age"] is None and "im Mittel 1985" not in methods.draft(f)


def test_shares_and_means_get_95_percent_intervals(app, client, survey):
    """Concept statistik-erweiterungen, stage 1 (K5): every list of shares has a Wilson interval per answer, multiple
    choice per option on its own base; numbers a t interval for M; below n = 10 none, with a note."""
    from app import intervals

    add(client, "multiple_choice")                                  # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "obst"), ("question", "Welches Obst?"), ("answer_mode", "optional"),
        ("option_id", "a"), ("option_code", "1"), ("option_label", "Apfel"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "Birne"),
        ("option_id", "c"), ("option_code", "3"), ("option_label", "Kiwi")]))
    add(client, "single_choice")                                    # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "farbe"), ("question", "Farbe?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "rot"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "blau")]))
    add(client, "text_input")                                       # 4
    client.post("/surveys/1/editor/items/4", data={"code": "anzahl", "question": "Anzahl?", "validation": "integer"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        for i in range(20):                                         # Kiwi shown to 10 of them only (-77 for the others)
            obst = ["1", "3"] if i < 7 else ["1"] if i < 10 else {"v": ["1", "2"], "h": ["3"]}
            _answer(s, {"2": obst, "3": "1" if i < 7 else "2", "4": i})
        db.session.commit()
        r = results.compute(s)
        items = {e["item"].code: e for e in r["items"]}
        rows = {row["label"]: row for row in items["obst"]["multi"][0]["rows"]}
        assert rows["Kiwi"]["ci"] == intervals.wilson(7, 10) and rows["Apfel"]["ci"] == intervals.wilson(20, 20)
        assert rows["Birne"]["ci"] == intervals.wilson(10, 20)
        farbe = {row["label"]: row for row in items["farbe"]["columns"][0]["rows"]}
        assert farbe["rot"]["ci"] == intervals.wilson(7, 20)
        num = items["anzahl"]["columns"][0]
        assert num["ci"] == intervals.mean_ci(9.5, num["sd"], 20)
    page = client.get("/surveys/1/results").data.decode()
    # the share first, the count in brackets; the interval as an error bar with the numbers as its title (Ralf, 2026-10-06)
    assert 'title="95-%-Konfidenzintervall (Wilson): 18–57 %"' in page and ">35 % (7)</span>" in page and 'class="res-ci-bar"' in page
    assert "70 % (7) <span class=\"hint\">von 10</span>" in page     # Kiwi on its base
    assert "Fehlerbalken: 95-%-Konfidenzintervall nach Wilson" in page and "res-bars res-bars-ci" in page
    assert "95-%-KI für M" in page and "Wilson, E. B. (1927)" in page
    lo, hi = intervals.mean_ci(9.5, num["sd"], 20)
    assert f"[{lo:.2f}; {hi:.2f}]".replace(".", ",") in page
    # below n = 10: no intervals, a note instead
    with app.app_context():
        s = db.session.get(Survey, 1)
        for response in list(s.responses)[9:]:
            db.session.delete(response)
        db.session.commit()
    page = client.get("/surveys/1/results").data.decode()
    assert "95-%-Konfidenzintervalle erst ab n = 10" in page and "res-ci-bar" not in page
    assert "Wilson, E. B. (1927)" not in page


def test_sources_are_short_references_at_their_places(app, client, survey):
    """Ralf, 2026-10-05: at the place the short reference in APA style, linked to its entry in the one folded list."""
    setup(app, client, level="ordinal")
    html = client.get("/surveys/1/results").data.decode()
    assert '<a class="cite" href="#ref-hyndman1996-sample">Hyndman &amp; Fan, 1996</a>' in html
    assert '<li id="ref-hyndman1996-sample">Hyndman, R. J., &amp; Fan, Y. (1996).' in html
    assert 'class="fn"' not in html and "src-" not in html
    assert results.footnotes([("Gini-Koeffizient", ["G"]), ("Median und Quartile geordneter Antworten", ["H"])]) ==         {"gini": ["G"], "ord": ["H"]}


def test_a_reversed_statement_lists_its_levels_like_its_neighbours(app, client, survey):
    setup(app, client, level="ordinal")
    with app.app_context():
        r = results.compute(db.session.get(Survey, 1))
        ta = next(e for e in r["items"] if e["item"].code == "TA")
        rev = next(c for c in ta["columns"] if c.get("reversed"))
        assert rev["rows"][-1]["cum_shown"] == rev["rows"][-1]["share"]          # summed from the other end
        assert abs(rev["rows"][0]["cum_shown"] - 1) < 1e-9
    html = client.get("/surveys/1/results").data.decode()
    assert ">umgepolt</span>" in html


def test_gini_with_weights():
    """Code review 2026-10-15, C9: the weighted view showed the Gini of the unweighted answers. A weight of 2 is the
    same as the value twice; equal weights give the plain coefficient."""
    xs = [1, 2, 5, 9, None]
    assert results.gini(xs, [1, 1, 1, 1, 1]) == pytest.approx(results.gini(xs))
    assert results.gini([1, 2, 5], [2, 1, 1]) == pytest.approx(results.gini([1, 1, 2, 5]))
    assert results.gini([1, 2, 5], [1, 0, 1]) == pytest.approx(results.gini([1, 5]))
    assert results.gini([-1, 2], [1, 1]) is None
    assert results.numeric_stats([1, 2, 5], [2, 1, 1])["gini"] == pytest.approx(results.gini([1, 1, 2, 5]))
