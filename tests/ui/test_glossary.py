"""The researchers' pages use the words of docs/glossar.md: none of the words its tables list as "nicht", in German in a
template, in the messages the Python code shows (flash, findings) and in the German halves of de_en(), in English in
the English halves of de_en(), Both() and of ui() outside the participants' pages; German also inside a compound word
("Buttontext").

The words come from the glossary itself (wording audit of 2026-10-16, W-12): a word in the column "nicht" is wrong in
every use; a word with a note in brackets ("Gruppe (für Ablauf-Bausteine)") is wrong in that use only and stays with
review. A compound the glossary names as a term itself ("Tagebuchstudie", "Studienplan") is no finding."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
GLOSSARY = ROOT / "docs" / "glossar.md"
# pages for participants and the public start page speak to other readers (a consent may say "Studie", the join
# page of a diary study "diary study")
OUTSIDE = ("participant", "public", "items/consent", "diary/join.html")
OUTSIDE_PY = ("app/items/consent.py",)
COMMENT = re.compile(r"\{#.*?#\}", re.S)
# ui("Merkmal", "Attribute") and de_en("Merkmal", "Attribute"): the English half is no German word
ENGLISH = re.compile(r"""\b(?:ui|de_en)\(("[^"]*"|'[^']*'),\s*(?:"[^"]*"|'[^']*')""")
FIELD = re.compile(r"\{\w*\}")  # the name of a value ("{persons}") is no word of the text
CLASSES = re.compile(r'\b(?:class|id|data-[\w-]+)="[^"]*"')  # names in the markup are no words of the page
# names of code and products that hold a ruled-out word: no word of the page
NAMES = {"BigBlueButton", "AttributeError", "Studierende"}
# the count word "responses" is ruled out with a number before it ("12 responses"), not in "responses to the item"
WORD, UPPER, REST = r"[A-Za-zÄÖÜäöüß]+", r"[A-ZÄÖÜ][a-zäöüß]+", r"[A-Za-zÄÖÜäöüß-]*"
COUNTED_RESPONSES = r"complete responses|\d[\d,.]*\s+(?:completed?\s+)?responses"


def _split(cell):
    """The entries of a table cell, split at ", " outside brackets."""
    out, depth, start = [], 0, 0
    for i, c in enumerate(cell):
        depth += c == "(" and 1 or c == ")" and -1 or 0
        if depth == 0 and cell.startswith(", ", i):
            out.append(cell[start:i])
            start = i + 2
    return [e.strip() for e in out + [cell[start:]] if e.strip()]


def _table(header):
    """The rows (lists of cells) of the glossary table whose header begins with `header`."""
    rows, inside = [], False
    for line in GLOSSARY.read_text(encoding="utf-8").splitlines():
        if line.startswith(header):
            inside = True
        elif inside and line.startswith("|---"):
            continue
        elif inside and line.startswith("|"):
            rows.append([c.strip() for c in line.strip().strip("|").split(" | ")])
        elif inside:
            break
    return rows


def ruled_out(header):
    """The words of the column "nicht" that are wrong in every use (no note in brackets)."""
    return sorted({e for row in _table(header) for e in _split(row[-1]) if "(" not in e})


def terms():
    """The words the glossary names as terms: bold in the German table, the cells of the English table."""
    words = set()
    for row in _table("| Begriff |"):
        words |= set(re.findall(r"\*\*(.+?)\*\*", row[0] + " " + row[1]))
    for row in _table("| Deutsch |"):
        words |= {w for cell in row[:2] for w in _split(cell)}
    return {w for t in words for w in re.findall(r"[\w-]+", t)}


def _german(words):
    """One pattern for the German words: whole, inflected or as part of a compound ("Konstrukt-Toolbox",
    "Buttontext", lowercase inside a word: "Antwortbutton"); names in code ("fold_all_button") are no compounds."""
    alts = []
    for w in sorted(words, key=len, reverse=True):
        e = re.escape(w)
        alts.append(rf"(?:{WORD}-)*{e}{REST}")
        if len(w) > 5:  # a short word hides inside others ("Duell" in "Individuelle")
            inner = re.escape(w[0].lower() + w[1:]) if w[0].isupper() else e
            alts.append(rf"{UPPER}{inner}{REST}")
    return re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?![\w-])")


def _english(words):
    alts = []
    for w in sorted(words, key=len, reverse=True):
        e = re.escape(w)
        alts.append(e[:-1] + "(?:y|ies)" if w.endswith("y") else e + "(?:s|es)?")
    return re.compile(r"(?i)(?<![\w-])(?:" + "|".join(alts + [COUNTED_RESPONSES]) + r")(?![\w-])")


GERMAN_NOT = _german(ruled_out("| Begriff |"))
ENGLISH_NOT = _english(ruled_out("| Deutsch |"))
TERMS = terms() | NAMES


def _term(word):
    """A term of the glossary or a name, also inflected ("Tagebuchstudien", "Studienplans")."""
    return any(word[:len(word) - k] in TERMS for k in range(4) if word[len(word) - k:] in ("", "e", "n", "r", "s", "en", "es", "er", "ern"))


def found_in(text):
    return [m.group(0) for m in GERMAN_NOT.finditer(text) if not _term(m.group(0))]


