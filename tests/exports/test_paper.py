"""Paper questionnaires (experimental "paper", app/paper.py, papierfragebogen-konzept.md): print runs with numbered
sheets, reading scans (corner marks, code, boxes, handwriting), checking, taking over through the page engine,
consent, deletion and the export column "mode".

Most scans here are drawn with Pillow from the plan of a sheet (`synth`), with the same geometry the PDF has; one test
renders the real PDF with pypdfium2 when it is installed (requirements-dev.txt)."""
import io

import pytest
from PIL import Image, ImageDraw

from app import paper_pdf as P
from app.extensions import db
from app.models import Item, PaperPage, PaperRun, PaperSheet, Response, Survey
from tests.conftest import add, experiments_on, fill_consent, fill_questions, own_ai

DPI = 200


def _unchecked(client):
    """Sheets without anything uncertain are taken over at once: most tests read a sheet straight through. The
    default is "check every sheet" (test_every_sheet_is_checked_by_default)."""
    experiments_on(client, "paper")
    client.post("/surveys/1/paper/settings", data={"review": "unsure", "keep": "30"})


def _setup(app, client, start=True):
    """Consent (1), single choice (2), multiple choice (3), Likert with two statements (4), free text (5)."""
    _unchecked(client)
    fill_consent(client)
    for t in ("single_choice", "multiple_choice", "likert", "text_input"):
        add(client, t)
    with app.app_context():
        items = {i.id: i for i in db.session.scalars(db.select(Item))}
        sc, mc, lk, tx = items[2], items[3], items[4], items[5]
        sc.code, mc.code, lk.code, tx.code = "sc", "mc", "lk", "tx"
        sc.config = {**sc.config, "options": [{"id": "o1", "code": "1"}, {"id": "o2", "code": "2"}]}
        sc.texts = {"de": {"question": "Raucht ihr?", "options": {"o1": "ja", "o2": "nein"}}}
        mc.config = {**mc.config, "options": [{"id": "m1", "code": "1"}, {"id": "m2", "code": "2"}, {"id": "m3", "code": "3"}]}
        mc.texts = {"de": {"question": "Welche Farben?", "options": {"m1": "rot", "m2": "blau", "m3": "grün"}}}
        lk.config = {**lk.config, "statements": [{"id": "s1", "code": "lk_1", "reverse": False},
                                                 {"id": "s2", "code": "lk_2", "reverse": False}]}
        lk.texts = {"de": {"question": "Wie sehr stimmen Sie zu?", "statements": {"s1": "Ich bin gern draußen.", "s2": "Ich lese viel."}}}
        tx.config = {**tx.config, "multiline": True}
        tx.texts = {"de": {"question": "Was noch?"}}
        db.session.commit()
    fill_questions(app)
    if start:
        client.post("/surveys/1/start", data={})
        with app.app_context():
            assert db.session.get(Survey, 1).status == "running"


def _run(client, n=2):
    r = client.post("/surveys/1/paper/runs", data={"language": "de", "count": str(n)})
    assert r.status_code == 302


def _sheets(app):
    with app.app_context():
        out = []
        for s in db.session.scalars(db.select(PaperSheet).order_by(PaperSheet.id)):
            plan = db.session.get(PaperRun, s.run_id).plans[s.plan]
            out.append({"id": s.id, "key": s.key, "pages": s.pages, "plan": plan})
        return out


def _field(plan, f, v=None, k="box"):
    """The index of the field with form field `f` (and value `v`)."""
    for n, field in enumerate(plan["fields"]):
        if field["k"] == k and field["f"] == f and (v is None or field.get("v") == v):
            return n
    raise KeyError((f, v))


def synth(plan, key, page, ticks=(), fills=(), writes=(), angle=0.0, dpi=DPI):
    """A scanned page drawn from the plan: corner squares, the code, the boxes, crosses in `ticks`, filled-in boxes
    in `fills`, a scribble in the writing fields `writes`."""
    ppm = dpi / 25.4
    img = Image.new("L", (int(210 * ppm), int(297 * ppm)), 250)
    d = ImageDraw.Draw(img)
    for cx, cy in P.MARKS:
        d.rectangle([(cx - P.MARK / 2) * ppm, (cy - P.MARK / 2) * ppm, (cx + P.MARK / 2) * ppm, (cy + P.MARK / 2) * ppm], fill=0)
    for bit, (x, y, w, h) in zip(P.code_bits(key, page), P.code_cells()):
        if bit:
            d.rectangle([x * ppm, y * ppm, (x + w) * ppm - 1, (y + h) * ppm - 1], fill=0)
    pen = max(1, int(0.5 * ppm))
    for n, f in enumerate(plan["fields"]):
        if f["p"] != page:
            continue
        x0, y0, x1, y1 = f["x"] * ppm, f["y"] * ppm, (f["x"] + f["w"]) * ppm, (f["y"] + f["h"]) * ppm
        d.rectangle([x0, y0, x1, y1], outline=0, width=max(1, int(0.3 * ppm)))
        if n in ticks:
            d.line([x0 + 3, y0 + 3, x1 - 3, y1 - 3], fill=30, width=pen)
            d.line([x0 + 3, y1 - 3, x1 - 3, y0 + 3], fill=30, width=pen)
        if n in fills:
            d.rectangle([x0, y0, x1, y1], fill=20)
        if n in writes:
            pts = [(x0 + 2 * ppm + k * 3 * ppm, y0 + (2 if k % 2 else f["h"] - 2) * ppm)
                   for k in range(int((f["w"] - 4) / 3))]
            d.line(pts, fill=40, width=pen)
    if angle:
        img = img.rotate(angle, expand=True, fillcolor=250)
    return img


def _pdf(images):
    buf = io.BytesIO()
    images[0].convert("RGB").save(buf, "PDF", save_all=True, append_images=[i.convert("RGB") for i in images[1:]],
                                  resolution=DPI)
    return buf.getvalue()


