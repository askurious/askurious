"""The figure "Askurious-Metadaten" for the handbook page /docs/metadata (Ralf 2026-10-27): what the DDI Lifecycle 4.0
export (app/ddi.py) writes, in panels for the study and for the reusable metadata (data collection, data set, data
file), with the main references. Writes app/static/handbook/<lang>/ddi-structure.svg, which the handbook puts into the
page inline (app/handbook_md.py), so that its colours are the page's (app/static/app.css variables, light and dark).

When the export changes (a new kind of item, a new reference, a new text), change BOXES, PANELS or ARROWS here and run
`python tools/ddi_figure.py`; tests/exports/test_ddi_figure.py fails while a kind of item of ddi.CONTAINER_ORDER is
missing from the figure or the files differ from this script's output.
"""
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "handbook"
WIDTH, HEIGHT = 1160, 880

# the variables of app/static/app.css, with the light values for a file opened on its own
CSS = """
.ddf-panel{fill:var(--surface,#fff);stroke:var(--border-strong,#cfcdc5)}
.ddf-sub{fill:var(--surface-2,#f8f8f5);stroke:var(--border-strong,#cfcdc5)}
.ddf-box{fill:var(--surface,#fff);stroke:var(--border-strong,#cfcdc5)}
.ddf-head{fill:var(--fg,#1c1c1a);font-size:17px;font-weight:650}
.ddf-brand{fill:var(--fg,#1c1c1a);font-size:22px;font-weight:600;font-family:var(--font-serif,Georgia,serif)}
.ddf-title{fill:var(--fg,#1c1c1a);font-size:13px;font-weight:650}
.ddf-line{fill:var(--muted,#6b6b66);font-size:11.5px}
.ddf-tagbg{fill:var(--accent-soft,rgba(41,87,164,.1))}
.ddf-tag{fill:var(--accent,#2957a4);font-size:11px;font-family:var(--mono,ui-monospace,monospace)}
.ddf-ref,.ddf-has{fill:none;stroke:var(--muted,#6b6b66);stroke-width:1.2}
.ddf-ref{stroke-dasharray:3 3}
.ddf-tip{fill:var(--muted,#6b6b66)}
.ddf-label{fill:var(--muted,#6b6b66);font-size:11px;font-style:italic}
"""

# the outer frame: (title, tag)
FRAME = {"de": ("Askurious-Metadaten", "= ItemContainer (DDI-L 4.0)"),
         "en": ("Askurious metadata", "= ItemContainer (DDI-L 4.0)")}

# panels: (x, y, w, h, kind "panel" | "sub", title de, title en, tag)
PANELS = [
    (28, 64, 320, 792, "panel", "Studie", "Study", "= TopLevelReference"),
    (370, 64, 770, 792, "panel", "Wiederverwendbare Metadaten", "Reusable metadata", ""),
    (392, 180, 726, 300, "sub", "Erhebung", "Data collection", "= DataCollection"),
    (392, 500, 726, 214, "sub", "Datensatz", "Data set", "= LogicalProduct"),
    (392, 734, 726, 104, "sub", "Datei", "Data file", "= PhysicalInstance"),
]

