"""Every element type describes itself (app/element_types.py, concept of 2026-10-08): a fact sheet (Steckbrief) in German and English,
the page "Elementtypen", what AInstein reads, and docs/elementtypen.md written from the fact sheets.

A new type without a fact sheet fails here; so does docs/elementtypen.md when a fact sheet changed. Write the file anew with

    python tests/items/test_element_types.py --update
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DOC = Path(__file__).resolve().parents[2] / "docs" / "elementtypen.md"


def test_every_type_has_a_whole_fact_sheet_in_both_languages():
    from app import items
    from app.ainstein_designs import DESIGN_NAMES
    from app.element_types import GROUPS
    from app.items.base import FactSheet

    wrong = []
    for key, kind in items.REGISTRY.items():
        p = kind.fact_sheet
        if not isinstance(p, FactSheet):
            wrong.append(f"{key}: kein Steckbrief (fact_sheet = FactSheet(...))")
            continue
        if p.group not in GROUPS:
            wrong.append(f"{key}: Gruppe {p.group!r}")
        if not p.can or not p.good_for:
            wrong.append(f"{key}: „can“ und „good_for“ brauchen je mindestens einen Eintrag")
        for text in p.can + p.good_for + p.not_for + p.examples:
            if not (isinstance(text, tuple) and len(text) == 2 and all(isinstance(t, str) and t.strip() for t in text)):
                wrong.append(f"{key}: {text!r} ist kein Paar (Deutsch, Englisch)")
        wrong += [f"{key}: Design {d!r}" for d in p.designs if d not in DESIGN_NAMES]
    assert not wrong, "; ".join(wrong)


def test_english_halves_use_the_glossary():
    from app import items

    words = re.compile(r"\b(?:item types?|question types?|respondents?|stud(?:y|ies))\b", re.I)
    found = [f"{key}: {t[1]}" for key, kind in items.REGISTRY.items()
             for t in kind.fact_sheet.can + kind.fact_sheet.good_for + kind.fact_sheet.not_for + kind.fact_sheet.examples
             if words.search(t[1])]
    assert not found, "; ".join(found)


def test_every_design_name_begins_a_card_of_ainstein():
    from app.ainstein_designs import DESIGN_NAMES, DESIGNS

    titles = [line[2:] for line in DESIGNS.splitlines() if line.startswith("- ")]
    assert len(titles) == len(DESIGN_NAMES)
    for _de, en in DESIGN_NAMES.values():
        assert any(t.startswith(en) for t in titles), en


def test_facts_come_from_the_class_attributes():
    from app import items
    from app.element_types import facts

    conjoint = [en for _de, en in facts(items.get("conjoint"))]
    assert "Not in loops" in conjoint and "Stands alone on its page" in conjoint
    assert "Measurement level: ordinal or metric (switchable)" in [en for _de, en in facts(items.get("likert"))]
    assert ("Ohne Antwort", "No answer") in facts(items.get("info"))
    assert facts(items.get("iat_feedback"))[0][1] == "Only in a survey with Implicit Association Test (IAT)"


def test_the_file_is_written_from_the_fact_sheets():
    from app import element_types

    assert DOC.read_text(encoding="utf-8") == element_types.markdown(), \
        "docs/elementtypen.md neu schreiben: python tests/items/test_element_types.py --update"


def test_the_file_lists_every_type_and_marks_the_experimental_ones():
    from app import items

    text = DOC.read_text(encoding="utf-8")
    assert set(re.findall(r"\*\* \(`([a-z0-9_]+)`", text)) == set(items.REGISTRY)
    for key, kind in items.REGISTRY.items():
        if getattr(kind, "experimental", None):
            assert f"(`{key}`, experimentell)" in text, key


def test_the_page_lists_the_types_with_the_own_usage(client, survey):
    from tests.conftest import add

    add(client, "likert")
    page = client.get("/itemtypes/").get_data(as_text=True)
    assert "Likert-Skala / Matrix" in page and "Passt nicht für" in page
    assert "in 1 Ihrer Befragungen" in page  # the survey of this researcher
    assert "Sprachnachricht" in page  # experimental until 2026-10-16, now for everyone
    one = client.get("/itemtypes/?q=Postleitzahl").get_data(as_text=True)
    assert 'id="postal_code"' in one and 'id="likert"' not in one
    choice = client.get("/itemtypes/?design=choice").get_data(as_text=True)
    assert 'id="conjoint"' in choice and 'id="likert"' not in choice


def test_the_page_counts_no_surveys_of_others(app, client, survey):
    from tests.conftest import add, allow

    add(client, "slider")
    allow(app, "*", keep=True)
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Andere")
    other.post("/terms/", data={"accept": "1"})
    page = other.get("/itemtypes/").get_data(as_text=True)
    assert 'id="slider"' in page and "„In Ihren Befragungen“ zählt" in page  # the explanation stands, no count
    assert "Ihrer Befragungen" not in page


def test_the_page_in_english(app, client):
    from app.extensions import db
    from app.models import User

    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        user.display = {**(user.display or {}), "lang": "en"}
        db.session.commit()
    page = client.get("/itemtypes/").get_data(as_text=True)
    assert "Element types" in page and "Does not fit" in page and "Likert scale / matrix" in page
    assert "Passt nicht für" not in page


def test_ainstein_gets_a_short_line_always_and_the_fact_sheet_of_named_types(app):
    from app import element_types, items
    from app.ainstein_designs import capabilities

    with app.test_request_context():
        lines = capabilities()
        assert "- Schieberegler:" in lines and "(passt zu: Querschnittsbefragung" in lines
        named = element_types.named_in("Wir planen ein Conjoint und danach eine Likert-Skala / Matrix.")
        assert [k.key for k in named] == ["likert", "conjoint"]
        assert element_types.named_in("Ein Text ohne Namen") == [items.get("info")]  # „Text“ is a name: harmless
        detail = element_types.details(items.get("conjoint"))
        assert "passt nicht für:" in detail and "Nicht in Schleifen" in detail


if __name__ == "__main__" and "--update" in sys.argv:
    from app import element_types

    DOC.write_text(element_types.markdown(), encoding="utf-8", newline="\n")
    print(f"{DOC} neu geschrieben")
