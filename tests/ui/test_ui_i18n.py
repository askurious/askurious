"""Texts of the platform around a questionnaire in every survey language (app/ui_i18n.py, app/ui_catalog.json)."""
import ast
import glob
import importlib
import os
import re

from app.extensions import db
from app.models import Survey
from tests.conftest import CONSENT_OK, add
from tests.helpers.page_pos import page_pos

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


SIE = re.compile(r"\b(Sie|Ihr|Ihre|Ihrem|Ihren|Ihrer|Ihres|Ihnen)\b")


def text_pairs():
    """Every (German, English) text the code passes to ui(), tr() and uiT(), and those of the tables of texts by
    language that go through pick()."""
    found = set()
    lit = r'(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')'
    pat = re.compile(r"ui\(\s*" + lit + r"\s*,\s*" + lit)
    for f in glob.glob(os.path.join(ROOT, "app/templates/**/*.html"), recursive=True):
        found |= {(m.group(1) or m.group(2), m.group(3) or m.group(4)) for m in pat.finditer(open(f, encoding="utf-8").read())}
    for f in glob.glob(os.path.join(ROOT, "app/**/*.py"), recursive=True):
        source = open(f, encoding="utf-8").read()
        for n in ast.walk(ast.parse(source)):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "tr" and len(n.args) >= 3 \
                    and all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in n.args[1:3]):
                found.add((n.args[1].value, n.args[2].value))
        module = None
        for name in set(re.findall(r"pick\(([A-Z_]+)", source)) | set(re.findall(r"\btr\([^()\n]*?\b([A-Z][A-Z_]{3,})\b", source)):
            module = module or importlib.import_module(os.path.relpath(f, ROOT)[:-3].replace(os.sep, "."))
            found |= set(_table_pairs(getattr(module, name, None)))  # None: imported in a function, read where it lives
        for table, at in _looped_tables(source):  # [tr(lang, de, en) for code, _days, de, en in self.DEFAULT_DELAYS]
            module = module or importlib.import_module(os.path.relpath(f, ROOT)[:-3].replace(os.sep, "."))
            value = getattr(module, table, None)
            if value is None:
                # a class attribute (self.DEFAULT_DELAYS); a table of another module (federal_state.STATES) is read there
                value = next((getattr(c, table) for c in vars(module).values() if isinstance(c, type) and hasattr(c, table)), ())
            for rows in (value.values() if isinstance(value, dict) else [value]):
                found |= {(row[at[0]], row[at[1]]) for row in rows}
    js = re.compile(r'uiT\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"')
    for f in glob.glob(os.path.join(ROOT, "app/static/*.js")):
        found |= {(m.group(1), m.group(2)) for m in js.finditer(open(f, encoding="utf-8").read())}
    return {(de, en) for de, en in found if en and en.strip()}


def _looped_tables(source):
    """(name of the table, (position of German, of English)) for each tr(lang, de, en) in a comprehension over the rows
    of a table: `for code, _days, de, en in self.DEFAULT_DELAYS`, `for d, e, _ in DEFAULTS[n]` (all rows of a dict)."""
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            continue
        call = n.value if isinstance(n, ast.DictComp) else n.elt
        if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "tr" and len(call.args) >= 3
                and all(isinstance(a, ast.Name) for a in call.args[1:3])):
            continue
        for gen in n.generators:
            source_node = gen.iter.value if isinstance(gen.iter, ast.Subscript) else gen.iter
            name = getattr(source_node, "attr", None) or getattr(source_node, "id", None)
            names = [getattr(t, "id", None) for t in getattr(gen.target, "elts", [])]
            if name and name.isupper() and call.args[1].id in names and call.args[2].id in names:
                yield name, (names.index(call.args[1].id), names.index(call.args[2].id))


def _table_pairs(table):
    """The (German, English) leaves of a table by language; tables nested in a dict (likert ANCHORS) as well;
    a pair (German, English) as STALE_NOTE."""
    if isinstance(table, tuple) and len(table) == 2 and all(isinstance(t, str) for t in table):
        yield table
    elif isinstance(table, dict) and "en" in table:
        yield from _leaves(table.get("de"), table["en"])
    elif isinstance(table, dict):
        for v in table.values():
            yield from _table_pairs(v)
    elif isinstance(table, (list, tuple)):
        for v in table:
            yield from _table_pairs(v)


def _leaves(de, en):
    if isinstance(en, str):
        yield (de if isinstance(de, str) else en, en)
    elif isinstance(en, (list, tuple)):
        for d, e in zip(de if isinstance(de, (list, tuple)) and len(de) == len(en) else en, en):
            yield from _leaves(d, e)
    elif isinstance(en, dict):
        for k, e in en.items():
            yield from _leaves((de or {}).get(k) if isinstance(de, dict) else None, e)


def english_texts():
    return {en for _, en in text_pairs()}


def test_every_platform_text_is_in_the_catalog_in_every_language():
    from app.surveys import LANGUAGE_NAMES
    from app.ui_i18n import CATALOG, JS_TEXTS

    others = set(LANGUAGE_NAMES) - {"de", "en", "de-du"}
    missing = sorted(en for en in english_texts() | set(JS_TEXTS) if set((CATALOG.get(en) or {})) < others)
    assert not missing, "nicht im Katalog (app/ui_catalog.json): " + "; ".join(missing[:10])


