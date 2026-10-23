"""The phone of a live vote loads the scripts its question needs (pre-test 2026-10-16: a heat map took no taps,
because participant-mark.js came only with the pages of a questionnaire)."""
from tests.live.test_live import add, make, steer


def test_marking_an_image_loads_its_script(app, client):
    code, sid, block = make(app, client, "Heatmap")
    add(app, client, sid, block, "image_mark", question="Wo ist es schön?")
    steer(client, code, action="start")
    page = app.test_client().get(f"/live/{code}").data.decode()
    assert "data-mark" in page or "p-mark" in page
    assert "participant-mark.js" in page