def _upload(client, data, name="scan.pdf"):
    return client.post("/surveys/1/paper/upload", data={"scans": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def _all_pages(sheet, ticks=(), fills=(), writes=()):
    return [synth(sheet["plan"], sheet["key"], p, ticks, fills, writes) for p in range(sheet["pages"])]


def _yes(plan):
    """The ticks of a filled sheet: consent agreed with all confirmations."""
    ticks = [n for n, f in enumerate(plan["fields"]) if f["k"] == "box" and "_c_" in f["f"]]
    return ticks + [_field(plan, "i1", "1")]


def test_number_and_code_round_trip():
    for key in (1, 2 ** 40 - 1, 123456789):
        assert P.parse_number(P.sheet_number(key)) == key
        assert P.decode_bits(P.code_bits(key, 3)) == (key, 3)
    bits = P.code_bits(99, 0)
    bits[5] ^= 1
    assert P.decode_bits(bits) is None
    assert P.parse_number("7k3q 9f2m") == P.parse_number("7K3Q-9F2M")
    assert P.parse_number("zu kurz") is None


def test_paper_is_for_everyone(app, client, survey):
    """No longer experimental (Ralf, 2026-10-11): every survey shows the block, no switch needed."""
    from app import experimental

    fill_consent(client)
    assert "paper" not in experimental.KEYS
    assert "Papierfragebogen" in client.get("/surveys/1/field").get_data(as_text=True)
    assert client.post("/surveys/1/paper/runs", data={"language": "de", "count": "1"}).status_code == 302


def test_print_run_makes_numbered_sheets_and_a_pdf(app, client, survey):
    _setup(app, client)
    _run(client, 3)
    sheets = _sheets(app)
    assert len(sheets) == 3 and len({s["key"] for s in sheets}) == 3
    plan = sheets[0]["plan"]
    kinds = {(f["f"], f.get("v")) for f in plan["fields"]}
    assert ("i2", "1") in kinds and ("i3", "3") in kinds and ("i4_s1", "5") in kinds and ("i5", None) in kinds
    assert all(P.LEFT - 1 <= f["x"] and f["x"] + f["w"] <= P.RIGHT + 1 and P.TOP <= f["y"] and
               f["y"] + f["h"] <= P.BOTTOM for f in plan["fields"])
    r = client.get("/surveys/1/paper/runs/1.pdf")
    assert r.status_code == 200 and r.data[:5] == b"%PDF-" and r.mimetype == "application/pdf"
    page = client.get("/surveys/1/field").get_data(as_text=True)
    assert "Druckauftrag 1" in page and "3 Bögen" in page


def test_scanned_sheet_is_taken_over_as_a_paper_participation(app, client, survey):
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    ticks = _yes(plan) + [_field(plan, "i2", "2"), _field(plan, "i3", "1"), _field(plan, "i3", "3"),
                          _field(plan, "i4_s1", "4"), _field(plan, "i4_s2", "2")]
    assert _upload(client, _pdf(_all_pages(s, ticks))).status_code == 302
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "done", sheet.state
        r = db.session.get(Response, sheet.response_id)
        assert r.channel == "paper" and r.source == "real" and r.status == Response.COMPLETED
        assert r.answers["2"] == "2" and r.answers["3"] == ["1", "3"] and r.answers["4"] == {"s1": 4, "s2": 2}
        assert "5" not in r.answers or r.answers["5"] in (None, "")
        assert r.paper["sheet"] == P.sheet_number(s["key"])
        assert r.started_at == r.completed_at
        # the scans stay 30 days by default
        assert db.session.scalar(db.select(db.func.count(PaperPage.id)).filter_by(sheet_id=sheet.id)) == s["pages"]
    csv = client.get("/surveys/1/export/data.csv").get_data(as_text=True)
    head, row = csv.splitlines()[0].split(","), csv.splitlines()[1].split(",")
    assert row[head.index("mode")] == "paper" and row[head.index("duration_s")] == ""


def test_handwriting_and_unclear_ticks_wait_for_a_check(app, client, survey):
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    text = _field(plan, "i5", k="write")
    ticks = _yes(plan) + [_field(plan, "i2", "1"), _field(plan, "i2", "2"), _field(plan, "i3", "2")]
    _upload(client, _pdf(_all_pages(s, ticks, writes=[text])))
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "review"
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "mehrere Kreuze" in page and "Handschrift abtippen" in page and f"w_{text}" in page
    assert client.get(f"/surveys/1/paper/sheets/{s['id']}/part/{text}.png").mimetype == "image/png"
    assert client.get(f"/surveys/1/paper/sheets/{s['id']}/part/{_field(plan, 'i2', '1')}.png?group=1").status_code == 200
    # taking over while a tick is unclear is refused
    r = client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take", f"w_{text}": "Mehr Radwege"})
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "review"
    r = client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take", "g_i2": "1", f"w_{text}": "Mehr Radwege"})
    assert r.status_code == 302
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "done"
        r = db.session.get(Response, sheet.response_id)
        assert r.answers["2"] == "1" and r.answers["3"] == ["2"] and r.answers["5"] == "Mehr Radwege"
        assert r.paper["changes"] >= 2 and r.paper["checked"]


def test_filled_in_box_is_a_correction(app, client, survey):
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    ticks = _yes(plan) + [_field(plan, "i2", "2")]
    _upload(client, _pdf(_all_pages(s, ticks, fills=[_field(plan, "i2", "1")])))
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "done"
        assert db.session.get(Response, sheet.response_id).answers["2"] == "2"


def test_without_consent_nothing_is_stored(app, client, survey):
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, [_field(s["plan"], "i2", "1")])))
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "declined" and sheet.response_id is None
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert db.session.scalar(db.select(db.func.count(PaperPage.id)).filter_by(sheet_id=sheet.id)) == 0
        assert db.session.get(Survey, 1).declined_count == 1


def test_pages_in_any_order_turned_and_twice(app, client, survey):
    _setup(app, client)
    _run(client, 2)
    a, b = _sheets(app)
    pages = [synth(b["plan"], b["key"], p, _yes(b["plan"])) for p in range(b["pages"])][::-1]
    pages += [synth(a["plan"], a["key"], p, _yes(a["plan"]), angle=180 if p == 0 else 1.2) for p in range(a["pages"])]
    pages.append(synth(a["plan"], a["key"], 0, _yes(a["plan"])))  # the first page of a once more
    pages.append(Image.new("L", (1700, 2400), 250))  # an empty page
    _upload(client, _pdf(pages))
    with app.app_context():
        assert {s.state for s in db.session.scalars(db.select(PaperSheet))} == {"done"}
        states = sorted(p.state for p in db.session.scalars(db.select(PaperPage)) if p.state != "read")
        assert states == ["double", "unreadable"]
    page = client.get("/surveys/1/field").get_data(as_text=True)
    assert "doppelt gescannt" in page and "Eckmarken nicht gefunden" in page


def test_pages_of_another_survey_are_refused_without_keeping_them(app, client, survey):
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf([synth(s["plan"], s["key"] ^ 1, 0)]))
    with app.app_context():
        page = db.session.scalar(db.select(PaperPage))
        assert page.state == "foreign" and page.filename == ""


def test_deleting_a_sheet_on_request(app, client, survey):
    _setup(app, client)
    _run(client, 2)
    a, b = _sheets(app)
    _upload(client, _pdf(_all_pages(a, _yes(a["plan"]))))
    client.post(f"/surveys/1/paper/sheets/{a['id']}/erase")
    client.post(f"/surveys/1/paper/sheets/{b['id']}/erase")  # before it was scanned
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert {s.state for s in db.session.scalars(db.select(PaperSheet))} == {"erased"}
        assert db.session.scalar(db.select(db.func.count(PaperPage.id))) == 0
    assert "Bitte Bogen" in client.get("/surveys/1/field").get_data(as_text=True)
    _upload(client, _pdf(_all_pages(b, _yes(b["plan"]))))  # comes later: not taken
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert all(p.filename == "" for p in db.session.scalars(db.select(PaperPage)))
    client.post(f"/surveys/1/paper/sheets/{a['id']}/destroyed")
    with app.app_context():
        assert not db.session.get(PaperSheet, a["id"]).destroy


