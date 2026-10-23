"""The scales of a survey as a table for a manuscript (.docx), in APA style (7th edition): number and title above,
lines only above and below the head and at the end, a note below. One row per scale or subscale with k, n, M, SD,
Cronbach's alpha and McDonald's omega, then the Pearson correlations of the scale scores below the diagonal; alpha,
omega and r with their 95 % confidence intervals in square brackets (app/intervals.py). A second table holds the item
statistics of every scale (scales.item_analysis).

The numbers are those of the results page (results.overview, app/scales.py): the survey's data pool without what
the quality control marks. English: decimal point and no leading zero where a value cannot exceed 1 (alpha, omega,
r); German: decimal comma with the zero."""
import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app import intervals

TEXT = {
    "de": {"table": "Tabelle 1", "title": "Deskriptive Statistiken, Reliabilität und Interkorrelationen der Skalen",
           "scale": "Skala", "note": "Anmerkung.",
           "body": ("N = {n} Teilnahmen (Datenpool der Befragung, ohne Speeder und von der Qualitätskontrolle markierte "
                    "Teilnahmen). k = Anzahl der Items; n = Teilnahmen mit allen Items beantwortet, Grundlage von α und ω; "
                    "M und SD der Skalenwerte (Mittelwert der Items je Person); α = Cronbachs Alpha; ω = McDonalds Omega "
                    "(Ein-Faktor-Modell, Maximum Likelihood), erst ab drei Items („–“ ab drei Items: nicht schätzbar). "
                    "Korrelationen nach Pearson, paarweise ({pairs})."),
           "stars": "* p < ,05. ** p < ,01 (zweiseitig).", "pairs": "n = {a} bis {b}", "pair": "n = {a}",
           "partial": (" Bei {names} sahen nicht alle Teilnehmenden alle Items; M und SD gehen dort über die gezeigten Items, "
                       "ihr n steht in Klammern."),
           "two": " Bei zwei Items ist α wenig aussagekräftig; Spearman-Brown-Koeffizient: {names}.",
           "negative": " Negatives α ({names}) ist nicht interpretierbar; oft ist ein gegenläufiges Item nicht umgepolt.",
           "negative_sb": (" Negatives α bzw. ein negativer Spearman-Brown-Koeffizient ({names}) ist nicht interpretierbar; "
                           "oft ist ein gegenläufiges Item nicht umgepolt."),
           "small": (" ᵃ ω aus einer kleinen Stichprobe (unter 30 bzw. dreimal so vielen vollständigen Fällen wie Items): "
                     "Das Ein-Faktor-Modell kann dort in einem lokalen Optimum enden, ω ist instabil."),
           "ci": (" Werte in eckigen Klammern: 95-%-Konfidenzintervalle (α nach Feldt et al., 1987; ω als "
                  "Perzentil-Bootstrap mit {draws} Ziehungen, Ziehungen ohne schätzbares ω als kleinster Wert gezählt, "
                  "„–“ als Untergrenze: nach unten offen; r über Fishers z-Transformation), ab n = {n} (ω ab n = {boot})."),
           "items_table": "Tabelle 2", "items_title": "Itemkennwerte der Skalen", "item": "Item",
           "without": "α ohne", "reverse": " (umgepolt)",
           "items_body": ("n = Teilnahmen mit allen Items der Skala beantwortet; M und SD nach Umpolung; P = Schwierigkeit "
                          "(M − kleinste Stufe) / (größte − kleinste Stufe) · 100; r_it = korrigierte Trennschärfe "
                          "(Korrelation mit der Summe der übrigen Items); α ohne = Cronbachs α der Skala ohne das Item "
                          "(ab drei Items); λ = standardisierte Ladung im Ein-Faktor-Modell (ab drei Items)."),
           "flag": " † r_it < 0,30 oder α ohne das Item höher als α der Skala (Faustregeln; Festlegung von Askurious)."},
    "en": {"table": "Table 1", "title": "Descriptive Statistics, Reliability, and Intercorrelations of the Scales",
           "scale": "Scale", "note": "Note.",
           "body": ("N = {n} participants (data pool of the survey, without speeders and participants flagged by the "
                    "quality control). k = number of items; n = participants who answered all items, on which α and ω "
                    "are based; M and SD of the scale scores (mean of the items per participant); α = Cronbach's alpha; "
                    "ω = McDonald's omega (one-factor model, maximum likelihood), from three items on (– with three or more "
                    "items: not estimable). Pearson correlations, pairwise ({pairs})."),
           "stars": "* p < .05. ** p < .01 (two-tailed).", "pairs": "n = {a} to {b}", "pair": "n = {a}",
           "partial": (" For {names}, not all participants saw every item; M and SD there are means over the items shown, "
                       "their n in brackets."),
           "two": " With two items, α is of limited meaning; Spearman-Brown coefficient: {names}.",
           "negative": " A negative α ({names}) is not interpretable; often an item worded in the opposite direction was not reverse-scored.",
           "negative_sb": (" A negative α or Spearman-Brown coefficient ({names}) is not interpretable; often an item worded "
                           "in the opposite direction was not reverse-scored."),
           "small": (" ᵃ ω from a small sample (fewer than 30 complete cases or than three times the number of items): the "
                     "one-factor model can end in a local optimum there, ω is unstable."),
           "ci": (" Values in square brackets are 95% confidence intervals (α after Feldt et al., 1987; ω by percentile "
                  "bootstrap with {draws} resamples, resamples without an estimable ω counted as the lowest value, – as the lower "
                  "limit: open below; r by Fisher's z transformation), from n = {n} on (ω from n = {boot})."),
           "items_table": "Table 2", "items_title": "Item Statistics of the Scales", "item": "Item",
           "without": "α if deleted", "reverse": " (reverse-scored)",
           "items_body": ("n = participants who answered all items of the scale; M and SD after reverse-scoring; P = item "
                          "difficulty (M − lowest level) / (highest − lowest level) × 100; r_it = corrected item-total "
                          "correlation (with the sum of the other items); α if deleted = Cronbach's alpha of the scale "
                          "without the item (from three items on); λ = standardized loading in the one-factor model "
                          "(from three items on)."),
           "flag": " † r_it < .30 or α higher without the item (rules of thumb set by Askurious)."},
}


