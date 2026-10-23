import csv
import io

from app import paradata
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
IPAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
WIN_EDGE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36 Edg/129.0"
ANDROID_TAB = "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"


def test_coarse_categories():
    assert paradata.collect(IPHONE, {"_pd_vw": "390", "_pd_or": "portrait", "_pd_touch": "1"}) == {
        "device": "mobile", "os": "iOS", "browser": "Safari", "viewport": "<480", "orientation": "portrait", "touch": 1}
    assert paradata.collect(IPAD, {"_pd_vw": "1024", "_pd_touch": "1"})["device"] == "tablet"  # iPadOS looks like macOS
    assert paradata.collect(IPAD, {"_pd_vw": "1440", "_pd_touch": "0"}) == {
        "device": "desktop", "os": "macOS", "browser": "Safari", "viewport": ">=1440", "orientation": "", "touch": 0}
    d = paradata.collect(WIN_EDGE, {"_pd_vw": "1280"})
    assert (d["os"], d["browser"], d["viewport"]) == ("Windows", "Edge", "1024-1439")
    assert paradata.collect(ANDROID_TAB, {"_pd_vw": "800", "_pd_touch": "1"})["device"] == "tablet"


def test_recorded_once_exported_and_disclosed(app, client, survey):
    fill_consent(client)
    add(client, "info")
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de"], "default_language": "de",
                                          "allow_back": "1", "paradata": "1"})
    client.post("/surveys/1/editor/items/1", data={"code": "consent", "body": "", "researchers": "0000-0000-0000-001X",
                                                    "draft": "template"})
    with app.app_context():
        from app.models import Item

        assert "grobe technische Angaben" in db.session.get(Item, 1).texts["de"]["body"]
    fill_consent(client)  # the template still has placeholders, which (correctly) block the start
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    assert "data-paradata" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={**CONSENT_OK, "_pd_vw": "390", "_pd_or": "portrait", "_pd_touch": "1"},
           headers={"User-Agent": IPHONE})
    p.post(f"/s/{uid}", data={"nav": "next", "_pd_vw": "1200"}, headers={"User-Agent": WIN_EDGE})  # not overwritten
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.paradata["device"] == "mobile" and r.paradata["viewport"] == "<480"
        assert "iPhone OS 17_5" not in str(r.paradata) and "Mozilla" not in str(r.paradata)
    row = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert (row["device"], row["os"], row["browser"], row["touch"]) == ("mobile", "iOS", "Safari", "1")


def test_off_by_default(app, client, survey):
    fill_consent(client)
    add(client, "info")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    assert "data-paradata" not in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={**CONSENT_OK, "_pd_vw": "390"}, headers={"User-Agent": IPHONE})
    with app.app_context():
        assert db.session.scalar(db.select(Response).filter_by(is_preview=False)).paradata == {"_assignment_network_starts": 0}
    header = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]
    assert "device" not in header


def test_time_per_page_measured_summed_and_exported(app, client, survey):
    from datetime import datetime, timedelta

    fill_consent(client)
    add(client, "text_input")                                       # item 2 on the page after the consent
    client.post("/surveys/1/editor/items/2", data={"code": "age", "question": "Alter"})
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de"], "default_language": "de",
                                          "allow_back": "1", "page_timing": "1"})
    client.post("/surveys/1/editor/items/1", data={"code": "consent", "body": "", "researchers": "0000-0000-0000-001X",
                                                    "draft": "template"})
    with app.app_context():
        from app.models import Item

        assert "wie lange Sie auf jeder Seite waren" in db.session.get(Item, 1).texts["de"]["body"]
    fill_consent(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()

    def wait(seconds):  # pretend the page has been on screen for a while
        with app.app_context():
            r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
            from datetime import datetime

            shown = dict(r.paradata["_shown"])
            shown["at"] = (datetime.fromisoformat(shown["at"]) - timedelta(seconds=seconds)).isoformat()
            r.paradata = {**r.paradata, "_shown": shown}
            db.session.commit()

    p.get(f"/s/{uid}")
    with p.session_transaction() as sess:                           # first page: nothing stored yet, the time
        first = dict(sess["n1"])                                     # it was shown waits in the session
        first["shown"] = (datetime.fromisoformat(first["shown"]) - timedelta(seconds=20)).isoformat()
        sess["n1"] = first
    p.post(f"/s/{uid}", data=CONSENT_OK)                             # consent page: 20 s
    p.get(f"/s/{uid}"); wait(5)
    p.post(f"/s/{uid}", data={"nav": "back"})                       # age page: 5 s, then back
    p.get(f"/s/{uid}"); wait(3)
    p.post(f"/s/{uid}", data=CONSENT_OK)                             # consent again: + 3 s
    p.get(f"/s/{uid}"); wait(7)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "40"})             # age again: + 7 s, done
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.status == Response.COMPLETED
        # 20 + 3 and 5 + 7 s pretended, plus the real time of the requests: under load (a full parallel suite) that
        # can be more than half a second, so a band instead of round(...) == 23; it still tells summed from single
        # visits (20, 3, 5, 7) and the pages apart
        assert 23 <= r.paradata["_t"]["1"] < 26 and 12 <= r.paradata["_t"]["2"] < 15
        stored = r.paradata["_t"]
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert abs(float(rows[0]["t_consent"]) - stored["1"]) < 0.6 and abs(float(rows[0]["t_age"]) - stored["2"]) < 0.6
    assert "_shown" not in rows[0] and "_t" not in rows[0]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "t_age" in book and "Sekunden" in book
