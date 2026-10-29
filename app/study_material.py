"""The survey material as one PDF (concept "Vereinheitlichung der Befragungsexporte", E10; Ralf 2026-10-23 in the thread
"Data Colada 140": A, B and C).

Part 1 shows the questionnaire as participants see it, in the flow's order, its questions numbered. Part 2 shows the same
questionnaire with what a reader of a study needs to judge it, in colour beside each element: variable names, the
display logic and the conditions of the flow, random orders and randomisations, required answers, checks of the
answers and quality rules, the instruments with their source. A condition stands beside the element it rules, not in an
appendix, so a reader sees at a glance who saw what (the reason of the thread "Data Colada 140": a study's material must
show what participants were shown and under which conditions).

The PDF is made without AI, from the survey alone, and it is deterministic: the same questionnaire gives the same bytes
(reportlab's invariant mode, no date of creation in it). The pre-registration freezes it next to the survey file
(app/prereg.py), so its SHA-256 there says which material belonged to the start of the fieldwork, and the Zenodo and
Coscine package carry it (public: without the wording of instruments that may not be passed on).

The texts of the questionnaire are in the survey's default language; the frame (headings, notes) in the language of the
person who downloads or freezes it. The fonts are those of app/pdf_fonts.py, as on the paper questionnaire: Noto Sans
for Latin, Greek and Cyrillic everywhere, Arabic (right to left) and Chinese where the image has their fonts."""
import functools
import io
import os
import re
from app import pdf_fonts
from app.items.base import plain

# The notes of part 2 take the colours of the researcher pages (app/static/app.css, read below, so the two never drift
# apart): conditions in the blue of conditions there (.badge-cond), instruments in the green of constructs
# (.badge-construct), required answers, checks and ends in the orange of "check this" (--caution), variables in the
# grey of secondary text. Random orders have no colour of their own there; they take the data colour "reddish purple"
# of Okabe & Ito (--chart-4), which judges nothing and is apart from the violet of AI (Ralf 2026-10-24, thread
# "Data Colada 140"). Each is 4.5 : 1 and more on white (tests/exports/test_study_material.py).
ROLES = {"logic": "--accent", "random": "--chart-4", "check": "--caution", "variable": "--muted", "source": "--ok"}
# the values of app.css at the time of writing: used only if the style sheet cannot be read
FALLBACK = {"--accent": "#2957a4", "--chart-4": "#8d5273", "--caution": "#b35c00", "--muted": "#6b6b66",
            "--ok": "#1f6b2a", "--fg": "#1c1c1a", "--border-strong": "#cfcdc5"}
MODES = {"required": ("Pflicht", "Required"), "nudge": ("Hinweis, wenn leer", "Prompt if empty"),
         "optional": ("Freiwillig", "Optional")}
# app/items/text.py names the checks in German only
CHECKS = {"integer": ("Ganze Zahl", "Whole number"), "decimal": ("Dezimalzahl", "Decimal number"),
          "email": ("E-Mail-Adresse", "Email address"), "plz_de": ("Postleitzahl (DE)", "Postcode (DE)"),
          "regex": ("Eigener regulärer Ausdruck", "Own regular expression")}
CHOICES = ("single_choice", "multiple_choice", "federal_state")  # answered by ticking one or more options
INDENT = 6  # mm per level of the flow in part 2 (inside a condition, a randomisation, a loop)
PAD = 6  # pt: reportlab's frame keeps this padding inside the margins
LOGO_MM = {"s": 9, "m": 12, "l": 16}  # height of the survey's logo on the first page, by its size
NAME = "survey-material.pdf"


@functools.lru_cache(maxsize=1)
def tokens():
    """{token: value} of the light token block of app.css (the first ":root"), with FALLBACK for what is missing. The
    Docker image serves the style sheet minified, so comments are removed before reading."""
    path = os.path.join(os.path.dirname(__file__), "static", "app.css")
    try:
        with open(path, encoding="utf-8") as f:
            text = re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)
        block = re.search(r":root\s*\{([^}]*)\}", text).group(1)
        found = {k: v.strip() for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+)", block)}
    except (OSError, AttributeError):
        found = {}
    return {k: found.get(k, v) if re.fullmatch(r"#[0-9a-fA-F]{6}", found.get(k, "")) else v for k, v in FALLBACK.items()}


def colours():
    """{role: #rrggbb} of the notes in part 2, the same in the legend."""
    return {role: tokens()[token] for role, token in ROLES.items()}


