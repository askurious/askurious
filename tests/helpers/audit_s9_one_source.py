"""Helpers of the tests from test_s9_one_source.py; the tests themselves are in tests/accounts/test_accounts_audit_findings.py, tests/participation/test_participation_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

What participants see comes from one source (UI/UX audit 2026-10-07, S9): the preview renders the templates of the
participation, standard wordings are taken per language when shown instead of being stored as copies, texts that
fall back to the default language say so, and every element type is rendered in Arabic and checked.
"""
import copy


import glob


import html as html_mod


import io


import json


import os


import re


from html.parser import HTMLParser


from alembic import command


from app import experimental, item_texts, items, migrations


from app.extensions import db


from app.models import Item, Survey


from tests.conftest import CONSENT_OK, add, experiments_on, fill_consent


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


CSS = open(os.path.join(ROOT, "app/static/participant.css"), encoding="utf-8").read()


E = "/surveys/1/editor"


def _set(app, **values):
    with app.app_context():
        s = db.session.get(Survey, 1)
        for k, v in values.items():
            setattr(s, k, v)
        db.session.commit()


class _Ranges(HTMLParser):
    """Each <input type=range> with the classes of its ancestors."""

    VOID = {"input", "img", "br", "hr", "meta", "link", "source", "area", "col", "wbr", "embed", "track", "param"}

    def __init__(self):
        super().__init__()
        self.stack, self.found, self.text, self.skip, self.main = [], [], [], 0, 0
        self.langs, self.said = [], []  # the lang of the open elements; (text, its language) of what is read

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag == "input" and a.get("type") == "range":
            self.found.append([classes] + [c for _, c in self.stack])
        if tag in ("script", "style", "title"):
            self.skip += 1
        if tag == "main":
            self.main += 1
        if tag not in self.VOID:
            self.stack.append((tag, classes))
            self.langs.append(a.get("lang"))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "title"):
            self.skip -= 1
        if tag == "main":
            self.main -= 1
        for n in range(len(self.stack) - 1, -1, -1):
            if self.stack[n][0] == tag:
                del self.stack[n:]
                del self.langs[n:]
                break

    def handle_data(self, data):
        if not self.skip and self.main:  # what participants read: the page, not the preview's bar and comments
            self.text.append(data)
            if data.strip():
                self.said.append((" ".join(data.split()), next((x for x in reversed(self.langs) if x), None)))


def _ltr_classes():
    """The classes kept left to right on right-to-left pages (the rule `[dir="rtl"] … { direction: ltr; }`)."""
    rule = re.search(r"([^{}]*)\{ direction: ltr; \}", CSS[CSS.index("/* Right to left"):]).group(1)
    return set(re.findall(r'\[dir="rtl"\] \.([\w-]+)', rule))


# German texts of their own in the newer types, untranslated on the Arabic page (UI/UX audit 2026-10-09, X41): the
# side-by-side matrix (rows, column questions, answers), the drill-down (lists, entries), the hot spot (areas) and the
# presort of the Q sort
OWN_GERMAN = ("Fahrrad", "Genutzt?", "täglich", "Kosten pro Monat", "Marke", "Golf", "Fahrbahn", "eher dagegen")


def _all_pages_in_arabic(app, client):
    from tests.items.test_drill_down import TABLE
    from tests.items.test_side_by_side import editor_form

    experiments_on(client, *[f[0] for f in experimental.FEATURES])
    for key in sorted(items.REGISTRY):
        add(client, key)
    with app.app_context():
        by_type = {i.type: i.id for i in db.session.scalars(db.select(Item))}
    client.post(f"{E}/items/{by_type['side_by_side']}", data=editor_form(by_type["side_by_side"]))
    client.post(f"{E}/items/{by_type['drill_down']}", data={"code": "auto", "question": "Welches Auto?", "table": TABLE})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.languages = ["de", "ar"]
        for item in (i for b in s.blocks for i in b.items):
            if item.type == "rating":
                item.config = {**item.config, "style": "slider"}
            if item.type == "image_mark":  # a hot spot whose area has a name of its own
                item.config = {**item.config, "mode": "rate", "areas": [{"id": "a1", "code": "1", "box": [0.1, 0.1, 0.3, 0.3]}]}
                item.texts = {**(item.texts or {}), "de": {**(item.texts or {}).get("de", {}), "areas": {"a1": "Fahrbahn"}}}
            if item.type == "q_sort":
                item.texts = {**(item.texts or {}), "de": {**(item.texts or {}).get("de", {}), "pre_low": "eher dagegen"}}
        kinds = sorted({i.type for b in s.blocks for i in b.items})
        db.session.commit()
    pages, n = [], 1
    while n < 60:
        r = client.get(f"{E}/preview?lang=ar&page={n}&device=desktop&validate=0", follow_redirects=True)
        assert r.status_code == 200
        html = r.data.decode()
        pages.append(html)
        if f'data-page="{n}"' not in html or 'data-next class="off"' in html:
            break
        n += 1
    return kinds, pages


# the physical sides participant.css may name: in frames kept left to right on right-to-left pages, rules for
# right-to-left pages themselves, a box centred with left: 50%, and two rules with a right-to-left twin
PHYSICAL_OK = {".p-jump-list", "form.auto-advancing .p-next::after", ".live-dot-track::before", ".live-dot-track::after",
               ".live-fullscreen.is-refused::after"}
