"""The language of the researchers' and admins' pages: German (the standard) or English, chosen per person on the page
"Konto" (concept "Mehrsprachige Oberfläche für Forschende und Admins", 2026-10-06, E1 to E16 as recommended).

The code writes each text in both languages side by side: de_en("Konto", "Account") in templates and Python, the same
de_en() in the scripts (app.js; it reads <html lang>). Texts with values use str.format fields and pass the values as
keywords: de_en("{n} Befragungen", "{n} surveys", n=3). Longer texts stand as tables {"de": ..., "en": ...} and go
through pick(). The participants' pages are a different thing: ui() and tr() of app/ui_i18n.py in every survey
language. tests/ui/test_researcher_i18n.py keeps new German text out of these pages unless it has its English half.

Signed in, the language is the person's (User.display["lang"]); before signing in, the browser's (German unless it
prefers English), and a new account takes that once. Outside a request it is German.
"""
from flask import g, has_request_context, request
from flask_login import current_user
from markupsafe import Markup

from app import brand

LANGUAGES = {"de": "Deutsch", "en": "English"}  # each in its own language: the buttons on "Konto"
STANDARD = "de"


def of(user):
    """The language a person chose; German if none."""
    display = getattr(user, "display", None)
    chosen = display.get("lang") if isinstance(display, dict) else None
    return chosen if chosen in LANGUAGES else STANDARD


def browser():
    """Before signing in: what the visitor picked in the header (public.language, kept in the session; Philipp,
    2026-10-09), else the browser's choice between German and English (Accept-Language); German if it names
    neither."""
    if not has_request_context():
        return STANDARD
    from flask import session

    if session.get(PICKED) in LANGUAGES:
        return session[PICKED]
    return request.accept_languages.best_match(tuple(LANGUAGES)) or STANDARD


PICKED = "ui_lang"  # in the session: the language a visitor picked in the header


def current():
    """The language of the page being built."""
    if not has_request_context():
        return STANDARD
    if "ui_lang" not in g:
        user = current_user if current_user else None
        g.ui_lang = of(user) if getattr(user, "is_authenticated", False) else browser()
    return g.ui_lang


def english():
    return current() == "en"


def de_en(de, en, html=False, **values):
    """The text in the language of the page: `de` or `en`, with the values filled in. html=True: the text is HTML
    (<strong>, <code>), the values are escaped unless they are Markup themselves; the result is Markup. Use it instead
    of `de_en(...)|safe`, which put every value in raw (audit 2026-10-13, XS-5)."""
    text = en if english() else de
    text = brand.apply(text)  # the name of the domain, only in this text, not in its values (app/brand.py, M1)
    if html or isinstance(de, Markup):
        text = Markup(text)
        return text.format(**values) if values else text
    if values:
        text = text.format(**values)
    return text


def de_en_n(n, de, en, **values):
    """de_en() for a count: de and en are pairs (singular, plural), chosen by n (one: singular; also 1.0) and
    filled with n and the values: de_en_n(n, ("{n} Teilnahme", "{n} Teilnahmen"), ("{n} participation",
    "{n} participations")) gives "1 participation" and "2 participations" (UI/UX audit 2026-10-07, W114: no
    "1 participations", no "version(s)")."""
    one = n == 1
    return de_en(de[0] if one else de[1], en[0] if one else en[1], n=n, **values)


def pick(table):
    """A table of texts by language ({"de": ..., "en": ...}) in the language of the page."""
    return table.get(current()) or table[STANDARD]


class Both:
    """A text in German and English as a class attribute, read in the language of the page:
    `label = Both("Likert-Skala / Matrix", "Likert scale / matrix")` on an ItemType gives the German or the English
    text wherever `kind.label` is used. Only for class attributes; elsewhere call de_en() when the text is shown."""

    def __init__(self, de, en):
        self.de, self.en = de, en

    def __get__(self, obj, owner=None):
        return de_en(self.de, self.en)
