"""Single choice (round radio buttons, a drop-down list or a search field) and multiple choice (square checkboxes).

Single choice as a search field ("Suchfeld mit Vorschlägen", config "display" = "search"; Ralf, 2026-10-08): for long
lists such as occupations, subjects or municipalities a text field suggests the matching options while one types
(participant.js [data-search]); without JavaScript the page shows the drop-down list. Codes, answers and export stay
those of single choice; options with a text field ("nicht in der Liste, und zwar") always stand below the suggestions
and take over what was typed. Long lists come in through the editor's field "Liste einfügen" (lines "Beschriftung" or
"Code;Beschriftung", pasted from a spreadsheet or read from a CSV file), see SingleChoice.options_from_paste.

Both may show their answer options as images ("Bildauswahl", config "images"): one image per option from the
image pipeline (upload or AI generation in the image style of the survey, approval; items/images.html), keyed
"o:<option id>". Codes, answers, export and results stay those of the choice question."""

import csv
import io
import re

from app import carry, rows
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_id, r_int, r_maybe, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import CODE_RE, AnswerError, ItemType, in_lang, is_place_list, read_places, since_note, unseen_since, whole
from app.items.base import form_ids, new_id as _new_id
from app.lang import Both, de_en
from app.ui_i18n import pick, tr


MAX_OPTIONS = 3000  # answer options of one question: a long list for the search field (KldB occupations: about 1,300)
MAX_OTHER = 500  # characters of the text typed next to an answer option ("Anderes: ...")
OTHER_MESSAGES = {
    "de": {"required": "Bitte tragen Sie Ihre Antwort in das Textfeld ein.",
           "hint": "Das Textfeld ist noch leer. Sie können es ausfüllen oder trotzdem weitergehen.",
           "too_long": "Der Text im Textfeld ist zu lang."},
    "en": {"required": "Please type your answer into the text field.",
           "hint": "The text field is still empty. You can fill it in or continue anyway.",
           "too_long": "The text in the text field is too long."},
}


def _with_text(answer):
    return isinstance(answer, dict) and "v" in answer and set(answer) <= {"v", "t", "h"}


def selected(answer):
    """The chosen code (single choice) or codes (multiple choice) of a stored answer. An answer is the plain
    code or list of codes; only with a typed text ("Anderes: ...") it is {"v": code(s), "t": {code: text}}, and
    a multiple choice some of whose options the person did not see (carried options, app/carry.py) has
    "h": [codes not shown]."""
    return answer["v"] if _with_text(answer) else answer


def not_shown(answer):
    """The codes of the options that were not shown when this answer was given."""
    return set(answer.get("h") or []) if _with_text(answer) else set()


def typed(answer):
    """{answer code: typed text} of a stored answer; empty without a text."""
    return (answer.get("t") or {}) if _with_text(answer) else {}


OPTION_RULES = r_rows(MAX_OPTIONS, id=r_id, code=r_text(64, 1), missing=r_maybe(r_flag), text=r_maybe(r_flag),
                      exclusive=r_maybe(r_flag),  # "keine davon": unticks the other options (multiple choice)
                      optional=r_maybe(r_flag),  # an option of a construct each element may leave out
                      prompt=r_maybe(r_text(1000)), src=carry.r_src)  # prompt: the English image description (images)
IMAGE_PREFIX = "o:"  # asset key of an option's image: "o:<option id>"
MAX_IMAGE_OPTIONS = 40
CARRY_RULES = {"carry": carry.r_carry}  # options carried from an earlier question (app/carry.py)
LAYOUT_RULE = r_one("vertical", "horizontal")



def presentation(form, field):
    """The editor's field "Darstellung" (`presentation`: vertical, horizontal, dropdown or images; UI/UX audit
    2026-10-05, V85) read as one of the former fields: "layout" (horizontal or vertical), "images" (images or not)
    or "display" (dropdown or not). Without `presentation` in the form the former field itself counts, as a
    file import or an older form sends it."""
    value = form.get("presentation")
    if not value:
        old = form.get(field)
        if field == "images":
            return "images" if old else ""
        return old or ""
    if field == "layout":
        return "horizontal" if value == "horizontal" else "vertical"
    return value

