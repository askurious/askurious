"""Text highlighting: participants mark words of a short text and so assign them to one of one to three
categories (by default positive and negative), e.g. to see what in a scenario or a product description
appeals or puts people off. Not every word has to be marked.

The text is split into words once (tokens); a word is addressed by its number in the text. Words can only be
marked one by one so far (no fixed phrases, no parts that cannot be marked).

Answer: {"<word number>": category 1..3} for the marked words. Export: <code>_w<n> per word (0 = not marked,
1..3 = category) and <code>_c<k> (number of words in category k). The text counts as structure: once the
survey runs it cannot change any more, as the word numbers would no longer fit."""
import json
import re

from markupsafe import Markup, escape

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_one, r_rows  # rules for survey files, see base.py
from app.items.base import AnswerError, ItemType, default_texts, in_lang, load_json, prepared, whole
from app.lang import Both, de_en
from app.ui_i18n import tr

WORD = re.compile(r"\w[\w'’\-]*")
COLORS = {  # name in the editor, background of a marked word (light, with dark text in both themes)
    "yellow": ("Gelb", "#ffe680"), "green": ("Grün", "#a8e6b0"), "red": ("Rot", "#f7b0aa"),
    "blue": ("Blau", "#b3d1f7"), "grey": ("Grau", "#dcdcd6"), "violet": ("Violett", "#dccaf7"),
}
DEFAULTS = {  # suggested categories per number of categories: (de, en, colour)
    1: [("wichtig", "important", "yellow")],
    2: [("positiv", "positive", "green"), ("negativ", "negative", "red")],
    3: [("positiv", "positive", "green"), ("negativ", "negative", "red"), ("neutral", "neutral", "blue")],
}


def tokens(text):
    """[(word number or None, text)]: the words numbered from 1, everything between them as it is."""
    out, pos, n = [], 0, 0
    for m in WORD.finditer(text or ""):
        if m.start() > pos:
            out.append((None, text[pos:m.start()]))
        n += 1
        out.append((n, m.group(0)))
        pos = m.end()
    if pos < len(text or ""):
        out.append((None, text[pos:]))
    return out


def words(text):
    return [t for n, t in tokens(text) if n]


