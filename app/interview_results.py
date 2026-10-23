"""Evaluation of the stored interviews of an interview guide ("Interviews erheben", part 2; concept
interviewleitfaden-konzept.md, version 2 section 6): case overview, coverage, notes per Leitfrage, duration per
Themenblock, and the exports (case overview as CSV, notes as Word and as text files with time marks).

The coverage describes the conversations, not their contents: which Leitfragen were asked or came up by themselves
in which interview, and how often each aspect came up. It helps to check the guide after the first interviews
(Helfferich, 2011; Kruse, 2015): an aspect that never comes up by itself may belong to the concrete questions.
Time marks are written as #hh:mm:ss-0#, the form of the transcription rules of Dresing & Pehl (2018), so that notes
and transcript can be put side by side in MAXQDA or f4analyse.

The case overview (CSV) keeps its columns and values as they are in every language (as the other data exports); the
notes as Word and text files describe in the language of the person who downloads (app.items.base.reader), the
guide's texts and answers stay in the survey's language."""
import csv
import io
import statistics
import zipfile

from flask import Blueprint, render_template, request, send_file

from app import interview, interview_data
from app.items.base import in_lang, plain, reader
from app.lang import de_en
from app.units import round_half_up
from flask_login import login_required

bp = Blueprint("interview_results", __name__, url_prefix="/guide")

DRESING_PEHL = ("Dresing, T., & Pehl, T. (2018). Praxisbuch Interview, Transkription & Analyse: Anleitungen und "
                "Regelsysteme für qualitativ Forschende (8. Aufl.). Eigenverlag. https://www.audiotranskription.de/praxisbuch")
STATE_MARK = {"asked": "●", "spontaneous": "◐", "skipped": "○"}
STATE_WORD = {"asked": "gestellt", "spontaneous": "von selbst angesprochen", "skipped": "nicht gestellt"}  # also the CSV's values
STATE_WORD_EN = {"asked": "asked", "spontaneous": "raised spontaneously", "skipped": "not asked"}


def state_words(ui=None):
    """STATE_WORD in the reader's language (the page's if not given)."""
    return STATE_WORD_EN if reader(ui) == "en" else STATE_WORD


def stamp(seconds):
    """#hh:mm:ss-0# (Dresing & Pehl)."""
    s = int(seconds or 0)
    return f"#{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}-0#"


def rows(survey, trials=False):
    return [r for r in interview_data.interviews(survey) if trials or r.source == "real"]


def questions(survey):
    """[(block, [Leitfragen])] in the order of the guide."""
    return [(b, [i for i in b.items if i.type == "guide_question"]) for b in interview.guide_blocks(survey)]


def answer_text(item, answer, lang):
    if answer in (None, "", [], {}):
        return ""
    if item.type in interview.TASK_ITEM_TYPES:  # a ranking reads "1 Preis, 2 Qualität" (app/interview_tasks.py)
        from app import interview_tasks

        return interview_tasks.result_text(item, answer, lang)
    kind = item.kind
    if kind.pipeable(item):
        return plain(kind.pipe_text(item, answer, lang)) or str(answer)
    return str(answer)


def coverage(survey, interviews):
    """Per Leitfrage: the state in each interview, and per aspect the share of interviews in which it came up. An
    Unterfrage is marked ("sub") to stand indented under its Leitfrage (concept unterfragen, E6)."""
    out = []
    for block, items in questions(survey):
        subs = {i.id for i, main in interview.outline(block) if main is not None}
        rows_ = []
        for item in items:
            states = [((r.answers or {}).get(str(item.id)) or {}).get("state") for r in interviews]
            aspects = []
            for rid, text in item.kind.rows(item, "aspects"):
                n = sum(1 for r in interviews if rid in (((r.answers or {}).get(str(item.id)) or {}).get("aspects") or []))
                aspects.append({"text": text, "n": n, "share": n / len(interviews) if interviews else 0})
            rows_.append({"item": item, "states": states, "aspects": aspects, "sub": item.id in subs,
                          "met": sum(1 for s in states if s in ("asked", "spontaneous"))})
        out.append({"block": block, "rows": rows_})
    return out


def durations(survey, interviews):
    """Per Themenblock: planned minutes, median and range of the minutes spent."""
    out = []
    for block in interview.guide_blocks(survey):
        spent = [((r.interview or {}).get("blocks") or {}).get(str(block.id)) for r in interviews]
        spent = [s / 60 for s in spent if s]
        out.append({"block": block, "planned": interview.planned_minutes(block), "n": len(spent),
                    "median": statistics.median(spent) if spent else None,
                    "low": min(spent) if spent else None, "high": max(spent) if spent else None})
    return out