class _Choice(ItemType):
    codes_in_columns = False  # True where every answer option has its own export column <code>_<answer code>

    supports_missing = False  # options that count as missing (single choice only)
    supports_text = False  # options with a text field ("Anderes: ..."), exported as <code>_<answer code>_text
    supports_exclusive = False  # options that exclude the others ("keine davon", multiple choice only)
    uses_answers = True  # carried options: which of them a person sees follows from an earlier answer (app/carry.py)
    # the answer is a code or a list of codes (single and multiple choice, Bundesland): a condition or a Teilquote can
    # compare with its options (flow.answer_codes). False where the answer is a placement, a ranking or points per
    # option: there only "beantwortet / nicht beantwortet" (UI/UX audit 2026-10-09, X17)
    code_answer = True

    def applies(self, item, answers):
        return carry.applies(item, answers)

    def remap_config(self, config, twin):
        return carry.remap(self.key, config, twin)

    def carry_view(self, item, lang):
        return carry.editor_view(item, lang)

    def visible(self, item, answers):
        """The options this person sees (all of them without answers to look at, or without carried options)."""
        opts = item.config.get("options", [])
        gone = rows.hidden(opts, answers, item.survey)
        return [o for o in opts if o["id"] not in gone] if gone else opts

    def _carry_note(self, item, column, ui=None):
        """Codebook: what -77 means in the column of an option only some people see (`ui`: the reader's language)."""
        notes = [in_lang(ui, "{n} = nicht gezeigt (aus einer früheren Frage übernommene Option), zählt als fehlend",
                         "{n} = not shown (option carried from an earlier question), counts as missing", n=rows.NOT_SHOWN)] \
            if column in self.partial_columns(item) else []
        later = next((o for o in item.config.get("options", []) if f"{item.code}_{o['code']}" == column
                      and since_note(o, ui)), None)  # an option added in a survey opened for editing again (N-5)
        return notes + ([since_note(later, ui)] if later else [])

    def since_columns(self, item):
        """The columns of options added after the first start ("since"): -77 for the participations before."""
        return {f"{item.code}_{o['code']}" for o in item.config.get("options", []) if isinstance(o.get("since"), int)}

    def partial_columns(self, item):
        """The export columns <code>_<answer code> of the options only some people see: -77 where not shown."""
        return {f"{item.code}_{o['code']}" for o in item.config.get("options", []) if rows.partial(o)}

    def default_config(self):
        return {"options": [{"id": _new_id(), "code": str(i)} for i in (1, 2)], "layout": "vertical"}

    def default_texts(self, lang):
        return {"question": "", "options": {}}

    def update_from_form(self, item, form, lang, structural):
        config = dict(item.config)
        texts = dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        labels = dict(lt.get("options", {}))
        ids = form_ids(form.getlist("option_id"))  # audit 2026-10-13, XS-2
        codes = form.getlist("option_code")
        label_values = form.getlist("option_label")
        # the alternative text of each option's image (image choice, V50), in the language edited
        alts = dict(lt.get("alts", {}))
        alt_values = form.getlist("option_alt") if self.supports_images else []
        if structural:
            # carried options are no rows of the form: sync() below writes them anew, in front of the own ones
            options = []
            prompts = {o["id"]: o["prompt"] for o in config.get("options", []) if o.get("prompt")}  # image descriptions
            since = {o["id"]: o["since"] for o in config.get("options", []) if o.get("since")}  # added later (N-5)
            missing = set(form.getlist("option_missing")) if self.supports_missing else set()
            with_text = set(form.getlist("option_text")) if self.supports_text else set()
            exclusive = set(form.getlist("option_exclusive")) if self.supports_exclusive else set()
            for oid, code, label in zip(ids, codes, label_values):
                if not (code.strip() or label.strip()):
                    continue
                oid = oid or _new_id()
                options.append({"id": oid, "code": code.strip() or str(len(options) + 1),
                                **({"missing": True} if oid in missing else {}),
                                **({"text": True} if oid in with_text else {}),
                                **({"exclusive": True} if oid in exclusive else {}),
                                **({"prompt": prompts[oid]} if oid in prompts else {}),
                                **({"since": since[oid]} if oid in since else {})})
                labels[oid] = label.strip()
            for oid, alt in zip(ids, alt_values):
                if oid in labels:
                    alts[oid] = " ".join(alt.split())[:300]
            config["options"] = options
            config["layout"] = "horizontal" if presentation(form, "layout") == "horizontal" else "vertical"
            self._update_extra_config(config, form)
            self.images_from_form(config, form)
        else:
            known = {o["id"] for o in config.get("options", [])}
            for oid, label in zip(ids, label_values):
                if oid in known:
                    labels[oid] = label.strip()
            for oid, alt in zip(ids, alt_values):
                if oid in known:
                    alts[oid] = " ".join(alt.split())[:300]
        lt["options"] = labels
        if alt_values or alts:
            lt["alts"] = {k: v for k, v in alts.items() if v}
        texts[lang] = lt
        item.config, item.texts = config, texts
        if structural:
            carry.from_form(item, form)
        carry.sync(item)

    def _update_extra_config(self, config, form):
        pass

    # --- answer options as images ("Bildauswahl") ------------------------------------------------

    supports_images = False
    image_set_label = Both("Bilder der Antwortoptionen", "Images of the answer options")
    image_set_unit = Both("Antwortoptionen", "answer options")
    image_uploads = True  # the image page offers an upload per option, next to the AI

    def images(self, item):
        return self.supports_images and bool((item.config or {}).get("images"))

    def image_options(self, item):
        """The own options of an image choice (carried options have no image of their own)."""
        return [o for o in item.config.get("options", []) if not o.get("src")] if self.images(item) else []

    def has_image_set(self, item):
        return self.images(item)

    def required_keys(self, item, variable_rows=None):
        return [IMAGE_PREFIX + o["id"] for o in self.image_options(item)]

    def _option_of(self, item, key):
        oid = key[len(IMAGE_PREFIX):] if str(key).startswith(IMAGE_PREFIX) else None
        return next((o for o in item.config.get("options", []) if o["id"] == oid), None)

    def key_label(self, item, key):
        from app.items.base import plain

        o = self._option_of(item, key)
        if o is None:
            return "?"
        label = plain(item.t(item.survey.default_language).get("options", {}).get(o["id"], ""))
        return f"{o['code']}: {label}" if label else o["code"]

    def prompt_for_key(self, item, key):
        """The option's own image description (English); without one its wording, which the pre-launch check names."""
        from app import image_styles
        from app.items.base import plain

        o = self._option_of(item, key) or {}
        scene = (o.get("prompt") or "").strip() or plain(item.t(item.survey.default_language).get("options", {}).get(o.get("id"), ""))
        return image_styles.compose(item.survey, scene, text_free=True)

    def asset_for_key(self, item, key):
        return next((a for a in item.assets if a.key == key and a.status == "approved"), None)

    def option_alt(self, item, option, lang):
        """The alternative text of an option's image for screen readers: the one written for it; else, in an English
        survey, the image description the image was made from; else "" (the wording beside the image says it; V50)."""
        own = (item.t(lang).get("alts") or {}).get(option["id"], "")
        if own:
            return own
        return (option.get("prompt") or "") if str(lang).startswith("en") else ""

    def option_image(self, item, option_id):
        """The approved image of an option, or None."""
        return self.asset_for_key(item, IMAGE_PREFIX + option_id) if self.images(item) else None

    def image_size(self, item):
        return "1024x1024"

    def image_problems(self, item):
        if not self.images(item):
            return []
        keys = self.required_keys(item)
        if len(keys) > MAX_IMAGE_OPTIONS:
            return [de_en("{n} Antwortoptionen mit Bild: zu viele (höchstens {max})", "{n} answer options with an image: too many (at most {max})",
                          n=len(keys), max=MAX_IMAGE_OPTIONS)]
        missing = [k for k in keys if self.asset_for_key(item, k) is None]
        return [de_en("{n} von {total} Bildern der Antwortoptionen fehlen oder sind nicht freigegeben",
                      "{n} of {total} images of the answer options are missing or not approved", n=len(missing), total=len(keys))] if missing else []

    def images_from_form(self, config, form):
        """The editor's tick "Antwortoptionen als Bilder" and the image description of each option
        (option_prompt_<id>); only with `images_shown` in the form."""
        if not self.supports_images or not form.get("images_shown"):
            return
        config["images"] = presentation(form, "images") == "images"
        if not config["images"]:
            config.pop("images")
        for o in config.get("options", []):
            field = f"option_prompt_{o['id']}"
            if field in form:
                prompt = " ".join(form.get(field, "").split())[:1000]
                if prompt:
                    o["prompt"] = prompt
                else:
                    o.pop("prompt", None)

    def validate(self, item):
        problems = []
        opts = item.config.get("options", [])
        if len(opts) < 2:
            problems.append(de_en("mindestens zwei Antwortoptionen", "at least two answer options"))
        codes = [o["code"] for o in opts]
        if len(set(codes)) != len(codes):
            problems.append(de_en("Antwortcodes sind nicht eindeutig", "answer codes are not unique"))
        # <code>_<answer code> is a variable name: "18-29" or "über 30" would break the SPSS syntax
        bad = ([c for c in codes if c.startswith("_") or not CODE_RE.match(f"{item.code}_{c}")]
               if self.codes_in_columns else [])
        bad_text = [o["code"] for o in self.text_options(item) if not CODE_RE.match(self.text_column(item, o))]
        # names that real answers are stored under stay as they are; asked only where a name is in doubt (the
        # question goes to the database, and the editor checks every element on every page)
        if (bad or bad_text) and not self.has_real_answers(item):
            if bad:
                problems.append(de_en("Antwortcodes werden Teil der Variablennamen und dürfen nur Buchstaben, Ziffern und "
                                      "Unterstriche enthalten (nicht: {bad})",
                                      "answer codes become part of the variable names and may only contain letters, digits "
                                      "and underscores (not: {bad})", bad=", ".join(bad[:3])))
            if bad_text:
                problems.append(de_en("Antwortcodes von Optionen mit Textfeld werden Teil der Variablennamen und dürfen nur "
                                      "Buchstaben, Ziffern und Unterstriche enthalten (nicht: {bad})",
                                      "answer codes of options with a text field become part of the variable names and may "
                                      "only contain letters, digits and underscores (not: {bad})", bad=", ".join(bad_text[:3])))
        labels = item.text("options") or {}
        if any(not labels.get(o["id"]) for o in opts):
            problems.append(de_en("Antwortoption ohne Beschriftung", "answer option without a label"))
        if any(o.get("missing") for o in opts) and sum(1 for o in opts if not o.get("missing")) < 2:
            problems.append(de_en("mindestens zwei Antwortoptionen, die nicht als fehlend zählen", "at least two answer options that do not count as missing"))
        return problems + carry.problems(item) + self.image_problems(item)

    def options(self, item, lang, answers=None):
        """answers: the answers so far of the person who sees the page: without the carried options that are
        not shown to them, and with what they typed as the wording of a carried "Anderes: …" (app/carry.py)."""
        labels = item.t(lang).get("options", {})
        gone = rows.hidden(item.config.get("options", []), answers, item.survey)
        return [{**o, "label": carry.label(o, answers, labels.get(o["id"], ""))}
                for o in item.config.get("options", []) if o["id"] not in gone]

    # --- options with a text field ("Anderes: ...") ---------------------------------------------

    def text_options(self, item):
        return [o for o in item.config.get("options", []) if o.get("text")] if self.supports_text else []

    def open_columns(self, item):
        return [c for c, _ in self.text_columns(item)]  # texts typed next to an option ("Anderes: ...")

    def text_column(self, item, option):
        return f"{item.code}_{option['code']}_text"

    def text_columns(self, item):
        """[(column, label)] of the typed texts, one column per option with a text field."""
        labels = item.text("options") or {}
        question = item.text("question") or ""
        return [(self.text_column(item, o), f"{question}: {labels.get(o['id'], '')} (Text)".lstrip(": "))
                for o in self.text_options(item)]

    def chosen(self, answer):
        return selected(answer)

    def typed(self, answer):
        return typed(answer)

    def _texts(self, item, form, codes, lang):
        """{code: text} for the chosen options with a text field. An empty field is an error where the
        question is required; otherwise the option counts without a text (see soft_warning)."""
        m = pick(OTHER_MESSAGES, lang)
        out = {}
        for o in self.text_options(item):
            if o["code"] not in codes:
                continue  # a text next to an option that was not chosen is not an answer
            text = " ".join(form.get(f"i{item.id}_t_{o['id']}", "").split())
            if len(text) > MAX_OTHER:
                raise AnswerError(m["too_long"])
            if text:
                out[o["code"]] = text
            elif item.required:
                raise AnswerError(m["required"])
        return out

    def _raw_texts(self, item, form):
        """The texts as typed, to show again when the page fails validation."""
        fields = {o["code"]: form.get(f"i{item.id}_t_{o['id']}", "") for o in self.text_options(item)}
        return {code: text for code, text in fields.items() if text}

    def soft_warning(self, item, answer, lang):
        """"Hinweis" mode: an option with a text field was chosen and the field left empty."""
        if not item.nudge:
            return None
        codes, texts = selected(answer), typed(answer)
        codes = codes if isinstance(codes, list) else [codes]
        if any(o["code"] in codes and not texts.get(o["code"]) for o in self.text_options(item)):
            return pick(OTHER_MESSAGES, lang)["hint"]
        return None

    def lists_texts(self, item, column):
        return column in {c for c, _ in self.text_columns(item)}

    def column_level(self, item, column):
        return "nominal" if self.lists_texts(item, column) else super().column_level(item, column)

    def codebook_info(self, item, column, lang, ui=None):
        for o in self.options(item, lang):
            if o.get("text") and self.supports_text and self.text_column(item, o) == column:
                return "Text", "", [in_lang(
                    ui, "Texteingabe zur Antwortoption {code} („{label}“); leer, wenn die Option nicht gewählt oder "
                        "nichts eingetragen wurde; höchstens {n} Zeichen",
                    "text typed for the answer option {code} (“{label}”); empty if the option was not chosen or nothing "
                    "was typed; at most {n} characters", code=o["code"], label=o["label"], n=MAX_OTHER)]
        return None

    def _text_values(self, item, answer):
        texts = typed(answer)
        return {self.text_column(item, o): texts.get(o["code"]) or None for o in self.text_options(item)}

    def _pipe_labels(self, item, answer, lang):
        """The labels of the chosen options (the typed text for "Anderes: ..."), in the order of the options."""
        from app.items.base import plain, typed_text

        codes, texts = selected(answer), typed(answer)
        codes = codes if isinstance(codes, list) else [codes]
        return [typed_text(texts.get(o["code"])) or plain(o["label"]) for o in self.options(item, lang) if o["code"] in codes]