def test_participant_erasure_takes_the_scans(app, client, survey):
    from app import erasure

    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]))))
    with app.app_context():
        survey_row = db.session.get(Survey, 1)
        response = db.session.scalar(db.select(Response))
        erasure.erase(survey_row, [response], "participant")
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "erased" and sheet.destroy
        assert db.session.scalar(db.select(db.func.count(PaperPage.id))) == 0


def test_draft_sheets_become_test_runs_and_keep_none(app, client, survey):
    _setup(app, client, start=False)
    client.post("/surveys/1/paper/settings", data={"review": "unsure", "keep": "0"})
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]))))
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        assert r.source == "test" and r.is_preview and r.channel == "paper"
        assert db.session.scalar(db.select(db.func.count(PaperPage.id))) == 0


def test_review_all_waits_for_every_sheet(app, client, survey):
    _setup(app, client)
    client.post("/surveys/1/paper/settings", data={"review": "all", "keep": "30"})
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]) + [_field(s["plan"], "i2", "1")])))
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "review"
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "Wie sehr stimmen Sie zu?" in page
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take"})
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "done"


def test_old_scans_and_loose_pages_go(app, client, survey):
    import os
    from datetime import timedelta

    from app import paper
    from app.models import utcnow

    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"])) + [Image.new("L", (1700, 2400), 250)]))
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(PaperPage.id))) == s["pages"] + 1
        assert paper.run(utcnow() + timedelta(days=8)) == 1  # the unreadable page
        survey_row, run = db.session.get(Survey, 1), db.session.scalar(db.select(PaperRun))
        assert os.path.isfile(paper.run_pdf_path(survey_row, run))
        assert paper.run(utcnow() + timedelta(days=31)) == 2  # the sheet's scans and the PDF of its run (P-S9)
        assert db.session.scalar(db.select(db.func.count(PaperPage.id))) == 0
        assert not os.path.isfile(paper.run_pdf_path(survey_row, run))
        assert db.session.scalar(db.select(Response)).channel == "paper"


def test_deleting_the_survey_takes_everything(app, client, survey):
    import os

    from app import lifecycle, paper

    _setup(app, client, start=False)
    _run(client, 1)
    with app.app_context():
        s = db.session.get(Survey, 1)
        path = paper.folder(s)
        assert os.path.isdir(path)
        lifecycle.delete(s, force=True)
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(PaperSheet.id))) == 0
        assert not os.path.isdir(path)


def test_real_pdf_is_read(app, client, survey):
    pdfium = pytest.importorskip("pypdfium2")
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    pdf = client.get("/surveys/1/paper/runs/1.pdf").data
    doc = pdfium.PdfDocument(pdf)
    ppm = 150 / 25.4
    plan = s["plan"]
    ticks = set(_yes(plan) + [_field(plan, "i2", "1"), _field(plan, "i4_s2", "5")])
    images = []
    for n in range(len(doc)):
        img = doc[n].render(scale=150 / 72).to_pil().convert("L")
        d = ImageDraw.Draw(img)
        for i in ticks:
            f = plan["fields"][i]
            if f["p"] == n:
                x0, y0, x1, y1 = f["x"] * ppm, f["y"] * ppm, (f["x"] + f["w"]) * ppm, (f["y"] + f["h"]) * ppm
                d.line([x0 + 2, y0 + 2, x1 - 2, y1 - 2], fill=40, width=3)
                d.line([x0 + 2, y1 - 2, x1 - 2, y0 + 2], fill=40, width=3)
        images.append(img.rotate(0.8, expand=True, fillcolor=255))
    doc.close()
    _upload(client, _pdf(images))
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "done"
        r = db.session.get(Response, sheet.response_id)
        assert r.answers["2"] == "1" and r.answers["4"]["s2"] == 5


# --- stage 2: filter notes, screen-out notes, deletion-code page, full quotas ---------------------------------------


def _pdf_text(client, run=1):
    from pypdf import PdfReader

    data = client.get(f"/surveys/1/paper/runs/{run}.pdf").data
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def _conditions(app, client):
    """Question 3 (multiple choice) only if question 2 is "ja" (1), on a page of its own; a screen-out after the block
    if it is "nein"."""
    add(client, "page_break")  # item 6
    with app.app_context():
        for item_id, position in ((2, 0), (6, 1), (3, 2), (4, 3), (5, 4)):
            db.session.get(Item, item_id).position = position
        mc = db.session.get(Item, 3)
        mc.config = {**mc.config, "when": {"item": 2, "op": "eq", "value": "1"}}
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2},
                                            {"t": "branch", "id": "b1", "cond": {"item": 2, "op": "eq", "value": "2"},
                                             "children": [{"t": "end", "id": "e1", "text": {}, "kind": "screenout"}]}]}
        db.session.commit()


def test_filter_and_screen_out_notes_are_printed_and_hold(app, client, survey):
    _setup(app, client, start=False)
    _conditions(app, client)
    client.post("/surveys/1/start", data={})
    _run(client, 2)
    text = _pdf_text(client)
    assert "Nur beantworten, wenn Frage 1: ja." in text
    assert "Wenn Frage 1: nein: Der Fragebogen endet hier" in text
    a, b = _sheets(app)
    # a: "nein" and still a tick at question 3 (should have been skipped), then the screen-out
    ticks = _yes(a["plan"]) + [_field(a["plan"], "i2", "2"), _field(a["plan"], "i3", "1")]
    _upload(client, _pdf(_all_pages(a, ticks)))
    with app.app_context():
        r = db.session.get(Response, db.session.get(PaperSheet, a["id"]).response_id)
        assert r.status == Response.SCREENOUT and "3" not in r.answers
    ticks = _yes(b["plan"]) + [_field(b["plan"], "i2", "1"), _field(b["plan"], "i3", "2")]
    _upload(client, _pdf(_all_pages(b, ticks)))
    with app.app_context():
        r = db.session.get(Response, db.session.get(PaperSheet, b["id"]).response_id)
        assert r.status == Response.COMPLETED and r.answers["3"] == ["2"]


