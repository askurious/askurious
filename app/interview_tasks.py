"""Tasks in an interview guide ("Aufgabe"; concept konzept-interview-formate.html, stage 1 = options A and D, Ralf
2026-10-20: E1-E8 as recommended).

In the interview the interviewed person does not only talk but also solves a small task, and the talk goes on about
it ("Warum steht das ganz oben?"): an elicitation technique (Barton, 2015; Rugg & McGeorge, 1997). A task is an
element of an existing type, with its own editor, participant rendering and export columns (E1):

  ranking        Rangfolge (app/items/ranking.py)
  card_sort      Kartensortierung, closed, open or both (app/items/card_sort.py)
  likert         Likert-Bewertung (app/items/likert.py)
  single_choice  Bildauswahl: a single choice with images (editor PRESETS "image_choice", app/items/choice.py)

A: in a Themenblock a task belongs to the Leitfrage above it (app/interview.py, task_owner) and stands on that
Leitfrage's page of the interview view, beneath its Unterfragen. The interviewer enters what the person says or lays,
or taps "Zum Zeigen": the task alone, full screen, without the guide, notes or the clock, to hand the tablet over;
"Zurück", held down for a moment, brings the interview view back (interview.js). D: the same types may stand in the
Kurzfragebogen and the Postskript (interview.FORM_TYPES).

Storage: as the answers of the Kurzfragebogen (interview_data.save_forms, part "task"), in Response.answers[item id];
the interview clock when the result was first given in Response.interview["tasks"] = {item id: seconds}. No new kind
of data about people: the result of a task is an answer of the interview, deleted with it (E4: regular, not
experimental; E7: no photos). Stage 2, the interviewee's own device (app/interview_screen.py), adds who gave a
result to that entry: {"t": seconds, "by": "screen", "s": seconds when shown there, "changed": True after a change in
the interview view, E8}.

Results (E5, E6): the case overview takes the export columns of the type; the .qdpx project a case variable (text)
per task and, like the transcript export, a line at the time mark in the transcript,
  [Aufgabe „Wichtigkeit“: 1 Preis, 2 Qualität, 3 Marke] #00:23:10-0#
in italics and without a speaker in Word and PDF (switchable off like the time marks; the transcript head names these
insertions, so the transcript still follows Dresing & Pehl, 2018); the category system a code "Aufgabe" per task
under its Leitfrage, to mark the talk about it."""
from app.items.base import DONT_KNOW, plain

TIME_KEY = "tasks"  # Response.interview[TIME_KEY]
NAME_MAX = 60
BARTON = ("Barton, K. C. (2015). Elicitation techniques: Getting people to talk about ideas they don’t usually talk "
          "about. Theory & Research in Social Education, 43(2), 179–205. https://doi.org/10.1080/00933104.2015.1034392")


def tasks(survey):
    """The tasks of the Themenblöcke in the order of the guide."""
    from app import interview

    return [i for b in interview.guide_blocks(survey) for i in b.items if i.type in interview.TASK_ITEM_TYPES]


def kind_name(item, ui=None):
    """The kind of task in the reader's language: Rangfolge, Kartensortierung, Likert-Bewertung, Bildauswahl."""
    from app import interview
    from app.items.base import reader

    key = "image_choice" if item.type == "single_choice" and (item.config or {}).get("images") else item.type
    names = interview.TASK_TYPES_EN if reader(ui) == "en" else interview.TASK_TYPES
    return names.get(key) or str(item.kind.label)


def name(item, lang=None):
    """The task's short name: its question (shortened), else its code."""
    text = " ".join(plain(item.text("question", lang) or "").split())
    return (text[:NAME_MAX - 1].rstrip() + "…" if len(text) > NAME_MAX else text) or item.code


def result_text(item, answer, lang):
    """The result as a line of text in the survey's language: "1 Preis, 2 Qualität, 3 Marke" (ranking), "Pile: card,
    card; Pile: card" (card sort), "Statement: 4 (agree)" (Likert), the label chosen (image choice); "" for none."""
    if answer in (None, "", [], {}):
        return ""
    labels = item.t(lang).get("options", {}) if item.type in ("ranking", "card_sort", "single_choice") else {}
    by_code = {o["code"]: plain(labels.get(o["id"], "")) or o["code"] for o in (item.config or {}).get("options", [])}
    if item.type == "ranking" and isinstance(answer, list):
        return ", ".join(f"{n} {by_code.get(code, code)}" for n, code in enumerate(answer, start=1))
    if item.type == "card_sort" and isinstance(answer, dict):
        groups = {g["code"]: plain(g["label"]) or g["code"] for g in item.kind.groups(item, lang)}
        own = answer.get("own") or {}
        parts = []
        for key, cards in (answer.get("s") or {}).items():
            pile = groups.get(key) or own.get(key) or key
            parts.append(f"{pile}: " + ", ".join(by_code.get(c, c) for c in cards))
        return "; ".join(parts)
    if item.type == "likert" and isinstance(answer, dict):
        from app.items.likert import read_cfg

        texts, scale = item.t(lang).get("statements") or {}, item.kind.labels(item, lang)
        parts = []
        for s in read_cfg(item).get("statements", []):
            v = answer.get(s["id"])
            if v is None:
                continue
            shown = "?" if v == DONT_KNOW else (f"{v} ({plain(scale[v - 1])})" if 0 < v <= len(scale) and plain(scale[v - 1]) else str(v))
            stmt = plain(texts.get(s["id"], "")) or s.get("code") or s["id"]
            parts.append(f"{stmt}: {shown}")
        return "; ".join(parts)
    if item.kind.pipeable(item):
        return plain(item.kind.pipe_text(item, answer, lang)) or str(answer)
    return str(answer)


