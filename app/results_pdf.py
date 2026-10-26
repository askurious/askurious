"""The results of a survey as one PDF report after APA 7 (Ralf 2026-10-26: "das ganze Ergebnisdokument muss dem
APA-Standard entsprechen"), for every kind of survey, from the button "Ergebnisbericht" on the export page.

What it holds: a title page with the data basis (pool, participations, those left out, weighting), the sample, then
every question in the order of the questionnaire with its figure and its table, the scales, the choice experiments
(aggregate logit and, where estimated, hierarchical Bayes) and at the end the references. It rests on the saved data
pool and the results the results page computed and kept (app/results_store.py); it estimates nothing anew (an HB
estimate that is missing is named as missing). Free texts are not printed, only how many there are.

APA 7 (Publication Manual, 7th ed., ch. 7): every table and every figure has a number in bold and a title in italics
above it, numbered through the whole report (Tabelle 1, 2, ...; Abbildung 1, 2, ...), notes below starting with
"Anmerkung." in italics; tables only with horizontal rules (above and below the head, at the end); statistical symbols
in italics (M, SD, Mdn, n, N, p); no leading zero for values that cannot exceed 1 (α, ω, p); percentages with one
decimal (as on the results page). Greek letters come from the standard font Symbol, as the standard PDF fonts print
only Western European scripts (cp1252).

The frame (headings, notes, names of statistics) is in the language of the person downloading; the questionnaire's
texts are in its default language. Layout, font and colours as the survey material (app/study_material.py)."""
import io
import re
from xml.sax.saxutils import escape

from app import study_material as sm
from app.items.base import plain

NAME = "ergebnisbericht.pdf"
# printed in the standard font Symbol, which reportlab fills from these Unicode characters (cp1252 has none of them)
GREEK = set("αωχηβρεσμΔτκλνπ−→≤≥∞")
SYMBOLS = ("M", "SD", "Mdn", "n", "N", "p", "Q1", "Q3", "RLH", "KI", "CI")
WIDTH_MM = 174  # the text width of A4 with 18 mm margins
BAR_MAX = 14  # rows of a bar figure; more categories: only the table


def _safe(text):
    """Escaped for a Paragraph, Greek letters in the font Symbol, the rest as the standard fonts print it."""
    out = []
    for ch in str(text if text is not None else ""):
        if ch in GREEK:
            out.append(f'<font face="Symbol">{ch}</font>')
        else:
            out.append(escape(ch.encode("cp1252", "replace").decode("cp1252")))
    return "".join(out)


def _q(text):
    """A text of the questionnaire as one line of plain words (no formatting marks, no piping placeholders)."""
    words = " ".join(plain(text or "").split())
    return re.sub(r"\{\{[^}]*\}\}|\[\[[^\]]*\]\]", "…", words)


