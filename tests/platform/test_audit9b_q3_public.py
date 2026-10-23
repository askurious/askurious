"""Audit 2026-10-09b, package Q3 (public levers): O1 images on comments, O2 plain() linear and a length limit for
question texts, O4 no-store on the public sign-in forms, V2 the quota of "Höchstzahl je Link" not on shared links."""
import io
import secrets
import time
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Board, BoardShare, Item, Quota, QuotaCell, QuotaShare, Survey, utcnow
from tests.conftest import add
from tests.editor.test_comment_shots import _phone_path, _png, _send, _setup


# --- O2 ---------------------------------------------------------------------------------------------------------


def test_o2_plain_is_linear_and_never_returns_markup():
    from app.items.base import plain

    nested = "<sub>"
    for _ in range(8000):
        nested = "<su" + nested + "b>"
    assert len(nested) >= 40_000
    started = time.process_time()
    out = plain(nested)
    assert time.process_time() - started < 0.1
    assert "<sub>" not in out and "<su" not in out
    # marks that form a tag only once others are gone, and ordinary angle brackets in a text
    assert "<sub>" not in plain("<**s**ub>x</**s**ub>")
    assert plain("x < 5 und y > 3") == "x < 5 und y > 3"
    assert plain("<u>heute</u> H~2~O") == "heute H2O"


def test_o2_question_text_has_a_length_limit_in_the_editor(app, client, survey):
    from app.editor import QUESTION_MAX

    add(client, "info")
    url = "/surveys/1/editor/items/2"
    assert client.post(url, data={"code": "intro", "question": "Kurz"}).status_code == 302
    r = client.post(url, data={"code": "intro", "question": "x" * (QUESTION_MAX + 1), "autosave": "1"})
    assert r.json["status"] == "invalid" and "zu lang" in r.json["message"]
    r = client.post(url, data={"code": "intro", "question": "x" * (QUESTION_MAX + 1)}, follow_redirects=True)
    assert "zu lang" in r.data.decode()
    with app.app_context():
        assert db.session.get(Item, 2).text("question") == "Kurz"
    client.post(url, data={"code": "intro", "question": "y" * QUESTION_MAX})
    with app.app_context():
        assert db.session.get(Item, 2).text("question") == "y" * QUESTION_MAX


# --- O1 ---------------------------------------------------------------------------------------------------------


def _webp(width, height):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height)).save(out, "WEBP", lossless=True)
    return out.getvalue()


def _no_decoding(monkeypatch):
    from app import comment_shots

    def boom(img):
        raise AssertionError("decoded")
    monkeypatch.setattr(comment_shots, "_decoded", boom)


def test_o1_too_many_pixels_are_refused_before_decoding(app, client, survey, monkeypatch):
    _setup(client)
    phone = _phone_path(client)
    _no_decoding(monkeypatch)
    small = _webp(4500, 4000)  # 18 megapixels in a few KB
    assert len(small) < 50_000
    r = _send(app.test_client(), phone + "/comment", image=small, name="a.webp")
    assert r.status_code == 400 and "zu viele Bildpunkte" in r.json["error"]
    r = _send(client, "/surveys/1/editor/preview/comment", image=small, name="a.webp")
    assert r.status_code == 400 and "zu viele Bildpunkte" in r.json["error"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(db.text("preview_comment"))) == 0


def test_o1_at_most_two_decodings_at_once(app, client, survey, monkeypatch):
    from app import comment_shots

    _setup(client)
    phone = _phone_path(client)
    monkeypatch.setattr(comment_shots, "DECODE_WAIT", 0)
    assert comment_shots.DECODES_AT_ONCE == 2
    assert comment_shots._decoding.acquire(timeout=1) and comment_shots._decoding.acquire(timeout=1)
    try:
        r = _send(app.test_client(), phone + "/comment", image=_png())
        assert r.status_code == 503 and "erneut senden" in r.json["error"]
    finally:
        comment_shots._decoding.release()
        comment_shots._decoding.release()
    assert _send(app.test_client(), phone + "/comment", image=_png()).status_code == 200


