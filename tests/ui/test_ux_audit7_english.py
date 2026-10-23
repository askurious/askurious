"""UI/UX audit 2026-10-07, package G (the English pages, W105 to W119): sentences Askurious builds in Python follow
the language of the page, numbers, per cent, dates and counts are written the English way, and the names Askurious
gives (runs, default titles, sessions, constructs) are English for an English account."""
import os
import re
import sys
from datetime import datetime

from werkzeug.datastructures import MultiDict

from app import design, quotas
from app.extensions import db
from app.models import Survey
from tests.conftest import add

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EN = {"headers": {"Accept-Language": "en"}}  # a request without a signed-in person: the browser's language


def english(client):
    client.post("/account/language", data={"lang": "en"})


# --- W105: the quota check ---------------------------------------------------------------------------------------


def test_w105_quota_gap_and_overlap_in_english_with_their_link(app, client, survey):
    from tests.participation.test_audit_g_concurrency import _coverage

    _coverage(app, client, [("Jung", "j", 1, [(3, "between", "18..29")]), ("Alt", "a", 1, [(3, "between", "29..45")])])
    with app.test_request_context(**EN):
        s = db.session.get(Survey, 1)
        found = quotas.coverage(s, s.quotas[0])
        checked = design.check(s)
    assert any(m.startswith("Quota “Geschlecht x Alter”: the sub-quotas “Jung” and “Alt” overlap, e.g. at age = 29")
               for m in found)
    with app.app_context():                                         # German as before, outside a request
        s = db.session.get(Survey, 1)
        assert any("überschneiden sich" in m for m in quotas.coverage(s, s.quotas[0]))
    assert not [f for f in checked if "Teilquote" in f.message]


def test_w105_the_gap_carries_its_page_not_its_words(app, client, survey):
    from tests.participation.test_audit_g_concurrency import _coverage

    _coverage(app, client, [("Jung", "j", 1, [(3, "between", "18..29")]), ("Alt", "a", 1, [(3, "between", "31..45")])])
    with app.test_request_context(**EN):
        gap = [f for f in design.check(db.session.get(Survey, 1)) if "fits no sub-quota" in f.message]
    assert len(gap) == 1 and gap[0].link == "flow_page.quotas"
    assert gap[0].message == ("Quota “Geschlecht x Alter”: anyone who answers like this (age = 30) fits no sub-quota and "
                              "counts there as “no sub-quota”. Add a sub-quota if these people should count.")
    assert not hasattr(quotas, "GAP")


def test_w105_a_decimal_bound_is_written_with_the_page_mark(app):
    with app.test_request_context(**EN):
        assert quotas._number_text(2.5) == "2.5" and quotas._number_text(3.0) == "3"
    with app.app_context():
        assert quotas._number_text(2.5) == "2,5"


# --- W106, W107: results per hypothesis ------------------------------------------------------------------------


def test_w106_w107_hypotheses_in_the_language_of_the_page(app, client, survey):
    from app import dummy
    from tests.analysis.test_power import _power_plan
    from tests.analysis.test_study_plan import _questions

    _questions(client, app)
    _power_plan(client)
    with app.app_context():
        dummy.generate(db.session.get(Survey, 1), 30, seed=1)
        db.session.commit()
    client.post("/surveys/1/pool", data={"source": ["real", "dummy"]})
    german_page = client.get("/surveys/1/results?go=1").data.decode()
    assert "Was der Test erkennen konnte" in german_page and "Mit " in german_page
    english(client)
    page = client.get("/surveys/1/results?go=1").data.decode()
    sens = page.split("What the test could detect</span>", 1)[1].split("</p>", 1)[0]
    assert sens.strip().startswith("With ") and "power" in sens and "Mit " not in sens
    assert re.search(r"\d+% power", sens) and not re.search(r"\d,\d", sens)
    # the sentence for the report as the code to copy beside the verdict (compact hypotheses, #1150)
    report = page.split('title="For the report: click to copy">', 1)[1].split("</code>", 1)[0]
    assert not re.search(r"\d,\d|= ,\d", report)                    # decimal point, p = .396 (W107)
    assert "as for a German report" not in page and "decimal comma" not in page
    assert "(p without a leading zero)" in page


