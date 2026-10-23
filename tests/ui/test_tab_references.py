"""A text that sends people to a tab names a tab that exists. Quotas became a page below "Ablauf" and the live
poll's "Fragen" became "Folien", but ten texts still pointed to the old tabs (UI/UX audit 2026-10-16, finding 34).
Every „Reiter „X““ / “tab “X”” in the app names one of the tabs in surveys/_tabs.html, admin/_tabs.html or the
account and organisation pages."""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
TAB_FILES = ["templates/surveys/_tabs.html", "templates/admin/_tabs.html", "templates/org/_tabs.html"]


def tab_names():
    names = set()
    for rel in TAB_FILES:
        path = APP / rel
        if path.exists():
            text = path.read_text(encoding="utf-8")
            for de, en in re.findall(r'de_en\("([^"]+)",\s*"([^"]+)"\)', text):
                names.update((de, en))
            names.update(re.findall(r'"([A-Z][\w-]+)", (?:none|n_done)', text))  # names in both languages ("Export")
    return names


def test_texts_name_tabs_that_exist():
    names, wrong = tab_names(), []
    pattern = re.compile(r"Reiter „([^“]+)“|[Tt]ab “([^”]+)”")
    for path in list(APP.rglob("*.py")) + list(APP.rglob("*.html")) + list((APP / "static").glob("*.js")):
        if "items" in path.parts:
            continue  # an element's own page has tabs of its own ("Inhalt", "2 · Ablauf")
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in pattern.finditer(text):
            name = m.group(1) or m.group(2)
            if name not in names and not re.search(r'["{]', name):  # a string split over lines, a format field
                wrong.append(f"{path.relative_to(APP)}:{text[:m.start()].count(chr(10)) + 1} „{name}“")
    assert not wrong, "tab that does not exist: " + ", ".join(wrong)