class Report:
    """Counters, language and styles of one report."""

    def __init__(self, survey, ui):
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.styles import ParagraphStyle

        self.survey, self.ui = survey, ui
        self.tables = self.figures = 0
        self.cited = []  # the full citations the report cites; only these go into the references (APA 7, 8.1)
        self.accent, self.logo = sm._look(survey) if survey is not None else (sm.tokens()["--accent"], None)
        self.styles = sm._styles(self.accent)
        ink, muted = sm.tokens()["--fg"], sm.tokens()["--muted"]
        body = self.styles["body"]
        self.styles.update({
            "label": ParagraphStyle("lab", parent=body, fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=0,
                                    keepWithNext=1, textColor=ink),
            "caption": ParagraphStyle("cap", parent=body, fontName="Helvetica-Oblique", spaceBefore=1, spaceAfter=4,
                                      keepWithNext=1, textColor=ink),
            "note": ParagraphStyle("note", parent=body, fontSize=8, leading=10.5, spaceBefore=3, spaceAfter=6,
                                   textColor=ink),
            "cell": ParagraphStyle("cell", parent=body, fontSize=8.5, leading=10.5, spaceBefore=0, spaceAfter=0,
                                   alignment=TA_LEFT, textColor=ink),
            "ref": ParagraphStyle("ref", parent=body, fontSize=9, leading=12, leftIndent=12 * 2.835,
                                  firstLineIndent=-12 * 2.835, spaceBefore=0, spaceAfter=4, textColor=ink),
            "muted": ParagraphStyle("mut", parent=body, textColor=muted),
            # a heading of the title page, kept out of the table of contents (only h1 and h2 go there)
            "front": ParagraphStyle("front", parent=self.styles["h2"]),
        })

    def say(self, de, en, **values):
        text = en if self.ui == "en" else de
        return text.format(**values) if values else text

    def cite(self, *sources):
        """The short reference "(Wilson, 1927)" of the sources, which then stand in the references."""
        from app import apa

        for src in sources:
            if src not in self.cited:
                self.cited.append(src)
        return "(" + "; ".join(apa.ref(src)["short"] for src in sources) + ")"

    # --- numbers ------------------------------------------------------------------------------------------------
    def num(self, value, places=2):
        from app import units

        return units.stat(value, places, self.ui)

    def bounded(self, value, places=2):
        """A value that cannot exceed 1 (α, ω, p, r) without its leading zero (APA 7, 6.36): ,82 or .82."""
        text = self.num(value, places)
        return re.sub(r"^(−?)0(?=[.,])", r"\1", text)

    def pct(self, share):
        from app import units

        return "–" if share is None else units.percent_text(share, self.ui)

    def ci(self, pair, places=2, share=False, bounded=False):
        if not pair or pair[0] is None or pair[1] is None:
            return ""
        if share:
            lo, hi = self.pct(pair[0]), self.pct(pair[1])
        elif bounded:
            lo, hi = self.bounded(pair[0], places), self.bounded(pair[1], places)
        else:
            lo, hi = self.num(pair[0], places), self.num(pair[1], places)
        sep = ", " if self.ui == "en" else "; "
        return f"[{lo}{sep}{hi}]"

    def sym(self, text):
        """A head or a line with its statistical symbols in italics."""
        out = _safe(text)
        for s in sorted(SYMBOLS, key=len, reverse=True):
            out = re.sub(rf"(?<![\w>/]){re.escape(s)}(?![\w<])", f"<i>{s}</i>", out)
        return out

    # --- tables and figures -------------------------------------------------------------------------------------
    def table(self, title, head, rows, note=None, widths=None, numeric_from=1, numeric=None):
        """[flowables] of one APA table: "Tabelle n" bold, its title in italics, horizontal rules only, the note."""
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import KeepTogether, Paragraph, Table, TableStyle

        self.tables += 1
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.styles import ParagraphStyle

        cell, ink = self.styles["cell"], colors.HexColor(sm.tokens()["--fg"])
        right = ParagraphStyle("cellr", parent=cell, alignment=TA_RIGHT)  # numbers flush right, under their head

        def style(i):
            if numeric is not None:
                return right if i < len(numeric) and numeric[i] else cell
            return right if i >= numeric_from else cell

        data = [[Paragraph(self.sym(h), style(i)) for i, h in enumerate(head)]]
        data += [[Paragraph(c if isinstance(c, Markup) else _safe(c), style(i)) for i, c in enumerate(row)] for row in rows]
        total = WIDTH_MM * mm
        if widths:
            col = [w * total for w in widths]
        else:
            first = 0.42 if len(head) > 2 else 0.6
            col = [first * total] + [(1 - first) * total / max(1, len(head) - 1)] * (len(head) - 1)
        t = Table(data, colWidths=col, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 0.8, ink), ("LINEBELOW", (0, 0), (-1, 0), 0.5, ink),
            ("LINEBELOW", (0, -1), (-1, -1), 0.8, ink), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        head_part = [Paragraph(_safe(self.say("Tabelle {n}", "Table {n}", n=self.tables)), self.styles["label"]),
                     Paragraph(_safe(title), self.styles["caption"])]
        out = [KeepTogether(head_part + [t] + self._note(note))] if len(rows) <= 25 else head_part + [t] + self._note(note)
        return out

    def figure(self, title, drawing, note=None):
        """[flowables] of one APA figure: "Abbildung n" bold and its title in italics above it, the note below."""
        from reportlab.platypus import KeepTogether, Paragraph

        self.figures += 1
        return [KeepTogether([Paragraph(_safe(self.say("Abbildung {n}", "Figure {n}", n=self.figures)), self.styles["label"]),
                              Paragraph(_safe(title), self.styles["caption"]), drawing] + self._note(note))]

    def _note(self, note):
        from reportlab.platypus import Paragraph

        if not note:
            return []
        return [Paragraph(f"<i>{_safe(self.say('Anmerkung.', 'Note.'))}</i> " + self.sym(note), self.styles["note"])]

    def bars(self, rows, value_text, axis_text=None, top=None):
        """A horizontal bar figure: rows [(label, value 0…top)], the value written at each bar's end, a thin axis with
        0 and the top; grey-blue bars in the accent, no frame and no grid (APA 7, 7.22: as simple as it can be)."""
        from reportlab.graphics.shapes import Drawing, Line, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.units import mm

        ink, accent = colors.HexColor(sm.tokens()["--fg"]), colors.HexColor(self.accent)
        top = top or max([v for _, v in rows if v is not None] + [1e-9])
        label_w, value_w, bar_h, gap = 62 * mm, 26 * mm, 4.2 * mm, 2.2 * mm
        plot_w = WIDTH_MM * mm - label_w - value_w
        height = len(rows) * (bar_h + gap) + 8 * mm
        d = Drawing(WIDTH_MM * mm, height)
        y = height - 2 * mm
        for label, value in rows:
            y -= bar_h + gap
            text = str(label)
            while len(text) > 6 and _width(text, 8) > label_w - 3 * mm:
                text = text[:-2]
            if text != str(label):
                text = text.rstrip() + "…"
            d.add(String(label_w - 2 * mm, y + 1.2 * mm, _cp(text), fontName="Helvetica", fontSize=8,
                         textAnchor="end", fillColor=ink))
            w = 0 if value is None else max(0.3, plot_w * value / top)
            d.add(Rect(label_w, y, w, bar_h, fillColor=accent, strokeColor=None))
            d.add(String(label_w + w + 1.5 * mm, y + 1.2 * mm, _cp(value_text(value, label)), fontName="Helvetica",
                         fontSize=8, fillColor=ink))
        d.add(Line(label_w, 6 * mm, label_w, height - 2 * mm, strokeColor=ink, strokeWidth=0.5))
        d.add(Line(label_w, 6 * mm, label_w + plot_w, 6 * mm, strokeColor=ink, strokeWidth=0.5))
        d.add(String(label_w, 2 * mm, "0", fontName="Helvetica", fontSize=7, textAnchor="middle", fillColor=ink))
        if axis_text:
            d.add(String(label_w + plot_w, 2 * mm, _cp(axis_text), fontName="Helvetica", fontSize=7,
                         textAnchor="end", fillColor=ink))
        return d


class Markup(str):
    """A cell already written as Paragraph markup."""


def _cp(text):
    """What the standard fonts draw on a figure: the minus sign as a hyphen there (no font change inside a String)."""
    return str(text).replace("−", "-").encode("cp1252", "replace").decode("cp1252")