def _cap(text):
    """A text after a colon starts with a capital (house style)."""
    text = str(text or "")
    return text[:1].upper() + text[1:]


def _safe(text):
    """Escaped for a Paragraph, every character in a font that has it (app/pdf_fonts.py)."""
    return pdf_fonts.markup(text)


def Paragraph(text, style, **kw):
    """reportlab's Paragraph, set right to left or broken between Chinese characters where the text needs it."""
    return pdf_fonts.paragraph(text, style, **kw)


def _words(text):
    """A text of the questionnaire as plain words: without its formatting marks, line breaks kept."""
    return _safe(plain(text or "")).replace("\n", "<br/>")


class _Doc:
    def __init__(self, survey, ui, public):
        from app import export

        self.survey, self.ui, self.public = survey, ui, public
        self.lang = survey.default_language
        self.withheld = export.withheld(survey) if public else set()
        self.numbers = {}
        self.accent, self.logo = _look(survey)

    def say(self, de, en):
        return en if self.ui == "en" else de


def _look(survey):
    """(accent, logo) of the survey's design (app/theme.py; an organisation design is one): the accent made readable
    on white paper (4.5 : 1), for the headings and the line in the head; the logo as (path, width, height in mm,
    position) or None. Without a design of its own: the blue of the researcher pages and no logo. An SVG logo is left
    out: the standard PDF tools of reportlab draw only raster images. The colours of the notes stay fixed."""
    from app import media, theme

    look = theme.of(survey)
    own = look.get("preset") != "askurious" and look["colors"].get("accent")
    accent = theme.readable(own, "#ffffff") if own else tokens()["--accent"]
    logo = look.get("logo") or {}
    name = logo.get("file") or ""
    path = os.path.join(media.upload_folder(survey.uid), name) if name and not name.endswith(".svg") else ""
    if not path or not os.path.isfile(path):
        return accent, None
    height = LOGO_MM.get(logo.get("size"), LOGO_MM["m"])
    width = min(80, height * max(1, logo.get("w", 1)) / max(1, logo.get("h", 1)))
    return accent, (path, width, width * max(1, logo.get("h", 1)) / max(1, logo.get("w", 1)), logo.get("position", "left"))


def _styles(accent):
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    pdf_fonts.register()
    base, ink, muted = getSampleStyleSheet(), tokens()["--fg"], tokens()["--muted"]
    out = {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=pdf_fonts.BOLD, fontSize=18, leading=22,
                                alignment=TA_LEFT, textColor=ink, spaceAfter=2),
        "subtitle": ParagraphStyle("st", parent=base["BodyText"], fontName=pdf_fonts.REGULAR, fontSize=9.5, leading=12.5,
                                   textColor=muted, spaceBefore=0),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName=pdf_fonts.BOLD, fontSize=14, leading=18,
                             spaceBefore=6, spaceAfter=6, textColor=accent),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=pdf_fonts.BOLD, fontSize=11.5, leading=15,
                             spaceBefore=8, spaceAfter=3, textColor=accent, keepWithNext=1),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontName=pdf_fonts.REGULAR, fontSize=9.5, leading=12.5,
                               spaceBefore=5, spaceAfter=1, textColor=ink),
        "answer": ParagraphStyle("a", parent=base["BodyText"], fontName=pdf_fonts.REGULAR, fontSize=9.5, leading=12,
                                 spaceBefore=0, spaceAfter=0, leftIndent=10, textColor=ink),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontName=pdf_fonts.REGULAR, fontSize=8, leading=10, textColor=ink),
    }
    for key, colour in colours().items():
        out[key] = ParagraphStyle(key, parent=out["small"], textColor=colour)
    return out


