"""Tasks in an interview guide (app/interview_tasks.py; concept konzept-interview-formate, stage 1 = A + D, Ralf
2026-10-20: E1-E8 as recommended): a ranking, card sort, Likert or image choice under a Leitfrage of a Themenblock,
entered in the interview view or shown full screen ("Zum Zeigen"), and the same types in the Kurzfragebogen; the
results in the case overview, the transcript export, the .qdpx project and the category system."""
import io
import re
import zipfile

from app.extensions import db
from app.models import Block, Item, Response, Survey
from tests.interview.test_interview import block_of, first, make
from tests.interview.test_interview_data import start

LABELS = ("Preis", "Qualität", "Marke")


def add(client, sid, bid, kind, **extra):
    return client.post(f"/surveys/{sid}/editor/blocks/{bid}/items", data={"type": kind, **extra})


def ranking_task(app, client):
    """A guide whose Leitfrage in "Themenblock 1" has a ranking task "Wichtigkeit" (Preis, Qualität, Marke).
    (code, survey id, Leitfrage id, task id)"""
    code, sid = make(app, client)
    q = first(app, sid)  # the Leitfrage of "Einstieg"
    with app.app_context():
        bid = db.session.get(Item, q).block_id
    r = add(client, sid, bid, "ranking")
    assert r.status_code == 302 and f"/guide/{code}" in r.headers["Location"]
    with app.app_context():
        task = next(i for i in db.session.get(Block, bid).items if i.type == "ranking")
        options = [{"id": f"o{n}", "code": str(n)} for n in (1, 2, 3)]
        task.config = {**task.config, "options": options, "randomize": False}
        lang = task.block.survey.default_language
        task.texts = {lang: {**(task.texts or {}).get(lang, {}), "question": "Wichtigkeit",
                             "options": {o["id"]: label for o, label in zip(options, LABELS)}}}
        db.session.commit()
        return code, sid, q, task.id


def test_task_added_to_a_topic_block_belongs_to_its_leitfrage(app, client):
    from app import interview

    code, sid, q, tid = ranking_task(app, client)
    with app.app_context():
        task = db.session.get(Item, tid)
        block = task.block
        assert interview.part(block) == "guide" and interview.is_task(task)
        assert interview.task_owner(block) == {tid: db.session.get(Item, q)}
        step = next(s for s in interview.steps(db.session.get(Survey, sid)) if s["item"] is not None and s["item"].id == q)
        assert [t.id for t in step["tasks"]] == [tid]                               # on the page of its Leitfrage
        assert all(s["item"] is None or s["item"].id != tid for s in interview.steps(db.session.get(Survey, sid)))
        assert [i.id for i in interview.units(block)[-1]][-1] == tid                # moves with its Leitfrage
    page = client.get(f"/guide/{code}").data.decode()
    assert "guide-task-tag" in page and "+ Aufgabe" in page and 'value="image_choice"' in page and 'value="card_sort"' in page
    # selected Leitfrage: the new task goes right after its unit (hidden position)
    assert re.search(r'name="position" value="\d+"', client.get(f"/guide/{code}?item={q}").data.decode())
    # image choice: a single choice with images; Likert and card sort the types themselves
    for kind in ("image_choice", "likert", "card_sort"):
        assert add(client, sid, block_of(app, sid, "guide"), kind).status_code == 302
    with app.app_context():
        types = [i.type for b in interview.guide_blocks(db.session.get(Survey, sid)) for i in b.items]
        assert {"likert", "card_sort", "single_choice"} <= set(types)
        image = next(i for b in interview.guide_blocks(db.session.get(Survey, sid)) for i in b.items if i.type == "single_choice")
        assert image.config.get("images") is True
    # print and Word name the task with its kind
    assert "Aufgabe · Rangfolge" in client.get(f"/guide/{code}/print").data.decode()
    from tests.interview.test_interview import docx_text

    assert "Aufgabe · Rangfolge" in docx_text(client.get(f"/guide/{code}/export/leitfaden.docx").data)
    assert "Aufgabe: Wichtigkeit" in docx_text(client.get(f"/guide/{code}/export/interviewkarte.docx").data)
    # a plain question of a questionnaire stays out of a Themenblock
    assert add(client, sid, block_of(app, sid, "guide"), "text_input").status_code == 400
    # moving the Leitfrage up takes its task along
    client.post(f"/guide/{code}/control", data={"action": "item_down", "item": tid})   # no error at the end of its unit