def fmt(v, lang, bounded=False, places=2):
    """A number as the manuscript writes it; bounded: a value that cannot exceed 1 (no leading zero in English)."""
    from app import units

    # the formatter of the results page and the dashboards (UI/UX audit 2026-10-05, V70): half up, a minus sign,
    # German with a decimal comma and a dot between thousands
    text = units.stat(v, places, "de" if lang == "de" else "en")
    if bounded and lang == "en" and text != "–":
        text = text.replace("0.", ".", 1)
    return text


def fmt_ci(ci, lang, bounded=True):
    """A 95 % interval in square brackets: [0,76; 0,87] in German, [.76, .87] in English (APA); "" without one."""
    if not ci:
        return ""
    sep = "; " if lang == "de" else ", "
    return f"[{fmt(ci[0], lang, bounded)}{sep}{fmt(ci[1], lang, bounded)}]"


def _with_ci(value, ci, lang):
    text = fmt(value, lang, True)
    return f"{text} {fmt_ci(ci, lang)}" if value is not None and ci else text


def _border(cell, edges):
    """Single lines on the given edges of a cell (APA tables have no vertical lines)."""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in edges:
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "6")
        el.set(qn("w:color"), "000000")
        borders.append(el)
    tc_pr.append(borders)


def _write(cell, text, italic=False, right=False):
    """Text into a cell; a line break in it ("\n") becomes one in the cell (a correlation over its interval)."""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    if right:
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for n, line in enumerate(text.split("\n")):
        run = p.add_run(line)
        run.italic = italic
        if n < text.count("\n"):
            run.add_break()


