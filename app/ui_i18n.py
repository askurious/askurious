"""Texts of the platform around a questionnaire (buttons, messages, hints) in every survey language.

The code writes each such text in German and English, as before: ui("Weiter", "Next") in templates, tr(lang,
"Weiter", "Next") in Python. German and English come straight from there; for every other language the English
text is looked up in app/ui_catalog.json ({English: {lang: text}}), else the English text is shown. Texts with
values use str.format fields ("{n} words marked") and pass the values as keywords. The texts researchers write
(questions, options, end texts of their own) are translated on the page "Übersetzung", not here.
"""
import json
import os

from markupsafe import Markup

from app import brand

_PATH = os.path.join(os.path.dirname(__file__), "ui_catalog.json")
try:
    with open(_PATH, encoding="utf-8") as _f:
        CATALOG = json.load(_f)
except (OSError, ValueError):
    CATALOG = {}


GERMAN = ("de", "de-du")  # "de-du": German with the informal "du"; the catalog holds only the texts that differ


def german(lang):
    return lang in GERMAN


def html_lang(lang):
    """The value for <html lang>: the language tag without the variant of address."""
    return "de" if lang == "de-du" else (lang or "de")


RTL = ("ar",)  # survey languages written from right to left


def html_dir(lang):
    """The value for <html dir>: "rtl" for Arabic, else "ltr" (participant.css mirrors the page with logical properties;
    figures, maps, sliders and timed sorting tasks stay left to right)."""
    return "rtl" if lang in RTL else "ltr"


def tr(lang, de, en, **values):
    """The text in `lang`: German, English, or the catalog's translation of the English text."""
    if lang == "de":
        text = de
    elif lang == "en" or not lang:
        text = en
    else:
        text = (CATALOG.get(en) or {}).get(lang) or (de if lang == "de-du" else en)
    text = brand.apply(text)  # the name of the domain, only in this text, not in its values (app/brand.py, M1)
    if values:
        text = text.format(**values)
    return Markup(text) if isinstance(de, Markup) else text


def date_text(day, lang):
    """A date as it is written in `lang`: 06.10.2026 (German, Polish, Russian, Turkish), 06/10/2026 (Spanish, French,
    Italian, Portuguese, Arabic), 06-10-2026 (Dutch), 2026年10月6日 (Chinese); English keeps 2026-10-06, the same in
    British and American English."""
    if german(lang) or lang in ("pl", "ru", "tr"):
        return day.strftime("%d.%m.%Y")
    if lang == "nl":
        return day.strftime("%d-%m-%Y")
    if lang in ("es", "fr", "it", "pt", "ar"):
        return day.strftime("%d/%m/%Y")
    if lang == "zh-Hans":
        return f"{day.year}年{day.month}月{day.day}日"
    return day.isoformat()[:10]


def day_month(day, lang):
    """Day and month without the year, for the axis of a figure: 2.10. (German), 02.10, 02/10, 02-10, 10月2日, 10-02."""
    if german(lang):
        return f"{day.day}.{day.month}."
    if lang in ("pl", "ru", "tr"):
        return day.strftime("%d.%m")
    if lang == "nl":
        return day.strftime("%d-%m")
    if lang in ("es", "fr", "it", "pt", "ar"):
        return day.strftime("%d/%m")
    if lang == "zh-Hans":
        return f"{day.month}月{day.day}日"
    return day.strftime("%m-%d")


def pick(table, lang):
    """A table of texts by language ({"de": ..., "en": ...}; strings, or lists and dicts of them) in `lang`: its
    own entry if it has one, else each English text through the catalog, as tr() does."""
    if lang in table:
        return table[lang]
    return _walk(table.get("de"), table["en"], lang)


def _walk(de, en, lang):
    if isinstance(en, str):
        return tr(lang, de if isinstance(de, str) else en, en)
    if isinstance(en, (list, tuple)):
        de = de if isinstance(de, (list, tuple)) and len(de) == len(en) else en
        return type(en)(_walk(d, e, lang) for d, e in zip(de, en))
    if isinstance(en, dict):
        de = de if isinstance(de, dict) else {}
        return {k: _walk(de.get(k), e, lang) for k, e in en.items()}
    return en


# the texts participant.js writes itself (uiT); the page carries their translation in <body data-ui>
JS_TEXTS = ("All terms sorted. You can continue.", "new pile", "This column is full.", "line", "lines", "spot", "spots", "maximum reached",
            "{n} word marked", "{n} words marked", "marker", "markers", "at least {n}", "questions open for marker {list}",
            "Nothing found.", "Search: Nominatim · data © OpenStreetMap contributors", "Search is unavailable right now.",
            "The place search is unavailable right now. Please try again or use the map.", "Places found: {n}", "Searching …",
            "Show on the map", "✕ Remove", " (required)", "Drag to move", "put back",
            # accessibility concept D2, D5, D7: the keyboard on images, the IAT's way out, what timed tasks say
            "{x}% from the left, {y}% from the top", "Spot {n} marked, {where}.", "Spot {n} removed.", "Spot {n}: {where}",
            "remove", "Remove spot {n}", "Image to mark", "Wrong, please choose the other side.",
            "You said that you cannot do this task. You can continue.", "New choice {n} of {m}: {s} or {l}.",
            "Step 2 of 2: now rate your words.", "Recording stopped, {t}.", "Recording, at most {t}.",
            # video / audio stimulus (items/stimulus.py)
            "Play", "Pause", "Play again", "Ended.", "Mute", "Unmute", "Captions",
            # single choice as a search field (items/choice.py)
            "Type to search", "1 suggestion", "{n} suggestions",
            "“{text}” is not one of the suggestions. Please pick a suggestion or:",
            "“{text}” is not one of the suggestions. Please pick a suggestion.",
            # sorting tasks on the phone (S20): the bar of targets and what a tap did
            "Next card: {card}", "full",
            # UI/UX audit 2026-10-16: the summary of messages (92), the number of a map marker and the position of a video (101)
            "Please check {n} questions:", "Marker {n}", "{t} of {d}")


def for_js(lang):
    """{English: text} for the scripts of the participant pages (participant.js: uiT)."""
    if lang in ("de", "en", None):
        return {}
    return {en: CATALOG[en][lang] for en in JS_TEXTS if (CATALOG.get(en) or {}).get(lang)}  # de-du: those with "du"