def _swatch(colour):
    """A small filled square in a note colour, for the legend (a drawn square: the standard fonts have no glyph for it)."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Flowable

    class Swatch(Flowable):
        def wrap(self, *_):
            return 2.8 * mm, 2.8 * mm

        def draw(self):
            self.canv.setFillColor(colour)
            self.canv.setStrokeColor(colour)
            self.canv.rect(0, 0.3 * mm, 2.8 * mm, 2.8 * mm, stroke=0, fill=1)

    return Swatch()


# --- what an element shows ------------------------------------------------------------------------------------


def _answers(doc, item, annotated=False):
    """[markup line] of what a participant answers with, as on the page; annotated (part 2): with the codes the data
    hold."""
    kind, lang = item.kind, doc.lang
    if item.type in CHOICES:
        mark = "[ ]" if item.type == "multiple_choice" else "( )"
        code = (lambda o: f' <font color="{colours()["variable"]}">[{_safe(o.get("code"))}]</font>') if annotated else (lambda o: "")
        return [f"{mark} {_words(o.get('label'))}{code(o)}" for o in kind.options(item, lang)]
    if item.type == "likert":
        labels = [l for l in kind.labels(item, lang)]
        scale = " | ".join(f"{n}" + (f" = {_words(l)}" if l else "") for n, l in enumerate(labels, start=1))
        return [_safe(doc.say("Skala: ", "Scale: ")) + scale] + [
            f"&bull; {_words(s.get('text'))}" for s in kind.statements(item, lang)]
    if item.type == "semantic":
        return [f"{_words(p.get('left'))} &nbsp;1 &ndash; {item.config.get('points')}&nbsp; {_words(p.get('right'))}"
                for p in kind.pairs(item, lang)]
    if item.type == "rating":
        lo, hi = kind.bounds(item)
        t = item.t(lang)
        return [f"{_words(t.get('left'))} &nbsp;{lo:g} &ndash; {hi:g}&nbsp; {_words(t.get('right'))}".strip()]
    if item.type == "text_input":
        return ["_" * 45]
    if item.type == "consent":  # a standard text is not stored: the one participants see (template_body)
        own = item.t(lang).get("body")
        if not own and doc.public:  # it names the people responsible and their address, which stay with the survey
            return ["<i>" + _safe(doc.say("Standardtext der Einwilligung von Askurious, mit den Angaben der "
                                          "Verantwortlichen", "Standard consent text of Askurious, with the details "
                                          "of the people responsible")) + "</i>"]
        return [_words(own or kind.template_body(item, lang))]
    lines = kind.questionnaire_lines(item, lang)
    if lines:
        return [_line(re.sub(r"^\s*-\s*", "", line), annotated) for line in lines if line.strip()]
    if kind.collects_answer:
        return ["<i>" + _safe(doc.say("Interaktives Element ({label}); online beantwortet.",
                                      "Interactive element ({label}); answered online.").format(label=_label(doc, item)))
                + "</i>"]
    return []


def _line(line, annotated):
    """A line of the questionnaire file (Markdown) as markup: the `codes` in it are for those who read the data, so
    part 1 leaves them out and part 2 shows them in the colour of variables."""
    parts = re.split(r"`([^`]*)`", line)
    if not annotated:
        return _words(re.sub(r"\s{2,}", " ", "".join(parts[::2])).strip())
    return "".join(_words(part) if n % 2 == 0 else f'<font color="{colours()["variable"]}">{_safe(part)}</font>'
                   for n, part in enumerate(parts))


def _label(doc, item):
    from app.lang import Both

    label = item.kind.label
    if isinstance(label, Both):
        return label.en if doc.ui == "en" else label.de
    return str(label)


def _element(doc, styles, item, annotated=False):
    """[Paragraph] of one element as participants see it (annotated: with the codes of the answers)."""

    t = item.t(doc.lang)
    number = doc.numbers.get(item.id)
    head = f"<b>{number}.</b> " if number else ""
    if item.id in doc.withheld:
        return [Paragraph(head + "<i>" + _safe(doc.say("Wortlaut nicht enthalten: keine allgemeine Freigabe zur "
                                                        "Veröffentlichung (siehe Quelle).",
                                                        "Wording not included: no general permission to publish (see "
                                                        "the source).")) + "</i>", styles["body"])]
    question = _words(t.get("question") or t.get("body") if item.type != "consent" else t.get("question"))
    if item.answer_mode == "required" and item.kind.collects_answer:
        question += " *"
    out = [Paragraph(head + question, styles["body"])] if (head or question) else []
    out += [Paragraph(line, styles["answer"]) for line in _answers(doc, item, annotated)
            if not (item.type not in ("single_choice", "multiple_choice", "likert", "semantic", "rating", "text_input",
                                      "consent") and line == question)]
    return out


# --- what part 2 adds -----------------------------------------------------------------------------------------


def _notes(doc, item):
    """[(colour key, text)] beside an element in part 2."""
    from app import constructs, export, item_condition
    from app import rows as row_logic

    out = []
    if item.kind.collects_answer:
        columns = [c for c, _ in export._plain_columns(item)]
        shown = ", ".join(columns[:12]) + (" ..." if len(columns) > 12 else "")
        out.append(("variable", doc.say("Variable: ", "Variable: ") + (shown or item.code) + f" ({_label(doc, item)})"))
        mode = MODES.get(item.answer_mode)
        if mode:
            out.append(("check", doc.say("Antwort: ", "Answer: ") + doc.say(*mode)))
    cond = item_condition.text(item, doc.lang, doc.ui)
    if cond:
        out.append(("logic", doc.say("Nur gezeigt, wenn ", "Shown only if ") + plain(cond)))
    if item.type == "likert":
        for s in item.kind.statements(item, doc.lang):
            if s.get("when"):
                out.append(("logic", f"{s.get('export_code')}: " + doc.say("Nur gezeigt, wenn ", "Shown only if ")
                            + plain(row_logic.text(item, s, doc.lang, doc.ui))))
        reverse = [s.get("export_code") for s in item.kind.statements(item, doc.lang) if s.get("reverse")]
        if reverse:
            out.append(("variable", doc.say("Umgepolt: ", "Reverse coded: ") + ", ".join(reverse)))
    if (item.config or {}).get("randomize"):
        out.append(("random", doc.say("Reihenfolge zufällig", "Random order")))
    validation = (item.config or {}).get("validation")
    if validation and validation != "none":
        name = CHECKS.get(validation)
        out.append(("check", doc.say("Prüfung der Eingabe: ", "Input check: ") + (doc.say(*name) if name else _cap(validation))))
    if (item.config or {}).get("dont_know"):
        out.append(("check", doc.say("Mit „weiß nicht“ (zählt als fehlend)", "With “don't know” (counts as missing)")))
    key = constructs.toolbox_key(item)
    if key:
        c = constructs.find(key)
        source = getattr(c, "citation", "") if c else ""
        name = (getattr(c, "name", "") if c else "") or key
        out.append(("source", doc.say("Instrument: ", "Instrument: ") + _cap(name) + (f"; {source}" if source else "")))
    return out


def _flow_note(doc, node):
    """(colour key, text) for a node of the flow that is no block, or None."""
    from app import flow

    kind = node.get("t")
    if kind == "branch":
        return "logic", doc.say("Wenn ", "If ") + plain(flow.condition_text(doc.survey, node.get("cond") or {}, doc.ui)) + ":"
    if kind == "else":
        return "logic", doc.say("Sonst:", "Otherwise:")
    if kind == "rand":
        n = len(node.get("children") or [])
        return "random", doc.say("Randomisierung „{name}“: {k} von {n} zufällig, je Person",
                                 "Randomisation “{name}”: {k} of {n} at random, per person").format(
            name=node.get("name") or node.get("code"), k=node.get("k", 1), n=n)
    if kind == "loop":
        return "random", doc.say("Schleife „{name}“ über {n} Zeilen", "Loop “{name}” over {n} rows").format(
            name=node.get("name") or node.get("code"), n=len(node.get("rows") or [])) + (
            doc.say(", Reihenfolge zufällig", ", random order") if node.get("order") == "random" else "")
    if kind == "end":
        return "check", doc.say("Ende ({what})", "End ({what})").format(what=_end_cases(doc, node) or "-")
    if kind == "quota":
        return "check", doc.say("Quotenberechnung", "Quota calculation")
    if kind == "split":
        return "logic", doc.say("Getrennter Abschnitt (eigener Datensatz)", "Separated section (own data set)")
    if kind == "ext":
        return "logic", doc.say("Externer Abschnitt „{name}“", "External section “{name}”").format(
            name=node.get("name") or node.get("code"))
    if kind == "ws":
        return "logic", doc.say("Web Service „{code}“", "Web service “{code}”").format(code=node.get("code"))
    return None


def _end_cases(doc, node):
    """The triggers of an end by their names (app/flow.py), each once and in their order: an end with a link per
    quota cell holds one trigger per cell, which reads as a count ("Teilquote voll (29 Zellen)")."""
    from app import flow

    counts = {}
    for case in flow.cases(node):
        when = case.get("when") if case.get("when") in flow.END_TRIGGERS else "screenout"
        counts[when] = counts.get(when, 0) + (max(1, len(case.get("cells") or [])) if when == "cellfull" else 1)
    names = []
    for when, n in counts.items():
        name = doc.say(flow.END_TRIGGERS[when][0], flow.END_TRIGGERS_EN[when])
        if when == "cellfull" and n > 1:
            name += doc.say(" ({n} Zellen)", " ({n} cells)").format(n=n)
        names.append(name)
    return ", ".join(names)


# --- the document ---------------------------------------------------------------------------------------------


def _ordered_blocks(survey):
    """[(node, depth)] of the flow in its order; blocks of the survey the flow does not name come last."""
    from app import flow

    out, seen = [], set()
    for node, _, depth in flow.walk(flow.tree(survey)):
        out.append((node, depth))
        if node.get("t") == "block":
            seen.add(node.get("block"))
    out += [({"t": "block", "block": b.id}, 0) for b in survey.blocks if b.id not in seen]
    return out


def _number(doc, order, blocks):
    n = 0
    for node, _ in order:
        block = blocks.get(node.get("block")) if node.get("t") == "block" else None
        for item in (block.items if block else []):
            if item.kind.collects_answer and item.type not in ("consent", "page_break") and item.id not in doc.numbers:
                n += 1
                doc.numbers[item.id] = n


def _part(doc, styles, order, blocks, annotated):
    """The flowables of part 1 or part 2. In part 2 what a condition, a randomisation or a loop holds is indented
    under its note (INDENT per level), so a reader sees which blocks it rules."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Indenter

    out, width = [], 174 * mm
    last_block = max((n for n, (node, _) in enumerate(order) if node.get("t") == "block"), default=-1)
    for n, (node, depth) in enumerate(order):
        indent = INDENT * depth * mm if annotated else 0
        shown = _node(doc, styles, node, n > last_block, blocks, annotated, width - indent)
        if shown and indent:
            shown = [Indenter(left=indent)] + shown + [Indenter(left=-indent)]
        out += shown
    return out