def test_w106_sem_fit_sentence_in_english():
    from app import sem_model

    fit = {"method": "ml", "chi2": 10.25, "df": 5, "p": 0.07, "cfi": 0.951, "tli": 0.9, "rmsea": 0.05,
           "rmsea_ci": (0.0, 0.1), "srmr": 0.04}
    assert "CFI = 0.95" in sem_model.fit_sentence(fit, "en") and "CFI = 0,95" in sem_model.fit_sentence(fit, "de")
    with open(os.path.join(ROOT, "app", "templates", "surveys", "_sem.html"), encoding="utf-8") as f:
        assert 'fit_sentence(fit, "de")' not in f.read()


# --- W108: passes of a loop -------------------------------------------------------------------------------------


def test_w108_pass_in_english_on_pages_durchgang_in_the_export(monkeypatch):
    from types import SimpleNamespace

    from app import loops

    block = SimpleNamespace(survey=SimpleNamespace(default_language="de"))
    monkeypatch.setattr(loops, "variables", lambda b, rid, lang: {"vm": "dem Auto"})
    assert loops.row_caption(block, "r1", 1) == "Durchgang 1: dem Auto"            # the export
    assert loops.row_caption(block, "r1", 1, lang="en") == "Pass 1: dem Auto"      # an English page
    block.survey.default_language = "en"
    assert loops.row_caption(block, "r1", 2) == "Pass 2: dem Auto"


# --- W109: references -------------------------------------------------------------------------------------------


def test_w109_references_in_english(app):
    from app import apa, methods

    # a web page without a year by a group author
    page = ("Beispielverein. (o. J.). Hilfe zum Versuchsplan. https://example.org/hilfe/versuchsplan.html "
            "(abgerufen am 3. Oktober 2026)")

    with app.test_request_context(**EN):
        html = str(apa.ref(page)["html"])
        assert "(n.d.)" in html and "Retrieved October 3, 2026, from" in html and "abgerufen" not in html
        assert apa.ref("Mann, H. B., & Whitney, D. R. (1947). On a test. Annals, 18(1), 50–60.")["narrative"] \
            == "Mann and Whitney (1947)"
        titles = [t for t, _ in methods.choice_sources([{"type": "conjoint", "hb": None, "robust": True, "dual": True,
                                                         "segments": {"covariates": []}}])]
        assert "Choice models: conditional logit and cluster-robust standard errors" in titles
        assert "Segments: latent class logit and choice of the number of segments" in titles
        assert "CBC experimental design: information of the logit model" in titles
    with app.app_context():
        html = str(apa.ref(page)["html"])
        assert "(o. J.)" in html and "abgerufen am 3. Oktober 2026" in html


# --- W110, W111: the quota table and per cent --------------------------------------------------------------------


def test_w110_quota_conditions_on_the_results_page_in_english(app, client, survey):
    from tests.analysis.test_weights import quota_survey

    quota_survey(app, client)
    english(client)
    page = client.get("/surveys/1/results").data.decode()
    table = page.split('class="quota-table quota-results"', 1)[1].split("</table>", 1)[0]
    assert "%" in table and " %" not in table                                          # one way to write per cent per language
    with app.test_request_context(**EN):
        s = db.session.get(Survey, 1)
        cell = s.quotas[0].cells[0]
        assert quotas.cell_text(s, type(cell)(name="x"), shown=True) == "no condition"
        assert quotas.cell_text(s, type(cell)(name="x")) == "ohne Bedingung"   # files keep German