# boxes: (key, x, y, w, h, title de, title en, tag (the DDI name), [lines de], [lines en], tag at the bottom)
BOXES = [
    ("study", 48, 128, 280, 178, "Studie", "Study", "StudyUnit",
     ["Titel, Zusammenfassung, Studiennummer", "DOI, Förderung, Schlagwörter", "CESSDA-Thema, Abdeckung (Land, Zeit)",
      "Herausgeber → Organisation"],
     ["Title, abstract, study number", "DOI, funding, keywords", "CESSDA topic, coverage (country, time)",
      "Publisher → organisation"], "Panelwellen = SeriesStatement|Panel waves = SeriesStatement"),
    ("person", 48, 330, 280, 62, "Forschende", "Researchers", "Individual", ["Name, ORCID, Rolle"],
     ["Name, ORCID, role"], ""),
    ("org", 48, 420, 280, 62, "Einrichtung", "Institution", "Organization", ["Organisation, Team; Herausgeber"],
     ["Organisation, team; publisher"], ""),
    ("orgs", 48, 506, 280, 58, "Team und Einrichtungen", "Team and institutions", "OrganizationScheme",
     ["Personen, Einrichtungen, Zugehörigkeit"], ["People, institutions, affiliation"], ""),
    ("archive", 48, 600, 280, 62, "Zugang", "Access", "Archive", ["Lizenz, Zugangsbedingungen"],
     ["Licence, access conditions"], ""),
    ("material", 48, 688, 280, 78, "Weiteres Material", "Other material", "OtherMaterial",
     ["Publikationen (DOI)", "Dateien im Paket"], ["Publications (DOI)", "Files in the package"], ""),
    ("universe", 392, 104, 220, 48, "Grundgesamtheit", "Population", "Universe", [], [], ""),
    ("concept", 904, 104, 214, 62, "Konzept", "Concept", "Concept", ["validierte Konstrukte"],
     ["validated constructs"], ""),
    ("method", 410, 240, 200, 62, "Methodik", "Methodology", "Methodology", ["Zeitbezug, Auswahlverfahren"],
     ["Time method, sampling"], ""),
    ("sample", 410, 326, 200, 62, "Stichprobe", "Sample", "Sample", ["Zielgröße, Ausschlüsse, Quoten"],
     ["Target size, exclusions, quotas"], ""),
    ("event", 410, 404, 200, 62, "Erhebungsereignis", "Collection event", "CollectionEvent",
     ["Zeitraum, Modus, Welle"], ["Period, mode, wave"], ""),
    ("instrument", 640, 240, 190, 46, "Instrument", "Instrument", "Instrument", [], [], ""),
    ("flow", 640, 310, 190, 154, "Ablauf", "Flow", "ControlConstructScheme",
     ["Sequence (Blöcke)", "IfThenElse (Bedingungen)", "Loop (Schleifen)", "Split (Randomisierung)",
      "QuestionConstruct,", "StatementItem"],
     ["Sequence (blocks)", "IfThenElse (conditions)", "Loop (loops)", "Split (randomisation)",
      "QuestionConstruct,", "StatementItem"], ""),
    ("questions", 858, 240, 240, 46, "Fragen", "Questions", "QuestionScheme", [], [], ""),
    ("question", 858, 310, 240, 62, "Frage", "Question", "QuestionItem", ["Fragetext, Antwortformat"],
     ["Question text, response domain"], ""),
    ("hints", 858, 394, 240, 70, "Hinweise", "Hints", "InterviewerInstructionScheme",
     ["Instruction: Hinweise für Interviewende,", "an der Frage im Ablauf"],
     ["Instruction: hints for interviewers,", "attached to the question in the flow"], ""),
    ("variables", 410, 560, 210, 46, "Variablen", "Variables", "VariableScheme", [], [], ""),
    ("variable", 410, 630, 210, 62, "Variable", "Variable", "Variable", ["verweist auf Frage und Konzept"],
     ["points at question and concept"], ""),
    ("generation", 642, 560, 220, 62, "Berechnung", "Calculation", "GenerationInstruction",
     ["abgeleitete Variablen, Formel"], ["derived variables, formula"], ""),
    ("weighting", 642, 640, 220, 52, "Gewichtung", "Weighting", "Weighting", ["Gewichtungsverfahren"],
     ["weighting procedure"], ""),
    ("codes", 886, 560, 212, 46, "Codes", "Codes", "CodeListScheme → CodeList", [], [], ""),
    ("categories", 886, 630, 212, 62, "Kategorien", "Categories", "CategoryScheme → Category",
     ["Antwortstufen, fehlende Werte"], ["Answer levels, missing values"], ""),
    ("file", 600, 762, 300, 58, "Datei", "File", "DataFileIdentification", ["data.csv, Sprachen"],
     ["data.csv, languages"], ""),
]

# arrows: (path, "has" | "ref", label de, label en, label x, label y); "has" solid, "ref" dashed (a reference by URN)
ARROWS = [
    ("M188 306V330", "has", "Creator", "Creator", 196, 322),
    ("M188 392V420", "has", "Affiliation = Relation", "Affiliation = Relation", 196, 410),
    ("M188 482V506", "ref", "", "", 0, 0),
    ("M48 217H38V631H48", "ref", "", "", 0, 0),
    ("M38 631V727H48", "ref", "", "", 0, 0),
    ("M328 217H356V128H392", "ref", "", "", 0, 0),
    ("M356 217V330H392", "ref", "", "", 0, 0),
    ("M356 330V607H392", "ref", "", "", 0, 0),
    ("M356 607V786H392", "ref", "", "", 0, 0),
    ("M510 302V326", "has", "", "", 0, 0),
    ("M735 286V310", "has", "", "", 0, 0),
    ("M830 341H858", "has", "", "", 0, 0),
    ("M978 286V310", "has", "", "", 0, 0),
    ("M515 606V630", "has", "", "", 0, 0),
    ("M620 661H631V591H642", "has", "", "", 0, 0),
    ("M992 606V630", "has", "", "", 0, 0),
    ("M1098 341H1128V583H1098", "ref", "", "", 0, 0),
]