def _node(doc, styles, node, after_last_block, blocks, annotated, width):
    """[flowable] of one node of the flow: a block with its elements, or (part 2) the note of any other node."""
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Spacer, Table, TableStyle

    out = []
    if node.get("t") != "block":
        if after_last_block and node.get("t") == "split":
            return out  # a separated section with no block behind it shows nothing
        note = _flow_note(doc, node) if annotated else None
        if note:
            out.append(Paragraph(f"<b>{_safe(note[1])}</b>", styles[note[0]]))
            out[-1].keepWithNext = True  # a note never stands alone at the foot of a page
        return out
    block = blocks.get(node.get("block"))
    if block is None:
        return out
    title = block.title(doc.lang)
    if title and (annotated or block.show_title):
        out.append(Paragraph(_words(title), styles["h2"]))
    if annotated and block.randomize_items:
        out.append(Paragraph(_safe(doc.say("Elemente je Seite in zufälliger Reihenfolge",
                                           "Elements in random order within each page")), styles["random"]))
    pages = block.pages()
    for p_no, page in enumerate(pages, start=1):
        if annotated and len(pages) > 1:  # a block of one page needs no page number
            out.append(Paragraph(_safe(doc.say("Seite {n}", "Page {n}").format(n=p_no)), styles["variable"]))
        for item in page:
            if item.type == "page_break" or (not annotated and item.type == "calc"):  # computed, never shown
                continue
            shown = _element(doc, styles, item, annotated)
            if not shown:
                continue
            if annotated:
                notes = [Paragraph(_safe(text), styles[key]) for key, text in _notes(doc, item)]
                table = Table([[shown, notes or ""]], colWidths=[width - 64 * mm, 64 * mm], hAlign="LEFT")
                table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                           *([("LINEBEFORE", (1, 0), (1, 0), 0.5, tokens()["--border-strong"])] if notes else []),
                                           ("LEFTPADDING", (0, 0), (0, 0), 0), ("LEFTPADDING", (1, 0), (1, 0), 6),
                                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
                out.append(table)
            else:
                out.append(KeepTogether(shown + [Spacer(1, 4)]))
    return out