def _english_halves():
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from tests.ui.test_researcher_i18n import counted_files

    lit = re.compile(r'\s*(?P<q>["\'])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)', re.S)

    def literals(source, i):
        out = []
        while True:
            m = lit.match(source, i)
            if not m:
                return out, i
            out.append(m.group("body"))
            i = m.end()

    for rel in counted_files():
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            source = f.read()
        for m in re.finditer(r"\bde_en\(", source):
            de, i = literals(source, m.end())
            comma = re.match(r"\s*,", source[i:])
            if de and comma:
                en, _ = literals(source, i + comma.end())
                yield rel, "".join(en)


def test_w111_english_halves_write_per_cent_without_a_space():
    """“95% interval”, “80% power”: units.percent_text writes 13% in English, the fixed texts the same."""
    spaced = [(rel, en[:60]) for rel, en in _english_halves() if re.search(r"(?:\d|\}) %", en)]
    assert not spaced, spaced[:5]


def test_w111_pctsign_and_percent_follow_the_page(app, client):
    from flask import render_template_string

    with app.test_request_context(**EN):
        assert render_template_string("{{ 13|pctsign }} {{ 0.125|percent }}") == "13% 13%"
    with app.test_request_context():
        assert render_template_string("{{ 13|pctsign }} {{ 0.125|percent }}") == "13 % 13 %"


# --- W112: hints of the weighting -------------------------------------------------------------------------------


def test_w112_weighting_hints_in_english(app, client, survey):
    from app import results
    from tests.quotas.test_quotas import build
    from tests.analysis.test_weights import W, people

    build(app, client, cells=[("Männer", "m", 25, [(2, "eq", "1")]), ("Frauen", "f", 75, [(2, "eq", "2")])])
    people(app, ["1", "1"])                                           # no woman: her target share goes to the men
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    with app.test_request_context(**EN):
        found = results.compute(db.session.get(Survey, 1), weighted=True)["weighting"]
    assert any("no one in “Frauen”" in h and "target share is spread over the other groups" in h for h in found["hints"])
    with app.app_context():
        found = results.compute(db.session.get(Survey, 1), weighted=True)["weighting"]
    assert any("niemand in „Frauen“" in h for h in found["hints"])


# --- W113: dates and times --------------------------------------------------------------------------------------


def test_w113_dates_follow_the_page(app):
    from app import timeutil
    from app.items import date_time

    local = datetime(2026, 10, 5, 17, 28)
    assert timeutil.fmt_local(local, lang="en") == "5 Oct 2026, 17:28"
    assert timeutil.fmt_local(local) == "05.10.2026 17:28"
    assert timeutil.fmt_local(local.date(), lang="en") == "5 Oct 2026"
    aware = datetime(2026, 10, 5, 15, 28, tzinfo=timeutil.timezone.utc)
    assert timeutil.fmt(aware, lang="en") == "5 Oct 2026, 17:28"   # a datetime with a zone: shown in local time
    assert date_time.show("1916-05-12", "en") == "1916-05-12" and date_time.show("1916-05-12") == "12.05.1916"
    assert date_time.show("1987-05-23T14:30", "en") == "1987-05-23 14:30" and date_time.show("1987-05", "en") == "1987-05"


def test_w113_date_results_and_hours_in_english(app):
    from types import SimpleNamespace

    from app.items import date_time

    kind = date_time.DateTimeType() if hasattr(date_time, "DateTimeType") else None
    if kind is None:
        kind = next(v for v in vars(date_time).values() if isinstance(v, type) and hasattr(v, "results")
                    and v.__module__ == date_time.__name__)()
    item = SimpleNamespace(code="d", config={}, type="date_time")
    kind.dk_column = lambda item: "d_dk"
    rows = [{"d": "1916-05-12"}, {"d": "1970-08-31"}, {"d": "2026-09-22"}]
    with app.test_request_context(**EN):
        out = kind.results(item, rows)
    assert (out["min"], out["median"], out["max"]) == ("1916-05-12", "1970-08-31", "2026-09-22")
    with app.test_request_context(**EN):
        hours = kind.results(item, [{"d": "14:30"}, {"d": "15:10"}])
    assert [r["label"] for r in hours["rows"]][:1] == ["14:00"]
    with app.app_context():
        hours = kind.results(item, [{"d": "14:30"}, {"d": "15:10"}])
    assert [r["label"] for r in hours["rows"]][:1] == ["14 Uhr"]


