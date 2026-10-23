import csv
import io
import re

from app.extensions import db
from app.models import PreviewComment
from tests.conftest import add


def _setup(client):
    add(client, "info")                                  # 2
    client.post("/surveys/1/editor/items/2", data={"code": "intro", "question": "Willkommen"})
    add(client, "page_break")                            # 3
    add(client, "info")                                  # 4


def test_comment_flag_handle_and_export(app, client, survey):
    _setup(client)
    html = client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    assert "data-comment-form" in html and '<option value="2">intro' in html
    r = client.post("/surveys/1/editor/preview/comment", data={"block_id": "2", "item_id": "2", "page": "2", "text": "Unklar"})
    assert r.status_code == 200 and r.json["target"].endswith("intro") and r.json["author"] == "Philipp"
    client.post("/surveys/1/editor/preview/comment", data={"block_id": "2", "page": "2", "text": "Seite zu lang"})
    assert client.post("/surveys/1/editor/preview/comment", data={"block_id": "2", "text": " "}).status_code == 400
    assert client.post("/surveys/1/editor/preview/comment", data={"block_id": "99", "text": "x"}).status_code == 400
    assert "Seite zu lang" in client.get("/surveys/1/editor/blocks/2/preview", follow_redirects=True).data.decode()
    editor = client.get("/surveys/1/editor/").data.decode()
    assert "💬 1</a>" in editor and "💬 2 offen" in editor and "has-comments" in editor
    with app.app_context():
        cid = db.session.scalar(db.select(PreviewComment.id).filter_by(text="Unklar"))
    client.post(f"/surveys/1/editor/comments/{cid}", data={"status": "resolved", "note": "Text gekürzt"})
    page = client.get("/surveys/1/editor/comments").data.decode()
    assert "Seite zu lang" in page and "Unklar" not in page                     # only open ones by default
    assert "Text gekürzt" in client.get("/surveys/1/editor/comments?show=all").data.decode()
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/comments.csv").data.decode("utf-8-sig"))))
    assert [r["status"] for r in rows] == ["resolved", "open"] and rows[0]["note"] == "Text gekürzt"
    assert rows[0]["handled_by"] == "Philipp" and rows[0]["block_item"].endswith("intro")


def test_testers_delete_own_comments_only_during_the_run(app, client, survey):
    _setup(client)
    html = client.get("/surveys/1/editor/preview?device=mobile").data.decode()
    assert "data-comment-form" in html                                          # next to the phone
    path = "/" + re.search(r'value="(https?://[^"]+/p/[^"]+)"', html).group(1).split("://", 1)[1].split("/", 1)[1]
    phone = app.test_client()                                                   # a test person, not logged in
    page = phone.get(path + "?page=2", follow_redirects=True).data.decode()
    assert 'name="name"' in page
    mine = phone.post(path + "/comment", data={"block_id": "2", "page": "2", "text": "Knopf zu klein", "name": "Anna"}).json
    assert mine["author"] == "Anna"
    other = client.post("/surveys/1/editor/preview/comment", data={"block_id": "2", "text": "von jemand anderem"}).json
    assert phone.post(f"{path}/comment/{other['id']}/delete").status_code == 403   # not theirs
    phone.get(path + "?restart=1")                                              # a new test run
    assert phone.post(f"{path}/comment/{mine['id']}/delete").status_code == 403
    again = phone.post(path + "/comment", data={"block_id": "2", "text": "zweiter Versuch"}).json
    assert phone.post(f"{path}/comment/{again['id']}/delete").status_code == 200
    with app.app_context():
        assert sorted(c.text for c in db.session.scalars(db.select(PreviewComment))) == ["Knopf zu klein", "von jemand anderem"]
        assert db.session.scalar(db.select(PreviewComment.via).filter_by(text="Knopf zu klein")) == "phone"


def test_the_comment_button_says_how_many_comments_there_are(app, client, survey):
    """UI/UX audit 2026-10-17, W-A07: the number on the comment button is part of its accessible name, not only a
    small figure beside the word."""
    _setup(client)
    url = "/surveys/1/editor/blocks/2/preview"
    html = client.get(url, follow_redirects=True).data.decode()
    toggle = re.search(r"<button[^>]*data-comments-toggle[^>]*>", html).group(0)
    assert 'aria-label="Kommentar"' in toggle
    for text in ("Unklar", "Seite zu lang", "Fehlt etwas"):
        client.post("/surveys/1/editor/preview/comment", data={"block_id": "2", "page": "2", "text": text})
    html = client.get(url, follow_redirects=True).data.decode()
    toggle = re.search(r"<button[^>]*data-comments-toggle[^>]*>.*?</button>", html, re.S).group(0)
    assert 'aria-label="Kommentare (3)"' in toggle and '<span class="p-comments-count" aria-hidden="true">3</span>' in toggle
    bar = re.search(r"<button[^>]*data-comments-bar[^>]*>", html).group(0)                 # the bar of a narrow page
    assert 'aria-label="Kommentare (3)"' in bar