def notes(survey, interviews):
    """Per Leitfrage the notes and time marks of all interviews, with the identifier."""
    out = []
    for block, items in questions(survey):
        subs = {i.id for i, main in interview.outline(block) if main is not None}
        for item in items:
            entries = []
            for r in interviews:
                note = ((r.answers or {}).get(str(item.id)) or {}).get("note")
                marks = [m for m in (r.interview or {}).get("marks") or [] if m.get("item") == str(item.id)]
                if note or marks:
                    entries.append({"label": (r.interview or {}).get("label"), "note": note or "", "marks": marks})
            out.append({"block": block, "item": item, "entries": entries, "sub": item.id in subs})
    return out


def _fitting(item):
    """[(n, concrete question)] of the concrete questions with the switch "trifft zu" (those with Nachfragen and those
    a condition names, concept bedingung-konkrete-frage E), n counting all concrete questions (without their
    Nachfragen) from 1."""
    return [(p["n"], p) for p in item.kind.probe_tree(item, ui="de") if p["fit"]]


def answer_items(survey):
    """The elements whose answers the case overview shows as the type's export columns: the tasks of the
    Themenblöcke (app/interview_tasks.py), then the Kurzfragebogen and the Postskript."""
    from app import interview_tasks

    return [i for i in interview_tasks.tasks(survey) + interview_data.form_items(survey) if i.kind.collects_answer]


def overview_columns(survey):
    """The columns of the case overview: meta data, per Leitfrage its state and its aspects (0/1), the tasks and the
    forms with the export columns of their types (a ranking: <code>_<option> = rank)."""
    cols = [("kennung", "Kennung"), ("probe", "Probeinterview (1 = ja)"), ("datum", "Datum"), ("form", "Interviewform"),
            ("dauer_min", "Dauer in Minuten"), ("interviewt_von", "interviewt von"), ("einwilligung", "Einwilligung"),
            ("tonaufnahme", "Tonaufnahme erlaubt (1 = ja)"), ("beendet", "beendet (1 = ja)")]
    for _, items in questions(survey):
        for item in items:
            cols.append((f"{item.code}_stand", f"{plain(item.text('question'))[:80]}: Stand"))
            for n, (rid, text) in enumerate(item.kind.rows(item, "aspects"), start=1):
                cols.append((f"{item.code}_a{n}", f"Aspekt „{text}“ (1 = vorgekommen)"))
            # "trifft zu" of a concrete question with Nachfragen or named by a condition (bedingungen-leitfaden B, E)
            for n, p in _fitting(item):
                cols.append((f"{item.code}_k{n}_zu", f"Konkrete Frage „{plain(p['text'])[:60]}“ trifft zu (1 = ja, 0 = nein)"))
    for item in answer_items(survey):
        cols += item.kind.export_columns(item)
    if _screen_used(survey):  # the interviewee's own screen (app/interview_screen.py): who gave each task's result (E8)
        from app import interview_tasks

        for item in interview_tasks.tasks(survey):
            cols += [(f"{item.code}_quelle", f"Aufgabe {item.code}: eingegeben von (Interviewende, Teilnehmerbildschirm, "
                                             "Teilnehmerbildschirm, vom Interviewenden geändert)"),
                     (f"{item.code}_dauer_s", f"Aufgabe {item.code}: Sekunden vom Zeigen auf dem Teilnehmerbildschirm bis zum Ergebnis")]
    return cols


def _screen_used(survey):
    from app import interview_screen

    return interview_screen.used(survey)


def source_mark(row, item, ui=None):
    """"(Teilnehmerbildschirm)" or "(Teilnehmerbildschirm, vom Interviewenden geändert)" after a task's result given on
    the interviewee's own screen (app/interview_screen.py, E8); "" for one entered in the interview view."""
    from app import interview_screen

    which = interview_screen.source(row, item)
    if which not in ("screen", "changed"):
        return ""
    return "(" + in_lang(reader(ui), *interview_screen.SOURCE_WORDS[which]) + ")"