# the places that still write a German date by hand: files and German texts (a manuscript, a README, a note in a
# Word file), the participants' preview, the axis of the AI statistics (already per language)
GERMAN_DATES_KEPT = {
    "app/admin.py", "app/boards/render.py", "app/conjoint_hb.py", "app/cva_hb.py", "app/maxdiff_hb.py", "app/paired_hb.py", "app/segments.py", "app/comments.py",
    "app/interview_docx.py", "app/interview_versions.py", "app/methods.py", "app/prereg.py",
    "app/questionnaire_docx.py", "app/slack_actions.py", "app/coscine.py", "app/ai_stats.py",
}


def test_w113_no_new_german_date_by_hand():
    """A date on a researcher's page goes through timeutil.fmt / |localdt / |walldt; a new %d.%m outside timeutil
    and ui_i18n needs a reason in GERMAN_DATES_KEPT."""
    found = set()
    for folder, ext in (("app", ".py"), (os.path.join("app", "templates"), ".html")):
        for base, _, files in os.walk(os.path.join(ROOT, folder)):
            rel_base = os.path.relpath(base, ROOT).replace(os.sep, "/")
            if rel_base.startswith("app/templates/participant") or (ext == ".py" and rel_base.startswith("app/templates")):
                continue
            for name in files:
                if not name.endswith(ext):
                    continue
                rel = f"{rel_base}/{name}"
                if rel in ("app/timeutil.py", "app/ui_i18n.py"):
                    continue
                with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                    if "%d.%m" in f.read():
                        found.add(rel)
    assert found <= GERMAN_DATES_KEPT, sorted(found - GERMAN_DATES_KEPT)


# --- W114: one and many -----------------------------------------------------------------------------------------


def test_w114_singular_and_plural(app):
    from app.lang import de_en_n

    one, many = ("{n} Teilnahme heute", "{n} Teilnahmen heute"), ("{n} participation today", "{n} participations today")
    with app.test_request_context(**EN):
        assert de_en_n(1, one, many) == "1 participation today" and de_en_n(2, one, many) == "2 participations today"
        assert de_en_n(0, one, many) == "0 participations today"
    with app.app_context():
        assert de_en_n(1, one, many) == "1 Teilnahme heute" and de_en_n(3, one, many) == "3 Teilnahmen heute"


def test_w114_no_version_s_left():
    for rel in ("app/methods.py", "app/items/conjoint.py", "app/items/maxdiff.py"):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            text = f.read()
        assert "version(s)" not in text and "Version(en)" not in text, rel


# --- W115, W117: names Askurious gives ---------------------------------------------------------------------------


def test_w115_w117_english_names_of_constructs_and_items(app, client):
    from app import constructs

    with app.test_request_context(**EN):
        assert constructs.seed_name("sorgfalt") == "Care taken in participating (self-report)"
    with app.app_context():
        assert constructs.seed_name("sorgfalt") == "Sorgfalt der Teilnahme (Selbstauskunft)"
    english(client)
    page = client.get("/constructs/").data.decode()
    assert re.search(r"\d+ items", page) and not re.search(r"\d+ Items", page)


def test_w115_quality_and_methods_texts_name_the_english_names(app, client, survey):
    english(client)
    quality = client.get("/surveys/1/quality").data.decode()
    assert "“Care taken in participating (self-report)”" in quality and "Sorgfalt der Teilnahme" not in quality


# --- W116: new live polls, interview guides and sessions --------------------------------------------------------


def test_w116_new_live_poll_and_guide_start_in_english(app, client):
    english(client)
    live = client.get("/presenter/new").data.decode()
    assert 'value="en" selected' in live and 'value="de-du" selected' not in live
    from app import live as live_mod

    with app.test_request_context(**EN):
        run = live_mod._run()
        assert run["name"].startswith("Run ") and re.search(r"\d{1,2} [A-Z][a-z]{2} \d{4}", run["name"])
    with app.app_context():
        assert live_mod._run()["name"].startswith("Durchgang ")


