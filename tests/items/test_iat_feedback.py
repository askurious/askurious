"""IAT-Ergebnis (app/items/iat_feedback.py): offered only with an IAT (and then marked "neu") in the palette's group "Rückmeldung", not from the
IAT's editor, the own result in words with its strength, every text editable, a run that cannot be scored, the others
from 5 completed participations on, and what is stored."""
import json

from app.extensions import db
from app.models import Item, Response
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.items.test_iat import E, _answer, _iat, _start


def _with_feedback(app, client):
    """Block 2: the IAT (item 2) in a fixed order (the answers of _answer() are built for order 1; a drawn order
    checks the answer's, audit 2026-10-12, N-12), a page break (3), the result (4) on the next page."""
    _iat(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "order": "fixed"}
        db.session.commit()
    assert add(client, "page_break").status_code == 302
    page = client.get(E + "/items/2").data.decode()
    assert "Ergebnis nach dem Test anzeigen" not in page                      # only in the palette (live feedback)
    assert client.post(E + "/blocks/2/items", data={"type": "iat_feedback"}).status_code == 302
    with app.app_context():
        return db.session.scalar(db.select(Item).filter_by(type="iat_feedback")).id


def _take(app, url, answer):
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", "i2": json.dumps(answer)})
    return p


def test_offered_only_with_an_iat_and_marked_new(app, client, survey):
    page = client.get(E + "/").data.decode()
    assert 'data-type="iat_feedback"' not in page                               # no IAT: not in the palette
    fill_consent(client)
    add(client, "iat")
    page = client.get(E + "/").data.decode()
    assert 'data-palette-new="iat_feedback"' in page and "palette-new-badge" in page
    add(client, "iat_feedback")
    assert 'data-palette-new="iat_feedback"' not in client.get(E + "/").data.decode()   # there is one now


def test_the_own_result_in_words(app, client, survey):
    fid = _with_feedback(app, client)
    with app.app_context():
        item = db.session.get(Item, fid)
        assert item.kind.validate(item) == []                                   # the last IAT before it, without choosing
        labels = item.kind.labels(db.session.get(Item, 2), "de")
    url = _start(app, client)
    p = _take(app, url, _answer(app, order=1, effect=400))                      # A with X much easier
    html = p.get(url).data.decode()
    assert (f"Sie haben <em>{labels['A']}</em> eher mit <em>{labels['X']}</em> verbunden als "
            f"<em>{labels['B']}</em>.") in html                                   # the categories in italics
    assert "ein starker Unterschied" in html
    assert "<strong>Was das bedeutet:</strong>" in html and "<strong>Was es nicht bedeutet:</strong>" in html  # one text
    assert 'class="p-iatfb-axis"' in html and "p-iat-step" not in html             # the own D on the axis -1 … +1
    assert 'class="p-iatfb-mark" style="left: 100.0%"' in html                   # D beyond +1: at the end
    assert all(f"<span>{t}</span>" in html for t in ("−1", "−0,65", "−0,35", "−0,15", "0", "+0,15", "+0,35", "+0,65", "+1"))
    assert "So verteilen sich" not in html                                      # fewer than 5 so far
    p.post(url, data={"nav": "next", f"i{fid}_value": "9", f"i{fid}_band": "1"})  # what the browser sends does not count
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        stored = r.answers[str(fid)]
        d = db.session.get(Item, 2).kind.scores(r.answers["2"])["d"]
    assert stored == {"value": round(d, 3), "band": 4}


def test_texts_are_editable_and_an_unscorable_run_is_explained(app, client, survey):
    fid = _with_feedback(app, client)
    with app.app_context():
        code = db.session.get(Item, fid).code
    client.post(E + f"/items/{fid}", data={"code": code, "question": "Dein Ergebnis", "explanation": "**Was es nicht bedeutet:** Eigener Hinweis zur Einordnung.",
                                           "result_neg": "Du verbindest {B} eher mit {X} {Name}.", "source": ""})
    url = _start(app, client)
    html = _take(app, url, _answer(app, order=1, effect=-250)).get(url).data.decode()
    assert "Dein Ergebnis" in html and "Eigener Hinweis zur Einordnung." in html and "Du verbindest" in html and "{Name}" in html
    html = _take(app, url, _answer(app, fast=True)).get(url).data.decode()       # too many answers under 300 ms
    assert "kein Ergebnis berechnen" in html


def test_the_others_from_five_participations_on(app, client, survey):
    fid = _with_feedback(app, client)
    url = _start(app, client)
    for effect in (100, 200, 300, -100, 0):
        p = _take(app, url, _answer(app, order=1, effect=effect))
        p.post(url, data={"nav": "next", f"i{fid}_value": "0", f"i{fid}_band": "1"})
    html = _take(app, url, _answer(app, order=1, effect=150)).get(url).data.decode()
    assert 'class="p-source"' not in html                                       # no "Quelle" for participants
    assert "So verteilen sich die bisherigen Teilnehmenden" in html and "p-iatfb-bar is-own" in html and "n = 5" in html
    header = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]
    with app.app_context():
        code = db.session.get(Item, fid).code
    assert f"{code}_value" in header and f"{code}_band" in header


def test_the_comparison_can_be_switched_off(app, client, survey):
    fid = _with_feedback(app, client)
    with app.app_context():
        code = db.session.get(Item, fid).code
    client.post(E + f"/items/{fid}", data={"code": code, "question": "Ihr IAT-Ergebnis", "source": ""})  # compare unticked
    with app.app_context():
        assert db.session.get(Item, fid).config["compare"] is False
    url = _start(app, client)
    for effect in (100, 200, 300, -100, 0, 150):
        html = _take(app, url, _answer(app, order=1, effect=effect)).get(url).data.decode()
    assert "So verteilen sich" not in html and "p-iatfb-bar" not in html and 'class="p-iatfb-axis"' in html


def test_older_texts_under_two_headings_are_joined(app, client, survey):
    fid = _with_feedback(app, client)
    with app.app_context():
        item = db.session.get(Item, fid)
        item.texts = {"de": {**item.texts.get("de", {}), "meaning": "Eigene Bedeutung.", "caveat": "Eigene Grenze."}}
        item.texts["de"].pop("explanation", None)
        db.session.commit()
        text = item.kind.text(item, "explanation", "de")
    assert text == "**Was das bedeutet:** Eigene Bedeutung.\n\n**Was es nicht bedeutet:** Eigene Grenze."