def test_deletion_code_page(app, client, survey):
    from app import erasure

    _setup(app, client)
    with app.app_context():  # the deletion code is off by default since the pre-test 2026-10-16
        from app.extensions import db as _db
        from app.models import Survey as _Survey

        _db.session.get(_Survey, 1).erase_code = True
        _db.session.commit()
    _run(client, 2)
    text = _pdf_text(client)
    assert "Diese Seite behalten Sie" in text and "/privacy/delete" in text
    a, b = _sheets(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        sheet_a, sheet_b = db.session.get(PaperSheet, a["id"]), db.session.get(PaperSheet, b["id"])
        code_a = erasure.written(s, erasure.raw_code(s, sheet_a.pid))
        code_b = erasure.written(s, erasure.raw_code(s, sheet_b.pid))
    assert code_a in text.replace("\n", " ")
    # b is deleted with its code before it is scanned; a after it was taken over
    other = app.test_client()
    assert other.post("/privacy/delete", data={"code": code_b, "sure": "1"}).status_code == 200
    _upload(client, _pdf(_all_pages(a, _yes(a["plan"]))))
    _upload(client, _pdf(_all_pages(b, _yes(b["plan"]))))
    with app.app_context():
        assert db.session.get(PaperSheet, b["id"]).state == "erased"
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1
    other.post("/privacy/delete", data={"code": code_a, "sure": "1"})
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0
        assert db.session.get(PaperSheet, a["id"]).destroy


def test_the_deletion_code_is_on_a_page_of_its_own(app, client, survey):
    """The code is on a page after the sheet that the participant keeps: no page that goes back to be scanned carries
    it, and nothing the scan needs is on the page kept (Ralf, 2026-10-11: a slip to cut off at the foot took the code
    row with it). Double-sided, every sheet and the page kept start on a paper of their own."""
    from pypdf import PdfReader

    from app import erasure

    _setup(app, client)
    with app.app_context():  # the deletion code is off by default since the pre-test 2026-10-16
        from app.extensions import db as _db
        from app.models import Survey as _Survey

        _db.session.get(_Survey, 1).erase_code = True
        _db.session.commit()
    _run(client, 2)
    a, b = _sheets(app)
    assert a["pages"] == 1 and "slip" not in a["plan"]
    with app.app_context():
        s = db.session.get(Survey, 1)
        codes = [erasure.written(s, erasure.raw_code(s, db.session.get(PaperSheet, x["id"]).pid)) for x in (a, b)]
    pdf = client.get("/surveys/1/paper/runs/1.pdf").data
    pages = [p.extract_text().replace(chr(10), " ") for p in PdfReader(io.BytesIO(pdf)).pages]
    assert len(pages) == 4  # sheet a, its page to keep, sheet b, its page to keep
    for n, code in enumerate(codes):
        sheet, keep = pages[2 * n], pages[2 * n + 1]
        assert code not in sheet and "Seite 1 von 1" in sheet
        assert code in keep and "Diese Seite behalten Sie" in keep and "Seite 1 von" not in keep
    # double-sided: an empty back after the one page of the sheet and after the page kept
    assert client.post("/surveys/1/paper/runs", data={"language": "de", "count": "1", "duplex": "1"}).status_code == 302
    pages = [p.extract_text() for p in PdfReader(io.BytesIO(client.get("/surveys/1/paper/runs/2.pdf").data)).pages]
    assert len(pages) == 4 and not pages[1].strip() and "Diese Seite behalten Sie" in pages[2] and not pages[3].strip()


def test_large_type_is_the_default(app, client, survey):
    """Type 12 instead of 10 pt unless "normal" is chosen (tester, 2026-10-11: "nicht so age-inclusive"); the layout
    follows the type, the boxes keep their size."""
    import random

    from app import runtime

    _setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        layout = runtime.build_layout(s, random.Random(1), separate=True)
        sizes, plans = {}, {}
        for size in ("normal", "large"):
            pages, plans[size] = P.build_sheet(s, "de", layout, size=size)
            sizes[size] = max(op[3] for page in pages for op in page if op[0] == "text")
        default = P.build_sheet(s, "de", layout)[1]
    assert sizes["large"] == pytest.approx(sizes["normal"] * 1.2) and default == plans["large"]
    assert {f["w"] for f in plans["large"]["fields"] if f["k"] == "box"} == {P.BOX}
    last = lambda plan: max(f["y"] for f in plan["fields"])  # noqa: E731
    assert last(plans["large"]) > last(plans["normal"]) or plans["large"]["pages"] > plans["normal"]["pages"]
    assert P.line_height(10) == pytest.approx(10 * 1.3 / P.MM)  # outside a sheet nothing is scaled
    # the print form passes the choice on
    _run(client, 1)
    assert client.post("/surveys/1/paper/runs", data={"language": "de", "count": "1", "size": "normal"}).status_code == 302
    with app.app_context():
        large, normal = (list(db.session.get(PaperRun, n).plans.values())[0] for n in (1, 2))
    assert last(large) > last(normal)


def test_a_full_quota_does_not_stop_a_paper_sheet(app, client, survey):
    from app import runtime

    _setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "end", "id": "e1", "text": {},
                                                                         "kind": "quotafull"}, {"t": "block", "block": 2}]}
        db.session.commit()
    _run(client, 1)
    s1 = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s1, _yes(s1["plan"]) + [_field(s1["plan"], "i2", "1")])))
    with app.app_context():
        r = db.session.get(Response, db.session.get(PaperSheet, s1["id"]).response_id)
        assert r.status == Response.COMPLETED and r.answers["2"] == "1"


def test_ai_reads_handwriting_only_after_the_tick(app, client, survey, monkeypatch):
    import json

    from app import ai

    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    text = _field(s["plan"], "i5", k="write")
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]), writes=[text])))
    app.config["OPENAI_API_KEY"] = "test"
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "Nur mit einem eigenen KI-Zugang" in page and "/account/connections?for=paper#ki-zugang" in page  # the button
    assert "Teilnehmende wurden über" not in page                       # stays, but the platform key is not used
    own_ai(app)  # handwriting of participants goes only over an own AI access (Ralf, 2026-10-21)
    sent = []

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None, store=False, units=1):
        sent.append(prompt)
        assert units == 1  # one image: one call on the daily limit (P-S11, paper.AI_UNIT)
        return json.dumps({"fields": [{"n": text, "text": "Mehr Radwege"}, {"n": 9999, "text": "nein"}]})

    monkeypatch.setattr(ai, "generate_text", fake)
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "Teilnehmende wurden über die Übermittlung informiert" in page and "Handschrift mit KI lesen" not in page
    assert client.post(f"/surveys/1/paper/sheets/{s['id']}/read").status_code == 404
    client.post("/surveys/1/paper/ai-informed", data={"informed": "1"})
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "Handschrift mit KI lesen" in page
    client.post(f"/surveys/1/paper/sheets/{s['id']}/read")
    assert any("png" in part for part in sent[0]) and not any(P.sheet_number(s["key"]) in str(p) for p in sent[0])
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "Mehr Radwege" in page and "KI-Vorschlag" in page
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take", f"w_{text}": "Mehr Radwege"})
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert db.session.get(Response, sheet.response_id).answers["5"] == "Mehr Radwege"


def test_ai_parts_for_both_apis():
    from app import ai

    parts = [{"text": "Field 3"}, {"png": b"\x89PNG"}]
    responses = ai._parts(parts, "responses")
    assert responses[0]["content"][1]["type"] == "input_image" and responses[0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    chat = ai._parts(parts, "chat")
    assert chat[0] == {"type": "text", "text": "Field 3"} and chat[1]["image_url"]["url"].startswith("data:image/png")
    assert ai._parts("plain", "chat") == "plain"


def test_results_split_by_mode(app, client, survey):
    from app import groupvars

    _setup(app, client)
    with app.app_context():
        assert "mode" not in groupvars.all_of(db.session.get(Survey, 1))
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]) + [_field(s["plan"], "i2", "1")])))
    with app.app_context():
        var = groupvars.all_of(db.session.get(Survey, 1))["mode"]
        assert [c for c, _ in var.groups] == ["online", "paper"] and var.member({"mode": "paper"}) == "paper"


