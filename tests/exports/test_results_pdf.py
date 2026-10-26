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


def _flow_text(flowables):
    """The text of report flowables, as a PDF of them would read."""
    from reportlab.platypus import SimpleDocTemplate

    out = io.BytesIO()
    SimpleDocTemplate(out).build(list(flowables))
    return _text(out.getvalue())


def test_tables_of_the_results_page_become_apa_tables(app):
    """Round 2: hypotheses, models, segments, simulator and rarer displays are read from the results page itself; a
    table there becomes an APA table with the heading as title and the small print as note."""
    import lxml.html

    page = lxml.html.fromstring(
        '<div><section class="panel" id="hypothesen"><details><ol class="hyp-results">'
        '<li class="hyp-result"><p class="hyp-head"><strong>H1</strong> Je mehr, desto besser.</p>'
        '<p class="hyp-line"><span class="hyp-verdict">Unterstützt</span> <span class="hyp-apa"><code><i>r</i>(98) = ,30, '
        '<i>p</i> = ,002</code></span> <span class="hint"><button>?</button>n = 100</span></p></li></ol>'
        '<p class="muted small">Signifikanz bei p &lt; ,05.</p></details></section>'
        '<section id="sem"><p class="res-group-head">Pfade</p><div class="res-scroll"><table class="res-table">'
        '<thead><tr><th>Pfad</th><th>β</th></tr></thead><tbody><tr><td>A → B</td><td>0,41</td></tr></tbody></table></div>'
        '<p class="muted small">Standardisierte Pfade.</p><select><option>x</option><option selected>gewählt</option></select>'
        '</section></div>')
    root = results_pdf._readable(page)
    assert "gewählt" in results_pdf._text(root) and "?" not in results_pdf._text(root.xpath("//li")[0])
    with app.app_context():
        rep = results_pdf.Report(None, "de")
        text = _flow_text(results_pdf._hypotheses(rep, root) + results_pdf._section(rep, root, "sem", "Strukturgleichungsmodell"))
    assert "Tabelle 1" in text and "Ergebnisse je Hypothese des Studienplans" in text and "Unterstützt" in text
    assert "Je mehr, desto besser." in text and "100" in text
    assert "Tabelle 2" in text and "Pfade" in text and "Anmerkung. Standardisierte Pfade." in text and "0,41" in text


def test_diagrams_bars_and_wide_tables_of_the_page(app):
    """Round 3: an SVG diagram of the page (here a path diagram) and a bar list become numbered figures; a table wider
    than the page is split into parts with the first column again, never a failed report."""
    import lxml.html

    page = lxml.html.fromstring(
        '<div><section id="sem"><p class="res-group-head">Pfaddiagramm</p><div class="res-scroll sem-diagram">'
        '<svg viewBox="0 0 400 120" role="img"><defs><marker id="a"><path d="M0 0L10 5L0 10z"/></marker></defs>'
        '<path d="M 110 60 C 160 60, 220 60, 280 60" fill="none" class="sem-edge" marker-end="url(#a)"/>'
        '<text x="195" y="52" text-anchor="middle">0,41*</text>'
        '<g class="sem-node is-latent"><ellipse cx="60" cy="60" rx="50" ry="25"/><text x="60" y="64" '
        'text-anchor="middle">Nutzen</text></g>'
        '<g class="sem-node"><rect x="280" y="35" width="100" height="50" rx="4"/><text x="330" y="64" '
        'text-anchor="middle">Absicht</text></g>'
        '<svg class="icon" aria-hidden="true"><use href="#x"/></svg></svg></div>'
        '<p class="muted small">Standardisierte Pfade.</p>'
        '<p class="res-group-head">Segmentgrößen</p><ul class="plain res-bars lc-sizes">'
        '<li><span class="res-bar-label">Segment 1 <span class="hint">x</span></span><span class="res-bar">'
        '<span style="width: 62.5%"></span></span><span class="res-bar-n">62,5 %</span></li>'
        '<li><span class="res-bar-label">Segment 2</span><span class="res-bar"><span style="width: 37.5%"></span></span>'
        '<span class="res-bar-n">37,5 %</span></li></ul>'
        '<table class="res-table"><thead><tr>' + "".join(f"<th>S{i}</th>" for i in range(14)) + '</tr></thead><tbody><tr>'
        + "".join(f"<td>{i},5</td>" for i in range(14)) + '</tr></tbody></table></section></div>')
    root = results_pdf._readable(page)
    with app.app_context():
        rep = results_pdf.Report(None, "de")
        text = _flow_text(results_pdf._section(rep, root, "sem", "Strukturgleichungsmodell"))
    assert "Abbildung 1" in text and "Pfaddiagramm" in text and "Nutzen" in text and "Absicht" in text and "0,41*" in text
    assert "Anmerkung. Standardisierte Pfade." in text
    assert "Abbildung 2" in text and "Segmentgrößen" in text and "62,5 %" in text and "Segment 2" in text
    assert "(Teil 1 von 2)" in text and "(Teil 2 von 2)" in text and "13,5" in text  # 14 columns in two parts


def test_path_reader():
    ops = results_pdf._path_points("M10 10 l 5 0 H 30 v 10 C 1 1 2 2 3 3 s 1 1 2 2 Q 0 0 5 5 z")
    assert [o for o, _ in ops] == ["M", "L", "L", "L", "C", "C", "C", "Z"]
    assert ops[1][1] == [(15.0, 10.0)] and ops[2][1] == [(30.0, 10.0)] and ops[3][1] == [(30.0, 20.0)]