def test_entering_a_ranking_in_the_interview_view(app, client):
    code, sid, q, tid = ranking_task(app, client)
    rid = start(app, client, code)
    page = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    assert f'data-iv-task="{tid}"' in page and 'name="part" value="task"' in page and f'name="task" value="{tid}"' in page
    assert "Zum Zeigen" in page and "data-iv-task-t" in page
    r = client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "task", "task": str(tid), "t": "1390",
                                                                  f"i{tid}": ["2", "1", "3"], f"i{tid}_touched": "1"})
    assert r.json["ok"] and r.json["errors"] == {}
    client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "task", "task": str(tid), "t": "2000",
                                                              f"i{tid}": ["1", "2", "3"], f"i{tid}_touched": "1"})
    with app.app_context():
        from app import interview_tasks

        row = db.session.get(Response, rid)
        assert row.answers[str(tid)] == ["1", "2", "3"]
        assert row.interview["tasks"] == {str(tid): {"t": 1390}}                   # the time it was first given
        assert interview_tasks.result_text(db.session.get(Item, tid), row.answers[str(tid)], "de") == "1 Preis, 2 Qualität, 3 Marke"
    # the view saves the clock afterwards: the task's time stays
    client.post(f"/guide/{code}/interviews/{rid}/save", json={"states": {}, "ticks": [], "elapsed": 5000})
    with app.app_context():
        assert db.session.get(Response, rid).interview["tasks"] == {str(tid): {"t": 1390}}
    again = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    assert again.index(">Preis<") < again.index(">Qualität<") < again.index(">Marke<")
    # a task of another guide or a Leitfrage is no task
    assert client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "task", "task": str(q)}).json["errors"] == {}
    # emptied: the answer and its time go
    client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "task", "task": str(tid)})
    with app.app_context():
        row = db.session.get(Response, rid)
        assert str(tid) not in row.answers and row.interview["tasks"] == {}


def _show_area(page, tid):
    start_ = page.index(f'data-iv-task="{tid}"')
    area = page.index("data-iv-show-area", start_)
    end = page.index("</section>", area)
    return page[area:end]


def test_show_mode_holds_only_the_task(app, client):
    from pathlib import Path

    code, sid, q, tid = ranking_task(app, client)
    rid = start(app, client, code)
    with app.app_context():
        item = db.session.get(Item, q)
        lang = item.block.survey.default_language
        item.texts = {lang: {**item.texts[lang], "hint": "Nur für die Interviewerin"}}
        db.session.commit()
    page = client.get(f"/guide/{code}/conversation?interview={rid}").data.decode()
    area = _show_area(page, tid)
    assert "Wichtigkeit" in area and "Preis" in area and "data-iv-show-back" in area
    for private in ("data-iv-note", "Notiz", "Nur für die Interviewerin", "data-iv-state", "Zum Zeigen", "data-iv-clock"):
        assert private not in area                                                   # notes, hint, status, clock stay out
    assert "Nur für die Interviewerin" in page
    css = (Path(__file__).resolve().parents[2] / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".iv-showing .iv { visibility: hidden; }" in css and ".iv-task.is-shown { visibility: visible; }" in css
    js = (Path(__file__).resolve().parents[2] / "app" / "static" / "interview.js").read_text(encoding="utf-8")
    assert "HOLD_MS" in js and "iv-showing" in js
    # the button the person holding the device sees is in their language
    from app.ui_i18n import tr

    assert tr("fr", "Zurück (gedrückt halten)", "Back (press and hold)") == "Retour (maintenir appuyé)"
    # a trial without saving shows the task too, without a form to the server
    trial = client.get(f"/guide/{code}/conversation").data.decode()
    assert f'data-iv-task="{tid}"' in trial and 'name="part" value="task"' not in trial


def _answered(app, client):
    code, sid, q, tid = ranking_task(app, client)
    rid = start(app, client, code)
    client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "task", "task": str(tid), "t": "1390",
                                                              f"i{tid}": ["2", "1", "3"], f"i{tid}_touched": "1"})
    return code, sid, tid, rid


def test_case_overview_has_the_columns_of_the_task_type(app, client):
    from app import interview_results

    code, sid, tid, rid = _answered(app, client)
    with app.app_context():
        task_code = db.session.get(Item, tid).code
        text = interview_results.overview_csv(db.session.get(Survey, sid))
    head, row = [line.split(";") for line in text.lstrip("﻿").strip().splitlines()[:2]]
    values = dict(zip(head, row))
    assert values[f"{task_code}_1"] == "2" and values[f"{task_code}_2"] == "1" and values[f"{task_code}_3"] == "3"
    page = client.get(f"/guide/{code}/interviews/analysis").data.decode()
    assert "2 Qualität, 1 Preis" not in page and "1 Qualität, 2 Preis, 3 Marke" in page     # the row of the case