# --- stage 3: choice experiments on paper, Hot Spot, four eyes, digits --------------------------------------------------


def _choice_setup(app, client, kind):
    from tests.analysis import test_conjoint
    from tests.analysis import test_maxdiff

    _unchecked(client)
    if kind == "conjoint":
        test_conjoint.setup(client, none=True, visual=(), versions=2)
    else:
        test_maxdiff.setup(client, explain=True, versions=2)
    fill_questions(app)
    client.post("/surveys/1/start", data={})


def test_conjoint_on_paper(app, client, survey):
    _choice_setup(app, client, "conjoint")
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    tasks = sorted({f["f"] for f in plan["fields"] if f["f"].startswith("i2_t")})
    assert len(tasks) == 4 and ("i2_t0", "0") in {(f["f"], f.get("v")) for f in plan["fields"]}
    text = _pdf_text(client)
    assert "Aufgabe 1 von 4" in text and "Datenschutz" in text and "Keine davon" in text
    ticks = _yes(plan) + [_field(plan, t, "2") for t in tasks]
    _upload(client, _pdf(_all_pages(s, ticks)))
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        r = db.session.get(Response, sheet.response_id)
        assert sheet.state == "done" and r.status == Response.COMPLETED
        assert all(r.answers["2"][f"t{n}"] == 2 for n in range(4))
        assert r.layout["cj"]["2"]["version"] == sheet.layout["cj"]["2"]["version"]


def test_maxdiff_on_paper(app, client, survey):
    _choice_setup(app, client, "maxdiff")
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    sets = sorted({f["f"][:-2] for f in plan["fields"] if f["f"].startswith("i2_t")})
    assert sets and "Sie sehen gleich" in _pdf_text(client)
    ticks = list(_yes(plan))
    for key in sets:
        values = [f["v"] for f in plan["fields"] if f["f"] == f"{key}_b"]
        ticks += [_field(plan, f"{key}_b", values[0]), _field(plan, f"{key}_w", values[-1])]
    _upload(client, _pdf(_all_pages(s, ticks)))
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "done", sheet.state
        r = db.session.get(Response, sheet.response_id)
        assert r.status == Response.COMPLETED and len([k for k in r.answers["2"] if k.startswith("t")]) == len(sets)


@pytest.mark.parametrize("form", ["single", "pairs"])
def test_cva_on_paper(app, client, survey, form):
    """Ratings-based conjoint (app/items/cva.py, C8): the offers and the scale as boxes, read back as the rating."""
    from tests.analysis import test_cva

    _unchecked(client)
    test_cva.setup(client, form=form, points=7, explain=True)
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    tasks = sorted({f["f"] for f in plan["fields"] if f["f"].startswith("i2_t")}, key=lambda f: int(f[4:]))
    assert len(tasks) == 12
    text = _pdf_text(client)
    assert "Aufgabe 1 von 12" in text and "Miete" in text
    assert ("Angebot 1 viel lieber" in text) if form == "pairs" else ("sehr wahrscheinlich" in text)
    value = "-2" if form == "pairs" else "6"
    _upload(client, _pdf(_all_pages(s, _yes(plan) + [_field(plan, t, value) for t in tasks])))
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        r = db.session.get(Response, sheet.response_id)
        assert sheet.state == "done" and r.status == Response.COMPLETED
        assert all(r.answers["2"][f"t{n}"] == int(value) for n in range(12))


@pytest.mark.parametrize("display", ["image", "both"])
def test_conjoint_pictures_on_paper(app, client, survey, display):
    """A CBC shown with pictures gets them on paper too, as online: "image" only the pictures, "both" the pictures
    and the attributes not in them; a picture missing on disk prints the task as a table (tester, 2026-10-11)."""
    import os

    from pypdf import PdfReader

    from app.models import Asset
    from tests.analysis import test_conjoint

    _unchecked(client)
    test_conjoint.setup(client, none=True, display=display, visual=("a1",), versions=1)
    with app.app_context():
        item = db.session.get(Item, 2)
        folder = os.path.join(app.config["UPLOAD_FOLDER"], db.session.get(Survey, 1).uid)
        os.makedirs(folder, exist_ok=True)
        for n, key in enumerate(item.kind.required_keys(item)):
            Image.new("RGB", (300, 200), (40 * n % 255, 120, 200)).save(os.path.join(folder, f"cj{n}.png"))
            db.session.add(Asset(item_id=item.id, key=key, filename=f"cj{n}.png", source="ai", status="approved"))
        db.session.commit()
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    _run(client, 1)
    reader = PdfReader(io.BytesIO(client.get("/surveys/1/paper/runs/1.pdf").data))
    assert sum(len(page.images) for page in reader.pages) >= 4  # pictures on the pages of the 4 tasks
    text = chr(10).join(page.extract_text() for page in reader.pages)
    assert "Aussehen" not in text  # the attribute in the picture
    assert ("Preis" in text) == (display == "both")
    # every task can still be answered and read
    s = _sheets(app)[0]
    assert len({f["f"] for f in s["plan"]["fields"] if f["f"].startswith("i2_t")}) == 4
    # a picture gone from disk: the task is printed as a table with text
    os.remove(os.path.join(folder, "cj0.png"))
    _run(client, 1)
    text = _pdf_text(client, 2)
    assert "Aussehen" in text and "Preis" in text


@pytest.mark.parametrize("mode", ["areas", "rate"])
def test_hot_spot_on_paper(app, client, survey, mode):
    from tests.items import test_image_mark

    _unchecked(client)
    fill_consent(client)
    test_image_mark.setup_hot(client, app, mode=mode)
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    if mode == "areas":
        ticks = [_field(plan, "i2", "logo"), _field(plan, "i2", "preis")]
    else:
        ticks = [_field(plan, "i2_logo", "1"), _field(plan, "i2_1", "-1")]
    _upload(client, _pdf(_all_pages(s, _yes(plan) + ticks)))
    with app.app_context():
        r = db.session.get(Response, db.session.get(PaperSheet, s["id"]).response_id)
        assert r.answers["2"] == (["logo", "preis"] if mode == "areas" else {"logo": 1, "1": -1})


def test_every_sheet_is_checked_by_default_and_can_be_looked_through(app, client, survey):
    """Without a choice, every sheet waits for a person, also one read without doubt; once taken over it is shown
    read-only, as it was read (Ralf, 2026-10-11: "man fühlt sich etwas out-of-the-loop")."""
    _setup(app, client)
    with app.app_context():
        db.session.get(Survey, 1).paper = None  # no choice made
        db.session.commit()
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]) + [_field(s["plan"], "i2", "1")])))
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "review"
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "Raucht ihr?" in page and "Übernehmen" in page
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take", "g_i2": "1"})
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "done"
    field = client.get("/surveys/1/field").get_data(as_text=True)
    assert "Zuletzt übernommen (1)" in field and f"/surveys/1/paper/sheets/{s['id']}" in field
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "So wurde der Bogen gelesen und übernommen" in page and "Raucht ihr?" in page
    assert '<fieldset class="bare" disabled>' in page and 'value="take"' not in page
    assert client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take"}).status_code == 409


