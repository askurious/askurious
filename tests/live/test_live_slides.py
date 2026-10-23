"""Presentation slides from a PDF in a live vote (app/live/ pdf, pdf_page, pdf_clear; experimental "slides";
concept konzept-live-feedback-praesentation-2026-10-12.md, variant C): the browser draws the pages (slides_pdf.js), the
server takes the pictures as image slides, the screen shows them whole with a small QR code in the corner."""
import io

from app.extensions import db
from app.models import Survey
from tests.conftest import experiments_on
from tests.live.test_live import key, make, steer


def _picture(kind="PNG", size=(320, 180)):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(out, format=kind)
    return out.getvalue()


def _upload(client, code, n=1, kind="PNG", name="vortrag.pdf"):
    return client.post(f"/presenter/{code}/pdf/page", data={"n": str(n), "name": name, "file": (io.BytesIO(_picture(kind)), f"folie-{n}.png")},
                       content_type="multipart/form-data")


def _slides(app, sid):
    with app.app_context():
        survey = db.session.get(Survey, sid)
        return [(i.id, i.texts[survey.default_language]["alt"], len(i.assets)) for b in survey.blocks for i in b.items
                if i.type == "image" and i.config.get("slide")]


def test_the_pdf_page_for_everyone(app, client):
    """No experimental feature any more (Ralf, 2026-10-17): "Folien aus PDF" without a switch."""
    code, sid, block = make(app, client)
    assert "Folien aus PDF" in client.get(f"/presenter/{code}").data.decode()
    page = client.get(f"/presenter/{code}/pdf").data.decode()
    assert "vendor/pdfjs/pdf.min.js" in page and "vendor/pdfjs/pdf.worker.min.js" in page and "slides_pdf.js" in page


def test_pages_become_presentation_slides(app, client):
    code, sid, block = make(app, client)
    experiments_on(client, "slides")
    assert _upload(client, code, 1).json["ok"] and _upload(client, code, 2, kind="WEBP").json["ok"]
    slides = _slides(app, sid)
    assert [alt for _, alt, _ in slides] == ["Folie 1 aus vortrag.pdf", "Folie 2 aus vortrag.pdf"] and all(n == 1 for *_, n in slides)
    listing = client.get(f"/presenter/{code}").data.decode()
    assert "Präsentationsfolie" in listing and "Folie 1 aus vortrag.pdf" in listing
    r = _upload(client, code, 3, kind="JPEG")
    assert r.status_code == 400 and not r.json["ok"]                                        # drawn pictures only
    r = client.post(f"/presenter/{code}/pdf/page", data={"file": (io.BytesIO(b"%PDF-1.7 ..."), "x.pdf")}, content_type="multipart/form-data")
    assert r.status_code == 400                                                               # the server reads no PDF


def test_the_screen_shows_a_slide_whole_with_the_qr_corner(app, client):
    code, sid, block = make(app, client)
    experiments_on(client, "slides")
    _upload(client, code)
    first = _slides(app, sid)[0][0]
    steer(client, code, action="start")
    s = client.get(f"/presenter/{code}/result.json").json
    assert s["current"]["kind"] == "slide" and s["current"]["image"].startswith("/media/") and s["corner"]
    assert s["current"]["alt"] == "Folie 1 aus vortrag.pdf"
    assert "data-qr-corner" in client.get(f"/presenter/{code}/screen").data.decode()
    steer(client, code, action="corner_off")
    assert client.get(f"/presenter/{code}/result.json").json["corner"] is False
    steer(client, code, action="corner_on")
    assert client.get(f"/presenter/{code}/result.json").json["corner"] is True
    p = app.test_client()
    assert key(p, code) == f"look-{first}"                                                    # the phones look up
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.live = {**survey.live, "slide_phone": True}
        db.session.commit()
    assert key(p, code) == f"slide-{first}"
    assert 'class="live-phone-slide"' in p.get(f"/live/{code}").data.decode()


def test_the_presenter_view_shows_the_next_slide(app, client):
    """"Als Nächstes" in the presenter view (concept live-naechste-folie-konzept.md): the next slide as its picture;
    the projector gets no preview, only the picture to load ahead (E7, E8)."""
    code, sid, block = make(app, client)
    _upload(client, code, 1)
    _upload(client, code, 2)
    steer(client, code, action="start")
    nxt = client.get(f"/presenter/{code}/result.json?view=speaker").json["next"]
    assert nxt["kind"] == "slide" and nxt["image"].startswith("/media/") and nxt["alt"] == "Folie 2 aus vortrag.pdf"
    assert nxt["type"] == "Präsentationsfolie"
    projector = client.get(f"/presenter/{code}/result.json").json
    assert "next" not in projector and projector["preload"] == nxt["image"]
    view = client.get(f"/presenter/{code}/speaker").data.decode()
    assert "data-next-box" in view and "data-resize" in view and "view=speaker" in view
    assert '<div class="live-qr" hidden>' in view and "data-qr-toggle" in view          # no QR code of its own, a button
    assert "data-qr-toggle" not in client.get(f"/presenter/{code}/screen").data.decode()
    screen = client.get(f"/presenter/{code}/screen").data.decode()
    assert "data-next-box" not in screen and "data-resize" not in screen and "view=speaker" not in screen


