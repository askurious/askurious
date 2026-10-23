"""Hardening of the security audit 2026-10-04: H1 (kept results name allowed classes only), H2 (more security
headers), H9 (contact and closing text of a live vote stay with its admins)."""
import io
import os
import pickle

from app import results_store
from app.extensions import db
from app.models import Survey
from tests.live.test_live import make

OTHER = "0000-0002-1825-0097"


class Boom:
    """Unpickling this calls os.system: the classic payload of a pickle placed where a program reads one."""

    def __reduce__(self):
        return (os.system, ("echo pwned",))


def _write_signed(survey, body):
    path = results_store._path(survey)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(results_store._signature(body) + body)


def test_kept_results_name_allowed_classes_only(app, client, survey, monkeypatch):
    head = {"format": results_store.FORMAT, "key": "k", "at": None}
    boom = pickle.dumps({**head, "data": Boom()}, protocol=pickle.HIGHEST_PROTOCOL)
    walk = pickle.dumps({**head, "data": _GetattrPayload()}, protocol=pickle.HIGHEST_PROTOCOL)
    calls = []
    monkeypatch.setattr(os, "system", lambda cmd: calls.append(cmd) or 0)  # caught if it were ever called
    with app.app_context():
        s = db.session.get(Survey, 1)
        # a file signed with the key (as if the key had become known) that names os.system: not run, not read
        _write_signed(s, boom)
        assert results_store.load(s, "k") is None and calls == []
        # getattr only for the one use in the results, not to walk from an allowed class elsewhere
        _write_signed(s, walk)
        assert results_store.load(s, "k") is None
        # what the results really hold is read back
        from datetime import datetime
        from zoneinfo import ZoneInfo

        import numpy

        data = {"n": numpy.float64(2.5), "when": datetime(2026, 10, 4, tzinfo=ZoneInfo("Europe/Berlin")),
                "arr": numpy.array([1, 2]), "t": numpy.dtype("int64")}
        assert results_store.save(s, "k2", data)
        kept, _ = results_store.load(s, "k2")
        assert kept["n"] == 2.5 and kept["when"].tzinfo == ZoneInfo("Europe/Berlin") and list(kept["arr"]) == [1, 2]


class _GetattrPayload:
    def __reduce__(self):
        from datetime import datetime

        return (getattr, (datetime, "now"))  # from an allowed class on to another attribute


def test_more_security_headers(app, client):
    h = client.get("/").headers
    assert "camera=()" in h["Permissions-Policy"] and "geolocation=()" in h["Permissions-Policy"]
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "form-action 'self'" in h["Content-Security-Policy"] and "javascript:" not in h["Content-Security-Policy"]


def test_live_contact_and_closing_text_stay_with_admins(app, client):
    code, sid, block = make(app, client)
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "contact_email": "ich@example.org",
                                                    "end_text": "Danke!"})
    client.post(f"/surveys/{sid}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "editor"})
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    page = other.get(f"/presenter/{code}/settings").data.decode()
    assert "Kontakt und Text der Endseite ändern nur Admins" in page
    other.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "contact_email": "fremd@example.org",
                                                   "end_text": "Gekapert", "qa": "1"})
    with app.app_context():
        s = db.session.get(Survey, sid)
        assert s.live["contact_email"] == "ich@example.org" and s.end_texts[s.default_language]["thanks"] == "Danke!"
        assert s.live["qa"] is True                                             # the presenter's options stay hers