def test_four_eyes(app, client, survey):
    _setup(app, client)
    client.post("/surveys/1/paper/settings", data={"review": "four", "keep": "30"})
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]) + [_field(s["plan"], "i2", "1")])))
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take"})
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "review" and sheet.checked_by
    r = client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take"}, follow_redirects=True)
    assert "andere Person" in r.get_data(as_text=True)
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "review"
        sheet.checked_by = 999  # as if someone else had checked first
        db.session.commit()
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take"})
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "done"
        assert db.session.get(Response, sheet.response_id).paper["second_check"]


def test_digits_read_locally():
    import numpy as np
    from PIL import ImageFont

    from app import paper_digits

    ppm = DPI / 25.4
    H = np.array([[ppm, 0, 0], [0, ppm, 0], [0, 0, 1.0]])
    field = {"x": 20, "y": 20, "w": 40, "h": 10}
    for text in ("42", "305", "7,5", ""):
        img = Image.new("L", (int(80 * ppm), int(40 * ppm)), 250)
        d = ImageDraw.Draw(img)
        d.rectangle([20 * ppm, 20 * ppm, 60 * ppm, 30 * ppm], outline=0, width=2)
        d.text((22 * ppm, 20.8 * ppm), text, fill=40, font=ImageFont.load_default(size=int(6.5 * ppm)))
        assert paper_digits.read_number(img, H, field) == (text or None)


def test_digit_suggestion_on_the_review_page(app, client, survey, monkeypatch):
    from app import paper_digits

    _setup(app, client, start=False)
    with app.app_context():
        tx = db.session.get(Item, 5)
        tx.config = {**tx.config, "validation": "integer", "multiline": False}
        db.session.commit()
    client.post("/surveys/1/start", data={})
    monkeypatch.setattr(paper_digits, "read_number", lambda img, H, field: "42")
    _run(client, 1)
    s = _sheets(app)[0]
    number = _field(s["plan"], "i5", k="write")
    assert s["plan"]["fields"][number]["fmt"] == "int"
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]), writes=[number])))
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert f'name="w_{number}" value="42"' in page and "Vorschlag der Ziffernerkennung" in page
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take", f"w_{number}": "42"})
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert db.session.get(Response, sheet.response_id).answers["5"] == 42


# --- security audit 2026-10-10, package U2 (P-S1 to P-S10) ------------------------------------------------------------


def _bomb(fmt="PNG", mode="1", side=13000):
    """A small file holding a huge page: one colour in mode 1 or P compresses to nothing (P-S1: 20 KB, 1.9 GB)."""
    buf = io.BytesIO()
    Image.new(mode, (side, side), 0).save(buf, fmt)
    return buf.getvalue()


def test_bomb_pages_are_refused_from_their_header(monkeypatch):
    from app import paper_scan

    decoded = []
    monkeypatch.setattr(paper_scan, "_grey", lambda img: decoded.append(img.size) or img)
    for mode in ("1", "P"):
        data = _bomb(mode=mode)
        assert len(data) < 100_000
        assert list(paper_scan.pages_from_upload("x.png", data, 10)) == [(None, "pixels")]
    assert decoded == []  # nothing was decoded


def test_only_scan_formats_reach_pillow():
    from app import paper_scan

    buf = io.BytesIO()
    Image.new("L", (50, 50), 255).save(buf, "BMP")
    with pytest.raises(paper_scan.Unreadable):
        list(paper_scan.pages_from_upload("x.png", buf.getvalue(), 10))
    with pytest.raises(paper_scan.Unreadable):
        list(paper_scan.pages_from_upload("x.pdf", b"not a pdf", 10))


def test_tiff_frames_are_read_one_by_one_and_checked_each():
    from app import paper_scan

    frames = [Image.new("L", (60, 80), 200) for _ in range(30)]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    pages = paper_scan.pages_from_upload("x.tif", buf.getvalue(), 5)
    assert sum(1 for _ in pages) == 5  # stops at the limit instead of decoding all frames first
    buf = io.BytesIO()  # a small first page, a huge second one
    Image.new("L", (60, 80), 200).save(buf, "TIFF", save_all=True, append_images=[Image.new("1", (13000, 13000), 0)])
    got = list(paper_scan.pages_from_upload("x.tif", buf.getvalue(), 10))
    assert got[0][0] is not None and got[0][0].size == (60, 80) and got[1] == (None, "pixels")


def test_large_pages_are_made_small_and_a_pdf_image_read_once(monkeypatch):
    from app import paper_scan

    big = Image.new("L", (paper_scan.MAX_SIDE * 2, 100), 128)
    buf = io.BytesIO()
    big.convert("RGB").save(buf, "JPEG")
    (img, why), = paper_scan.pages_from_upload("x.jpg", buf.getvalue(), 5)
    assert why is None and max(img.size) <= paper_scan.MAX_SIDE and img.mode == "L"
    calls = []
    real = paper_scan._pdf_image
    monkeypatch.setattr(paper_scan, "_pdf_image", lambda x: calls.append(1) or real(x))
    page = Image.new("L", (300, 400), 250)
    pdf = _pdf([page, page, page])
    got = list(paper_scan.pages_from_upload("x.pdf", pdf, 2))
    assert len(got) == 2 and all(img is not None for img, _ in got)  # the limit holds for PDFs too
    assert len(calls) <= 2


def test_a_bomb_upload_leaves_an_unreadable_page(app, client, survey):
    _setup(app, client)
    r = _upload(client, _bomb(), "bomb.png")
    assert r.status_code == 302
    with app.app_context():
        page = db.session.scalar(db.select(PaperPage))
        assert page.state == "unreadable" and page.reading == {"why": "pixels"} and not page.filename


def test_a_wrong_file_in_an_upload_stores_none(app, client, survey):
    import os

    from app import paper

    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    good = _pdf(_all_pages(s, _yes(s["plan"])))
    r = client.post("/surveys/1/paper/upload", data={"scans": [(io.BytesIO(good), "a.pdf"), (io.BytesIO(b"MZ..."), "b.png")]},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Es wurde nichts hochgeladen" in r.get_data(as_text=True)
    with app.app_context():
        inbox = paper._inbox(db.session.get(Survey, 1))
        assert not os.path.isdir(inbox) or os.listdir(inbox) == []
        assert db.session.scalar(db.select(db.func.count(PaperPage.id))) == 0


def test_two_takeovers_at_once_make_one_participation(app, client, survey, monkeypatch):
    import threading
    import time

    from app import paper

    _setup(app, client)
    client.post("/surveys/1/paper/settings", data={"review": "all", "keep": "30"})
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]))))
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "review"
        survey_row = db.session.get(Survey, 1)
        survey_row.paper = {**survey_row.paper, "review": "none"}
        db.session.commit()
    real = paper.take_over

    def slow(*args, **kwargs):
        time.sleep(0.3)  # the other thread has every chance to read the sheet as "review" meanwhile
        return real(*args, **kwargs)

    monkeypatch.setattr(paper, "take_over", slow)
    errors = []

    def settle():
        try:
            with app.app_context():
                paper.settle_locked(1, s["id"])
                db.session.remove()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=settle) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1
        assert db.session.get(PaperSheet, s["id"]).state == "done"
    assert client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take"}).status_code == 409