def _width(text, size):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(_cp(text), "Helvetica", size)


# --- the data -----------------------------------------------------------------------------------------------------

def collect(survey):
    """(data, pool, computed_at) of the saved data pool, from the kept results when they still fit, else computed
    and kept as the results page would (marked participations left out, unweighted)."""
    from app import results as res, results_store
    from app.surveys.base import data_pool
    from app.models import utcnow

    f = {**data_pool(survey, values={}, drop=True), "drop": True, "speed_real": True, "weighted": False}
    key = results_store.fingerprint(survey, f)
    kept = results_store.load(survey, key, f)
    if kept is not None and not res.refresh_estimates(kept[0], survey):
        return kept[0], f, kept[1]
    data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"], weighted=False)
    results_store.save(survey, key, data, f, basis=key)
    return data, f, utcnow()


# --- what the results page shows beyond the questions (round 2) ---------------------------------------------------
# The hypotheses of the research plan, the structural equation model, the panel models, the segments and the market
# simulator of a choice experiment, and the displays of the rarer kinds of question are read from the results page
# itself, as the person sees it (the same numbers, the same rounding): its tables become APA tables, their heading the
# title, the small print under them the note. The page is made by its own view within this request, from the results
# kept for the saved pool, so the report and the page never disagree.

def page_tree(survey):
    """The results page of the saved pool as an lxml tree, or None when it cannot be read (then the report keeps to
    what it renders itself)."""
    import inspect

    import lxml.html
    from flask import current_app

    from app.surveys import results as page

    try:
        view = inspect.unwrap(page.results)
        out = view(survey.id)
        html = out.get_data(as_text=True) if hasattr(out, "get_data") else str(out)
        return _readable(lxml.html.fromstring(html))
    except Exception:  # noqa: BLE001 - the report stays useful without the page's extra parts
        current_app.logger.exception("results report: the results page could not be read")
        return None


def _readable(root):
    """The page as text a report can take over: no buttons (the "?" of the explanations), icons, scripts or hidden
    parts; a selection shows its chosen option and a field its value, as the page shows them."""
    for el in root.xpath("//button|//svg|//script|//style|//template|//*[@hidden]|//*[contains(@class, 'sr-only')]"
                         "|//*[contains(@class, 'term-help')]"):
        el.drop_tree()
    for sel in root.xpath("//select"):
        chosen = sel.xpath(".//option[@selected]") or sel.xpath(".//option")
        span = sel.makeelement("span", {})
        span.text = _text(chosen[0]) if chosen else ""
        span.tail = sel.tail
        sel.getparent().replace(sel, span)
    for inp in root.xpath("//input[not(@type='hidden')]"):
        span = inp.makeelement("span", {})
        span.text = inp.get("value") or ""
        span.tail = inp.tail
        inp.getparent().replace(inp, span)
    return root


NUMBER = re.compile(r"^[−\-+]?[\d.,]+\s*(%|€)?$|^\[.*\]$|^–$")


def _text_of_markup(markup):
    return " ".join(re.sub(r"<[^>]+>", "", str(markup)).split())


def _text(el):
    return " ".join((el.text_content() if el is not None else "").split())


def _inline(el):
    """The text of an element as Paragraph markup: italics, sub- and superscripts kept, everything else as text."""
    tags = {"i": "i", "em": "i", "var": "i", "sub": "sub", "sup": "super", "b": "b", "strong": "b"}

    def walk(node):
        out = _safe(node.text or "")
        for child in node:
            tag = tags.get(child.tag if isinstance(child.tag, str) else "")
            inner = walk(child)
            out += f"<{tag}>{inner}</{tag}>" if tag else inner
            out += _safe(child.tail or "")
        return out

    return " ".join(walk(el).split())


def _heading_of(table):
    """The heading a table stands under on the page: the nearest heading, group head or fold summary before it."""
    found = table.xpath("preceding::*[self::h2 or self::h3 or self::summary or contains(concat(' ', @class, ' '), "
                        "' res-group-head ') or contains(concat(' ', @class, ' '), ' res-freq-head ')][1]")
    return _text(found[0]) if found else ""


def _note_of(table):
    """The small print right after a table (in its scroll box or after it), as the note."""
    parent = table.getparent()
    box = parent if parent is not None and "res-scroll" in (parent.get("class") or "") else table
    nxt = box.getnext()
    if nxt is not None and nxt.tag == "p" and "small" in (nxt.get("class") or ""):
        return _text(nxt)
    return None


def harvested(rep, root, skip=()):
    """[flowables]: every table under root as an APA table (head, rows, note), in the page's order."""
    out = []
    for table in root.xpath(".//table"):
        cls = table.get("class") or ""
        if any(k in cls for k in skip):
            continue
        head = [_text(th) for th in table.xpath("./thead/tr[1]/th")]
        rows = [[Markup(_inline(td)) for td in tr.xpath("./th|./td")] for tr in table.xpath("./tbody/tr|./tr")]
        rows = [r for r in rows if any(str(c).strip() for c in r)]
        if not rows:
            continue
        cols = max([len(head)] + [len(r) for r in rows])
        head = (head + [""] * cols)[:cols]
        rows = [(r + [""] * cols)[:cols] for r in rows]
        # widths by the longest word and the length of each column, numbers flush right (a column of numbers)
        plain = [[_text_of_markup(c) for c in r] for r in rows]
        numeric = [bool(col) and sum(bool(NUMBER.match(x)) for x in col if x) >= max(1, len([x for x in col if x])) * 0.7
                   for col in zip(*plain)]
        need = []
        for i in range(cols):
            texts = [head[i]] + [r[i] for r in plain]
            longest_word = max((len(w) for t in texts for w in t.split()), default=1)
            need.append(max(longest_word, min(40, max((len(t) for t in texts), default=1))))
        total = sum(need) or 1
        widths = [n / total for n in need]
        title = _heading_of(table) or rep.say("Ergebnisse", "Results")
        if len(title) > 90 and ":" in title[:90]:  # a fold's summary with its explanation: the name before it
            title = title.split(":", 1)[0]
        elif len(title) > 140:
            title = title[:138].rsplit(" ", 1)[0] + " …"
        out += rep.table(title, head, rows, _note_of(table), widths=widths, numeric=numeric)
    return out