def overview_row(survey, r):
    from app.interview import MODES

    meta = r.interview or {}
    row = {"kennung": meta.get("label", ""), "probe": int(r.source == "test"), "datum": meta.get("date", ""),
           "form": MODES.get(meta.get("mode"), ""), "dauer_min": round_half_up((meta.get("elapsed") or 0) / 60, 1),
           "interviewt_von": meta.get("by_name", ""), "einwilligung": (meta.get("consent") or {}).get("form", ""),
           "tonaufnahme": int(bool((meta.get("consent") or {}).get("audio"))), "beendet": int(r.status == "completed")}
    for _, items in questions(survey):
        for item in items:
            a = (r.answers or {}).get(str(item.id)) or {}
            row[f"{item.code}_stand"] = STATE_WORD.get(a.get("state"), "")
            for n, (rid, _) in enumerate(item.kind.rows(item, "aspects"), start=1):
                row[f"{item.code}_a{n}"] = int(rid in (a.get("aspects") or []))
            fits = a.get("fits") or {}
            for n, p in _fitting(item):
                row[f"{item.code}_k{n}_zu"] = int(fits[p["id"]]) if p["id"] in fits else ""
    for item in answer_items(survey):
        answer = (r.answers or {}).get(str(item.id))
        if answer is not None:
            row.update(item.kind.export_values(item, answer))
    if _screen_used(survey):
        from app import interview_screen, interview_tasks

        for item in interview_tasks.tasks(survey):
            which = interview_screen.source(r, item)
            row[f"{item.code}_quelle"] = interview_screen.SOURCE_WORDS[which][0] if which else ""
            taken = interview_screen.seconds_taken(r, item)
            row[f"{item.code}_dauer_s"] = "" if taken is None else taken
    return row


def overview_csv(survey, trials=False):
    from app.export import safe_text

    cols = overview_columns(survey)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([c for c, _ in cols])
    for r in rows(survey, trials):
        values = overview_row(survey, r)
        # formula protection like the other exports: a typed answer starting with "=" is no formula in Excel
        writer.writerow(["" if values.get(c) is None else safe_text(values.get(c)) for c, _ in cols])
    return "﻿" + buf.getvalue()


def notes_text(survey, r, ui=None):
    """One interview as plain text: meta data, per Leitfrage stand, aspects, note and time marks, the forms. The frame
    in the reader's language `ui` (reader())."""
    ui = reader(ui)
    lang, meta = survey.default_language, r.interview or {}
    modes, words = (interview.MODES_EN if ui == "en" else interview.MODES), state_words(ui)
    lines = [f"Interview {meta.get('label', '')} · {meta.get('date', '')} · {modes.get(meta.get('mode'), '')}"
             + in_lang(ui, " · Dauer ca. {n} Min. · ", " · duration approx. {n} min · ",
                       n=round_half_up((meta.get('elapsed') or 0) / 60)) + f"{meta.get('by_name', '')}", ""]
    for block, items in questions(survey):
        lines.append(f"== {block.title()} ==")
        for item in items:
            a = (r.answers or {}).get(str(item.id)) or {}
            lines.append(f"[{item.code}] {plain(item.text('question'))}")
            if a.get("state"):
                lines.append(in_lang(ui, "Stand: {s}", "Status: {s}", s=words[a["state"]]))
            names = dict(item.kind.rows(item, "aspects"))
            if a.get("aspects"):
                lines.append(in_lang(ui, "Aspekte: ", "Aspects: ") + "; ".join(names.get(x, x) for x in a["aspects"]))
            if a.get("note"):
                lines.append(in_lang(ui, "Notiz: ", "Note: ") + a["note"])
            for m in [m for m in meta.get("marks") or [] if m.get("item") == str(item.id)]:
                lines.append(f"{stamp(m['t'])} {m.get('text', '')}".rstrip())
            lines.append("")
        for task in [i for i in block.items if i.type in interview.TASK_ITEM_TYPES]:  # app/interview_tasks.py
            text = answer_text(task, (r.answers or {}).get(str(task.id)), lang)
            if text:
                mark = source_mark(r, task, ui)  # given on the interviewee's own screen (app/interview_screen.py)
                lines += [in_lang(ui, "[{code}] Aufgabe „{q}“: {text}", "[{code}] Task “{q}”: {text}", code=task.code,
                                  q=plain(task.text("question")), text=text) + (" " + mark if mark else ""), ""]
    loose = [m for m in meta.get("marks") or [] if not m.get("item")]
    if loose:
        lines += [in_lang(ui, "== Weitere Zeitmarken ==", "== Further time marks ==")] + [f"{stamp(m['t'])} {m.get('text', '')}".rstrip() for m in loose] + [""]
    for which in ("short", "post"):
        block = interview.shown_form(survey, which)
        if block is None:
            continue
        lines.append(f"== {(interview.PARTS_EN if ui == 'en' else interview.PARTS)[which]} ==")
        for item in block.items:
            if item.kind.collects_answer:
                text = answer_text(item, (r.answers or {}).get(str(item.id)), lang)
                lines.append(f"{plain(item.text('question'))}: {text}")
        lines.append("")
    return "\n".join(lines)


def notes_zip(survey, trials=False, ui=None):
    ui = reader(ui)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows(survey, trials):
            label = "".join(ch for ch in (r.interview or {}).get("label", str(r.id)) if ch.isalnum()) or str(r.id)
            z.writestr(f"{label}.txt", notes_text(survey, r, ui).replace("\n", "\r\n").encode("utf-8"))
            transcript = ((r.interview or {}).get("transcript") or {}).get("text")
            if transcript:  # the checked or draft transcript beside the notes (app/interview_transcribe.py)
                z.writestr(f"{label}-transkript.txt", transcript.replace("\n", "\r\n").encode("utf-8"))
    return buf.getvalue()