def test_no_template_uses_a_word_the_glossary_rules_out():
    found = []
    for path in TEMPLATES.rglob("*.html"):
        rel = path.relative_to(TEMPLATES).as_posix()
        if rel.startswith(OUTSIDE):
            continue
        text = ENGLISH.sub(r"ui(\1)", CLASSES.sub("", COMMENT.sub("", path.read_text(encoding="utf-8"))))
        found += [f"{rel}: {word}" for word in found_in(text)]
    assert not found, "; ".join(found)


def _messages(tree):
    """The texts of flash(...), Finding(...) and of ("error"|"warning"|..., text) pairs: what researchers read."""
    for n in ast.walk(tree):
        parts = []
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("flash", "Finding") and n.args:
            parts = n.args
        elif isinstance(n, ast.Tuple) and len(n.elts) >= 2 and isinstance(n.elts[0], ast.Constant) \
                and n.elts[0].value in ("error", "warning", "info", "success"):
            parts = n.elts[1:]
        for part in parts:
            for c in ast.walk(part):
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    yield n.lineno, c.value


def test_no_message_of_the_code_uses_a_word_the_glossary_rules_out():
    found = []
    for path in (ROOT / "app").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in OUTSIDE_PY:
            continue
        for line, text in _messages(ast.parse(path.read_text(encoding="utf-8"))):
            found += [f"{rel}:{line}: {word}" for word in found_in(text)]
    assert not found, "; ".join(found)


def _pairs():
    """(file, German, English) of de_en() and Both() everywhere and of ui() in templates outside the participants'."""
    from tests.ui.test_researcher_i18n import pairs

    out = [(f.replace("\\", "/"), de, en) for f, de, en in pairs()]
    ui = re.compile(r"""\bui\(\s*("[^"]*"|'[^']*')\s*,\s*("[^"]*"|'[^']*')""")
    for path in TEMPLATES.rglob("*.html"):
        rel = path.relative_to(TEMPLATES).as_posix()
        if not rel.startswith(OUTSIDE):
            out += [(f"app/templates/{rel}", m.group(1)[1:-1], m.group(2)[1:-1])
                    for m in ui.finditer(path.read_text(encoding="utf-8"))]
    return [(f, de, en) for f, de, en in out
            if not f.removeprefix("app/templates/").startswith(OUTSIDE) and f not in OUTSIDE_PY]


def test_the_german_halves_use_the_words_of_the_glossary():
    found = [f"{f}: {word}" for f, de, _en in _pairs() for word in found_in(de)]
    assert not found, "; ".join(found)


def test_the_english_texts_use_the_words_of_the_glossary():
    from tests.ui.test_researcher_i18n import REFERENCE

    found = [f"{f}: {m.group(0)}" for f, _de, en in _pairs() if not REFERENCE.search(en)
             for m in ENGLISH_NOT.finditer(FIELD.sub("", en))]
    assert not found, "; ".join(found)


def test_the_words_come_from_the_glossary():
    german, english = ruled_out("| Begriff |"), ruled_out("| Deutsch |")
    assert {"Umfrage", "Button", "Teilnehmer", "Registerkarte", "Toolbox", "Abschlusstext"} <= set(german)
    assert {"study", "respondents", "kind of user", "take over", "tag cloud", "handout"} <= set(english)
    assert not [w for w in german + english if "(" in w]  # a word with a note is wrong in one use only
    assert _split("Version (bei Conjoint, MaxDiff), Fassung, 0,80") == ["Version (bei Conjoint, MaxDiff)", "Fassung", "0,80"]


def test_the_check_finds_compounds_and_messages():
    assert found_in("Buttontext und Fragetypen") == ["Buttontext", "Fragetypen"]
    assert found_in("im Reiter „Export“, der Teilnahmelink, die Teilnehmenden, befragten Personen") == []
    assert found_in("Fragebogen-Version 2 aus der Konstrukt-Toolbox, Testläufe") == ["Fragebogen-Version", "Konstrukt-Toolbox", "Testläufe"]
    assert found_in("Fragebogen-Fassung 2, Testdurchläufe, Konstrukte, Tagebuchstudie, Studienplan") == []
    assert found_in("Bild-Attribute, ein Attribut, Attributnamen") == ["Bild-Attribute", "Attribut", "Attributnamen"]
    assert found_in("Merkmale und Stufen, AttributeError, BigBlueButton") == []
    tree = ast.parse('flash("Siehe Registerkarte „Übersetzung“.", "warning")\nout.append(("error", f"Der Teilnahme-Link {x}"))')
    assert [word for _, text in _messages(tree) for word in found_in(text)] == ["Registerkarte", "Teilnahme-Link"]
    assert ENGLISH_NOT.findall("A study with respondents, a question type") == ["study", "respondents", "question type"]
    assert ENGLISH_NOT.findall("ask the operator; of 215 complete responses; 12 responses; the study plan") == [
        "operator", "215 complete responses", "12 responses", "study plan"]
    assert ENGLISH_NOT.findall("Take over the ones that fit; 3 options taken over") == ["Take over", "taken over"]
    assert ENGLISH_NOT.findall("a platform admin, 215 completed participations, the provider of Askurious") == []