def _hypotheses(rep, root):
    """The results per hypothesis of the page as one table: hypothesis, result in APA form, n, verdict."""
    from reportlab.platypus import Paragraph

    items = root.xpath("//section[@id='hypothesen']//li[contains(@class, 'hyp-result')]")
    if not items:
        return []
    body = []
    for i, li in enumerate(items, 1):
        head = li.xpath(".//p[contains(@class, 'hyp-head')]")
        verdict = li.xpath(".//span[contains(@class, 'hyp-verdict')]")
        sentence = li.xpath(".//span[contains(@class, 'hyp-apa')]//code")
        n = li.xpath(".//p[contains(@class, 'hyp-line')]/span[contains(@class, 'hint')]")
        text = re.sub(r"^H\d+\s*", "", _text(head[0]) if head else "")
        body.append([f"H{i}", Markup(_safe(text)), Markup(_inline(sentence[0])) if sentence else "–",
                     (re.findall(r"\d[\d.,]*", _text(n[0])) or [""])[0] if n else "", _text(verdict[0]) if verdict else ""])
    rules = root.xpath("//section[@id='hypothesen']/details/p[contains(@class, 'small')]")
    out = [Paragraph(_safe(rep.say("Hypothesen", "Hypotheses")), rep.styles["h1"])]
    out += rep.table(rep.say("Ergebnisse je Hypothese des Studienplans", "Results per hypothesis of the research plan"),
                     ["", rep.say("Hypothese", "Hypothesis"), rep.say("Ergebnis", "Result"), "n", rep.say("Urteil", "Verdict")],
                     body, _text(rules[-1]) if rules else None, widths=[0.06, 0.34, 0.34, 0.08, 0.18], numeric_from=99)
    return out


def _section(rep, root, sid, heading):
    """A whole section of the page (the structural equation model, the panel models) with its tables."""
    from reportlab.platypus import Paragraph

    found = root.xpath(sid if sid.startswith("/") else f"//section[@id='{sid}']")
    if not found:
        return []
    body = harvested(rep, found[0])
    if not body:
        # no tables yet (too few participations, still being estimated): what the page says instead
        text = _text(found[0])
        if not text:
            return []
        body = [Paragraph(_safe(text[:900]), rep.styles["muted"])]
    return [Paragraph(_safe(heading), rep.styles["h1"])] + body


# --- the parts ----------------------------------------------------------------------------------------------------

def _title_page(rep, data, f, computed_at):
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, Spacer

    from app import brand, weights

    s, st = rep.survey, rep.styles
    out = []
    if rep.logo:
        path, w, h, position = rep.logo
        out += [Image(path, width=w * mm, height=h * mm, hAlign={"center": "CENTER", "right": "RIGHT"}.get(position, "LEFT")),
                Spacer(1, 5 * mm)]
    sources = {"real": rep.say("echte Teilnahmen", "real participations"), "test": rep.say("Testdurchläufe", "test runs"),
               "dummy": rep.say("Dummy-Daten", "dummy data")}
    pool = ", ".join(sources.get(x, x) for x in f["sources"])
    if f["all"]:
        pool += rep.say(", auch nicht abgeschlossene", ", also those not completed")
    from app import timeutil

    when = timeutil.fmt(computed_at, lang=rep.ui)
    out += [Paragraph(_safe(_q(s.title)), st["title"]),
            Paragraph(_safe(rep.say("Ergebnisbericht", "Results report")), st["subtitle"]), Spacer(1, 6 * mm),
            Paragraph(_safe(rep.say("Datengrundlage", "Data basis")), st["front"]),
            Paragraph(rep.sym(rep.say("N = {n} Teilnahmen ({pool}).", "N = {n} participations ({pool}).",
                                      n=data.get("n_rows", 0), pool=pool)), st["body"])]
    marked = data.get("marked") or 0
    if marked:
        out.append(Paragraph(_safe(rep.say("Nicht enthalten: {m} von der Qualitätssicherung markierte Teilnahmen (davon {s} "
                                           "Speeder).", "Not included: {m} participations marked by quality assurance "
                                           "({s} of them speeders).", m=marked, s=data.get("speeders") or 0)), st["body"]))
    out += [Paragraph(_safe(rep.say("Ungewichtete Ergebnisse. Berechnet am {d} ohne KI, aus den Ergebnissen der "
                                    "Ergebnisseite.", "Unweighted results. Computed on {d} without AI, from the "
                                    "results of the results page.", d=when)), st["body"]),
            Paragraph(_safe(rep.say("Konfidenzintervalle und p-Werte setzen eine Zufallsstichprobe voraus; bei "
                                    "Gelegenheitsstichproben gelten sie nur unter dieser Annahme {c}.",
                                    "Confidence intervals and p values assume a random sample; with convenience samples "
                                    "they hold only under this assumption {c}.", c=rep.cite(weights.AAPOR))), st["muted"]),
            Spacer(1, 4 * mm),
            Paragraph(_safe(rep.say("Erstellt mit {b}", "Made with {b}", b=brand.name())), st["muted"])]
    return out