def test_replacing_the_slides(app, client):
    code, sid, block = make(app, client)
    experiments_on(client, "slides")
    for n in (1, 2, 3):
        _upload(client, code, n)
    assert "Bisherige Präsentationsfolien ersetzen" in client.get(f"/presenter/{code}/pdf").data.decode()
    assert client.post(f"/presenter/{code}/pdf/clear").json == {"ok": True, "removed": 3}
    assert _slides(app, sid) == []
    _upload(client, code, 1, name="neu.pdf")
    assert [alt for _, alt, _ in _slides(app, sid)] == ["Folie 1 aus neu.pdf"]


def test_the_review_shows_a_slide_with_its_picture(app, client):
    code, sid, block = make(app, client)
    experiments_on(client, "slides")
    _upload(client, code)
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")
    assert p.post(f"/live/{code}/reaction", data={"e": "👍"}).json["ok"]
    steer(client, code, action="review_on")
    row = client.get(f"/presenter/{code}/result.json").json["current"]["rows"][0]
    assert row["title"] == "Folie 1" and row["image"].startswith("/media/") and row["emojis"] == [["👍", 1]]


def test_slides_are_no_experimental_feature(app, client):
    from app import experimental

    assert "slides" not in experimental.KEYS
    code, sid, block = make(app, client)
    experiments_on(client)                                                                  # nothing switched on
    assert _upload(client, code).json["ok"] and client.get(f"/presenter/{code}/pdf").status_code == 200


def test_slides_reach_the_phone_in_blocks_too(app, client):
    """Ralf, 2026-10-16: in a vote "In Blöcken" the phone said "Gleich geht es los" while the screen was on slide 3."""
    code, sid, block = make(app, client)
    experiments_on(client, "slides")
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.live = {**survey.live, "flow": "blocks"}
        db.session.commit()
    assert _upload(client, code, 1).json["ok"] and _upload(client, code, 2).json["ok"]
    second = _slides(app, sid)[1][0]
    steer(client, code, action="screen", item=second)                                       # no block runs
    p = app.test_client()
    assert key(p, code) == f"look-{second}"                                                  # look up, not "starting soon"
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.live = {**survey.live, "slide_phone": True}
        db.session.commit()
    assert key(p, code) == f"slide-{second}"
    assert 'class="live-phone-slide"' in p.get(f"/live/{code}").data.decode()


def test_slides_survive_a_survey_zip(app, client):
    """Pre-test 2026-10-20, finding 13: a live vote with slides from a PDF, exported as ZIP and read in as a new
    survey, showed a broken picture "Folie 1 aus TEST PDF FOLIEN.pdf" on the screen. The slides and their pictures
    come along, and the media address of each serves the picture."""
    from tests.exports.test_transfer import _upload as read_in

    code, sid, block = make(app, client)
    assert _upload(client, code, 1, name="TEST PDF FOLIEN.pdf").json["ok"]
    assert _upload(client, code, 2, kind="WEBP", name="TEST PDF FOLIEN.pdf").json["ok"]
    r = client.get(f"/surveys/{sid}/export/survey.zip")
    assert r.status_code == 200
    read_in(client, None, raw=r.data, name="live.zip", title="Aus ZIP")
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="Aus ZIP"))
        assert new is not None and new.id != sid
        slides = [i for b in new.blocks for i in b.items if i.type == "image" and i.config.get("slide")]
        names = [a.filename for i in slides for a in i.assets]
        uid, new_code = new.uid, new.code
        assert [i.texts[new.default_language]["alt"] for i in slides] == ["Folie 1 aus TEST PDF FOLIEN.pdf",
                                                                           "Folie 2 aus TEST PDF FOLIEN.pdf"]
    assert len(names) == 2
    for name in names:
        got = client.get(f"/media/{uid}/{name}")
        assert got.status_code == 200 and got.data, name
    listing = client.get(f"/presenter/{new_code}").data.decode()
    assert "Folie 1 aus TEST PDF FOLIEN.pdf" in listing
    steer(client, new_code, action="start")
    s = client.get(f"/presenter/{new_code}/result.json").json
    assert s["current"]["kind"] == "slide" and s["current"]["image"] == f"/media/{uid}/{names[0]}"