def test_erasing_a_sheet_needs_admin_fresh_sign_in_and_a_reason(app, client, survey, monkeypatch):
    import time

    from app import accounts

    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]))))
    url = f"/surveys/1/paper/sheets/{s['id']}/erase"
    with monkeypatch.context() as m:  # an editor may check and take over, not delete
        m.setattr(Survey, "role_of", lambda self, user: "editor")
        assert "Bogen löschen" not in client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
        client.post(url)
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "done"
    client.post(url, data={"why": "other", "why_text": "kurz"})  # a reason too short
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "done"
    with client.session_transaction() as sess:
        sess["signed_in_at"] = time.time() - accounts.FRESH_SECONDS - 5
    r = client.post(url, data={"why": "wish"})
    assert r.status_code == 302 and "/erase" not in r.headers["Location"]
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "done"
    with client.session_transaction() as sess:
        sess["signed_in_at"] = time.time()
    client.post(url, data={"why": "consent"})
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "erased"
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0


def test_ai_informed_goes_back_only_to_this_site(app, client, survey):
    _setup(app, client)
    for bad in ("https://evil.example/x", "//evil.example/x", "/\\evil.example", "javascript:alert(1)"):
        r = client.post("/surveys/1/paper/ai-informed", data={"informed": "1", "back": bad})
        assert "evil" not in r.headers["Location"] and "javascript" not in r.headers["Location"]
    r = client.post("/surveys/1/paper/ai-informed", data={"informed": "1", "back": "/surveys/1/paper/sheets/1"})
    assert r.headers["Location"].endswith("/surveys/1/paper/sheets/1")


def test_erase_hash_is_indexed(app):
    with app.app_context():
        names = {i["name"] for i in db.inspect(db.engine).get_indexes("paper_sheet")}
        assert "ix_paper_sheet_erase_hash" in names


def test_sheet_page_renders_with_ainstein_on(app, client, survey):
    """The page passes its own AI switch as paper_ai_on: a variable ai_on hid the global ai_on() of base.html, a 500
    for everyone with AInstein on."""
    _setup(app, client)
    experiments_on(client, "paper", "ainstein")
    _run(client, 1)
    s = _sheets(app)[0]
    assert client.get(f"/surveys/1/paper/sheets/{s['id']}").status_code == 200


def _pictures_setup(app, client, side=(1400, 1050)):
    """A CBC with a JPEG picture per option (as large as media.fit_still makes them), running."""
    import os

    import numpy as np

    from app.models import Asset
    from tests.analysis import test_conjoint

    _unchecked(client)
    test_conjoint.setup(client, none=True, display="both", visual=("a1",), versions=2)
    rng = np.random.default_rng(1)
    with app.app_context():
        item = db.session.get(Item, 2)
        folder = os.path.join(app.config["UPLOAD_FOLDER"], db.session.get(Survey, 1).uid)
        os.makedirs(folder, exist_ok=True)
        keys = item.kind.required_keys(item)
        for n, key in enumerate(keys):
            Image.fromarray(rng.integers(0, 255, (side[1], side[0], 3), dtype=np.uint8)).save(
                os.path.join(folder, f"cj{n}.jpg"), quality=85)
            db.session.add(Asset(item_id=item.id, key=key, filename=f"cj{n}.jpg", source="ai", status="approved"))
        db.session.commit()
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    return len(keys)


def _streams(pdf):
    """The decoded drawing of every page, image names made neutral (they are digests of the image or its file)."""
    import re

    from pypdf import PdfReader

    return [re.sub(rb"/FormXob\.[0-9a-f]+", b"/IMG", page.get_contents().get_data())
            for page in PdfReader(io.BytesIO(pdf)).pages]


def test_print_run_is_the_same_and_reads_each_picture_once(app, client, survey, monkeypatch):
    """Audit 2026-10-11 (M11, F-P1): a print run lays out each text once (wrap kept), reads each picture once, made
    small to the print resolution, and writes binary streams without ASCII85. The pages draw exactly what they drew
    before: the same operations, the same text, the pictures in the same places."""
    from pypdf import PdfReader

    from app import runtime

    pictures = _pictures_setup(app, client)
    _run(client, 6)
    pdf = client.get("/surveys/1/paper/runs/1.pdf").data
    reader = PdfReader(io.BytesIO(pdf))
    xobjects = {}
    for page in reader.pages:
        for name, ref in (page["/Resources"].get("/XObject") or {}).items():
            xobjects[ref.idnum] = ref.get_object()
    assert 0 < len(xobjects) <= pictures  # each picture embedded once, not once per sheet
    assert all(max(x["/Width"], x["/Height"]) <= 900 for x in xobjects.values())  # ~70 mm at 300 dpi, not 1400 px
    assert b"ASCII85Decode" not in pdf

    with app.app_context():
        survey_ = db.session.get(Survey, 1)
        import random

        layouts = [runtime.build_layout(survey_, random.Random(n), separate=True) for n in range(3)]
        built = [P.build_sheet(survey_, "de", lay)[0] for lay in layouts]
        P._wrapped.cache_clear()
        monkeypatch.setattr(P, "wrap", lambda text, width, size=P.SIZE, bold=False: P._wrap(text, width, size, bold))
        assert [P.build_sheet(survey_, "de", lay)[0] for lay in layouts] == built  # the kept wrap changes nothing
        monkeypatch.undo()
        sheets = [(1000 + n, pages, None) for n, pages in enumerate(built)]
        new = P.render(sheets, "Titel", "de")
        monkeypatch.setattr(P, "A85", True)
        monkeypatch.setattr(P, "PRINT_DPI", 10 ** 6)  # the pictures as they are
        old = P.render(sheets, "Titel", "de")
        monkeypatch.undo()
    assert _streams(new) == _streams(old)
    assert [p.extract_text() for p in PdfReader(io.BytesIO(new)).pages] == \
        [p.extract_text() for p in PdfReader(io.BytesIO(old)).pages]
    assert len(new) < len(old)