def _legend(doc, styles):
    """The legend of part 2: a drawn square in each note colour and what it marks."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    rows = (("logic", doc.say("Anzeigelogik und Bedingungen", "Display logic and conditions")),
            ("random", doc.say("Zufallsreihenfolgen, Randomisierungen, Schleifen", "Random orders, randomisations, loops")),
            ("check", doc.say("Pflichtangaben, Prüfungen, Enden", "Required answers, checks, ends")),
            ("variable", doc.say("Variablennamen und Seiten", "Variable names and pages")),
            ("source", doc.say("Instrumente und Quellen", "Instruments and sources")))
    table = Table([[_swatch(colours()[key]), Paragraph(_safe(text), styles[key])] for key, text in rows],
                  colWidths=[5 * mm, 120 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return table


def build(survey, ui=None, public=False):
    """The PDF as bytes. ui: the language of the frame ("de"/"en"; None: the page's, German outside a request);
    public: for a deposit, without the wording of instruments that may not be passed on (export.withheld)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, SimpleDocTemplate, Spacer

    from app import brand
    from app.items.base import reader

    doc = _Doc(survey, reader(ui), public)
    styles = _styles(doc.accent)
    blocks = {b.id: b for b in survey.blocks}
    order = _ordered_blocks(survey)
    _number(doc, order, blocks)
    plain_title = plain(survey.title_in(doc.lang) if hasattr(survey, "title_in") else survey.title)
    title = _safe(plain_title)
    version, maker = survey.qversion or 1, brand.name()
    story = []
    if doc.logo:  # the survey's logo, as on its pages: never altered, only scaled
        path, w, h, position = doc.logo
        story += [Image(path, width=w * mm, height=h * mm,
                        hAlign={"center": "CENTER", "right": "RIGHT"}.get(position, "LEFT")), Spacer(1, 5 * mm)]
    story += [Paragraph(title, styles["title"]),
              Paragraph(_safe(doc.say("Befragungsmaterial, Fragebogen-Fassung {v}; Sprache des Fragebogens: {lang}",
                                      "Survey material, questionnaire version {v}; language of the questionnaire: {lang}")
                              .format(v=version, lang=doc.lang)), styles["subtitle"]),
              Spacer(1, 6),
              Paragraph(_safe(doc.say("Teil 1 zeigt den Fragebogen, wie Teilnehmende ihn sehen (Pflichtfragen mit *). "
                                      "Teil 2 zeigt ihn mit Anmerkungen neben jedem Element:",
                                      "Part 1 shows the questionnaire as participants see it (required questions with *). "
                                      "Part 2 shows it with notes beside each element:")), styles["body"]),
              Spacer(1, 3), _legend(doc, styles)]
    rules = _quality(doc)
    if rules:
        story += [Spacer(1, 4), Paragraph(_safe(rules), styles["check"])]
    story += [Spacer(1, 8), Paragraph(_safe(doc.say("Teil 1: Fragebogen", "Part 1: Questionnaire")), styles["h1"])]
    story += _part(doc, styles, order, blocks, annotated=False)
    story += [PageBreak(), Paragraph(_safe(doc.say("Teil 2: Fragebogen mit Anmerkungen",
                                                   "Part 2: Questionnaire with notes")), styles["h1"])]
    story += _part(doc, styles, order, blocks, annotated=True)
    muted = tokens()["--muted"]
    width, height = A4

    def frame(canvas, _doc):
        """A thin line in the accent at the head of every page; at the foot title and version, the name of the tool
        in the serif of its mark, and the page number."""
        canvas.saveState()
        canvas.setStrokeColor(doc.accent)
        canvas.setLineWidth(0.8)
        left, right = 18 * mm + PAD, width - 18 * mm - PAD  # flush with the text (the frame's padding)
        canvas.line(left, height - 13 * mm, right, height - 13 * mm)
        canvas.setFont(pdf_fonts.REGULAR, 7.5)
        canvas.setFillColor(muted)
        pdf_fonts.draw(canvas, left, 10 * mm, _plain_footer(plain_title, version, doc), 7.5)
        number = str(canvas.getPageNumber())
        canvas.drawRightString(right, 10 * mm, number)
        pdf_fonts.draw(canvas, right - canvas.stringWidth(number, pdf_fonts.REGULAR, 7.5) - 4 * mm, 10 * mm, str(maker),
                       8, "Times-Bold", anchor="end")
        canvas.restoreState()

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm,
                      bottomMargin=18 * mm, title=plain_title, author=maker, invariant=1).build(
        story, onFirstPage=frame, onLaterPages=frame)
    return out.getvalue()


def _plain_footer(title, version, doc):
    return str(title or "")[:90] + " | " + doc.say("Fassung {v}", "version {v}").format(v=version)


def _quality(doc):
    """The speeder rule of the survey in one sentence (app/export.rule_text), as the codebook names it."""
    from app import export

    return doc.say("Schnellantworter (Speeder): ", "Speeders: ") + _cap(export.rule_text(doc.survey, doc.ui))
