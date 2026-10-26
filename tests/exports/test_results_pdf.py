"""The results report as PDF after APA 7 (app/results_pdf.py; Ralf 2026-10-26): numbered tables and figures with their
titles above, notes below, only the cited works in the references, a button on the export page, no free texts."""
import io

from pypdf import PdfReader

from app import results_pdf
from app.extensions import db
from app.models import Item, Response, Survey, new_pid
from tests.conftest import add, fill_consent


def _text(body):
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(body)).pages)


def _survey(app, client):
    fill_consent(client)
    add(client, "single_choice")
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        choice = db.session.scalar(db.select(Item).filter_by(type="single_choice"))
        text = db.session.scalar(db.select(Item).filter_by(type="text_input"))
        texts = dict(choice.texts or {})
        texts[s.default_language] = {**texts.get(s.default_language, {}), "question": "Wie oft fahren Sie Rad?"}
        choice.texts, choice.code, text.code = texts, "rad", "warum"
        codes = [o["code"] for o in choice.config["options"]]
        for n in range(20):
            s.responses.append(Response(pid=new_pid(s.code), source="real", is_preview=False, language="de",
                                        status="completed", answers={str(choice.id): codes[0] if n < 8 else codes[1],
                                                                     str(text.id): f"geheim {n}"}))
        db.session.commit()


def test_numbered_tables_and_figures_after_apa(app, client, survey):
    _survey(app, client)
    with app.test_request_context():
        body = results_pdf.build(db.session.get(Survey, 1), "de")
    assert body[:5] == b"%PDF-"
    text = _text(body)
    assert "Ergebnisbericht" in text and "Datengrundlage" in text and "Inhalt" in text and "Stichprobe" in text
    assert "Abbildung 1" in text and "Tabelle 1" in text and "Tabelle 2" in text  # numbered through the document
    assert "rad: Anteile der Antworten" in text and "rad: Häufigkeiten" in text
    assert "Anmerkung." in text and "Wilson, 1927" in text  # notes below with their short reference
    assert "40,0 %" in text  # shares with one decimal
    assert "Literatur" in text and "Wilson, E. B. (1927)" in text and "Cronbach" not in text  # only what is cited
    assert "geheim" not in text and "20 offene Antworten" in text  # free texts are not printed


def test_download_button_and_english_frame(app, client, survey):
    _survey(app, client)
    r = client.get("/surveys/1/export/ergebnisbericht.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:5] == b"%PDF-"
    assert "Tabelle 1" in _text(r.data)
    with app.test_request_context():
        english = _text(results_pdf.build(db.session.get(Survey, 1), "en"))
    assert "Results report" in english and "Table 1" in english and "Figure 1" in english and "Note." in english
    page = client.get("/surveys/1/data").data.decode()
    assert "/surveys/1/export/ergebnisbericht.pdf" in page and "Ergebnisbericht (PDF)" in page


def test_bounded_values_without_leading_zero(app):
    with app.test_request_context():
        rep = results_pdf.Report.__new__(results_pdf.Report)
        rep.ui = "de"
        assert rep.bounded(0.823) == ",82" and rep.ci((0.71, 0.9), bounded=True) == "[,71; ,90]"
        rep.ui = "en"
        assert rep.bounded(0.823) == ".82" and rep.ci((0.71, 0.9), bounded=True) == "[.71, .90]"
