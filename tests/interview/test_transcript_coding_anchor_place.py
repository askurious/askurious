"""Interviews kodieren: where an anchor example stands is shown only to those who see its interview (Ralf 2026-11-20
after the audit of that day); the quote itself belongs to the category system and stays for everyone who codes."""
import io
import zipfile

from app.extensions import db
from tests.interview.test_transcript_coding_coder_exports import FILES, _coder, _set
from tests.interview.test_transcript_coding_review import _code, _setup, _sub


def test_an_anchor_from_an_interview_the_link_does_not_see_keeps_its_quote_not_its_place(app, client):
    """Audit 2026-11-20 (Ralf: quote yes, place no): the anchor example belongs to the category system, so a link with
    "eigene Interviews" reads it; where it stands (the other interview's label and position) it does not, on the
    page, in the coding guide and in every file. The team reads the place as before."""
    code, pid, rid, base, main, bea = _setup(app, client)
    sub = _sub(client, base, main, "Erschöpfung")
    cid = _code(client, base, rid, "Abends", sub, 29)                              # in Anna's interview
    assert client.post(f"{base}/i/{rid}/codings/{cid}/anchor").status_code == 200
    with app.app_context():
        from app.models import Response

        label = db.session.get(Response, rid).interview["label"]
    coder, mine = _coder(app, client, code, base)
    _set(client, base, exports=True, hidden=True)

    def text_of(response):
        assert response.status_code == 200
        try:
            with zipfile.ZipFile(io.BytesIO(response.data)) as z:
                return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
        except zipfile.BadZipFile:
            return response.data.decode("utf-8", "replace")

    for url in FILES + ("kodierleitfaden.docx", "?view=categories"):
        sep = "" if url.startswith("?") else "/"
        seen = text_of(coder.get(f"{base}{sep}{url}"))
        assert "Abends bin ich" in seen, url                                       # the quote
        assert f"({label}" not in seen and f"{label}, Pos." not in seen, url       # not where it stands
        team = text_of(client.get(f"{base}{sep}{url}"))
        assert "Abends bin ich" in team and label in team, url
    spots = coder.get(f"{base}/i/{mine}").data.decode()
    assert f"{label}, Pos." not in spots
