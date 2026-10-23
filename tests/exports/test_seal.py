"""Seal chain (app/seal.py) and RFC 3161 timestamps of the DFN (app/seal_verify.py), also for the
pre-registration (app/prereg.py). A stand-in for the service: tests/tsa_fake.py."""
import csv
import hashlib
import io
import json
import os
import zipfile

import pytest
import requests

from app import seal, seal_verify
from app.extensions import db
from app.models import Response, SealAnchor, SealEntry, Survey
from tests import tsa_fake
from tests.conftest import experiments_on
from tests.privacy.test_erasure import CODE, build, take

HERE = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def tsa(app, monkeypatch):
    """The fake service in place of the DFN, its root trusted."""
    app.config["TSA_URL"] = "http://tsa.test"
    monkeypatch.setattr(requests, "post", tsa_fake.post)
    monkeypatch.setitem(seal_verify.TRUSTED_ROOTS, tsa_fake.ROOT, "Test Root")
    return tsa_fake


def sealed_survey(app, client):
    experiments_on(client, "seal")
    uid = build(app, client)
    client.post("/surveys/1/seal-chain")
    return uid


def chain(app):
    with app.app_context():
        return [(e.seq, e.kind, e.ref, e.status, e.reason) for e in seal.entries(db.session.get(Survey, 1).uid)]


def export_rows(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))


def test_a_real_dfn_timestamp_holds_offline():
    """A token the DFN issued on 2026-10-08 for the bytes "hello\\n": signature and certificates up to the pinned root.
    Origin of tests/data/dfn_timestamp.der: requested from the public time-stamp service of the DFN
    (https://zeitstempel.dfn.de, RFC 3161) for these bytes; it holds only DFN's signature and certificates."""
    with open(os.path.join(HERE, "data", "dfn_timestamp.der"), "rb") as f:
        token = f.read()
    digest = hashlib.sha256(b"hello\n").digest()
    when = seal_verify.verify(token, digest)
    assert when.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-08 06:05:41"
    with pytest.raises(seal_verify.Invalid, match="another value"):
        seal_verify.verify(token, hashlib.sha256(b"hallo\n").digest())
    broken = bytearray(token)
    broken[-20] ^= 1  # inside the signature, the last field of the token
    with pytest.raises(seal_verify.Invalid):
        seal_verify.verify(bytes(broken), digest)
    with pytest.raises(seal_verify.Invalid, match="trusted root"):
        seal_verify.verify(token, digest, roots={})


