"""Case summaries of a guide's interviews ("Interviews erheben", evaluation 3; Ralf 2026-10-04).

A thematic matrix after Kuckartz & Rädiker (2022): the interviews as rows, the Themenblöcke as columns, in each cell a
short summary in the researchers' words, and one column for the case as a whole. The notes of the interview's
Leitfragen in that Themenblock stand beside the cell as a reminder. Rows can be grouped by a characteristic of the
Kurzfragebogen (a choice question): the comparison of cases along a theme (Meuser & Nagel, 1991) and the first step of
a typology by contrasting cases (Kelle & Kluge, 2010).

  Response.interview["summaries"] = {"<block id>": text, "case": text}"""
import csv
import io

from flask import Blueprint, abort, jsonify, render_template, request, send_file
from flask_login import login_required

from app import interview, interview_data
from app.extensions import db
from app.items.base import in_lang, plain, reader
from app.transcript_coding import signed_in_or_coder
from app.lang import de_en

bp = Blueprint("interview_summaries", __name__, url_prefix="/guide")

MAX_TEXT = 4000
KUCKARTZ = ("Kuckartz, U., & Rädiker, S. (2022). Qualitative Inhaltsanalyse: Methoden, Praxis, Computerunterstützung "
            "(5. Aufl.). Beltz Juventa.")
MEUSER_NAGEL = ("Meuser, M., & Nagel, U. (1991). ExpertInneninterviews: vielfach erprobt, wenig bedacht. Ein Beitrag zur "
                "qualitativen Methodendiskussion. In D. Garz & K. Kraimer (Hrsg.), Qualitativ-empirische Sozialforschung: "
                "Konzepte, Methoden, Analysen (S. 441–471). Westdeutscher Verlag.")
KELLE_KLUGE = ("Kelle, U., & Kluge, S. (2010). Vom Einzelfall zum Typus: Fallvergleich und Fallkontrastierung in der "
               "qualitativen Sozialforschung (2. Aufl.). VS Verlag für Sozialwissenschaften. https://doi.org/10.1007/978-3-531-92366-6")


def rows(survey):
    return [r for r in interview_data.interviews(survey) if r.source == "real"]


def groupers(survey):
    """The choice questions of the Kurzfragebogen that rows can be grouped by."""
    short = interview.shown_form(survey, "short")
    return [i for i in (short.items if short is not None else []) if i.type in ("single_choice", "federal_state")]


def summaries(row):
    return dict((row.interview or {}).get("summaries") or {})


def reminders(survey, row):
    """{block id: [note texts]} of the interview's Leitfragen, to stand beside the cells."""
    out = {}
    for block in interview.guide_blocks(survey):
        notes = [((row.answers or {}).get(str(i.id)) or {}).get("note") for i in block.items if i.type == "guide_question"]
        out[str(block.id)] = [n for n in notes if n]
    return out


def grouped(survey, interviews, grouper):
    """[(group label, [rows])] by the answer to `grouper` (one group "alle" without it)."""
    if grouper is None:
        return [("", interviews)]
    from app.interview_results import answer_text

    groups, missing = {}, de_en("keine Angabe", "no answer")
    for r in interviews:
        label = answer_text(grouper, (r.answers or {}).get(str(grouper.id)), survey.default_language) or missing
        groups.setdefault(label, []).append(r)
    return sorted(groups.items(), key=lambda g: (g[0] == missing, g[0]))


def matrix_csv(survey):
    from app.export import safe_text

    blocks = interview.guide_blocks(survey)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([safe_text(c) for c in ["kennung"] + [plain(b.title()) for b in blocks] + ["fall"]])
    for r in rows(survey):
        s = summaries(r)
        # formula protection like the other exports: a summary starting with "- " or "=" is no formula in Excel
        w.writerow([safe_text(c) for c in [(r.interview or {}).get("label", "")] + [s.get(str(b.id), "") for b in blocks]
                    + [s.get("case", "")]])
    return "﻿" + buf.getvalue()


def matrix_docx(survey, ui=None):
    """The matrix as Word: the frame in the reader's language `ui` (reader()), the summaries as written."""
    from app.interview_docx import _document, _head, _note

    ui = reader(ui)
    doc = _document()
    _head(doc, survey, in_lang(ui, "Fallzusammenfassungen", "Case summaries"), ui)
    blocks = interview.guide_blocks(survey)
    for r in rows(survey):
        s = summaries(r)
        doc.add_heading(f"Interview {(r.interview or {}).get('label', '')}", level=1)
        if s.get("case"):
            p = doc.add_paragraph()
            p.add_run(in_lang(ui, "Fall insgesamt: ", "Case as a whole: ")).bold = True
            p.add_run(s["case"])
        for b in blocks:
            if s.get(str(b.id)):
                doc.add_heading(plain(b.title()), level=2)
                doc.add_paragraph(s[str(b.id)])
    _note(doc, in_lang(ui, "Thematische Matrix nach ", "Thematic matrix after ") + KUCKARTZ)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _survey(code):
    return interview_data._survey(code, need="editor")