def test_a_reviewed_ai_slide_keeps_its_approved_picture_through_a_survey_zip(app, client):
    """Pre-test 2026-10-20, finding 13, as on the server: the slide's picture was one of seven generated images (one
    approved, three rejected, three waiting). Read in again, all seven waited for approval, the slide had no
    approved picture and the screen stayed blank. The review comes along as the file has it."""
    from app import media
    from app.models import Asset
    from tests.exports.test_transfer import _upload as read_in

    code, sid, block = make(app, client)
    assert _upload(client, code, 1, name="TEST PDF FOLIEN.pdf").json["ok"]
    with app.test_request_context():
        survey = db.session.get(Survey, sid)
        item = next(i for b in survey.blocks for i in b.items if i.type == "image")
        item.config = {**item.config, "source": "ai"}
        item.assets[0].source = "ai"
        for status in [Asset.REJECTED] * 3 + [Asset.PENDING] * 3:
            item.assets.append(Asset(key="", filename=media.save_bytes(survey, _picture()), source="ai", status=status))
        db.session.commit()
    read_in(client, None, raw=client.get(f"/surveys/{sid}/export/survey.zip").data, name="live.zip", title="KI-Folien")
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="KI-Folien"))
        slide = next(i for b in new.blocks for i in b.items if i.type == "image")
        assert sorted(a.status for a in slide.assets) == sorted([Asset.APPROVED] + [Asset.REJECTED] * 3 + [Asset.PENDING] * 3)
        approved = next(a.filename for a in slide.assets if a.status == Asset.APPROVED)
        uid, new_code = new.uid, new.code
    steer(client, new_code, action="start")
    assert client.get(f"/presenter/{new_code}/result.json").json["current"]["image"] == f"/media/{uid}/{approved}"


def test_audience_questions_below_or_beside_the_slide(app, client):
    """The presenter view puts the questions from the audience below the slide or beside it, with a border to drag
    (Ralf, 2026-10-23; live.js keeps the choice in the browser); the projector keeps its own column."""
    code, sid, block = make(app, client)
    speaker = client.get(f"/presenter/{code}/speaker").data.decode()
    assert "data-qa-place" in speaker and "data-qa-grip" in speaker and 'aria-orientation="horizontal"' in speaker
    projector = client.get(f"/presenter/{code}/screen").data.decode()
    assert "data-qa-place" not in projector and "data-qa-grip" not in projector and "data-qa-side-list" in projector


def test_the_next_question_shows_every_option(app, client):
    """"Als Nächstes" lists every answer option of the next question, not only the first three (Ralf, 2026-10-23)."""
    from tests.live.test_live import add

    code, sid, block = make(app, client)
    add(app, client, sid, block, "info", text="Start")
    labels = ["eins", "zwei", "drei", "vier", "fünf", "sechs"]
    add(app, client, sid, block, "single_choice", question="Welche?", options=labels)
    steer(client, code, action="start")
    nxt = client.get(f"/presenter/{code}/result.json?view=speaker").json["next"]
    assert nxt["kind"] == "question" and nxt["options"] == labels and nxt["more"] == 0


def test_presenting_keeps_the_display_awake(app, client):
    """No standby or dimming while presenting (Ralf, 2026-10-23): the presenter view and the audience view keep the
    display awake; "Präsentieren" only while its full screen shows the audience view on the second screen."""
    code, sid, block = make(app, client)
    for page in ("speaker", "screen"):
        assert 'live_wake.js' in client.get(f"/presenter/{code}/{page}").data.decode()
    assert 'data-wake="fullscreen"' in client.get(f"/presenter/{code}/present").data.decode()


def test_the_phone_slides_tick_stays_its_own_checkbox(app, client):
    """Bug (Ralf, 2026-10-23): with the tick off its label vanished and its open input tag swallowed the next one,
    so "Kurze Texte und Fragen erst nach Freigabe zeigen" showed two boxes."""
    code, sid, block = make(app, client)
    for on in (False, True):
        with app.app_context():
            survey = db.session.get(Survey, sid)
            survey.live = {**survey.live, "slide_phone": on}
            db.session.commit()
        html = client.get(f"/presenter/{code}/settings").data.decode()
        assert "Präsentationsfolien auch auf den Handys zeigen" in html
        assert ('name="slide_phone" value="1" checked>' in html) == on
        assert 'name="slide_phone" value="1" >' in html or on


def test_the_presenter_controls_sit_side_by_side_without_the_address(app, client):
    """Ralf, 2026-10-23: ↺ Zurück Weiter in one row, the other buttons in one grid, "Beenden" last; no address line."""
    code, sid, block = make(app, client)
    steer(client, code, action="start")
    page = client.get(f"/presenter/{code}/speaker").data.decode()
    assert '<p class="live-joinurl" hidden>' in page                         # kept only for the large QR view
    grid = page.split('class="live-steer-grid"')[1].split("</div>\n      </div>")[0]
    for mark in ('data-toggle="voting"', "data-qr-toggle", 'data-toggle="corner"', "data-ink-open", 'data-toggle="board"', 'data-toggle="review"'):
        assert mark in grid and grid.index(mark) < grid.index('data-step="end"')
    row = page.split('class="live-steer live-steer-restart"')[1].split("</div>")[0]
    assert "data-restart" in row and 'data-step="prev"' in row and 'data-step="next"' in row