def test_the_switch_asks_first_and_cannot_be_switched_off_once_used(app, client, survey):
    """No longer experimental (Philipp, 2026-10-08): everyone sees it; on asks first; off is no longer offered once
    the chain has an entry."""
    build(app, client)
    page = client.get("/surveys/1").data.decode()
    assert 'id="seal"' in page and "experimentell" not in page.split('id="seal"')[1].split("</div>")[0]
    assert 'data-confirm="Siegelkette einschalten?"' in page
    assert "/surveys/1/seal-chain" not in page.split('id="settings-privacy"')[1].split("</form>")[0]  # not autosaved
    client.post("/surveys/1/seal-chain", follow_redirects=True)
    page = client.get("/surveys/1").data.decode()
    assert "nicht mehr ausschalten" in page and "Siegelkette ausschalten" not in page
    client.post("/surveys/1/seal-chain", data={"off": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).seal                          # it has entries: stays on


def test_a_new_survey_starts_with_the_chain_on_and_may_switch_it_off_before_its_first_entry(app, client):
    app.config["SEAL_DEFAULT"] = True
    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    page = client.get("/surveys/1").data.decode()
    assert "Standard für neue Befragungen" in page and "Siegelkette ausschalten" in page
    client.post("/surveys/1/seal-chain", data={"off": "1"})
    with app.app_context():
        assert not db.session.get(Survey, 1).seal


def test_the_seal_chain_is_a_tick_that_saves_at_once(app, client):
    """UI audit 2026-10-12, U16: a tick like every other setting instead of a button; taking it away switches the
    chain off (toggle=1 without on), ticking it switches it on, each after a question."""
    app.config["SEAL_DEFAULT"] = True
    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    seal_part = client.get("/surveys/1").data.decode().split('id="seal"')[1].split('<div class="consent-mentions"')[0]
    assert 'type="checkbox" name="on"' in seal_part and "data-submit-on-change" in seal_part and "checked" in seal_part
    # no button besides the fallback without script and "Nicht mehr zeigen" in the fold "Warum?" (Lernhilfen, G2)
    assert "<button" not in seal_part.replace("<noscript><button", "").replace('<button type="button" class="learn-hide"', "")
    client.post("/surveys/1/seal-chain", data={"toggle": "1"})
    with app.app_context():
        assert not db.session.get(Survey, 1).seal
    client.post("/surveys/1/seal-chain", data={"toggle": "1", "on": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).seal


def test_cases_that_ended_are_sealed_and_a_deletion_leaves_a_record(app, client, survey):
    uid = build(app, client)
    first = app.test_client()
    end = take(first, uid, "vorher")                               # ended before the switch: sealed when it goes on
    experiments_on(client, "seal")
    client.post("/surveys/1/seal-chain")
    take(app.test_client(), uid, "zwei")
    half = app.test_client()
    half.get(f"/s/{uid}")
    half.post(f"/s/{uid}", data=__import__("tests.conftest", fromlist=["CONSENT_OK"]).CONSENT_OK)  # begun, not ended
    take(app.test_client(), uid, "drei")
    assert chain(app) == [(1, "enabled", None, None, None), (2, "sealed", None, "completed", None),
                          (3, "sealed", None, "completed", None), (4, "sealed", None, "completed", None)]
    code = CODE.search(end).group(1)
    app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": code})
    assert chain(app)[-1] == (5, "deleted", 2, "completed", "erasure_self")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert seal.check(s)["problems"] == []
        rows = db.session.scalars(db.select(SealEntry).filter_by(survey_uid=s.uid, seq=2)).all()
        assert rows[0].salt is None and rows[0].response_id is None   # nothing left to test a guess of the answers
        whole = seal.chain(s)
    assert seal_verify.check_chain(whole)["problems"] == []
    rows = export_rows(client)
    assert sorted(r["seal_seq"] for r in rows) == ["3", "4"] and all(len(r["seal_hash"]) == 64 for r in rows)
    assert seal_verify.check_rows(whole, rows)["missing"] == {}
    assert list(seal_verify.check_rows(whole, rows[:1])["missing"].values()) == ["completed"]   # a row taken out
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "seal_seq" in codebook and "Fallnummer in der Siegelkette" in codebook


def test_a_chain_rewritten_or_data_changed_behind_the_app_is_found(app, client, survey):
    uid = sealed_survey(app, client)
    for answer in ("a", "b", "c"):
        take(app.test_client(), uid, answer)
    with app.app_context():
        s = db.session.get(Survey, 1)
        ids = db.session.scalars(db.select(Response.id).filter_by(survey_id=1).order_by(Response.id)).all()
        table = Response.__table__
        db.session.execute(table.update().where(table.c.id == ids[0]).values(answers={"2": "anders"}))
        db.session.execute(table.delete().where(table.c.id == ids[1]))   # past the session: no record
        db.session.commit()
        problems = seal.check(s)["problems"]
        assert any("Fall 2: Daten außerhalb der Plattform verändert" in p for p in problems)
        assert any("Fall 3 fehlt ohne Löschvermerk" in p for p in problems)
        whole = seal.chain(s)
    whole["entries"].pop(2)                                        # an entry taken out of a download
    assert any("does not follow" in p for p in seal_verify.check_chain(whole)["problems"])


def test_changes_after_the_end_are_recorded(app, client, survey):
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(survey_id=1))
        r.answers = {**r.answers, "2": "korrigiert"}
        db.session.commit()
        r.paradata = {"device": "phone"}                           # not part of the content: nothing recorded
        db.session.commit()
        assert seal.check(db.session.get(Survey, 1))["problems"] == []
    assert [e[1:] for e in chain(app)] == [("enabled", None, None, None), ("sealed", None, "completed", None),
                                           ("amended", 2, "completed", "changed")]


def test_timestamps_and_the_download_check_a_data_file(app, client, survey, tsa, tmp_path):
    uid = sealed_survey(app, client)
    for answer in ("a", "b"):
        take(app.test_client(), uid, answer)
    with app.app_context():
        assert seal.due() == [db.session.get(Survey, 1).uid]
        seal.anchor_due()
        assert seal.due() == [] and db.session.scalar(db.select(db.func.count(SealAnchor.id))) == 1
    take(app.test_client(), uid, "c")
    page = client.get("/surveys/1/data").data.decode()
    assert 'id="seal"' in page and "Zuletzt gestempelt vom DFN: Eintrag 3" in page and "1 neuerer Eintrag ist noch ohne Stempel" in page
    r = client.get("/surveys/1/export/seal-chain.zip")                # stamps the newest entry first
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert set(z.namelist()) == {"seal.json", "seal_verify.py", "LIESMICH.txt"}
        data = json.loads(z.read("seal.json"))
        assert "python seal_verify.py seal.json daten.csv" in z.read("LIESMICH.txt").decode()
        checker = z.read("seal_verify.py")
        assert checker == client.get("/verify/seal_verify.py").data
        assert checker == client.get("/surveys/1/export/seal_verify.py").data
        assert checker.startswith(b"# SPDX-License-Identifier: MIT")
        assert b"Permission is hereby granted, free of charge" in checker
        assert b"The above copyright notice and this permission notice" in checker
        assert b"OUT OF OR IN CONNECTION WITH THE SOFTWARE" in checker
        assert "MIT-Lizenz" in z.read("LIESMICH.txt").decode()
        z.extractall(tmp_path)
    assert [a["seq"] for a in data["anchors"]] == [3, 4] and data["format"] == seal_verify.FORMAT
    assert "answers" not in json.dumps(data)                         # the chain holds hashes, never answers
    rows_path = tmp_path / "daten.csv"
    rows_path.write_bytes(client.get("/surveys/1/export/data.csv").data)
    assert seal_verify.main(["x", str(tmp_path / "seal.json"), str(rows_path)]) == 0
    lines, ok = seal_verify.report(data, seal_verify.read_rows(rows_path)[1:])
    assert not ok and any("MISSING without deletion record (completed): 1 cases" in line for line in lines)
    lines, ok = seal_verify.report({**data, "anchors": []})
    assert not ok and any("No timestamp" in line for line in lines)
    flash = client.post("/surveys/1/export/seal-chain/check", follow_redirects=True).data.decode()
    assert "Die Siegelkette hält: 4 Einträge" in flash
    # the page's script asks for the result block under the button (Philipp, 2026-10-08)
    block = client.post("/surveys/1/export/seal-chain/check", headers={"X-Requested-With": "fetch"}).data.decode()
    assert "Siegel intakt" in block and "Alle 4 Einträge" in block and "vom Zeitstempeldienst des DFN" in block
    assert "Prüfsumme der Kette (Eintrag 4)" in block and "<html" not in block
    page = client.get("/surveys/1/data").data.decode()
    assert 'class="button button-secondary">Jetzt prüfen' in page and "data-seal-result" in page


def test_a_service_that_does_not_answer_puts_the_stamp_off(app, client, survey, tsa, monkeypatch):
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    monkeypatch.setattr(requests, "post", tsa_fake.down)
    with app.app_context():
        assert seal.anchor_due() == 0 and len(seal.due()) == 1
    assert client.get("/surveys/1/export/seal-chain.zip").status_code == 200   # the download goes without
    assert "Noch nicht gestempelt" in client.get("/surveys/1/data").data.decode()


def test_the_chain_outlives_the_answers_and_the_survey(app, client, survey):
    from app import lifecycle, retention

    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    with app.app_context():
        s = db.session.get(Survey, 1)
        survey_uid = s.uid
        retention.purge(s)
        db.session.commit()
        assert seal.check(s)["problems"] == []
    assert chain(app)[-1][1:] == ("purged", None, None, "retention")
    with app.app_context():
        lifecycle.delete(db.session.get(Survey, 1), force=True)  # as with an account deleted
        db.session.commit()
        kinds = [e.kind for e in seal.entries(survey_uid)]
    assert kinds == ["enabled", "sealed", "purged", "closed"]


def test_test_runs_and_surveys_without_the_chain_write_nothing(app, client, survey):
    uid = build(app, client)
    take(app.test_client(), uid, "a")
    client.post("/surveys/1/editor/preview")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SealEntry.id))) == 0


