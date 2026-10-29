"""Listening while correcting a transcript (app/static/transcript_player.js, templates/interview/transcript.html;
Ralf 2026-10-29): the bar only while the recording is there, the paragraphs counted as in the coding view
(app/transcript_coding_view.py, paragraphs), the start of a paragraph from the time mark before it, the right part
of a recording in several parts, and F4/Esc with the tempo buttons on the page."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Response
from app.transcript_coding_view import paragraphs
from tests.interview.test_interviewer_rights import _urls
from tests.interview.test_transcript_coding import _guide, _transcript
from tests.interview.test_interviewer import add, begin, open_link

NODE = shutil.which("node")
TEXT = "I: Wie war das? #00:00:04-0#\n\nB: Anstrengend, jeden Abend. #00:14:32-0#\n\nI: Und dann?\n\nB: Weiter. #00:20:00-0#"


def _cases(calls):
    script = Path(__file__).parents[1] / "transcript_player_cases.js"
    out = subprocess.run([NODE, str(script)], input=json.dumps(calls), capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_paragraphs_starts_and_parts_with_node():
    single = "B: eins\nI: zwei #00:01:00-0#\nB: drei"
    para, para1, at3, at4, first, part, early, clock = _cases([
        ["paragraphs", TEXT], ["paragraphs", single], ["at", TEXT, TEXT.index("Und dann")], ["at", TEXT, TEXT.index("Weiter")],
        ["at", TEXT, 0], ["part", [0, 600], 872], ["part", [0, 600], 10], ["clock", 872]])
    assert para == paragraphs(TEXT) and para1 == paragraphs(single)               # the same count as the exports
    assert at3 == {"n": 3, "start": 872, "end": None}                             # starts where the one before ended
    assert at4 == {"n": 4, "start": None, "end": 1200}                            # the one before has no mark
    assert first == {"n": 1, "start": 0, "end": 4}
    assert part == [1, 272] and early == [0, 10] and clock == "00:14:32"


def test_bar_only_with_the_recording(app, client):
    code, sid = _guide(app, client)
    c, _ = open_link(app, code, add(client, code, "Anna"))
    rid = begin(c, code)
    _transcript(app, rid, TEXT)
    url = _urls(app, code, rid)["transcript"]
    page = client.get(url).data.decode()
    assert "data-tp" not in page and "transcript_player.js" not in page            # no recording: nothing of it
    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**row.interview, "audio": [{"part": 1, "file": "teil-1.webm", "mime": "audio/webm", "start": 0},
                                                    {"part": 2, "file": "teil-2.webm", "mime": "audio/webm", "start": 600}]}
        db.session.commit()
    page = client.get(url).data.decode()
    assert page.count("data-tp-part") == 2 and 'data-start="600"' in page and "transcript_player.js" in page
    assert "Hier abspielen" in page and 'data-tp-rate="0.75"' in page and 'data-tp-rate="1.25"' in page
    assert "F4 oder Esc" in page
    assert "Hier abspielen" in c.get(url).data.decode()                            # the interviewer too