def times(row):
    """{item id (str): seconds on the interview clock when its result was first given}"""
    raw = (row.interview or {}).get(TIME_KEY) or {}
    return {k: int(v["t"]) for k, v in raw.items() if isinstance(v, dict) and isinstance(v.get("t"), int)} if isinstance(raw, dict) else {}


def note_time(row, item, answered, seconds):
    """Keep (or forget) when the result of `item` was first given; the caller stores the row."""
    raw = dict((row.interview or {}).get(TIME_KEY) or {})
    key = str(item.id)
    if not answered:
        raw.pop(key, None)
    elif key not in raw and isinstance(seconds, int) and 0 <= seconds < 86400:
        raw[key] = {"t": seconds}
    row.interview = {**(row.interview or {}), TIME_KEY: raw}


def note_by(row, item, by):
    """Keep who gave the result of `item` (app/interview_screen.py): "screen" marks it as given on the interviewee's
    own screen, with the clock when it was shown there ("s"); a later change in the interview view keeps that mark
    and adds "changed" (E8: "vom Interviewenden geändert"). A result entered in the view alone stays unmarked."""
    raw = dict((row.interview or {}).get(TIME_KEY) or {})
    key, entry = str(item.id), raw.get(str(item.id))
    if not isinstance(entry, dict):
        return
    if by == "screen":
        shown = ((row.interview or {}).get("screen") or {}).get("s")
        entry = {k: v for k, v in entry.items() if k != "changed"}
        entry.update(by="screen", **({"s": shown} if isinstance(shown, int) else {}))
    elif entry.get("by") == "screen":
        entry = {**entry, "changed": True}
    else:
        return
    raw[key] = entry
    row.interview = {**(row.interview or {}), TIME_KEY: raw}


def stamp(seconds):
    return f"#{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}-0#"


def line(item, answer, lang, ui="de", seconds=None):
    """[Aufgabe „Wichtigkeit“: 1 Preis, 2 Qualität, 3 Marke] #00:23:10-0# (the time mark only with `seconds`)."""
    head = f"Task “{name(item, lang)}”" if ui == "en" else f"Aufgabe „{name(item, lang)}“"
    text = f"[{head}: {result_text(item, answer, lang)}]"
    return text + (f" {stamp(seconds)}" if seconds is not None else "")


def inserts(survey, row, ui="de", marks=True):
    """[(seconds, line)] of the tasks of an interview with a result and a time, in the order of the clock."""
    when, lang, out = times(row), survey.default_language, []
    for item in tasks(survey):
        answer = (row.answers or {}).get(str(item.id))
        if answer in (None, "", [], {}) or str(item.id) not in when:
            continue
        t = when[str(item.id)]
        out.append((t, line(item, answer, lang, ui, t if marks else None)))
    return sorted(out, key=lambda x: x[0])


def merge(text, extra):
    """The lines of a transcript with the lines `extra` ([(seconds, line)]) put in at their time: after the last line
    whose time mark (the last one in it, Dresing & Pehl put it at the end of a turn) is not later than the task's
    time; before the first line if every mark is later, at the end if the transcript has none. ([lines], {indices of
    the lines put in})"""
    import re

    lines = (text or "").replace("\r\n", "\n").split("\n")
    marks = []
    for raw in lines:
        found = re.findall(r"#(\d{1,2}):(\d{2}):(\d{2})-\d#", raw)
        marks.append(max(int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in found) if found else None)
    at = []
    for t, text_ in extra:
        known = [n for n, m in enumerate(marks) if m is not None]
        after = [n for n in known if marks[n] <= t]
        at.append((after[-1] + 1 if after else (0 if known else len(lines)), text_))
    out, put = [], set()
    by_place = {}
    for place, text_ in at:
        by_place.setdefault(place, []).append(text_)
    for n in range(len(lines) + 1):
        for text_ in by_place.get(n, []):
            put.add(len(out))
            out.append(text_)
        if n < len(lines):
            out.append(lines[n])
    return out, put


def with_lines(survey, row, text, ui="de"):
    """The transcript text with the task lines put in (the .qdpx project's document), as one text."""
    extra = inserts(survey, row, ui)
    return "\n".join(merge(text, extra)[0]) if extra else text


HEAD_NOTE = ("Einfügungen in eckigen Klammern und kursiv: Ergebnis einer Aufgabe im Interview an ihrer Zeitmarke, nicht gesprochen",
             "Insertions in square brackets and italics: the result of a task in the interview at its time mark, not spoken")