def test_the_preregistration_gets_a_timestamp_of_the_dfn(app, client, survey, tsa, tmp_path):
    from app import prereg

    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        [(name, _v, _at, digest)] = prereg.listing(s)
        assert prereg.stamp_time(s, name) is not None
    page = client.get("/surveys/1/data").data.decode()
    assert "Zeitstempel des DFN:" in page and "Zeitstempel (.tsr)" in page
    stamp = client.get(f"/surveys/1/export/preregistration/{name}/timestamp")
    assert stamp.status_code == 200 and stamp.mimetype == "application/timestamp-reply"
    (tmp_path / "s.tsr").write_bytes(stamp.data)
    (tmp_path / "p.zip").write_bytes(client.get(f"/surveys/1/export/preregistration/{name}").data)
    assert hashlib.sha256((tmp_path / "p.zip").read_bytes()).hexdigest() == digest
    assert seal_verify.main(["x", str(tmp_path / "s.tsr"), str(tmp_path / "p.zip")]) == 0
    (tmp_path / "p.zip").write_bytes(b"something else")
    assert seal_verify.main(["x", str(tmp_path / "s.tsr"), str(tmp_path / "p.zip")]) == 1
    assert client.get("/surveys/1/export/seal_verify.py").data.startswith(b'# SPDX-License-Identifier: MIT')