ARIA = {
    "de": "Aufbau der Askurious-Metadaten nach DDI Lifecycle 4.0: links die Studie mit Forschenden, Einrichtungen, "
          "Zugang und weiterem Material; rechts die wiederverwendbaren Metadaten mit Grundgesamtheit, Konzept, "
          "Erhebung (Methodik, Stichprobe, Instrument, Ablauf, Fragen, Hinweise für Interviewende), Datensatz "
          "(Variablen, Berechnung, Gewichtung, Codes, Kategorien) und Datei.",
    "en": "Structure of the Askurious metadata after DDI Lifecycle 4.0: on the left the study with researchers, "
          "institutions, access and other material; on the right the reusable metadata with population, concept, "
          "data collection (methodology, sample, instrument, flow, questions, hints for interviewers), data set "
          "(variables, calculation, weighting, codes, categories) and data file.",
}


def _width(text, size, mono=False):
    """About how wide a text is drawn (for placing the tags)."""
    return len(text) * size * (0.6 if mono else 0.55)


def _tag(x, y, text):
    w = _width(text, 11, mono=True) + 12
    return (f'<rect class="ddf-tagbg" x="{x:g}" y="{y:g}" width="{w:.0f}" height="17" rx="6"/>'
            f'<text class="ddf-tag" x="{x + 6:g}" y="{y + 12.5:g}">{escape(text)}</text>'), w


def svg(lang):
    de = lang == "de"
    out = [f'<svg class="ddi-figure" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
           f'aria-label="{escape(ARIA[lang])}">', f"<title>{escape(ARIA[lang])}</title>", f"<style>{CSS}</style>",
           '<defs><marker id="ddf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path class="ddf-tip" d="M0 0L10 5L0 10z"/></marker></defs>']
    title, tag = FRAME[lang]
    out.append(f'<text class="ddf-brand" x="28" y="40">{escape(title)}</text>')
    out.append(_tag(28 + _width(title, 22) + 14, 26, tag)[0])
    for x, y, w, h, kind, t_de, t_en, t in PANELS:
        name = t_de if de else t_en
        out.append(f'<rect class="ddf-{kind}" x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>')
        out.append(f'<text class="ddf-head" x="{x + 16}" y="{y + 26}">{escape(name)}</text>')
        if t:
            out.append(_tag(x + 16 + _width(name, 17) + 12, y + 12, t)[0])
    for _, x, y, w, h, t_de, t_en, t, l_de, l_en, bottom in BOXES:
        name = t_de if de else t_en
        out.append(f'<g><rect class="ddf-box" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>')
        out.append(f'<text class="ddf-title" x="{x + 12}" y="{y + 19}">{escape(name)}</text>')
        line_y = y + 37
        if t and t != name:
            if 12 + _width(name, 13) + 10 + _width(t, 11, True) + 12 + 12 <= w:  # the tag beside the title
                out.append(_tag(x + w - 12 - (_width(t, 11, True) + 12), y + 6, t)[0])
            else:  # below it
                out.append(_tag(x + 12, y + 24, t)[0])
                line_y = y + 54
        for n, line in enumerate(l_de if de else l_en):
            out.append(f'<text class="ddf-line" x="{x + 12}" y="{line_y + n * 14.5:g}">{escape(line)}</text>')
        if bottom:
            out.append(_tag(x + 12, y + h - 26, bottom.split("|")[0 if de else 1])[0])
        out.append("</g>")
    for path, kind, l_de, l_en, lx, ly in ARROWS:
        out.append(f'<path class="ddf-{kind}" d="{path}" marker-end="url(#ddf-arrow)"/>')
        if l_de:
            out.append(f'<text class="ddf-label" x="{lx}" y="{ly}">{escape(l_de if de else l_en)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def words(lang="de"):
    """Every DDI name the figure shows (tags, lines, labels)."""
    import re

    return set(re.findall(r"[A-Z][A-Za-z]+", " ".join(
        [b[7] for b in BOXES] + [p[7] for p in PANELS] + [l for b in BOXES for l in b[8]] + [b[10] for b in BOXES]
        + [a[2] for a in ARROWS] + [FRAME[lang][1]])))


def main():
    for lang in ("de", "en"):
        (OUT / lang / "ddi-structure.svg").write_text(svg(lang), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