def test_w116_unknown_browser_in_the_language_of_the_page(app):
    from app import sessions

    with app.test_request_context(**EN):
        assert sessions.shown_label("") == "Unknown browser"
        assert sessions.shown_label("Unbekannter Browser") == "Unknown browser"     # stored before
        assert sessions.shown_label("Firefox · Windows") == "Firefox · Windows"
    with app.test_request_context():
        assert sessions._label() == "" and sessions.shown_label("") == "Unbekannter Browser"


# --- W118, W119 -------------------------------------------------------------------------------------------------


def test_w118_duration_sentences_are_separated(app, client, survey):
    from tests.conftest import fill_consent

    fill_consent(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.blocks[0].items[0]
        consent.config = {**(consent.config or {}), "duration": 30}
        db.session.commit()
    for _ in range(3):
        add(client, "text_input")
    with app.test_request_context(**EN):
        found = [f.message for f in design.check(db.session.get(Survey, 1)) if f.message.startswith("Consent: about")]
    assert found
    for m in found:
        assert re.search(r"the estimate is .+?[.)]\. Please adjust", m) or "min. Please" in m or " s. Please" in m, m  # under a minute in seconds (U14)


def test_w119_dashboard_fields_have_their_labels_above(app, client, survey):
    r = client.post("/surveys/1/dashboards/new", follow_redirects=True)
    page = r.data.decode()
    part = page.split('board-name', 1)[1].split("</section>", 1)[0]
    assert part.count('class="label-title"') == 2 and 'name="min_tile"' in part


# --- the English results page as a whole (recommended with the package: Python texts outside flash()) ----------


def _content(app):
    """What the researchers wrote: the survey's title, questions, answers, quotas and cells; German there is theirs."""
    import json

    with app.app_context():
        s = db.session.get(Survey, 1)
        parts = [s.title or ""]
        for b in s.blocks:
            parts.append(json.dumps(b.titles or {}, ensure_ascii=False))
            for i in b.items:
                parts.append(json.dumps(i.texts or {}, ensure_ascii=False))
                parts.append(json.dumps(i.config or {}, ensure_ascii=False))
        for q in s.quotas:
            parts.append(q.name)
            parts += [c.name for c in q.cells]
    return "\n".join(parts)


def _german_bits(page, content):
    """Text nodes of a page in German (the words of tests/ui/test_researcher_i18n.py) that are no content."""
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from tests.ui.test_researcher_i18n import german

    # scripts, styles and the sources (German titles of German works stay German)
    page = re.sub(r'<script\b.*?</script>|<style\b.*?</style>|<ul class="(?:refs|learn-sources)">.*?</ul>|<span class="cite-group">.*?</span>',
                  "", page, flags=re.S)
    bits = []
    for text in re.findall(r">([^<>]+)<", page):
        text = " ".join(text.split())
        for part in re.split(r"[·:;,()“”„\"]", text):
            part = part.strip()
            # "Element types" is English (menu "Help and tools"), only the word list counts "Element" as German
            if part and german(part) and part not in content and part != "Element types":
                bits.append(part)
    return bits


def test_english_results_page_speaks_english(app, client, survey):
    """Quotas, weighting and the sentences Askurious builds in Python (W105, W110, W112) on the English results
    page: no German text but what the researchers wrote themselves."""
    from tests.analysis.test_weights import W, quota_survey

    quota_survey(app, client)
    client.post(W, data=MultiDict([("kind", "quotas"), ("w_quota", "qa")]))
    english(client)
    content = _content(app)
    for address in ("/surveys/1/results", "/surveys/1/results?gewichtet=1"):
        bits = _german_bits(client.get(address).data.decode(), content)
        assert not bits, (address, bits[:10])