def test_a_missing_preregistration_stamp_is_fetched_later(app, client, survey, tsa, monkeypatch):
    from app import prereg

    monkeypatch.setattr(requests, "post", tsa_fake.down)
    build(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        [(name, *_)] = prereg.listing(s)
        assert prereg.stamp_time(s, name) is None
        assert prereg.stamp_missing() == 0
        monkeypatch.setattr(requests, "post", tsa_fake.post)
        assert prereg.stamp_missing() == 1 and prereg.stamp_time(s, name) is not None
        assert prereg.stamp_missing() == 0


def test_start_and_close_of_the_fieldwork_are_entries_stamped_at_once(app, client, survey, monkeypatch):
    """Closing (not pausing) and starting again write an entry each and stamp the chain right after the commit, not
    at the next hourly round (Philipp, 2026-10-08)."""
    asked = []
    monkeypatch.setattr(seal, "anchor_in_background", lambda: asked.append(1))
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "eins")
    asked.clear()
    client.post("/surveys/1/close", data={"pause": "1"})
    assert asked == [] and "fieldclosed" not in [k for _, k, *_ in chain(app)]     # a pause is no end
    client.post("/surveys/1/close")
    assert asked == [1] and chain(app)[-1][1:] == ("fieldclosed", None, None, "manual")
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    assert chain(app)[-1][1] == "fieldstarted" and len(asked) == 2
    with app.app_context():
        assert not seal.check(db.session.get(Survey, 1))["problems"]


def test_a_cited_state_can_be_checked_by_anyone_and_goes_to_zenodo(app, client, survey, tsa):
    """A short check code names a stamped state; /verify/<code> confirms it without an account; the archive for
    Zenodo holds seal.json and the checker, its README the code (Philipp, 2026-10-08)."""
    from app import zenodo

    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "eins")
    with app.app_context():
        s = db.session.get(Survey, 1)
        seal.anchor(s.uid)
        code = seal.summary(s)["cite"]
        files = dict(zenodo.package(s))
    assert code and code.startswith("1-") and len(code.split("-")[2]) == seal.PREFIX
    assert "seal.json" in files and "seal_verify.py" in files and code.encode() in files["README.md"]
    assert "answers" not in files["seal.json"].decode()
    guest = app.test_client()
    page = guest.get(f"/verify/{code}").data.decode()
    assert "Siegel intakt" in page and "Zeitstempel des DFN" in page and "seal.json" in page
    # the page confirms the chain, not a data set: it points to the comparison (a tamper test, 2026-10-09)
    assert "nicht ein Datensatz" in page and 'id="abgleich"' in page
    assert json.loads(guest.get(f"/verify/{code}/seal.json").data)["format"] == seal_verify.FORMAT
    assert guest.get("/verify/seal_verify.py").status_code == 200
    wrong = code[:-4] + ("0000" if not code.endswith("0000") else "1111")
    assert guest.get(f"/verify/{wrong}").status_code == 404 and "Kein solcher Stand" in guest.get(f"/verify/{wrong}").data.decode()
    assert guest.get("/verify/?code=" + code).headers["Location"].endswith(f"/verify/{code}")
    assert guest.get("/verify/").status_code == 200 and guest.get("/verify").status_code == 308   # both ways in
    assert code in client.get("/surveys/1/data").data.decode()               # in the panel, to cite


def test_an_entry_of_a_case_holds_the_day_only(app, client, survey):
    """The case number (seal_seq) is in the data file, also in public files; a time to the microsecond in the chain
    would give back the end of every participation that public data cut to the month (audit 2026-10-08, S4). Entries
    of a case hold the day, the others their exact time; the chain checks as before."""
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "eins")
    client.post("/surveys/1/close")
    with app.app_context():
        s = db.session.get(Survey, 1)
        whole = seal.chain(s)
        assert seal.check(s)["problems"] == []
    times = {e["kind"]: e["at"] for e in whole["entries"]}
    assert len(times["sealed"]) == 10 and times["sealed"].count("-") == 2       # YYYY-MM-DD
    assert times["fieldclosed"].startswith(times["sealed"] + "T") and times["fieldclosed"].endswith("Z")
    assert seal_verify.check_chain(whole)["problems"] == []
    rows = export_rows(client)
    assert seal_verify.check_rows(whole, rows)["missing"] == {}