def _sample(rep, f):
    """The participations of the pool's sources by their end, and the field period of a survey with fieldwork."""
    from reportlab.platypus import Paragraph

    from app import plugins
    from app.models import Response as R

    s = rep.survey
    tally = s.tally()
    ends = [(R.COMPLETED, rep.say("abgeschlossen", "completed")), (R.ACTIVE, rep.say("abgebrochen oder laufend", "broken off or running")),
            (R.SCREENOUT, rep.say("Screen-out", "screen-out")), (R.QUOTAFULL, rep.say("Quote voll", "quota full")),
            (R.EXCLUDED, rep.say("ausgeschlossen", "excluded"))]
    counts = {k: sum(n for (source, _, status), n in tally.items() if source in f["sources"] and status == k) for k, _ in ends}
    total = sum(counts.values())
    if not total:
        return []
    out = [Paragraph(_safe(rep.say("Stichprobe", "Sample")), rep.styles["h1"])]
    body = [[label, str(counts[k]), rep.pct(counts[k] / total)] for k, label in ends if counts[k]]
    body.append([rep.say("gesamt", "total"), str(total), rep.pct(1)])
    view = plugins.view(s)
    note = rep.say("Teilnahmen der Quellen des Datenpools nach ihrem Ende.", "Participations of the data pool's sources by "
                   "their end.")
    if s.opened_at and not (view and "quality" not in view.get("tabs", ())):
        from app import timeutil

        end = timeutil.fmt(s.closed_at, False, rep.ui) if s.closed_at else rep.say("laufend", "running")
        note += " " + rep.say("Erhebungszeitraum: {a} bis {b}.", "Field period: {a} to {b}.",
                              a=timeutil.fmt(s.opened_at, False, rep.ui), b=end)
    out += rep.table(rep.say("Teilnahmen nach ihrem Ende", "Participations by their end"),
                     [rep.say("Ende", "End"), "n", "%"], body, note, widths=[0.6, 0.2, 0.2])
    return out


def _freq(rep, e, c, title):
    """A nominal or ordinal column: the figure (shares, most frequent first for nominal answers) and the table."""
    rows = list(c.get("rows") or [])
    if not rows:
        return []
    if c.get("type") == "freq":
        rows = sorted(rows, key=lambda r: -(r.get("count") or 0))
    labels = [str(r.get("label") or r.get("value")) for r in rows]
    out = []
    if len(rows) <= BAR_MAX:
        values = {label: r for label, r in zip(labels, rows)}
        top = max([r.get("share") or 0 for r in rows] + [0.01])
        out += rep.figure(rep.say("{t}: Anteile der Antworten", "{t}: shares of the answers", t=title),
                          rep.bars([(label, r.get("share")) for label, r in zip(labels, rows)],
                                   lambda v, label: f"{rep.pct(v)} ({values[label].get('count')})", rep.pct(top), top=top),
                          rep.say("n = {n}. Anteil in Prozent, Anzahl in Klammern.",
                                  "n = {n}. Share in per cent, count in brackets.", n=c.get("n")))
    body = [[label, str(r.get("count")), rep.pct(r.get("share")), rep.ci(r.get("ci"), share=True)]
            for label, r in zip(labels, rows)]
    from app import intervals

    note = rep.say("n = {n}. KI = 95-%-Konfidenzintervall nach Wilson {c}.", "n = {n}. CI = 95% confidence interval after "
                   "Wilson {c}.", n=c.get("n"), c=rep.cite(intervals.WILSON))
    if c.get("type") == "ord" and c.get("median"):
        md = c["median"]
        note += " " + rep.say("Mdn = {m}.", "Mdn = {m}.", m=md.get("label") or md.get("value"))
    missing = [m for m in c.get("missing") or [] if m.get("count")]
    if missing:
        note += " " + rep.say("Als fehlend gewertet: {x}.", "Counted as missing: {x}.",
                              x=", ".join(f"{m.get('label') or m.get('value')} ({m.get('count')})" for m in missing))
    out += rep.table(rep.say("{t}: Häufigkeiten", "{t}: frequencies", t=title),
                     [rep.say("Antwort", "Answer"), "n", "%", rep.say("95-%-KI", "95% CI")], body, note,
                     widths=[0.52, 0.12, 0.14, 0.22])
    return out


def _numbers(rep, e, cols, title):
    """Numeric columns: one table with n, M, SD, Mdn, Min, Max and the 95 % interval of M."""
    body = []
    for c in cols:
        body.append([str(c.get("label") or c.get("code")) if len(cols) > 1 else _q(e.get("question")) or c.get("code"),
                     str(c.get("n")), rep.num(c.get("mean")), rep.num(c.get("sd")), rep.num(c.get("median")),
                     rep.num(c.get("min")), rep.num(c.get("max")), rep.ci(c.get("ci"))])
    unit = next((c.get("unit") for c in cols if c.get("unit")), "")
    note = rep.say("KI = 95-%-Konfidenzintervall des Mittelwerts.", "CI = 95% confidence interval of the mean.")
    if unit:
        note = rep.say("Einheit: {u}. ", "Unit: {u}. ", u=unit) + note
    return rep.table(rep.say("{t}: Kennwerte", "{t}: descriptive statistics", t=title),
                     ["", "n", "M", "SD", "Mdn", "Min", "Max", rep.say("95-%-KI", "95% CI")], body, note,
                     widths=[0.30, 0.08, 0.09, 0.09, 0.09, 0.09, 0.09, 0.17])