@register
class Highlight(ItemType):
    key = "highlight"
    import_rules = {"cats": r_rows(3, 1, unique=None, color=r_one(*COLORS))}
    label = Both("Texthervorhebung", "Text highlighting")
    short = Both("Wörter in einem Text markieren", "Mark words in a text")
    description = Both("Wörter in einem Text markieren und damit einer Kategorie zuordnen, z. B. positiv oder negativ",
                       "Mark words in a text and so assign them to a category, e.g. positive or negative")
    order = 43
    fact_sheet = FactSheet(
        "tasks",
        keywords=["Textmarker", "Highlighter", "Markieren", "Mark", "Hervorheben", "Highlight"],
        can=[("Wörter markieren und so 1 bis 3 Kategorien mit eigener Farbe zuordnen.",
              "Mark words and so assign them to 1 to 3 categories, each with its own colour.")],
        good_for=[("Welche Stellen eines Textes überzeugen oder stören: Nachrichten, Werbung, Informationstexte",
                   "Which parts of a text convince or bother: news, adverts, information texts")],
        not_for=[("Stellen in einem Bild (dann Bild markieren)", "Spots in an image (then image marking)")],
        designs=["framing", "ab"],
        examples=[("Markieren Sie, was Sie an dieser Meldung glaubwürdig (grün) oder unglaubwürdig (rot) finden.",
                   "Mark what you find credible (green) or not credible (red) in this news item.")],
    )
    levels = ("nominal",)
    allowed_in_loop = False

    def default_config(self):
        return {"cats": [{"color": c} for _, _, c in DEFAULTS[2]]}

    def default_texts(self, lang):
        return {"question": "", "passage": "",
                "cats": [tr(lang, d, e) for d, e, _ in DEFAULTS[2]]}

    def cats(self, item, lang=None):
        """[(number 1..3, label, css colour)]"""
        labels = item.text("cats", lang) or []
        out = []
        for k, c in enumerate(item.config.get("cats") or [], start=1):
            label = labels[k - 1] if k - 1 < len(labels) and labels[k - 1] else f"Kategorie {k}"
            out.append((k, label, COLORS.get(c.get("color"), COLORS["yellow"])[1]))
        return out

    def read_shape(self, item):
        """The words of the text in the default language and the number of categories: an answer holds word
        numbers and category numbers, so another text or fewer categories would put earlier marks on other words
        (audit 2026-10-12, N-2)."""
        return {"words": words(default_texts(item).get("passage")), "cats": len((item.config or {}).get("cats") or [])}

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        mine = {**texts.get(lang, {}), "question": form.get("question", "").strip()}
        if structural:
            mine["passage"] = form.get("passage", "").strip()
        n_old = len(item.config.get("cats") or [])
        n = n_old
        if structural and whole(form.get("ncats", "")) is not None:
            n = min(max(whole(form["ncats"]), 1), 3)
        if n != n_old:
            # a new number of categories: the suggested names and colours for it, in every language
            item.config = {**item.config, "cats": [{"color": c} for _, _, c in DEFAULTS[n]]}
            for code in set(texts) | {lang}:
                entry = mine if code == lang else dict(texts.get(code, {}))
                entry["cats"] = [tr(code, d, e) for d, e, _ in DEFAULTS[n]]
                texts[code] = entry
        else:
            labels = [s.strip()[:40] for s in form.getlist("cat_label")][:n]
            mine["cats"] = labels + [""] * (n - len(labels))
            colors = [c if c in COLORS else "yellow" for c in form.getlist("cat_color")][:n]
            if structural and len(colors) == n:  # the colour is what participants mark with: part of the design
                item.config = {**item.config, "cats": [{"color": c} for c in colors]}
        texts[lang] = mine
        item.texts = texts

    def validate(self, item):
        survey = item.block.survey
        default = words(item.text("passage"))
        if not default:
            return [de_en("Text zum Markieren fehlt", "text to mark is missing")]
        problems = []
        for lang in survey.languages:
            own = (item.texts or {}).get(lang, {}).get("passage")
            if lang != survey.default_language and own and len(words(own)) != len(default):
                problems.append(de_en("Der Text hat in „{lang}“ {own} statt {n} Wörter; die Wörter werden über ihre Nummer ausgewertet",
                                      "the text has {own} instead of {n} words in “{lang}”; the words are analysed by their number",
                                      lang=lang, own=len(words(own)), n=len(default)))
        return problems

    # --- participant side --------------------------------------------------------

    def passage_html(self, item, lang, answer):
        """The text with every word as a markable element (marked ones carry their category)."""
        marked = answer or {}
        parts = []
        for n, text in tokens(item.text("passage", lang)):
            if n is None:
                parts.append(str(escape(text)))
            else:
                cat = marked.get(str(n))
                parts.append(f'<span class="p-hl-w" data-hl-w="{n}"{f" data-cat={cat}" if cat else ""}>{escape(text)}</span>')
        return Markup("".join(parts))

    def _read(self, item, form, lang=None):
        raw = load_json(form.get(f"i{item.id}") or "{}", {})
        n_words = len(words(item.text("passage", lang)))
        n_cats = len(item.config.get("cats") or [])
        out = {}
        for k, v in (raw.items() if isinstance(raw, dict) else []):
            if whole(k) is not None and 1 <= whole(k) <= n_words and type(v) is int and 1 <= v <= n_cats:
                out[str(whole(k))] = v  # type(): `true` is an int for isinstance and would be stored as true
        return dict(sorted(out.items(), key=lambda kv: int(kv[0])))

    def parse_answer(self, item, form, lang="de", meta=None):
        marked = self._read(item, form, lang)
        if item.required and not marked:
            raise AnswerError(tr(lang, "Bitte markieren Sie mindestens ein Wort.", "Please mark at least one word."))
        return marked or None

    def display_value(self, item, form, meta=None):
        return self._read(item, form) or None

    def is_missing(self, item, answer):
        return not answer

    def dummy_fields(self, item, rng, meta, answers=None):
        n_words = len(words(item.text("passage")))
        n_cats = len(item.config.get("cats") or [])
        if not n_words or not n_cats:
            return {}
        chosen = rng.sample(range(1, n_words + 1), rng.randint(1, max(1, min(8, n_words // 4 or 1))))
        return {f"i{item.id}": json.dumps({str(n): rng.randint(1, n_cats) for n in chosen})}

    def seconds(self, item, read):
        return read + 10 + 0.5 * len(words(item.text("passage")))

    # --- export and results ------------------------------------------------------

    def export_columns(self, item):
        cols = [(f"{item.code}_w{n}", f"Wort {n}: {w}") for n, w in enumerate(words(item.text("passage")), start=1)]
        return cols + [(f"{item.code}_c{k}", f"Anzahl markierter Wörter: {label}") for k, label, _ in self.cats(item)]

    def export_values(self, item, answer):
        # the words and categories once per export, not per participation (code review 2026-10-07, R4)
        word_cols, cat_cols = prepared(item, "highlight_columns", lambda: (
            [(f"{item.code}_w{n}", str(n)) for n in range(1, len(words(item.text("passage"))) + 1)],
            [(f"{item.code}_c{k}", k) for k, _, _ in self.cats(item)]))
        out = {column: (None if answer is None else (answer or {}).get(n, 0)) for column, n in word_cols}
        for column, k in cat_cols:
            out[column] = None if answer is None else sum(1 for v in answer.values() if v == k)
        return out

    def codebook_info(self, item, column, lang, ui=None):
        if re.search(r"_c\d+$", column):
            return "Zahl", "", [in_lang(ui, "Anzahl der Wörter, die diese Person der Kategorie zugeordnet hat",
                                        "number of words this person assigned to the category")]
        values = "; ".join(["0 = nicht markiert"] + [f"{k} = {label}" for k, label, _ in self.cats(item, lang)])
        notes = [in_lang(ui, "leer = Element nicht gesehen oder nichts markiert", "empty = element not seen or nothing marked")]
        survey = item.block.survey if item.block is not None else None
        if survey is not None and len(survey.languages) > 1:
            # word n is the n-th word of the text in the language of the participation (audit 2026-10-12, N-10)
            notes.append(in_lang(ui, "Wortnummer im Text der Sprache der Teilnahme (Spalte language); das Wort im "
                                     "Variablenlabel ist das der Standardsprache ({lang}) und kann in einer Übersetzung "
                                     "ein anderes sein",
                                 "word number in the text of the language of the participation (column language); the "
                                 "word in the variable label is that of the default language ({lang}) and may be "
                                 "another one in a translation", lang=survey.default_language))
        return "Kategorie", values, notes

    def result_column(self, item, column):
        if re.search(r"_c\d+$", column):
            return "auto", "", None
        return None, "", None  # the words are shown in the text with their markings (results)

    def results(self, item, table, lang):
        """The text with, per word, how many persons marked it in which category."""
        cats = self.cats(item, lang)
        rows = [r for r in table if any(r.get(f"{item.code}_c{k}") not in (None, "") for k, _, _ in cats)]
        persons = len(rows)
        out = []
        for n, text in tokens(item.text("passage", lang)):
            if n is None:
                out.append({"text": text})
                continue
            counts = [sum(1 for r in rows if str(r.get(f"{item.code}_w{n}")) in (str(k), f"{k}.0")) for k, _, _ in cats]
            out.append({"text": text, "n": n, "counts": counts, "total": sum(counts)})
        top = []
        for i, (k, label, color) in enumerate(cats):
            ranked = sorted((t for t in out if t.get("n") and t["counts"][i]), key=lambda t: (-t["counts"][i], t["n"]))[:8]
            top.append({"label": label, "color": color, "words": [(t["text"], t["counts"][i]) for t in ranked]})
        return {"tokens": out, "cats": cats, "persons": persons, "top": top}

    def questionnaire_lines(self, item, lang):
        cats = ", ".join(label for _, label, _ in self.cats(item, lang))
        return [f"> {line}" if line else ">" for line in (item.text("passage", lang) or "").split("\n")] + \
            ["", f"Kategorien zum Markieren: {cats}"]