@register
class SingleChoice(_Choice):
    key = "single_choice"
    import_rules = {"options": OPTION_RULES, "layout": LAYOUT_RULE, "display": r_one("dropdown", "search"), "images": r_flag,
                    "places": r_flag, **CARRY_RULES}
    result_kind = 'categorical'
    levels = ("nominal", "ordinal")  # ordinal for ordered answers, e.g. school leaving qualification
    label = Both("Einfachauswahl", "Single choice")
    short = Both("Eine Antwort aus mehreren", "One answer out of several")
    description = Both("Eine Antwort aus mehreren (Auswahlknöpfe, Dropdown-Liste oder Suchfeld mit Vorschlägen)",
                       "One answer out of several (radio buttons, a drop-down list or a search field with suggestions)")
    order = 10
    fact_sheet = FactSheet(
        "classic",
        keywords=["Radio", "Optionsfeld", "Dropdown", "Drop-down", "Auswahlliste", "Suchfeld", "Search field",
                  "Autovervollständigung", "Autocomplete", "Typeahead", "Bildauswahl", "Image choice", "Sonstiges",
                  "Other"],
        can=[("Runde Auswahlknöpfe, senkrecht oder waagerecht, eine Dropdown-Liste oder ein Suchfeld.",
              "Round radio buttons, vertical or horizontal, a drop-down list or a search field."),
             ("Optionen können als fehlender Wert zählen oder ein Freitextfeld haben („Sonstiges“).",
              "Options can count as a missing value or have a free text field (“Other”)."),
             ("Optionen lassen sich aus einer früheren Frage übernehmen.",
              "Options can be carried over from an earlier question."),
             ("Variante Bildauswahl: Antworten als Bilder zum Antippen, hochgeladen oder mit KI erzeugt.",
              "Variant image choice: answers as images to tap, uploaded or generated by AI."),
             ("Variante Suchfeld: Beim Tippen erscheinen passende Antworten aus einer langen Liste (Berufe, Fächer, "
              "Gemeinden); die Liste lässt sich aus Excel einfügen oder als CSV-Datei laden.",
              "Variant search field: matching answers from a long list (occupations, subjects, municipalities) appear "
              "while typing; the list can be pasted from Excel or loaded as a CSV file.")],
        good_for=[("Eine Antwort aus einer festen Liste, z. B. Bildung, Beruf oder eine Entscheidung",
                   "One answer from a fixed list, e.g. education, occupation or a decision"),
                  ("Filterfragen, nach denen der Ablauf verzweigt", "Screening questions the flow branches on"),
                  ("Manipulationschecks in Experimenten", "Manipulation checks in experiments")],
        not_for=[("Mehrere zutreffende Antworten (dann Mehrfachauswahl)",
                  "Several answers that apply (then multiple choice)"),
                 ("Abgestufte Zustimmung zu mehreren Aussagen (dann Likert-Skala)",
                  "Graded agreement with several statements (then a Likert scale)")],
        designs=["cross", "perception", "framing", "ab"],
        examples=[("Höchster Bildungsabschluss", "Highest level of education"),
                  ("A/B-Test: Welche von zwei Verpackungen würden Sie kaufen?",
                   "A/B test: which of two packages would you buy?")],
    )
    supports_missing = True
    supports_text = True
    supports_dropdown = True
    supports_search = True
    supports_images = True
    max_options = MAX_OPTIONS

    def one_click(self, item):
        # a text field needs typing, and a list is easily changed by accident (arrow keys, the wheel):
        # the page waits for "Next"
        return not self.text_options(item) and not self.dropdown(item) and not self.search(item)

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        if self.supports_search:
            read_places(item, form)  # a privacy setting: also in a running survey
        if structural and (form.get("options_paste") or "").strip():
            from flask import flash

            report = []
            added = self.options_from_paste(item, form["options_paste"], lang, report)
            if added:  # the page loads anew with the rows (data-reload-saved) and says what came in (X18)
                flash(de_en("{n} Option aus der Liste übernommen.", "{n} option copied from the list.", n=added)
                      if added == 1 else
                      de_en("{n} Optionen aus der Liste übernommen.", "{n} options copied from the list.", n=added),
                      "success")
            if report:  # what did not come in as written (audit 2026-10-10, N-9)
                flash(" ".join(report), "warning")

    def options_from_paste(self, item, text, lang, report=None):
        """Add the lines of the editor's field "Liste einfügen" as options at the end: a line is a label, or a code
        and a label (separated by a tab from a spreadsheet, else a semicolon) if the first cell can be a code. A
        label the list has already (in this language, ignoring case) is left out unless the line brings a code of
        its own (two places called Neustadt); a code already used gets the number after the highest so far, as
        does an option without a code. A first line whose first cell is no code while the others start with
        numbers is a header and left out. At most MAX_OPTIONS options. `report` (a list) takes a sentence on each
        kind of line that did not come in as written (audit 2026-10-10, N-9).
        Options without a label in any language (the two empty ones of a new question) go first and free their
        codes, so that a pasted list starts at 1 (UI/UX audit 2026-10-09, X19). Returns the number added."""
        lines = [line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
        if not lines:
            return 0
        sep = "\t" if any("\t" in line for line in lines) else ";" if any(";" in line for line in lines) else None
        texts = dict(item.texts or {})

        def blank(o):
            return not o.get("src") and not o.get("prompt") and not any(
                (((t or {}).get("options") or {}).get(o["id"]) or "").strip() for t in texts.values())
        options = [o for o in item.config.get("options", []) if not blank(o)]
        before = len(options)
        lt = dict(texts.get(lang, {}))
        labels = dict(lt.get("options", {}))
        seen = {" ".join((labels.get(o["id"]) or "").split()).casefold() for o in options}
        codes = {o["code"] for o in options}

        def counted(c):  # a key with a leading zero (AGS 07337047) is no running number to go on from; only ASCII
            # digits: "²" or "١" count as digits for str.isdigit, but int() refuses them (code review 2026-10-15, K4)
            return c.isascii() and c.isdigit() and not (len(c) > 1 and c.startswith("0"))
        n = max((int(c) for c in codes if counted(c)), default=0)
        rows_ = csv.reader(io.StringIO("\n".join(lines)), delimiter=sep) if sep else ([line] for line in lines)
        try:
            rows_ = [cells for cells in ([" ".join(c.split()) for c in cells if c.strip()] for cells in rows_) if cells]
        except csv.Error:  # a cell beyond the csv module's field limit: one option per line (audit 2026-10-22, C-03)
            rows_ = [[" ".join(line.split())] for line in lines if line.strip()]
        coded = [cells for cells in rows_[1:] if len(cells) > 1]
        header = None
        if (len(rows_) > 1 and len(rows_[0]) > 1 and not re.search(r"\d", rows_[0][0])
                and coded and all(cells[0].isdigit() for cells in coded)):
            header, rows_ = "; ".join(rows_[0]), rows_[1:]
        same, recoded = [], []
        for cells in rows_:
            first_is_code = len(cells) > 1 and re.fullmatch(r"[\w.\-]{1,64}", cells[0], re.ASCII)
            code, label = (cells[0], " ".join(cells[1:])) if first_is_code else ("", " ".join(cells))
            label = label[:300]
            if not label or len(options) >= MAX_OPTIONS:
                continue
            if label.casefold() in seen and (not code or code in codes):
                same.append(label)  # the same option once more
                continue
            if not code or code in codes:
                taken = code
                n += 1
                while str(n) in codes:
                    n += 1
                code = str(n)
                if taken:
                    recoded.append(f"{taken} → {code}")
            elif counted(code):
                n = max(n, int(code))
            oid = _new_id()
            options.append({"id": oid, "code": code})
            labels[oid] = label
            seen.add(label.casefold())
            codes.add(code)
        lt["options"] = labels
        texts[lang] = lt
        item.config, item.texts = {**item.config, "options": options}, texts
        if report is not None:
            if header:
                report.append(de_en("Erste Zeile „{line}“ als Kopfzeile ausgelassen.",
                                    "First line “{line}” left out as a header.", line=header[:80]))
            if same:
                report.append(de_en("Ausgelassen, weil die Beschriftung schon vorkommt und kein eigener Code dabei war: {labels}.",
                                    "Left out because the label is there already and came without a code of its own: {labels}.",
                                    labels=", ".join(same[:5]) + (" …" if len(same) > 5 else "")))
            if recoded:
                report.append(de_en("Code schon vergeben, neu nummeriert: {pairs}.", "Code already taken, numbered anew: {pairs}.",
                                    pairs=", ".join(recoded[:5]) + (" …" if len(recoded) > 5 else "")))
        return len(options) - before

    def place_columns(self, item):
        if not self.supports_search:
            return []
        return [item.code] if is_place_list(item.config, [o["code"] for o in item.config.get("options", [])]) else []

    def check_warnings(self, item):
        """Two options with the same wording (two places called Neustadt from "Liste einfügen", each with its own
        code): the participants cannot tell them apart (audit 2026-10-10, N-9)."""
        from app.items.base import plain

        out = list(super().check_warnings(item))
        labels = item.text("options", item.survey.default_language) or {}
        seen, double = set(), []
        for o in item.config.get("options", []):
            label = " ".join(plain(labels.get(o["id"]) or "").split())
            if label and label.casefold() in seen and label not in double:
                double.append(label)
            seen.add(label.casefold())
        if double:
            out.append(de_en("doppelte Beschriftung der Antwortoptionen ({labels}); Teilnehmende können sie nicht "
                             "unterscheiden", "duplicate labels of the answer options ({labels}); participants cannot "
                             "tell them apart", labels=", ".join(double[:3])))
        return out

    def missing_codes(self, item, column):
        return tuple(o["code"] for o in item.config.get("options", []) if o.get("missing"))

    def result_column(self, item, column):
        """Results page: labels straight from the options, in their order, so letter codes (w/m/d),
        empty categories and labels containing ";" come through."""
        if self.lists_texts(item, column):
            return "auto", "", None
        from app.items.base import answer_label

        lang = item.survey.default_language
        # without the colon of "Anderes, und zwar:" (UI audit 2026-10-12, U27)
        return self.result_kind, "", {o["code"]: answer_label(o["label"]) for o in self.options(item, lang)}

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        value = form.get(f"i{item.id}")
        codes = {o["code"] for o in self.visible(item, answers)}
        if value in (None, ""):
            if item.required:
                raise AnswerError(tr(lang, "Bitte wählen Sie eine Antwort.", "Please choose an answer."))
            return None
        if value not in codes:
            raise AnswerError(tr(lang, "Ungültige Antwort.", "Invalid answer."))
        texts = self._texts(item, form, [value], lang)
        return {"v": value, "t": texts} if texts else value

    def display_value(self, item, form, meta=None):
        texts = self._raw_texts(item, form)
        return {"v": form.get(f"i{item.id}"), "t": texts} if texts else form.get(f"i{item.id}")

    def export_columns(self, item):
        return super().export_columns(item) + self.text_columns(item)

    def export_values(self, item, answer):
        return {item.code: selected(answer), **self._text_values(item, answer)}

    def pipeable(self, item):
        return True

    def pipe_text(self, item, answer, lang):
        return "".join(self._pipe_labels(item, answer, lang)[:1])

    def pipe_live(self, item, lang):
        return {"kind": "choice", "item": item.id}


@register
class MultipleChoice(_Choice):
    key = "multiple_choice"
    import_rules = {"options": OPTION_RULES, "layout": LAYOUT_RULE, "min": r_int(0, 500, none=True), "max": r_int(0, 500, none=True),
                    "images": r_flag, "multi_hint": r_flag, **CARRY_RULES}

    since_lists = ("options",)  # an option added later: -77 for the participations before (audit 2026-10-12, N-5)

    def missing_codes(self, item, column):
        return (rows.NOT_SHOWN,) if column in self.partial_columns(item) | self.since_columns(item) else ()

    def export_response_values(self, item, answer, response):
        """-77 (not shown) in the columns of options added after the version this participation answered, not 0
        (audit 2026-10-12, N-5)."""
        if answer is None:
            return {}
        return {f"{item.code}_{code}": rows.NOT_SHOWN for code in unseen_since(item, "options", response)}
    result_kind = 'categorical'
    levels = ("nominal",)  # one 0/1 column per option
    label = Both("Mehrfachauswahl", "Multiple choice")
    codes_in_columns = True
    supports_text = True
    supports_images = True
    supports_exclusive = True
    short = Both("Mehrere Antworten möglich", "Several answers possible")
    description = Both("Mehrere Antworten möglich (eckige Kästchen)", "Several answers possible (square tick boxes)")
    order = 20
    fact_sheet = FactSheet(
        "classic",
        keywords=["Checkbox", "Kästchen", "Tick boxes", "Mehrfachnennung", "Multiple response", "Bildauswahl"],
        can=[("Kästchen, senkrecht oder waagerecht.", "Tick boxes, vertical or horizontal."),
             ("Optionen lassen sich aus einer früheren Frage übernehmen.",
              "Options can be carried over from an earlier question."),
             ("Auch als Bildauswahl mit mehreren Antworten.", "Also as an image choice with several answers.")],
        good_for=[("Alles, was zutrifft: genutzte Medien, Geräte, Informationsquellen",
                   "Everything that applies: media used, devices, sources of information"),
                  ("Eine Auswahl, aus der spätere Fragen ihre Optionen übernehmen",
                   "A selection from which later questions take their options")],
        not_for=[("Eine Rangfolge der Optionen (dann Rangfolge)", "An order of the options (then ranking)"),
                 ("Wie stark etwas zutrifft (dann Likert-Skala oder Bewertung)",
                  "How strongly something applies (then a Likert scale or rating)")],
        designs=["cross", "perception"],
        examples=[("Welche dieser Sprachassistenten haben Sie schon genutzt?",
                   "Which of these voice assistants have you used?")],
    )

    def default_config(self):
        # multi_hint: "Mehrere Antworten möglich." under the question when no minimum or maximum says more (Dillman:
        # check-all-that-apply; UI/UX audit 2026-10-05, V36); on unless switched off, also for older elements
        return {**super().default_config(), "min": None, "max": None, "multi_hint": True}

    def _update_extra_config(self, config, form):
        for k in ("min", "max"):
            v = form.get(k, "").strip()
            config[k] = whole(v)
        config["multi_hint"] = bool(form.get("multi_hint"))

    def parse_answer(self, item, form, lang="de", meta=None, answers=None):
        codes = [o["code"] for o in self.visible(item, answers)]
        unseen = [o["code"] for o in item.config.get("options", []) if o["code"] not in codes]
        # each option once: the same code sent a million times would be stored a million times (and would
        # count as "at least three answers")
        values = list(dict.fromkeys(v for v in form.getlist(f"i{item.id}") if v in codes))
        lo, hi = item.config.get("min"), item.config.get("max")
        if lo:  # fewer options shown (carried, or under a condition) than the minimum: all of them are enough
            lo = min(lo, len(codes))
        if item.required and not values:
            raise AnswerError(tr(lang, "Bitte wählen Sie mindestens eine Antwort.", "Please choose at least one answer."))
        if values and lo and len(values) < lo and not (meta or {}).get("draft"):
            raise AnswerError(tr(lang, "Bitte wählen Sie mindestens {n} Antworten.", "Please choose at least {n} answers.", n=lo))
        if hi and len(values) > hi:
            raise AnswerError(tr(lang, "Bitte wählen Sie höchstens {n} Antworten.", "Please choose at most {n} answers.", n=hi))
        alone = {o["code"] for o in item.config.get("options", []) if o.get("exclusive")}
        if len(values) > 1 and alone & set(values):  # "keine davon" with something else (without JavaScript)
            labels = item.t(lang).get("options", {})
            name = next((labels.get(o["id"]) or o["code"] for o in item.config.get("options", []) if o["code"] in alone & set(values)), "")
            raise AnswerError(tr(lang, "„{o}“ lässt sich nicht mit anderen Antworten zusammen wählen.",
                                 "“{o}” cannot be chosen together with other answers.", o=name))
        texts = self._texts(item, form, values, lang)
        if unseen:  # not shown is not the same as not ticked (export: -77)
            return {"v": values, "t": texts, "h": unseen}
        return {"v": values, "t": texts} if texts else values

    def display_value(self, item, form, meta=None):
        texts = self._raw_texts(item, form)
        return {"v": form.getlist(f"i{item.id}"), "t": texts} if texts else form.getlist(f"i{item.id}")

    def is_missing(self, item, answer):
        """No tick, also when some carried options were not shown ({"v": [], "h": [...]}): the nudge asks the same
        whatever the person chose before (audit data storage 2026-10-21, A1)."""
        return self.collects_answer and not selected(answer)

    def pipeable(self, item):
        return True

    def pipe_text(self, item, answer, lang):
        """"A, B und C" (the labels of the chosen options)."""
        labels = self._pipe_labels(item, answer, lang)
        if len(labels) < 2:
            return "".join(labels)
        return tr(lang, "{a} und {b}", "{a} and {b}", a=", ".join(labels[:-1]), b=labels[-1])

    def pipe_live(self, item, lang):
        return {"kind": "multi", "item": item.id, "and": tr(lang, "{a} und {b}", "{a} and {b}", a="", b="").strip()}

    def result_column(self, item, column):
        return ("auto", "", None) if self.lists_texts(item, column) else super().result_column(item, column)

    def export_columns(self, item):
        labels = item.text("options") or {}
        texts = dict(self.text_columns(item))
        cols = []
        for o in item.config.get("options", []):  # the text right after the 0/1 column of its option
            cols.append((f"{item.code}_{o['code']}", labels.get(o["id"], "")))
            if o.get("text"):
                cols.append((self.text_column(item, o), texts[self.text_column(item, o)]))
        return cols

    def skipped_when_empty(self, item, unseen=()):
        """Whether nothing ticked means skipped: a question that may be left out with an exclusive option ("keine
        davon") that was shown. Whoever meant "none" could tick it, so nothing ticked is missing, not 0 in every
        column (audit 2026-10-10, B-23; as decision 5 of the audit 2026-10-06, E-7)."""
        return not item.required and any(o.get("exclusive") and o["code"] not in unseen
                                         for o in item.config.get("options", []))

    def export_values(self, item, answer):
        # None = never answered (skipped by a condition, loop pass not shown, not reached): missing, not 0.
        # [] = seen and nothing ticked: 0 in every column, missing where "keine davon" was there (skipped_when_empty).
        if answer is None:
            return {**{f"{item.code}_{o['code']}": None for o in item.config.get("options", [])},
                    **self._text_values(item, None)}
        chosen, unseen = set(selected(answer)), not_shown(answer)
        if not chosen and self.skipped_when_empty(item, unseen):
            return {**{f"{item.code}_{o['code']}": None for o in item.config.get("options", [])},
                    **self._text_values(item, answer)}
        return {**{f"{item.code}_{o['code']}": rows.NOT_SHOWN if o["code"] in unseen else int(o["code"] in chosen)
                   for o in item.config.get("options", [])},
                **self._text_values(item, answer)}

    def validate(self, item):
        problems = super().validate(item)
        lo, hi = item.config.get("min"), item.config.get("max")
        if lo and hi and lo > hi:
            problems.append(de_en("Minimum ist größer als Maximum", "minimum is greater than maximum"))
        n = len(item.config.get("options", []))
        if lo and lo > 1 and any(o.get("exclusive") for o in item.config.get("options", [])):
            problems.append(de_en("Eine Option, die andere ausschließt, verträgt sich nicht mit einer Mindestzahl über 1", "an option that excludes the others does not go with a minimum above 1"))
        if lo and lo > n:  # a required question could never be answered
            problems.append(de_en("Mindestens {lo} Antworten verlangt, es gibt aber nur {n} Antwortoptionen",
                                  "at least {lo} answers required, but there are only {n} answer options", lo=lo, n=n))
        return problems
