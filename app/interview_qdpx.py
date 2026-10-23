"""The interviews of a guide as a REFI-QDA project (.qdpx) for MAXQDA, ATLAS.ti, NVivo and QDA Miner
("Interviews erheben", evaluation 1; Ralf 2026-10-04).

One file holds what a QDA program needs to start coding: the category system from the guide (the same tree and
GUIDs as the .qdc, app/qdc.py), each interview's transcript as a text document (with a line per task at its time
mark, app/interview_tasks.py), the interviews as cases with the answers of the Kurzfragebogen, the results of the
tasks (as text, "1 Preis; 2 Qualität", E6) and the meta data (date, mode, duration) as case variables, and the notes and the
Postskript of each interview as a memo of its document. Interviews without a transcript are cases without a
document. Probeinterviews are left out.

Format: REFI-QDA Project Exchange, namespace urn:QDA-XML:project:1.0: a ZIP with project.qde (XML) and the texts
under Sources/<guid>.txt, referred to as internal://<guid>.txt (https://www.qdasoftware.org).

The human-readable descriptions (the project's description, the names and texts of the memos) are in the language of
the person who downloads (app.items.base.reader); variable names and values stay as they are in every language."""
import io
import uuid
import zipfile
from xml.etree import ElementTree as ET

from app import brand
from app import interview, interview_data, interview_results, qdc
from app.items.base import in_lang, plain, reader
from app.models import utcnow

NS = "urn:QDA-XML:project:1.0"
REFI_PROJECT = ("REFI-QDA (2019). REFI-QDA Project Exchange Standard (Version 1.0). Rotterdam Exchange Format "
                "Initiative. https://www.qdasoftware.org")


def _guid(survey, *parts):
    # "queryous" stays after the rename to Askurious: a new export keeps the ids of an earlier one
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "queryous-project:" + survey.uid + ":" + ":".join(str(p) for p in parts))).upper()


def _el(parent, tag, attrs=None, text=None):
    e = ET.SubElement(parent, f"{{{NS}}}{tag}", attrs or {})
    if text is not None:
        e.text = text
    return e


def variables(survey):
    """[(key, name, function row -> text)] of the case variables: meta data, the tasks, then the Kurzfragebogen."""
    from app import interview_tasks

    lang = survey.default_language
    out = [("datum", "Datum", lambda r: (r.interview or {}).get("date", "")),
           ("form", "Interviewform", lambda r: interview.MODES.get((r.interview or {}).get("mode"), "")),
           ("dauer", "Dauer in Minuten", lambda r: str(round(((r.interview or {}).get("elapsed") or 0) / 60))),
           ("von", "interviewt von", lambda r: (r.interview or {}).get("by_name", ""))]
    for item in interview_tasks.tasks(survey):  # one text variable per task (concept konzept-interview-formate, E6)
        out.append((f"task{item.id}", "Aufgabe: " + _short(interview_tasks.name(item, lang), item.code),
                    lambda r, item=item: interview_results.answer_text(item, (r.answers or {}).get(str(item.id)), lang)))
    short = interview.shown_form(survey, "short")
    for item in (short.items if short is not None else []):
        if item.kind.collects_answer:
            out.append((f"item{item.id}", _short(plain(item.text("question", lang)), item.code),
                        lambda r, item=item: interview_results.answer_text(item, (r.answers or {}).get(str(item.id)), lang)))
    return out


def _short(text, fallback):
    text = " ".join(text.split())
    return (text[:60].rstrip() + "…" if len(text) > 61 else text) or fallback


def build(survey, ui=None):
    """The project as .qdpx bytes; `ui`: the reader's language for the descriptions (reader())."""
    ui = reader(ui)
    ET.register_namespace("", NS)
    now = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    user = _guid(survey, "user")
    root = ET.Element(f"{{{NS}}}Project", {"name": survey.title[:200], "origin": brand.name(), "creatingUserGUID": user,
                                             "creationDateTime": now})
    users = _el(root, "Users")
    _el(users, "User", {"guid": user, "name": brand.name()})
    codebook = _el(root, "CodeBook")
    qdc.codes(survey, codebook, ns=NS)
    rows = [r for r in interview_data.interviews(survey) if r.source == "real"]
    vars_ = variables(survey)
    if vars_:
        holder = _el(root, "Variables")
        for key, name, _ in vars_:
            _el(holder, "Variable", {"guid": _guid(survey, "var", key), "name": name, "typeOfVariable": "Text"})
    texts = {}
    if rows:
        cases = _el(root, "Cases")
        for r in rows:
            label = (r.interview or {}).get("label", str(r.id))
            case = _el(cases, "Case", {"guid": _guid(survey, "case", r.id), "name": label})
            for key, _name, value in vars_:
                vv = _el(case, "VariableValue")
                _el(vv, "VariableRef", {"targetGUID": _guid(survey, "var", key)})
                _el(vv, "TextValue", text=value(r) or "")
            if ((r.interview or {}).get("transcript") or {}).get("text"):
                from app import interview_tasks

                _el(case, "SourceRef", {"targetGUID": _guid(survey, "source", r.id)})
                texts[r.id] = (label, interview_tasks.with_lines(survey, r, r.interview["transcript"]["text"],
                                                                 "en" if survey.default_language == "en" else "de"))
    files = {}
    if texts:
        sources = _el(root, "Sources")
        notes = []
        for rid, (label, text) in texts.items():
            guid = _guid(survey, "source", rid)
            src = _el(sources, "TextSource", {"guid": guid, "name": label, "plainTextPath": f"internal://{guid}.txt",
                                              "creatingUser": user, "creationDateTime": now})
            note = _guid(survey, "note", rid)
            _el(src, "NoteRef", {"targetGUID": note})
            notes.append((note, label, rid))
            files[f"Sources/{guid}.txt"] = text.replace("\r\n", "\n").encode("utf-8")
        holder = _el(root, "Notes")
        by_id = {r.id: r for r in rows}
        for note, label, rid in notes:
            n = _el(holder, "Note", {"guid": note, "name": in_lang(ui, "Notizen {label}", "Notes {label}", label=label),
                                     "creatingUser": user, "creationDateTime": now})
            _el(n, "PlainTextContent", text=interview_results.notes_text(survey, by_id[rid], ui))
    _el(root, "Description", text=in_lang(ui, "Interviewleitfaden „{title}“, exportiert aus Askurious am {day}.",
                                          "Interview guide “{title}”, exported from Askurious on {day}.",
                                          title=survey.title, day=now[:10]))
    xml = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.qde", xml.encode("utf-8"))
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()