def test_o1_images_per_sender_are_counted_before_decoding(app, client, survey, monkeypatch):
    from app import comment_shots, limits

    _setup(client)
    phone = _phone_path(client)
    app.config["COMMENT_SHOT_SENDER_LIMIT"] = 2
    monkeypatch.setattr(limits, "_shot_senders", {})
    guest = app.test_client()
    sender = {"REMOTE_ADDR": "203.0.113.77"}

    def send(image):
        form = {"block_id": "2", "item_id": "2", "page": "2", "text": "Bricht um", "shot": (io.BytesIO(image), "b.png")}
        return guest.post(phone + "/comment", data=form, content_type="multipart/form-data", environ_base=sender)

    assert send(_png()).status_code == 200 and send(_png()).status_code == 200
    _no_decoding(monkeypatch)
    r = send(_png())
    assert r.status_code == 429 and "sehr viele Bilder" in r.json["error"]
    # a comment without an image still goes through
    form = {"block_id": "2", "item_id": "2", "page": "2", "text": "ohne Bild"}
    assert guest.post(phone + "/comment", data=form, environ_base=sender).status_code == 200
    assert comment_shots.MAX_PIXELS == 16_000_000


# --- O4 ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/auth/sign-in", "/auth/forgot-password", "/verify/"])
def test_o4_public_forms_are_not_stored(app, path):
    r = app.test_client().get(path)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"


# --- V2 ---------------------------------------------------------------------------------------------------------


def _capped(app, client):
    """A survey with links per person (w = Anna, Ben, Cem) limited per link, a further quota and the dashboard
    "Feldphase"; returns the three codes."""
    app.config["BOARD_CHECK_SECONDS"] = 0
    add(client, "single_choice")
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna\nBen\nCem"})
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "5"})
    with app.app_context():
        from app import panel

        survey = db.session.get(Survey, 1)
        survey.quotas.append(Quota(code="gender", name="Geschlecht", cells=[
            QuotaCell(code="w", name="Frauen", target=20, done=5), QuotaCell(code="m", name="Männer", target=20, done=2)]))
        db.session.commit()
        codes = next(p for p in panel.config(survey)["params"] if p["name"] == "w")["allowed"]
        assert panel.cap_quota(survey, "w") is not None
    client.post("/surveys/1/dashboards/new", data={"template": "field"})
    return codes


def test_v2_dashboard_link_leaves_out_the_quota_of_the_links(app, client, survey):
    codes = _capped(app, client)
    with app.app_context():
        board = db.session.get(Board, 1)
        cap = next(q.id for q in board.survey.quotas if q.code.startswith("links_"))
        gender = next(q.id for q in board.survey.quotas if q.code == "gender")
        board.tiles = [*board.tiles, {"key": f"quota:{gender}"}] if not any(
            t["key"] == f"quota:{gender}" for t in board.tiles) else board.tiles
        assert any(t["key"] == f"quota:{cap}" for t in board.tiles)
        db.session.commit()
    for hall in (False, True):
        with app.app_context():
            share = BoardShare(board_id=1, token=secrets.token_urlsafe(24), label="Kurs", hall=hall, revoked=False,
                               opens=0, expires_at=utcnow() + timedelta(days=30))
            db.session.add(share)
            db.session.commit()
            token = share.token
        guest = app.test_client()
        seen = guest.get(f"/d/{token}").get_data(as_text=True) + guest.get(f"/d/{token}/state").get_data(as_text=True)
        assert f'data-key="quota:{cap}"' not in seen and not any(code in seen for code in codes)
        assert f'data-key="quota:{gender}"' in seen and "Geschlecht" in seen
    # the members' own view keeps it
    page = client.get("/surveys/1/dashboards/1").get_data(as_text=True)
    assert f'data-key="quota:{cap}"' in page and all(code in page for code in codes)


def test_v2_quota_link_leaves_out_the_quota_of_the_links(app, client, survey):
    codes = _capped(app, client)
    with app.app_context():
        share = QuotaShare(survey_id=1, token=secrets.token_urlsafe(24), label="Panel", show_conds=True,
                           expires_at=utcnow() + timedelta(days=30))
        db.session.add(share)
        db.session.commit()
        token = share.token
    guest = app.test_client()
    page = guest.get(f"/q/{token}").get_data(as_text=True)
    body = guest.get(f"/q/{token}/state.csv").data.decode("utf-8-sig")
    for seen in (page, body):
        assert "Geschlecht" in seen and "Frauen" in seen
        assert "Höchstzahl je Link" not in seen and not any(code in seen for code in codes)
    # the researchers still see it in the app
    inside = client.get("/surveys/1/flow/quotas/state.csv").data.decode("utf-8-sig")
    assert "Höchstzahl je Link" in inside and all(code in inside for code in codes)