def build(survey, data, lang=None):
    """The tables as .docx bytes. data: what results.compute returned (its "overview" and items); lang "de" or "en"
    (None: the language of the person who downloads, app.lang.current(); German outside a request)."""
    if lang is None:
        from app import lang as ui_lang

        lang = ui_lang.current()
    lang = lang if lang in TEXT else "de"
    t = dict(TEXT[lang])
    ov = data.get("overview") or {"rows": [], "corr": []}
    if ov.get("between_persons"):
        t["body"] = ("N = {n} Personen. Kennwerte, Reliabilität und Korrelationen zwischen Personen beruhen auf "
                     "Personenmittelwerten; n = Personen mit vollständigen Aussagenwerten. k = Anzahl der Items; "
                     "M = Mittelwert, SD = Standardabweichung; α = Cronbachs Alpha; ω = McDonalds Omega "
                     "(Ein-Faktor-Modell, Maximum Likelihood). Korrelationen nach Pearson, paarweise ({pairs})."
                     if lang == "de" else
                     "N = {n} people. Statistics, reliability and between-person correlations use person means; "
                     "n = people with complete statement values. k = number of items; M = mean, SD = standard "
                     "deviation; α = Cronbach's alpha; ω = McDonald's omega (one-factor model, maximum likelihood). "
                     "Pearson correlations, pairwise ({pairs}).")
        t["items_body"] = ("Kennwerte der Personenmittelwerte. " + t["items_body"].replace("Teilnahmen", "Personen")
                           if lang == "de" else "Statistics of person means. " + t["items_body"].replace("participants", "people"))
    rows, corr = ov["rows"], ov["corr"]
    corr_ci = ov.get("corr_ci") or [[None] * len(line) for line in corr]
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = section.top_margin = section.bottom_margin = Cm(2.5)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Times New Roman", Pt(12)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")

    following = 1
    if rows:
        _scales_table(doc, data, rows, corr, corr_ci, t, lang)
        following = 3 if _item_table(doc, rows, t, lang) else 2
    # the saved scenarios of the market simulator (app/simulator.py), one table each
    from app import simulator

    simulator.add_to_docx(doc, data, lang, following)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _scales_table(doc, data, rows, corr, corr_ci, t, lang):
    """Table 1: the scales with their correlations."""
    p = doc.add_paragraph()
    p.add_run(t["table"]).bold = True
    p.paragraph_format.space_after = Pt(0)
    p = doc.add_paragraph()
    p.add_run(t["title"]).italic = True

    m = len(rows)
    head = [t["scale"], "k", "n", "M", "SD", "α", "ω"] + [str(j + 1) for j in range(max(m - 1, 0))]
    table = doc.add_table(rows=1 + m, cols=len(head))
    for j, name in enumerate(head):
        cell = table.rows[0].cells[j]
        _write(cell, name, italic=0 < j < 7, right=j > 0)
        _border(cell, ("top", "bottom"))
    for i, row in enumerate(rows):
        label = row["label"] + (f": {row['sub']}" if row.get("sub") else "")
        # where not everybody saw every item, M and SD rest on more participants than α and ω: their n in brackets
        # (code review 2026-10-15, C10)
        n_text = str(row["n"]) + (f" ({row['n_mean']})" if row.get("partial") and row.get("n_mean") not in (None, row["n"])
                                  else "")
        values = [f"{i + 1}. {label}", str(row["k"]), n_text, fmt(row["mean"], lang), fmt(row["sd"], lang),
                  _with_ci(row["alpha"], row.get("alpha_ci"), lang),
                  (_with_ci(row["omega"], row.get("omega_ci"), lang) + ("ᵃ" if row.get("omega_small") else ""))
                  if row["k"] > 2 else "–"]
        for j in range(m - 1):
            if j < i:
                r, _, p_value = corr[i][j]
                stars = "" if p_value is None else "**" if p_value < .01 else "*" if p_value < .05 else ""
                ci = fmt_ci(corr_ci[i][j], lang) if r is not None else ""
                values.append(fmt(r, lang, True) + stars + (f"\n{ci}" if ci else ""))
            elif j == i:
                values.append("—")
            else:
                values.append("")
        for j, text in enumerate(values):
            cell = table.rows[i + 1].cells[j]
            _write(cell, text, right=j > 0)
            if i == m - 1:
                _border(cell, ("bottom",))

    ns = sorted({n for line in corr for _, n, _ in line})
    pairs = (t["pairs"].format(a=ns[0], b=ns[-1]) if len(ns) > 1 else t["pair"].format(a=ns[0])) if ns else "–"
    name = lambda r: r["label"] + (f": {r['sub']}" if r.get("sub") else "")
    partial = [name(r) for r in rows if r.get("partial")]
    twos = [f"{name(r)} {_with_ci(r.get('sb'), r.get('sb_ci'), lang)}" for r in rows if r["k"] == 2]
    with_ci = any(r.get("alpha_ci") or r.get("omega_ci") or r.get("sb_ci") for r in rows) \
        or any(c for line in corr_ci for c in line)
    # with two items alpha and Spearman-Brown are negative together (both have the sign of the covariance)
    negative = [name(r) for r in rows if r.get("alpha") is not None and r["alpha"] < 0
                or r["k"] == 2 and r.get("sb") is not None and r["sb"] < 0]
    negative_sb = any(r["k"] == 2 and r.get("sb") is not None and r["sb"] < 0 for r in rows)
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.add_run(t["note"]).italic = True
    n = data.get("diary", {}).get("persons", 0) if (data.get("overview") or {}).get("between_persons") else data.get("n_rows", 0)
    p.add_run(" " + t["body"].format(n=n, pairs=pairs)
              + (t["partial"].format(names=", ".join(partial)) if partial else "")
              + (t["two"].format(names="; ".join(twos)) if twos else "")
              + (t["negative_sb" if negative_sb else "negative"].format(names=", ".join(negative)) if negative else "")
              + (t["small"] if any(r.get("omega_small") for r in rows) else "")
              + (t["ci"].format(draws=intervals.OMEGA_DRAWS, n=intervals.MIN_N, boot=intervals.MIN_BOOT) if with_ci else "")
              + (" " + t["stars"] if m > 1 else ""))


