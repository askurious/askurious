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
person who downloads or freezes it. The standard PDF fonts print Western European scripts (cp1252), as on the paper
questionnaire (app/paper_pdf.py); other characters show as "?"."""
import io
import re
from xml.sax.saxutils import escape

from app.items.base import plain

# colours of the notes in part 2: one per kind, the same in the legend
COLOURS = {"logic": "#1f5fa8", "random": "#7a3fa0", "check": "#a35a00", "variable": "#555555", "source": "#2e7d4f"}
MODES = {"required": ("Pflicht", "required"), "nudge": ("Hinweis, wenn leer", "prompt if empty"),
         "optional": ("freiwillig", "optional")}
ENDS = {"screenout": ("Screen-out", "screen-out"), "quotafull": ("Quote voll", "quota full"),
        "cellfull": ("Quotenzelle voll", "quota cell full"), "excluded": ("Ausschluss", "exclusion")}
NAME = "survey-material.pdf"


def _safe(text):
    """What the standard fonts can print (cp1252), escaped for a Paragraph."""
    return escape(str(text or "").encode("cp1252", "replace").decode("cp1252"))


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

    def say(self, de, en):
        return en if self.ui == "en" else de


def _styles():
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    out = {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=14, leading=18,
                             spaceBefore=6, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=15,
                             spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=12.5,
                               spaceBefore=5, spaceAfter=1),
        "answer": ParagraphStyle("a", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=12,
                                 spaceBefore=0, spaceAfter=0, leftIndent=10),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10),
    }
    for key, colour in COLOURS.items():
        out[key] = ParagraphStyle(key, parent=out["small"], textColor=colour)
    return out


# --- what an element shows ------------------------------------------------------------------------------------


def _answers(doc, item, annotated=False):
    """[markup line] of what a participant answers with, as on the page; annotated (part 2): with the codes the data
    hold."""
    kind, lang = item.kind, doc.lang
    if item.type in ("single_choice", "multiple_choice"):
        mark = "( )" if item.type == "single_choice" else "[ ]"
        code = (lambda o: f' <font color="{COLOURS["variable"]}">[{_safe(o.get("code"))}]</font>') if annotated else (lambda o: "")
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
        return [_words(re.sub(r"^\s*-\s*", "", line)) for line in lines if line.strip()]
    if kind.collects_answer:
        return ["<i>" + _safe(doc.say("Interaktives Element ({label}); online beantwortet.",
                                      "Interactive element ({label}); answered online.").format(label=_label(doc, item)))
                + "</i>"]
    return []


def _label(doc, item):
    from app.lang import Both

    label = item.kind.label
    if isinstance(label, Both):
        return label.en if doc.ui == "en" else label.de
    return str(label)


def _element(doc, styles, item, annotated=False):
    """[Paragraph] of one element as participants see it (annotated: with the codes of the answers)."""
    from reportlab.platypus import Paragraph

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
            out.append(("check", doc.say("Antwort: ", "Answer: ") + (mode[1] if doc.ui == "en" else mode[0])))
    cond = item_condition.text(item, doc.lang, doc.ui)
    if cond:
        out.append(("logic", doc.say("Nur gezeigt, wenn ", "Shown only if ") + plain(cond)))
    if item.type == "likert":
        for s in item.kind.statements(item, doc.lang):
            if s.get("when"):
                out.append(("logic", f"{s.get('export_code')}: " + doc.say("nur gezeigt, wenn ", "shown only if ")
                            + plain(row_logic.text(item, s, doc.lang, doc.ui))))
        reverse = [s.get("export_code") for s in item.kind.statements(item, doc.lang) if s.get("reverse")]
        if reverse:
            out.append(("variable", doc.say("Umgepolt: ", "Reverse coded: ") + ", ".join(reverse)))
    if (item.config or {}).get("randomize"):
        out.append(("random", doc.say("Reihenfolge zufällig", "Random order")))
    validation = (item.config or {}).get("validation")
    if validation and validation != "none":
        out.append(("check", doc.say("Prüfung der Eingabe: ", "Input check: ") + str(validation)))
    if (item.config or {}).get("dont_know"):
        out.append(("check", doc.say("mit „weiß nicht“ (fehlend)", "with “don't know” (missing)")))
    key = constructs.toolbox_key(item)
    if key:
        c = constructs.find(key)
        source = getattr(c, "citation", "") if c else ""
        out.append(("source", doc.say("Instrument: ", "Instrument: ") + key + (f"; {source}" if source else "")))
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
        whens = [w for w in (case.get("when") for case in flow.cases(node)) if w]
        named = ", ".join((ENDS.get(w, (w, w))[1] if doc.ui == "en" else ENDS.get(w, (w, w))[0]) for w in whens)
        return "check", doc.say("Ende ({what})", "End ({what})").format(what=named or "-")
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
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    out = []
    last_block = max((n for n, (node, _) in enumerate(order) if node.get("t") == "block"), default=-1)
    for n, (node, depth) in enumerate(order):
        if node.get("t") != "block":
            if n > last_block and node.get("t") == "split":
                continue  # a separated section with no block behind it shows nothing
            note = _flow_note(doc, node) if annotated else None
            if note:
                out.append(Paragraph(f"<b>{_safe(note[1])}</b>", styles[note[0]]))
            continue
        block = blocks.get(node.get("block"))
        if block is None:
            continue
        title = block.title(doc.lang)
        if title and (annotated or block.show_title):
            out.append(Paragraph(_words(title), styles["h2"]))
        if annotated and block.randomize_items:
            out.append(Paragraph(_safe(doc.say("Elemente je Seite in zufälliger Reihenfolge",
                                               "Elements in random order within each page")), styles["random"]))
        for p_no, page in enumerate(block.pages(), start=1):
            if annotated:
                out.append(Paragraph(_safe(doc.say("Seite {n}", "Page {n}").format(n=p_no)), styles["variable"]))
            for item in page:
                if item.type == "page_break":
                    continue
                shown = _element(doc, styles, item, annotated)
                if not shown:
                    continue
                if annotated:
                    notes = [Paragraph(_safe(text), styles[key]) for key, text in _notes(doc, item)]
                    table = Table([[shown, notes or ""]], colWidths=[105 * mm, 65 * mm])
                    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                               ("LINEBEFORE", (1, 0), (1, 0), 0.5, "#bbbbbb"),
                                               ("LEFTPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
                    out.append(table)
                else:
                    out.append(KeepTogether(shown + [Spacer(1, 4)]))
    return out


def build(survey, ui=None, public=False):
    """The PDF as bytes. ui: the language of the frame ("de"/"en"; None: the page's, German outside a request);
    public: for a deposit, without the wording of instruments that may not be passed on (export.withheld)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    from app.items.base import reader

    doc = _Doc(survey, reader(ui), public)
    styles = _styles()
    blocks = {b.id: b for b in survey.blocks}
    order = _ordered_blocks(survey)
    _number(doc, order, blocks)
    title = _safe(plain(survey.title_in(doc.lang) if hasattr(survey, "title_in") else survey.title))
    version = survey.qversion or 1
    story = [Paragraph(title, styles["title"]),
             Paragraph(_safe(doc.say("Befragungsmaterial, Fragebogen-Fassung {v}; Sprache des Fragebogens: {lang}",
                                     "Survey material, questionnaire version {v}; language of the questionnaire: {lang}")
                             .format(v=version, lang=doc.lang)), styles["body"]),
             Spacer(1, 6),
             Paragraph(_safe(doc.say("Teil 1 zeigt den Fragebogen, wie Teilnehmende ihn sehen (Pflichtfragen mit *). "
                                     "Teil 2 zeigt ihn mit Anmerkungen neben jedem Element:",
                                     "Part 1 shows the questionnaire as participants see it (required questions with *). "
                                     "Part 2 shows it with notes beside each element:")), styles["body"])]
    legend = (("logic", doc.say("Anzeigelogik und Bedingungen", "display logic and conditions")),
              ("random", doc.say("Zufallsreihenfolgen, Randomisierungen, Schleifen", "random orders, randomisations, loops")),
              ("check", doc.say("Pflichtangaben, Prüfungen, Enden", "required answers, checks, ends")),
              ("variable", doc.say("Variablennamen und Seiten", "variable names and pages")),
              ("source", doc.say("Instrumente und Quellen", "instruments and sources")))
    story += [Paragraph(f"&#9632; {_safe(text)}", styles[key]) for key, text in legend]
    rules = _quality(doc)
    if rules:
        story += [Spacer(1, 4), Paragraph(_safe(rules), styles["check"])]
    story += [Spacer(1, 8), Paragraph(_safe(doc.say("Teil 1: Fragebogen", "Part 1: Questionnaire")), styles["h1"])]
    story += _part(doc, styles, order, blocks, annotated=False)
    story += [PageBreak(), Paragraph(_safe(doc.say("Teil 2: Fragebogen mit Anmerkungen",
                                                   "Part 2: Questionnaire with notes")), styles["h1"])]
    story += _part(doc, styles, order, blocks, annotated=True)

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor("#666666")
        canvas.drawString(18 * mm, 10 * mm, _plain_footer(title, version, doc))
        canvas.drawRightString(192 * mm, 10 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                      bottomMargin=18 * mm, title=title, author="Askurious", invariant=1).build(
        story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def _plain_footer(title, version, doc):
    from html import unescape

    return unescape(title)[:90] + " | " + doc.say("Fassung {v}", "version {v}").format(v=version)


def _quality(doc):
    """The speeder rule of the survey in one sentence (app/export.rule_text), as the codebook names it."""
    from app import export

    return doc.say("Schnellantworter (Speeder): ", "Speeders: ") + export.rule_text(doc.survey, doc.ui)
