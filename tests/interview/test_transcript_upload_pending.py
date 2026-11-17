"""A file chosen for "Transkript hochladen" but not sent yet is named on the page (user report 2026-11-17: a
corrected transcript was chosen as a Word file and never arrived, because choosing alone uploads nothing)."""
from pathlib import Path

from tests.interview.test_interview import make
from tests.interview.test_interview_data import start


def test_the_upload_form_says_when_a_file_waits(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    page = client.get(f"/guide/{code}/interviews/{rid}/transcript").get_data(as_text=True)
    form = page[page.index('class="row-fields transcript-upload"'):]
    form = form[:form.index("</form>")]
    assert 'data-upload-pending role="status" hidden' in form and "noch nicht hochgeladen" in form
    script = (Path(app.root_path) / "static" / "app.js").read_text(encoding="utf-8")
    assert 'querySelector("[data-upload-pending]")' in script and "note.hidden = !chosen" in script