def _ordinal_matrix(rep, e, cols, title):
    """A matrix of ordinal statements: one table, a row per statement with the share of every step and the median."""
    steps = [str(r.get("label") or r.get("value")) for r in cols[0].get("rows") or []]
    if not steps or len(steps) > 7:
        return [x for c in cols for x in _freq(rep, e, c, f"{title}, {c.get('label') or c.get('code')}")]
    body = []
    for c in cols:
        shares = {str(r.get("label") or r.get("value")): r.get("share") for r in c.get("rows") or []}
        md = (c.get("median") or {})
        body.append([str(c.get("label") or c.get("code")), str(c.get("n"))]
                    + [rep.pct(shares.get(s)) for s in steps] + [str(md.get("label") or md.get("value") or "–")])
    first = 0.30
    rest = (1 - first - 0.07 - 0.13) / len(steps)
    note = rep.say("Anteile je Aussage in Prozent. Stufen: {s}.", "Shares per statement in per cent. Steps: {s}.",
                   s="; ".join(f"{i + 1} = {s}" for i, s in enumerate(steps)))
    return rep.table(rep.say("{t}: Verteilung je Aussage", "{t}: distribution per statement", t=title),
                     ["", "n"] + [str(i + 1) for i in range(len(steps))] + ["Mdn"], body, note,
                     widths=[first, 0.07] + [rest] * len(steps) + [0.13])


def _multi(rep, m, title):
    rows = list(m.get("rows") or [])
    if not m.get("n") or not rows:
        return []
    labels = [str(r.get("label") or r.get("value")) for r in rows]
    out = []
    if len(rows) <= BAR_MAX:
        values = dict(zip(labels, rows))
        out += rep.figure(rep.say("{t}: Anteil der Personen je Antwort", "{t}: share of people per answer", t=title),
                          rep.bars(list(zip(labels, [r.get("share") for r in rows])),
                                   lambda v, label: f"{rep.pct(v)} ({values[label].get('count')})", rep.pct(1), top=1),
                          rep.say("n = {n}. Mehrere Antworten möglich; die Anteile ergeben zusammen nicht 100 %.",
                                  "n = {n}. Several answers possible; the shares do not add up to 100%.", n=m.get("n")))
    out += rep.table(rep.say("{t}: Häufigkeiten", "{t}: frequencies", t=title),
                     [rep.say("Antwort", "Answer"), "n", "%", rep.say("95-%-KI", "95% CI")],
                     [[label, str(r.get("count")), rep.pct(r.get("share")), rep.ci(r.get("ci"), share=True)]
                      for label, r in zip(labels, rows)],
                     rep.say("n = {n} Personen, die die Frage beantwortet haben.", "n = {n} people who answered.",
                             n=m.get("n")), widths=[0.52, 0.12, 0.14, 0.22])
    return out


def _scales(rep, e, title):
    """The scale of a question and its subscales in one reliability table."""
    found = ([(rep.say("Gesamtskala", "Whole scale"), e["scale"])] if e.get("scale") else [])         + [(s.get("name") or "", s) for s in e.get("subscales") or []]
    if not found:
        return []
    head = [rep.say("Skala", "Scale"), rep.say("Aussagen", "Items"), "n", "α", rep.say("95-%-KI", "95% CI"), "ω", "M", "SD"]
    body = [[name, str(s.get("k")), str(s.get("n")), rep.bounded(s.get("alpha")), rep.ci(s.get("alpha_ci"), bounded=True),
             rep.bounded(s.get("omega")), rep.num(s.get("mean")), rep.num(s.get("sd"))] for name, s in found]
    from app import intervals, scales

    note = rep.say("α = Cronbachs Alpha {a}, KI nach Feldt et al. {f}; ω = McDonalds Omega {m}; M und SD des Skalenmittelwerts.",
                   "α = Cronbach's alpha {a}, CI after Feldt et al. {f}; ω = McDonald's omega {m}; M and SD of the scale mean.",
                   a=rep.cite(scales.CRONBACH), f=rep.cite(intervals.FELDT), m=rep.cite(scales.MCDONALD))
    return rep.table(rep.say("{t}: Reliabilität", "{t}: reliability", t=title), head, body, note,
                     widths=[0.28, 0.09, 0.08, 0.09, 0.18, 0.09, 0.09, 0.10])