def _item_table(doc, rows, t, lang):
    """Table 2: per scale (subscale) a row with its name and n, then one row per item with M, SD, P, r_it, alpha if
    deleted and the loading; values the item analysis highlights get a dagger (see the note)."""
    scales_with = [r for r in rows if r.get("item_stats")]
    if not scales_with:
        return False
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    p = doc.add_paragraph()
    p.add_run(t["items_table"]).bold = True
    p.paragraph_format.space_after = Pt(0)
    p = doc.add_paragraph()
    p.add_run(t["items_title"]).italic = True
    head = [t["item"], "M", "SD", "P", "r_it", t["without"], "λ"]
    count = sum(1 + len(r["item_stats"]) for r in scales_with)
    table = doc.add_table(rows=1 + count, cols=len(head))
    for j, name in enumerate(head):
        cell = table.rows[0].cells[j]
        _write(cell, name, italic=0 < j < 5 or j == 6, right=j > 0)
        _border(cell, ("top", "bottom"))
    line, flagged = 1, False
    for i, row in enumerate(scales_with):
        label = row["label"] + (f": {row['sub']}" if row.get("sub") else "")
        first = table.rows[line].cells
        _write(first[0], f"{label} (n = {row['n']})", italic=True)
        line += 1
        for item in row["item_stats"]:
            text = (item.get("text") or "")
            text = (text[:57] + "…") if len(text) > 58 else text
            name = f"{item['code']} {text}".strip() + (t["reverse"] if item.get("reverse") else "")
            rit = fmt(item["rit"], lang, True) + ("†" if item.get("low_rit") else "")
            without = fmt(item["alpha_without"], lang, True) + ("†" if item.get("high_without") else "")
            flagged = flagged or bool(item.get("low_rit") or item.get("high_without"))
            values = [name, fmt(item["mean"], lang), fmt(item["sd"], lang), fmt(item["p"], lang, places=0), rit, without,
                      fmt(item["loading"], lang, True)]
            for j, text in enumerate(values):
                _write(table.rows[line].cells[j], text, right=j > 0)
            line += 1
    for cell in table.rows[line - 1].cells:
        _border(cell, ("bottom",))
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.add_run(t["note"]).italic = True
    p.add_run(" " + t["items_body"] + (t["flag"] if flagged else ""))
    return True