TEXT = ("I: Bitte ordnen Sie die Karten. #00:20:00-0#\n\n"
        "B: Preis kommt zuerst. #00:23:05-0#\n\n"
        "I: Warum steht das ganz oben? #00:23:30-0#")


def _with_transcript(app, client):
    code, sid, tid, rid = _answered(app, client)
    client.post(f"/guide/{code}/interviews/{rid}/transcript", data={"text": TEXT, "checked_shown": "1", "checked": "1"})
    return code, sid, tid, rid


def test_transcript_line_at_the_time_mark_and_switched_off(app, client):
    from app import transcript_export

    code, sid, tid, rid = _with_transcript(app, client)
    line = "[Aufgabe „Wichtigkeit“: 1 Qualität, 2 Preis, 3 Marke]"
    with app.app_context():
        survey, row = db.session.get(Survey, sid), db.session.get(Response, rid)
        body = transcript_export.body(survey, row, "de")
        assert [t[1] for t in body][1:3] == ["Preis kommt zuerst.", line]          # after the turn ending before 00:23:10
        assert body[2] == ("", line, False, True)
        marked = transcript_export.body(survey, row, "de", marks=True)
        assert ("", line + " #00:23:10-0#", False, True) in marked
        assert all(not t[3] for t in transcript_export.body(survey, row, "de", tasks=False))
        doc = transcript_export.docx(survey, row)
        xml = zipfile.ZipFile(io.BytesIO(doc)).read("word/document.xml").decode("utf-8")
        assert re.search(r"<w:i/>.*?Aufgabe „Wichtigkeit“", xml) and "Einfügungen in eckigen Klammern" in xml
        off = zipfile.ZipFile(io.BytesIO(transcript_export.docx(survey, row, tasks=False))).read("word/document.xml").decode("utf-8")
        assert "Aufgabe „Wichtigkeit“" not in off and "Einfügungen in eckigen Klammern" not in off
        assert transcript_export.pdf(survey, row).startswith(b"%PDF")
    # the page offers the switch; tasks=0 alone (box unticked) leaves the lines out
    assert "Ergebnisse der Aufgaben einfügen" in client.get(f"/guide/{code}/interviews/{rid}/transcript").data.decode()
    on = client.get(f"/guide/{code}/interviews/{rid}/transcript/export?format=docx&tasks=0&tasks=1").data
    off = client.get(f"/guide/{code}/interviews/{rid}/transcript/export?format=docx&tasks=0").data
    assert "Wichtigkeit" in zipfile.ZipFile(io.BytesIO(on)).read("word/document.xml").decode("utf-8")
    assert "Wichtigkeit" not in zipfile.ZipFile(io.BytesIO(off)).read("word/document.xml").decode("utf-8")


def test_qdpx_case_variable_line_and_code(app, client):
    from app import interview_qdpx, qdc

    code, sid, tid, rid = _with_transcript(app, client)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        data = interview_qdpx.build(survey)
        codebook = qdc.build(survey).decode("utf-8")
    z = zipfile.ZipFile(io.BytesIO(data))
    project = z.read("project.qde").decode("utf-8")
    assert 'name="Aufgabe: Wichtigkeit"' in project and 'typeOfVariable="Text"' in project
    assert "1 Qualität, 2 Preis, 3 Marke" in project
    text = next(z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("Sources/"))
    assert "[Aufgabe „Wichtigkeit“: 1 Qualität, 2 Preis, 3 Marke] #00:23:10-0#" in text
    assert text.index("Preis kommt zuerst.") < text.index("[Aufgabe") < text.index("Warum steht")
    assert 'name="Aufgabe: Wichtigkeit"' in codebook


def test_short_questionnaire_accepts_the_task_types(app, client):
    from app import interview

    code, sid = make(app, client)
    short = block_of(app, sid, "short")
    for kind in ("ranking", "card_sort", "likert", "image_choice"):
        assert add(client, sid, short, kind).status_code == 302
    page = client.get(f"/guide/{code}").data.decode()
    assert '<option value="ranking">Rangfolge</option>' in page
    with app.app_context():
        block = db.session.get(Block, short)
        ranking = next(i for i in block.items if i.type == "ranking")
        assert not interview.is_task(ranking) and interview.may_add(block.survey, block, "ranking")
        options = [o["code"] for o in ranking.config["options"]]
        rid_ = ranking.id
    rid = start(app, client, code)
    r = client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "short", f"i{rid_}": list(reversed(options)),
                                                                  f"i{rid_}_touched": "1"})
    assert r.json["errors"] == {}
    with app.app_context():
        assert db.session.get(Response, rid).answers[str(rid_)] == list(reversed(options))
