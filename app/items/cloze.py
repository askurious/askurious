"""Cloze text: a text with gaps that participants fill in. A gap is written as [ ] in the text; the number of
spaces between the brackets sets its width: [] or [ ] small, [  ] or [   ] medium, four or more large.

Gap answers are free text without checks, at most 50 characters, on one line. Answer: [text per gap] in the
order of the gaps. Export: one column <code> with the gap answers joined by ";" (a ";" typed into a gap
becomes ","), so the gaps stay apart."""
import re

from markupsafe import Markup, escape

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import AnswerError, ItemType, default_texts, in_lang, rich
from app.lang import Both, de_en
from app.ui_i18n import tr

GAP = re.compile(r"\[( *)\]")
MAX_CHARS = 50
SEPARATOR = ";"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def gap_sizes(text):
    """["s" | "m" | "l"] per gap, in text order."""
    return ["s" if len(m.group(1)) <= 1 else ("m" if len(m.group(1)) <= 3 else "l") for m in GAP.finditer(text or "")]


def clean(value):
    """One line of ordinary text: no control characters, single spaces, no separator, at most 50 characters."""
    value = _CONTROL.sub(" ", value or "").replace(SEPARATOR, ",")
    return re.sub(r"\s+", " ", value).strip()[:MAX_CHARS]