def test_print_run_writes_after_the_pdf_and_waits_for_a_heavy_place(app, client, survey, monkeypatch):
    """Audit 2026-10-11 (M11): no row of the run is written before its PDF is made (the write transaction stays
    short), and a print run takes one of the places for heavy evaluations."""
    from sqlalchemy import event

    from app import heavy

    _setup(app, client)
    seen = []
    render = P.render

    def note_render(*args, **kwargs):
        seen.append("render")
        return render(*args, **kwargs)

    def note_sql(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            seen.append("write")

    monkeypatch.setattr(P, "render", note_render)
    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", note_sql)
    try:
        _run(client, 3)
    finally:
        event.remove(engine, "before_cursor_execute", note_sql)
    assert seen[0] == "render" and seen.count("render") == 1 and "write" in seen
    monkeypatch.setitem(app.config, "HEAVY_PARALLEL", 1)
    monkeypatch.setitem(app.config, "HEAVY_WAIT", 0)
    place = heavy._semaphore(1)
    assert place.acquire(timeout=1)
    try:
        r = client.post("/surveys/1/paper/runs", data={"language": "de", "count": "1"})
        assert r.status_code == 503
    finally:
        place.release()
    assert client.post("/surveys/1/paper/runs", data={"language": "de", "count": "1"}).status_code == 302


def test_ai_reading_keeps_what_happened_to_the_sheet_meanwhile(app, client, survey, monkeypatch):
    """Code review 2026-10-15, D3: the AI reads for seconds without a lock; what was typed meanwhile stays, and a sheet
    erased (or thrown away) meanwhile does not get its values back."""
    import json

    from app import ai

    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    text = _field(s["plan"], "i5", k="write")
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]), writes=[text])))
    own_ai(app)
    client.post("/surveys/1/paper/ai-informed", data={"informed": "1"})
    meanwhile = {}

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None, store=False, units=1):
        db.session.execute(db.update(PaperSheet).where(PaperSheet.id == s["id"]).values(**meanwhile))
        db.session.commit()
        return json.dumps({"fields": [{"n": text, "text": "Mehr Radwege"}]})

    monkeypatch.setattr(ai, "generate_text", fake)
    meanwhile.update(values={str(text): "von Hand"})                      # typed while the AI read
    client.post(f"/surveys/1/paper/sheets/{s['id']}/read")
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).values == {str(text): "von Hand", "_ai": []}
    with app.app_context():                                                # nothing typed again
        db.session.execute(db.update(PaperSheet).where(PaperSheet.id == s["id"]).values(values={}))
        db.session.commit()
    meanwhile.update(values={}, state="erased")                            # erased while the AI read
    r = client.post(f"/surveys/1/paper/sheets/{s['id']}/read")
    assert r.status_code == 302 and "#paper" in r.headers["Location"]
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "erased" and sheet.values == {}


def test_a_sheet_printed_before_a_change_waits_and_is_matched_by_block(app, client, survey):
    """Code review 2026-10-15, J1: a block put in front after the print moved every page by one; the answers were
    put onto the pages by printed position. Now the sheet waits for a person and is matched by block and question."""
    _setup(app, client, start=False)
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    client.post("/surveys/1/editor/blocks")  # a new first block with a question of its own
    with app.app_context():
        new = max(b.id for b in db.session.get(Survey, 1).blocks)
    add(client, "text_input", block=new)
    for _ in range(3):
        client.post(f"/surveys/1/editor/blocks/{new}/move/up")
    fill_questions(app)
    with app.app_context():
        from app import runtime

        survey_ = db.session.get(Survey, 1)
        sheet = db.session.get(PaperSheet, s["id"])
        assert not runtime.layout_matches(survey_, sheet.layout)
        assert runtime.build_layout(survey_, __import__("random").Random(1))["pages"][0].get("block") != \
            sheet.layout["pages"][0].get("block")
    ticks = _yes(plan) + [_field(plan, "i2", "2"), _field(plan, "i3", "1"), _field(plan, "i4_s1", "4")]
    _upload(client, _pdf(_all_pages(s, ticks)))  # review "unsure", nothing unclear: before, taken over at once
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "review"
    page = client.get(f"/surveys/1/paper/sheets/{s['id']}").get_data(as_text=True)
    assert "nach dem Druck dieses Bogens geändert" in page
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take"})
    with app.app_context():
        sheet = db.session.get(PaperSheet, s["id"])
        assert sheet.state == "done"
        r = db.session.get(Response, sheet.response_id)
        assert r.answers["2"] == "2" and r.answers["3"] == ["1"] and r.answers["4"]["s1"] == 4
        assert runtime.layout_matches(db.session.get(Survey, 1), r.layout)


def test_a_changed_sheet_that_cannot_be_matched_is_not_taken_over(app, client, survey, monkeypatch):
    """J1: no page of today's questionnaire fits: the sheet stays in review with what was typed, nothing stored."""
    from app import paper

    _setup(app, client, start=False)
    _run(client, 1)
    s = _sheets(app)[0]
    client.post("/surveys/1/editor/blocks")
    _upload(client, _pdf(_all_pages(s, _yes(s["plan"]) + [_field(s["plan"], "i2", "2")])))
    monkeypatch.setattr(paper, "remap_forms", lambda *a: None)
    r = client.post(f"/surveys/1/paper/sheets/{s['id']}", data={"action": "take"}, follow_redirects=True)
    assert "nicht übernommen werden" in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(PaperSheet, s["id"]).state == "review"
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 0


def test_remap_forms_by_block_and_question():
    """J1: printed pages matched to today's layout by block, loop pass and question ids, never by position."""
    from app.paper import remap_forms

    old = {"pages": [{"block": 1, "items": [1]}, {"block": 2, "items": [2, 3]}, {"end": 9}]}
    new = {"pages": [{"block": 7, "items": [8]}, {"block": 1, "items": [1]}, {"block": 2, "items": [3, 2, 4]}]}
    forms = {0: {"i1": "1"}, 1: {"i2": "2", "i3_touched": "1"}}
    assert remap_forms(old, new, forms) == {1: {"i1": "1"}, 2: {"i2": "2", "i3_touched": "1"}}
    assert remap_forms(old, {"pages": [{"block": 1, "items": [1]}]}, forms) is None  # block 2 gone
    gone = {"pages": [{"block": 1, "items": [1]}, {"block": 2, "items": [3]}]}
    assert remap_forms(old, gone, forms) is None  # question 2 gone
    loop = {"pages": [{"block": 2, "items": [2], "row": "a"}]}
    assert remap_forms(loop, {"pages": [{"block": 2, "items": [2], "row": "b"}, {"block": 2, "items": [2], "row": "a"}]},
                       {0: {"i2": "1"}}) == {1: {"i2": "1"}}


def test_saving_the_paper_settings_keeps_the_ai_confirmation(app, client, survey):
    """Code review 2026-10-15, J3: the settings form replaced the whole dict and dropped ai_ok."""
    _setup(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.paper = {**(s.paper or {}), "ai_ok": {"x": {"at": "2026-10-15"}}}
        db.session.commit()
    client.post("/surveys/1/paper/settings", data={"review": "all", "keep": "0"})
    with app.app_context():
        assert db.session.get(Survey, 1).paper == {"review": "all", "keep": "0", "ai_ok": {"x": {"at": "2026-10-15"}}}