@bp.route("/<code>/interviews/summaries")
@login_required
def page(code):
    from app import transcript_coding_cases as cases

    survey = _survey(code)
    options = groupers(survey)
    interviews = rows(survey)
    # with an analysis of "Interviews kodieren": its main categories as the columns (app/transcript_coding_cases.py);
    # without, the Themenblöcke as before
    analyses = cases.projects_for(survey)
    project = cases.chosen(survey, request.args.get("auswertung", type=int))
    coding = None
    grouper = next((i for i in options if i.id == request.args.get("nach", type=int)), None)
    groups = None
    if project is not None:
        from app import transcript_coding_ai
        from app.transcript_coding import tree

        mains, _candidates = tree(project)
        pairs, counts = cases.types(project, interviews)
        sub = next((s for _m, s in pairs if f"sub{s.id}" == request.args.get("nach")), None)
        if sub is not None:
            groups, grouper = cases.grouped_by_sub(interviews, counts, sub), None
        cells = cases.cells(project, interviews)
        coding = {"project": project, "mains": [m for m, _s in mains], "cells": cells, "draft": cases.draft,
                  "pairs": pairs, "counts": counts, "sub": sub,
                  "ai": transcript_coding_ai.state(project, cases.team_actor()) == "on"}
    return render_template("interview/summaries.html", survey=survey, blocks=interview.guide_blocks(survey),
                           groups=groups or grouped(survey, interviews, grouper), grouper=grouper, options=options,
                           summaries=summaries, reminders=reminders, n=len(interviews), analyses=analyses, coding=coding,
                           author_line=author_line,
                           sources=[(de_en("Thematische Matrix und Fallzusammenfassungen", "Thematic matrix and case summaries"), [KUCKARTZ]),
                                    (de_en("Fallvergleich und Typenbildung", "Case comparison and typology"), [MEUSER_NAGEL, KELLE_KLUGE])])


def _cell(code, rid):
    """(survey, interview, key, actor) of a request on one cell, else 400/404: the team of the guide on every cell,
    whoever works in an analysis on its cells of the interviews they see (app/transcript_coding_cases.py, writer)."""
    from app import transcript_coding_cases
    from app.transcript_coding import guide

    survey = guide(code)
    key = request.form.get("key", "")
    if key != "case" and key not in {str(b.id) for b in interview.guide_blocks(survey)}:
        if not transcript_coding_cases.valid_key(survey, key, any_reader=True):  # a cell per main category of an analysis
            abort(400)
    row = interview_data._interview(survey, rid)
    who = transcript_coding_cases.writer(survey, row, key)
    if who is None:
        abort(404)
    return survey, row, key, who


@bp.route("/<code>/interviews/<int:rid>/summary", methods=["POST"])
@signed_in_or_coder
def save(code, rid):
    """One cell, as the page sends it while typing: with who wrote it and a step of its history."""
    from app import interview_audio, transcript_coding_cases

    survey, row, key, who = _cell(code, rid)
    text = request.form.get("text", "").replace("\r\n", "\n")[:MAX_TEXT].strip()
    changed = []

    def store():
        db.session.refresh(row)
        changed[:] = [transcript_coding_cases.write(survey, row, key, text, who)]

    interview_audio.retried(store)  # a piece of the recording or the transcription stored the interview meanwhile
    if changed and changed[0]:
        transcript_coding_cases.note_history(survey, row, key, who)
        db.session.commit()
    meta = transcript_coding_cases.meta_of(row, key) or {}
    return jsonify(ok=True, by=meta.get("by_name", ""), at=_when(meta.get("at")),
                   undo=transcript_coding_cases.undoable(row, key, who) is not None)


@bp.route("/<code>/interviews/<int:rid>/summary/undo", methods=["POST"])
@signed_in_or_coder
def undo_cell(code, rid):
    """"Rückgängig" of a cell: the own last step in it, while nobody wrote it since."""
    from app import interview_audio, transcript_coding_cases

    survey, row, key, who = _cell(code, rid)
    got = []

    def work():
        db.session.refresh(row)
        got[:] = [transcript_coding_cases.undo(survey, row, key, who)]

    interview_audio.retried(work)
    if not got or got[0] is None:
        return jsonify(ok=False, error=de_en("Nichts zum Zurücknehmen.", "Nothing to take back.")), 400
    transcript_coding_cases.note_history(survey, row, key, who)
    db.session.commit()
    meta = transcript_coding_cases.meta_of(row, key) or {}
    return jsonify(ok=True, text=got[0], by=meta.get("by_name", ""), at=_when(meta.get("at")),
                   undo=transcript_coding_cases.undoable(row, key, who) is not None)


def _when(iso):
    """The time of a cell's last change as the pages show it."""
    from datetime import datetime

    from app.lang import english
    from app.timeutil import fmt

    try:
        return fmt(datetime.fromisoformat(iso), lang="en" if english() else "de") if iso else ""
    except ValueError:
        return ""


def author_line(row, key):
    """"von Bea, 30.10.2026 18:40" under a cell; a cell from before 2026-10-30: "von Forschenden"."""
    from app import transcript_coding_cases

    if not summaries(row).get(key):
        return ""
    meta = transcript_coding_cases.meta_of(row, key)
    if meta is None:
        return de_en("von Forschenden", "by researchers")
    return de_en("von {who}, {when}", "by {who}, {when}", who=meta.get("by_name") or de_en("Forschenden", "researchers"),
                 when=_when(meta.get("at")))


@bp.route("/<code>/interviews/summaries.csv")
@login_required
def file_csv(code):
    survey = _survey(code)
    return send_file(io.BytesIO(matrix_csv(survey).encode("utf-8")), as_attachment=True, mimetype="text/csv",
                     download_name=f"fallzusammenfassungen-{survey.code}.csv")


@bp.route("/<code>/interviews/summaries.docx")
@login_required
def file_docx(code):
    survey = _survey(code)
    return send_file(io.BytesIO(matrix_docx(survey)), as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     download_name=f"fallzusammenfassungen-{survey.code}.docx")


from app import transcript_coding_cases as _cases  # noqa: E402  the routes of the case comparison from codings

_cases.register(bp, _survey)