def test_every_text_with_sie_has_its_du_version():
    """German with "du" (de-du): the catalog holds the texts that address people, and none of them says "Sie"."""
    from app.ui_i18n import CATALOG

    missing = sorted(en for de, en in text_pairs() if SIE.search(de) and not (CATALOG.get(en) or {}).get("de-du"))
    assert not missing, "ohne Du-Fassung (de-du): " + "; ".join(missing[:10])
    wrong = sorted(t["de-du"] for t in CATALOG.values() if t.get("de-du") and SIE.search(t["de-du"]))
    assert not wrong, "Du-Fassung mit „Sie“: " + "; ".join(wrong[:5])


def test_tr_keeps_german_and_english_and_fills_values():
    from app.ui_i18n import tr

    assert tr("de", "Weiter", "Next") == "Weiter" and tr("en", "Weiter", "Next") == "Next"
    assert tr("es", "Weiter", "Next") == "Siguiente" and tr("zh-Hans", "Weiter", "Next") == "下一步"
    assert tr("fr", "Bitte wählen Sie mindestens {n} Antworten.", "Please choose at least {n} answers.", n=3) \
        == "Veuillez choisir au moins 3 réponses."
    assert tr("es", "Unbekannt", "Not in the catalog") == "Not in the catalog"   # English as the fallback


def test_a_spanish_participation(app, client, survey):
    from tests.conftest import fill_consent

    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "en", "es"]
        db.session.commit()
    add(client, "text_input")                                                     # item 2
    client.post("/surveys/1/editor/items/2", data={"code": "q1", "question": "Ihr Lieblingsessen?"})
    fill_consent(client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "running"
        url = f"/s/{db.session.get(Survey, 1).code}"
    p = app.test_client()
    html = p.get(url + "?lang=es").data.decode()
    assert '<html lang="es"' in html and ">Privacidad y aviso legal</a>" in html                  # platform texts on the consent page
    privacy = p.get(url + "/privacy?lang=es").data.decode()
    assert "Qué se guarda" in privacy and "Sus respuestas se guardan" in privacy
    p.post(url, data=CONSENT_OK)
    page = p.get(url).data.decode()
    assert "Enviar" in page and "Absenden" not in page                            # the last page: "Submit"
    done = p.post(url, data={"nav": "next", "i2": "Paella", "pos": page_pos(p, 1)}, follow_redirects=True).data.decode()
    assert "Gracias por participar" in done


def test_scripts_get_their_texts_only_for_other_languages(app):
    from app.ui_i18n import for_js

    assert for_js("de") == {} and for_js("en") == {}
    assert for_js("pl")["{n} words marked"] == "Zaznaczono słów: {n}"


def test_messages_of_elements_in_every_language():
    """Messages that were German or English only (2026-10-06): Spanish gets Spanish, du gets du, not German."""
    from app.items.consent import pool_text
    from app.participate import end_text

    assert pool_text("es", ["ATI"]).startswith("**Cuestionarios estandarizados**") and "(ATI)" in pool_text("es", ["ATI"])
    assert "deine Einwilligung" in pool_text("de-du", ["ATI"])

    class Survey:
        end_texts = None

    assert "Du kannst dieses Fenster" in end_text(Survey(), "de-du", "thanks")


def test_dates_and_federal_states_in_every_language():
    from datetime import date

    from app.items.federal_state import names
    from app.ui_i18n import date_text, day_month

    day = date(2026, 10, 6)
    assert [date_text(day, lang) for lang in ("de", "en", "es", "pl", "zh-Hans")] == \
        ["06.10.2026", "2026-10-06", "06/10/2026", "06.10.2026", "2026年10月6日"]
    assert day_month(day, "de") == "6.10." and day_month(day, "fr") == "06/10"
    assert names("de")["s9"] == "Bayern" and names("en")["s9"] == "Bavaria" and names("es")["s9"] == "Baviera"


def test_standard_consent_text_in_every_language(app, client, survey):
    """The standard text of the consent with every paragraph in the language of the survey (2026-10-06)."""
    from app.items.consent import Consent

    with app.app_context():
        item = next(i for b in db.session.get(Survey, 1).blocks for i in b.items if i.type == "consent")
        es = Consent().template_body(item, "es")
        du = Consent().template_body(item, "de-du")
    assert es.startswith("**¿De qué trata este estudio?**") and "**Anonimato y protección de datos:**" in es
    assert "**Contacto:**" in es and "Responsable del estudio" in es and "The study" not in es
    assert "Deine Teilnahme ist freiwillig." in du and not SIE.search(du)


def test_arabic_reads_right_to_left_and_dutch_portuguese_dates(app, client, survey):
    """Arabic pages carry dir="rtl" (participant.css mirrors them); the others stay left to right (2026-10-06)."""
    from datetime import date

    from app.ui_i18n import date_text, html_dir, tr
    from app.units import writes_like_english

    assert html_dir("ar") == "rtl" and html_dir("nl") == html_dir("de") == "ltr"
    assert date_text(date(2026, 10, 6), "nl") == "06-10-2026" and date_text(date(2026, 10, 6), "pt") == "06/10/2026"
    assert writes_like_english("ar") and not writes_like_english("nl") and not writes_like_english("pt")
    assert tr("nl", "Weiter", "Next") != "Next" and tr("pt", "Weiter", "Next") != "Next" and tr("ar", "Weiter", "Next") != "Next"
    from tests.conftest import fill_consent

    fill_consent(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages, s.status = ["de", "ar"], "running"
        db.session.commit()
        url = f"/s/{s.code}"
    html = app.test_client().get(url + "?lang=ar").data.decode()
    assert '<html lang="ar" dir="rtl"' in html
    assert '<html lang="de" dir="ltr"' in app.test_client().get(url + "?lang=de").data.decode()