def _cbc(rep, e, title):
    """A choice-based conjoint: importances and part-worths of the aggregate logit; with an HB estimate its
    importances (M, SD, Mdn, Q1, Q3 over the people) as figure and table."""
    from app import conjoint_hb

    cbc, out = e.get("cbc") or {}, []
    m = cbc.get("model") or {}
    if m.get("attributes"):
        rows = [(a["label"], a.get("importance")) for a in m["attributes"] if a.get("importance") is not None]
        if m.get("price") and m["price"].get("importance") is not None:
            rows.append((rep.say("Preis (linear)", "Price (linear)"), m["price"]["importance"]))
        rows.sort(key=lambda r: -r[1])
        out += rep.figure(rep.say("{t}: Wichtigkeit der Merkmale (aggregiertes Logit-Modell)",
                                  "{t}: importance of the attributes (aggregate logit model)", t=title),
                          rep.bars(rows, lambda v, label: rep.pct(v), rep.pct(1), top=1),
                          rep.say("N = {p} Personen, {s} Entscheidungen. Spannweite der Teilnutzen eines Merkmals als "
                                  "Anteil an der Summe der Spannweiten.", "N = {p} people, {s} decisions. Range of an "
                                  "attribute's part-worths as a share of the sum of ranges.", p=m.get("persons"), s=m.get("sets")))
        body = []
        for a in m["attributes"]:
            for lv in a.get("levels") or []:
                body.append([f"{a['label']}: {lv.get('label')}", rep.num(lv.get("beta")), rep.num(lv.get("se"))])
        if body:
            from app import choice_model

            note = rep.say("Effektkodierte Teilnutzen (β) des konditionalen Logit-Modells {c} mit Standardfehler (SE). "
                           "McFaddens Pseudo-R² = {r}.", "Effects-coded part-worths (β) of the conditional logit model {c} with "
                           "standard error (SE). McFadden's pseudo R² = {r}.", r=rep.bounded(m.get("rho2")),
                           c=rep.cite(choice_model.MCFADDEN))
            out += rep.table(rep.say("{t}: Teilnutzen (aggregiertes Logit-Modell)", "{t}: part-worths (aggregate logit model)",
                                     t=title), [rep.say("Merkmal: Stufe", "Attribute: level"), "β", "SE"], body, note,
                             widths=[0.6, 0.2, 0.2])
    hb = (cbc.get("hb") or {}).get("result")
    if hb:
        rows = [a for a in hb.get("attributes") or [] if a.get("importance") is not None]
        if hb.get("price") and hb["price"].get("importance") is not None:
            rows.append({**hb["price"], "label": (hb["price"].get("label") or rep.say("Preis", "Price"))
                         + rep.say(" (linear)", " (linear)")})
        rows.sort(key=lambda a: -a["importance"])
        from app import hb as hb_mod

        note = rep.say("Hierarchical Bayes {c}, N = {p} Personen; Wichtigkeit je Person aus ihren Teilnutzen, dann gemittelt. "
                       "SD, Mdn, Q1 und Q3 beschreiben die Unterschiede zwischen den Personen.",
                       "Hierarchical Bayes {c}, N = {p} people; importance per person from their part-worths, then averaged. "
                       "SD, Mdn, Q1 and Q3 describe the differences between the people.", p=hb.get("persons"),
                       c=rep.cite(hb_mod.ALLENBY))
        if not hb.get("converged", True):
            note += " " + rep.say("Die Kette hat die Konvergenzprüfung nicht bestanden.", "The chain did not pass the "
                                  "convergence check.")
        out += rep.figure(rep.say("{t}: Wichtigkeit der Merkmale (Hierarchical Bayes)",
                                  "{t}: importance of the attributes (hierarchical Bayes)", t=title),
                          rep.bars([(a["label"], a["importance"]) for a in rows], lambda v, label: rep.pct(v), rep.pct(1), top=1), note)
        body = [[a["label"], rep.pct(a["importance"]), rep.num((a.get("importance_sd") or 0) * 100, 1),
                 *([rep.pct(a["importance_q"][1]), rep.pct(a["importance_q"][0]), rep.pct(a["importance_q"][2])]
                   if a.get("importance_q") else ["–", "–", "–"])] for a in rows]
        out += rep.table(rep.say("{t}: Wichtigkeit der Merkmale je Person (Hierarchical Bayes)",
                                 "{t}: importance of the attributes per person (hierarchical Bayes)", t=title),
                         [rep.say("Merkmal", "Attribute"), "M", "SD", "Mdn", "Q1", "Q3"], body,
                         rep.say("Werte in Prozent; SD in Prozentpunkten.", "Values in per cent; SD in percentage points."),
                         widths=[0.40, 0.12, 0.12, 0.12, 0.12, 0.12])
    elif (cbc.get("hb") or {}).get("available"):
        out.append(_para(rep, rep.say("Für dieses Experiment liegt keine aktuelle Hierarchical-Bayes-Schätzung vor; sie "
                                      "lässt sich auf der Ergebnisseite starten.", "There is no current hierarchical Bayes "
                                      "estimate for this experiment; it can be started on the results page.")))
    return out


def _para(rep, text, style="body"):
    from reportlab.platypus import Paragraph

    return Paragraph(_safe(text), rep.styles[style])


HANDLED = {"single_choice", "multiple_choice", "likert", "rating", "text_input", "number", "slider", "dropdown", "conjoint",
           "federal_state", "semantic", "matrix", "date_time"}


def _item(rep, e, number, root=None):
    """[flowables] of one question: heading, then its tables and figures."""
    from reportlab.platypus import Paragraph

    item = e["item"]
    question = _q(e.get("question")) or item.code
    title = question if len(question) <= 90 else question[:88].rstrip() + "…"
    out = [Paragraph(_safe(f"{number} {title}"), rep.styles["h2"]),
           Paragraph(_safe(rep.say("Variable {c} · {k} · beantwortet von {n}", "Variable {c} · {k} · answered by {n}",
                                   c=item.code, k=getattr(item.kind, "label", None) or item.type, n=e.get("answered", 0))), rep.styles["muted"])]
    body = []
    short = item.code
    if e.get("cbc"):
        body += _cbc(rep, e, short)
    for m in e.get("multi") or []:
        body += _multi(rep, m, short + (f"_{m['pass']}" if m.get("pass") else ""))
    cols = [c for c in e.get("columns") or [] if c.get("n")]
    ords = [c for c in cols if c.get("type") == "ord"]
    if len(ords) > 1:
        body += _ordinal_matrix(rep, e, ords, short)
    else:
        body += [x for c in ords for x in _freq(rep, e, c, short)]
    for c in cols:
        if c.get("type") == "freq":
            body += _freq(rep, e, c, short if c.get("code") in (None, item.code) else f"{short}, {c.get('code')}")
    nums = [c for c in cols if c.get("type") == "num"]
    if nums:
        body += _numbers(rep, e, nums, short)
    texts = sum(c.get("n") or 0 for c in cols if c.get("type") == "text")
    if texts:
        body.append(_para(rep, rep.say("{n} offene Antworten; im Bericht nicht abgedruckt (Datensatz und Ergebnisseite).",
                                       "{n} open answers; not printed in the report (data set and results page).", n=texts),
                          "muted"))
    body += _scales(rep, e, short)
    section = root.xpath(f"//section[@id='res-{item.code}']") if root is not None else []
    if section and e.get("cbc"):
        # the segments (latent class) and the market simulator of the page, below the models
        for part in section[0].xpath(".//section[contains(@class, 'lc-part')]|.//section[contains(@class, 'sim-section')]"):
            body += harvested(rep, part)
    elif section and (item.type not in HANDLED or not body):
        # a rarer kind of question: the tables of its display on the page (MaxDiff, rankings, card sorting, maps, ...)
        body += harvested(rep, section[0])
    if not body:
        body.append(_para(rep, rep.say("Für diese Art Frage enthält der Bericht keine Darstellung; die Auswertung steht "
                                       "auf der Ergebnisseite.", "The report has no display for this kind of question; "
                                       "the analysis is on the results page."), "muted"))
    return out + body