@register
class Cloze(ItemType):
    key = "cloze"
    import_rules = {}
    label = Both("Lückentext", "Cloze text")
    short = Both("Text mit Lücken zum Ausfüllen", "Text with gaps to fill in")
    description = Both("Ein Text mit Lücken zum Ausfüllen, die Lücken in drei Breiten", "A text with gaps to fill in, the gaps in three widths")
    order = 42
    fact_sheet = FactSheet(
        "tasks",
        keywords=["Lücken", "Gaps", "Cloze test", "Fill in the blanks", "Ausfüllen"],
        can=[("Lücken in drei Breiten.", "Gaps in three widths.")],
        good_for=[("Wissenstests und Satzergänzungen", "Knowledge tests and sentence completions")],
        not_for=[("Offene Antworten ohne Textrahmen (dann Freitext)",
                  "Open answers without a text around them (then free text)")],
        designs=["cross"],
        examples=[("Die Hauptstadt von Australien ist ___.", "The capital of Australia is ___.")],
    )
    levels = ("nominal",)
    allowed_in_loop = False

    def default_texts(self, lang):
        return {"question": "", "text": ""}

    def read_shape(self, item):
        """The number of gaps: answers are lists by the place of the gap; also in a survey opened for editing
        again (audit 2026-10-12, N-3)."""
        return {"gaps": len(gap_sizes(default_texts(item).get("text")))}

    def update_from_form(self, item, form, lang, structural):
        texts = dict(item.texts or {})
        mine = {**texts.get(lang, {}), "question": form.get("question", "").strip()}
        text = form.get("text", "").strip()
        if structural or len(gap_sizes(text)) == len(gap_sizes(mine.get("text", ""))):
            mine["text"] = text  # once running, the wording may change, the number of gaps not
        texts[lang] = mine
        item.texts = texts

    def gaps(self, item, lang=None):
        return gap_sizes(item.text("text", lang))

    def validate(self, item):
        survey = item.block.survey
        n = len(self.gaps(item))
        if not n:
            return [de_en("Der Text enthält keine Lücke (als [ ] schreiben)", "the text contains no gap (write it as [ ])")]
        problems = []
        for lang in survey.languages:
            own = (item.texts or {}).get(lang, {}).get("text")
            if lang != survey.default_language and own and len(gap_sizes(own)) != n:
                problems.append(de_en("Der Text hat in „{lang}“ {own} statt {n} Lücken", "the text has {own} instead of {n} gaps in “{lang}”",
                                      lang=lang, own=len(gap_sizes(own)), n=n))
        return problems

    # --- participant side --------------------------------------------------------

    def text_html(self, item, lang, answer, variables=None):
        """The text (paragraphs, lists, **bold** … as in rich) with an input field in every gap. Each gap is described
        by the whole text (participant.html, id cloze<id>), so a screen reader says what it stands in (R62)."""
        given = answer or []
        fields = []

        def gap(m):
            k = len(fields) + 1
            size = "s" if len(m.group(1)) <= 1 else ("m" if len(m.group(1)) <= 3 else "l")
            value = given[k - 1] if k - 1 < len(given) else ""
            fields.append(f'<input type="text" dir="auto" class="p-cloze-gap is-{size}" name="i{item.id}_{k}" value="{escape(value or "")}" '
                          f'maxlength="{MAX_CHARS}" autocomplete="off" spellcheck="true" '
                          f'aria-label="{tr(lang, "Lücke", "Gap")} {k}" aria-describedby="cloze{item.id}">')
            return f"\x02{k - 1}\x03"

        marked = GAP.sub(gap, (item.text("text", lang) or "").replace("\x02", "").replace("\x03", ""))
        html = str(rich(marked, variables))
        # a marker that names no gap (it came with a value put into the text) is dropped
        return Markup(re.sub("\x02(\\d+)\x03", lambda m: fields[int(m.group(1))] if int(m.group(1)) < len(fields) else "", html))

    def _read(self, item, form, lang=None):
        return [clean(form.get(f"i{item.id}_{k}", "")) for k in range(1, len(self.gaps(item, lang)) + 1)]

    def parse_answer(self, item, form, lang="de", meta=None):
        given = self._read(item, form, lang)
        if item.required and not all(given):
            raise AnswerError(tr(lang, "Bitte füllen Sie alle Lücken aus.", "Please fill in all gaps."))
        return given if any(given) else None

    def display_value(self, item, form, meta=None):
        given = self._read(item, form)
        return given if any(given) else None

    def is_missing(self, item, answer):
        return not answer or not all(answer)

    def dummy_fields(self, item, rng, meta, answers=None):
        pool = ["gut", "schnell", "wichtig", "neu", "Vertrauen", "Technik", "Zukunft", "sicher", "teuer", "praktisch"]
        return {f"i{item.id}_{k}": rng.choice(pool) for k in range(1, len(self.gaps(item)) + 1)}

    def seconds(self, item, read):
        return read + 6 * len(self.gaps(item))

    # --- export and results ------------------------------------------------------

    # piping: what was typed into a gap (<code>_gap1 = the first gap); the data set holds all gaps in one column
    def pipe_parts(self, item, lang):
        return [(f"{item.code}_gap{n}", f"Lücke {n}") for n in range(1, len(self.gaps(item)) + 1)]

    def pipe_part_text(self, item, answer, name, lang):
        from app.items.base import typed_text

        prefix = f"{item.code}_gap"
        if not name.startswith(prefix) or not name[len(prefix):].isdigit() or not isinstance(answer, list):
            return ""
        n = int(name[len(prefix):])
        return typed_text(answer[n - 1]) if 1 <= n <= len(answer) else ""

    def open_columns(self, item):
        return [c for c, _ in self.export_columns(item)]  # what was typed into the gaps

    def export_columns(self, item):
        return [(item.code, f"Lückentext: {len(self.gaps(item))} Lücken, getrennt mit „{SEPARATOR}“")]

    def export_values(self, item, answer):
        return {item.code: None if not answer else SEPARATOR.join(a or "" for a in answer)}

    def codebook_info(self, item, column, lang, ui=None):
        return "Text", "", [in_lang(
            ui, "die Antworten der {n} Lücken in Textreihenfolge, getrennt mit „{sep}“; leere Lücke = nichts zwischen den "
                "Trennzeichen; je Lücke höchstens {chars} Zeichen, ein „{sep}“ in einer Antwort wird zu „,“",
            "the answers to the {n} gaps in the order of the text, separated by “{sep}”; empty gap = nothing between the "
            "separators; at most {chars} characters per gap, a “{sep}” in an answer becomes “,”",
            n=len(self.gaps(item)), sep=SEPARATOR, chars=MAX_CHARS)]

    def result_column(self, item, column):
        return None, "", None  # summarised per gap (results)

    def results(self, item, table, lang, top=8):
        """Per gap: the text up to the gap and the most frequent answers (lower case, as typed otherwise)."""
        n = len(self.gaps(item, lang))
        rows = [str(r.get(item.code)) for r in table if r.get(item.code) not in (None, "")]
        context = [c.strip() for c in GAP.split(item.text("text", lang) or "")[0::2]]
        out = []
        for k in range(n):
            found = {}
            for row in rows:
                parts = row.split(SEPARATOR)
                word = parts[k].strip().lower() if k < len(parts) else ""
                if word:
                    found[word] = found.get(word, 0) + 1
            ranked = sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))
            before = context[k][-40:] if k < len(context) else ""
            out.append({"k": k + 1, "before": before, "answers": ranked[:top], "filled": sum(found.values()),
                        "distinct": len(found)})
        return {"gaps": out, "persons": len(rows)}

    def questionnaire_lines(self, item, lang):
        return [GAP.sub(lambda m: "[" + "_" * {"s": 6, "m": 12, "l": 20}[gap_sizes(m.group(0))[0]] + "]",
                        item.text("text", lang) or "")]
