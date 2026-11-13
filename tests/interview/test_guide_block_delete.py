"""Deleting a whole Themenblock of an interview guide, also one the AI assistant made (user feedback 2026-11-10)."""
from app.extensions import db
from app.models import Survey
from tests.interview.test_interview import blocks, make


def _guide_ids(app, sid):
    from app import interview

    with app.app_context():
        return [b.id for b in interview.guide_blocks(db.session.get(Survey, sid))]


def test_ai_block_deleted_whole(app, client):
    code, sid = make(app, client)
    from app import interview_ai

    with app.app_context():
        survey = db.session.get(Survey, sid)
        made = interview_ai.apply_blocks(survey, [{"title": "KI-Block", "prompt": "Erzählen Sie …", "aspects": [],
                                                   "keep": [], "probe": [], "minutes": 5}])
        db.session.commit()
        bid = made[0].id
    before = _guide_ids(app, sid)
    r = client.post(f"/surveys/{sid}/editor/blocks/{bid}/delete")
    assert r.status_code == 302
    after = _guide_ids(app, sid)
    assert bid not in after and len(after) == len(before) - 1


def test_short_and_post_removed_whole_and_back_with_undo(app, client):
    """Kurzfragebogen and Postskript (every template brings both) have their own bin: removed as a whole, as when
    switched off under "Einstellungen"; "Rückgängig" brings each back with its questions."""
    code, sid = make(app, client)
    page = client.get(f"/guide/{code}").get_data(as_text=True)
    assert page.count('name="action" value="remove_part"') == 2
    for which in ("short", "post"):
        r = client.post(f"/guide/{code}/control", data={"action": "remove_part", "part": which})
        assert r.status_code == 302
        assert which not in [p for p, _, _ in blocks(app, sid)]
    with app.app_context():
        st = db.session.get(Survey, sid).interview
        assert st["short"] == "off" and st["post"] is False
    client.post(f"/surveys/{sid}/editor/undo")
    rows = blocks(app, sid)
    assert [p for p, _, _ in rows][-1] == "post" and "short" not in [p for p, _, _ in rows]
    client.post(f"/surveys/{sid}/editor/undo")
    rows = blocks(app, sid)
    assert [p for p, _, _ in rows][-2:] == ["short", "post"]
    assert len(rows[-2][2]) == 4  # its questions came back with it


def test_remove_part_refuses_a_topic_block(app, client):
    code, sid = make(app, client)
    before = blocks(app, sid)
    assert client.post(f"/guide/{code}/control", data={"action": "remove_part", "part": "guide"}).status_code == 400
    assert blocks(app, sid) == before