def _references(rep, data):
    from reportlab.platypus import Paragraph

    from app import apa

    entries = apa.references(list(rep.cited))
    if not entries:
        return []
    out = [Paragraph(_safe(rep.say("Literatur", "References")), rep.styles["h1"])]
    for e in entries:
        html = str(e["html"])
        html = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>', r'<a href="\1">', html)
        html = re.sub(r"<(?!/?(?:i|b|a)\b)[^>]+>", "", html)
        out.append(Paragraph(_greek_markup(html), rep.styles["ref"]))
    return out


def _greek_markup(html):
    """Greek letters of an already escaped markup string into the font Symbol, other characters made printable."""
    out = []
    for ch in html:
        out.append(f'<font face="Symbol">{ch}</font>' if ch in GREEK else ch.encode("cp1252", "replace").decode("cp1252"))
    return "".join(out)


def build(survey, ui=None, data=None, pool=None, computed_at=None, page=True):
    """The report as PDF bytes. ui: the language of the frame ("de"/"en"; None: the page's). page: also read the
    results page for the hypotheses, models, segments, simulator and rarer displays (needs a request of a person)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.platypus.tableofcontents import TableOfContents

    from app import brand
    from app.items.base import reader

    if data is None:
        data, pool, computed_at = collect(survey)
    rep = Report(survey, reader(ui))
    story = _title_page(rep, data, pool, computed_at)
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("toc1", parent=rep.styles["body"], fontName="Helvetica-Bold", spaceBefore=3),
                       ParagraphStyle("toc2", parent=rep.styles["body"], leftIndent=12, spaceBefore=0)]
    story += [Spacer(1, 6 * mm), Paragraph(_safe(rep.say("Inhalt", "Contents")), rep.styles["front"]), toc, PageBreak()]
    story += _sample(rep, pool)
    root = page_tree(survey) if page else None
    if root is not None:
        story += _section(rep, root, "//section[.//table[contains(@class, 'quota-results')]]", rep.say("Quoten", "Quotas"))
        story += _hypotheses(rep, root)
    entries = [e for e in data.get("items") or []]
    if entries:
        story += [Paragraph(_safe(rep.say("Ergebnisse je Frage", "Results per question")), rep.styles["h1"])]
        for i, e in enumerate(entries, 1):
            story += _item(rep, e, f"{i}.", root)
    if root is not None:
        story += _section(rep, root, "scales", rep.say("Skalen im Überblick", "Scales at a glance"))
        story += _section(rep, root, "sem", rep.say("Strukturgleichungsmodell", "Structural equation model"))
        story += _section(rep, root, "panel", rep.say("Modelle über die Wellen", "Models over the waves"))
    refs = _references(rep, data)
    if refs:
        story += [PageBreak()] + refs
    title = _q(survey.title)
    muted, width, height = sm.tokens()["--muted"], *A4
    maker = brand.name()

    def frame(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(rep.accent)
        canvas.setLineWidth(0.8)
        left, right = 18 * mm + sm.PAD, width - 18 * mm - sm.PAD
        canvas.line(left, height - 13 * mm, right, height - 13 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(left, 10 * mm, _cp(title[:80] + " | " + rep.say("Ergebnisbericht", "Results report")))
        number = str(canvas.getPageNumber())
        canvas.drawRightString(right, 10 * mm, number)
        canvas.setFont("Times-Bold", 8)
        canvas.drawRightString(right - canvas.stringWidth(number, "Helvetica", 7.5) - 4 * mm, 10 * mm, _cp(maker))
        canvas.restoreState()

    class Doc(SimpleDocTemplate):
        def afterFlowable(self, flowable):
            """The headings into the table of contents: chapters (h1) and questions (h2)."""
            name = getattr(getattr(flowable, "style", None), "name", "")
            if name in ("h1", "h2") and flowable is not None and getattr(flowable, "_toc", True):
                self.notify("TOCEntry", (0 if name == "h1" else 1, flowable.getPlainText(), self.page))

    out = io.BytesIO()
    Doc(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
        title=_cp(title), author=_cp(maker), invariant=1).multiBuild(story, onFirstPage=frame, onLaterPages=frame)
    return out.getvalue()


def filename(survey):
    from app import export

    return export.filename(survey, "ergebnisbericht")[:-4] + ".pdf"