def notes_docx(survey, trials=False, ui=None):
    from app.interview_docx import _document, _head, _note

    ui = reader(ui)
    doc = _document()
    _head(doc, survey, in_lang(ui, "Notizen und Postskripte", "Notes and postscripts"), ui)
    for r in rows(survey, trials):
        doc.add_heading(f"Interview {(r.interview or {}).get('label', '')}", level=1)
        for line in notes_text(survey, r, ui).split("\n")[1:]:
            if line.startswith("== "):
                doc.add_heading(line.strip("= "), level=2)
            elif line:
                doc.add_paragraph(line)
    _note(doc, in_lang(ui, "Zeitmarken im Format #hh:mm:ss-0# nach ", "Time marks in the format #hh:mm:ss-0# after ") + DRESING_PEHL)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# --- the pages ---------------------------------------------------------------------------------------------------


def _survey(code):
    return interview_data._survey(code, need="editor")  # notes and data: not for "Ansehen" (concept 5)


@bp.route("/<code>/interviews/analysis")
@login_required
def page(code):
    from app.items.guide_question import HELFFERICH
    from app.interview_ai import KRUSE

    from app import interview_tasks

    survey = _survey(code)
    trials = request.args.get("probe") == "1"
    interviews = rows(survey, trials)
    form_items = answer_items(survey)  # the tasks, then the Kurzfragebogen and the Postskript (case overview)
    return render_template("interview/results.html", survey=survey, interviews=interviews, trials=trials,
                           coverage=coverage(survey, interviews), durations=durations(survey, interviews),
                           notes=notes(survey, interviews), form_items=form_items, marks=STATE_MARK, words=state_words(),
                           has_tasks=bool(interview_tasks.tasks(survey)),
                           answer_text=answer_text, source_mark=source_mark, stamp=stamp, modes=interview.modes(), day=interview.day_text,
                           sources=[(de_en("Abdeckung und Anpassen des Leitfadens", "Coverage and adapting the guide"), [HELFFERICH, KRUSE]),
                                    (de_en("Zeitmarken", "Time marks"), [DRESING_PEHL])])


@bp.route("/<code>/interviews/cases.csv")
@login_required
def overview_file(code):
    survey = _survey(code)
    return send_file(io.BytesIO(overview_csv(survey, request.args.get("probe") == "1").encode("utf-8")), as_attachment=True,
                     mimetype="text/csv", download_name=f"fallübersicht-{survey.code}.csv")


@bp.route("/<code>/interviews/project.qdpx")
@login_required
def project_file(code):
    """Transcripts, category system, case variables and notes as one REFI-QDA project (app/interview_qdpx.py)."""
    from app import interview_qdpx

    survey = _survey(code)
    return send_file(io.BytesIO(interview_qdpx.build(survey)), as_attachment=True, mimetype="application/zip",
                     download_name=f"interviews-{survey.code}.qdpx")


@bp.route("/<code>/interviews/notes.docx")
@login_required
def notes_word(code):
    survey = _survey(code)
    return send_file(io.BytesIO(notes_docx(survey, request.args.get("probe") == "1")), as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     download_name=f"notizen-{survey.code}.docx")


@bp.route("/<code>/interviews/notes.zip")
@login_required
def notes_files(code):
    survey = _survey(code)
    return send_file(io.BytesIO(notes_zip(survey, request.args.get("probe") == "1")), as_attachment=True,
                     mimetype="application/zip", download_name=f"notizen-{survey.code}.zip")


@bp.route("/<code>/interviews/transcripts.zip")
@login_required
def transcripts_file(code):
    """Every transcript as Word, PDF or both, one file each, in a ZIP (app/transcript_export.py)."""
    from app import transcript_export

    survey = _survey(code)
    fmt = request.args.get("format", "docx")
    formats = ("docx", "pdf") if fmt == "both" else (fmt,) if fmt in transcript_export.FORMATS else ("docx",)
    # the results of the tasks: in unless switched off (the form sends tasks=0 and, ticked, tasks=1)
    tasks = "1" in request.args.getlist("tasks") or "tasks" not in request.args
    data = transcript_export.zip_all(survey, rows(survey, request.args.get("probe") == "1"), formats,
                                     marks=request.args.get("marks") == "1", tasks=tasks)
    return send_file(io.BytesIO(data), as_attachment=True, mimetype="application/zip",
                     download_name=f"transkripte-{survey.code}.zip")
