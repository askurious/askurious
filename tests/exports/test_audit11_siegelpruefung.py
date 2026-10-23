"""Audit 2026-10-10, package U1 "Siegelprüfung" (Ralf 2026-10-11, decisions 1a and 7a).

S2: cases removed from a data file, with deletion records appended to its seal.json by hand (time in the future, or
now), held in the browser and in seal_verify.py. Now the browser compares every entry of a seal.json picked with the
server's chain, seal_verify.py does so with the address in seal.json (or offline checks up to the stamped state only),
and a time in the future does not hold.
S1: the texts say what the check of a data file shows: completeness and origin of the cases, not the answer values.
S5: a timestamp is checked once per process, not again at every new entry; "Jetzt prüfen" is limited per survey.
S6/P5: the hourly check skips unchanged surveys (tests/exports/test_seal_data_check.py); the tab "Export" does not check in
the request.
S7: a survey taken down by the platform, or deleted with its account, keeps its chain without its title."""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app import limits, seal, seal_verify
from app.extensions import db
from app.models import Response, SealStatus, Survey, User
from tests.privacy.test_erasure import take
from tests.exports.test_seal import sealed_survey, tsa  # noqa: F401
from tests.exports.test_seal_browser_check import NODE, STATIC, needs_node, sealed, to_csv  # noqa: F401
from tests.exports.test_seal_data_check import cases, cite

HARNESS = """
const api = require(process.argv[2]);
const inp = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
(async () => {
  const rows = api.readRows(inp.csv);
  const c = await api.compare(inp.chain, rows, inp.cited, false, inp.server);
  process.stdout.write(JSON.stringify({chainOk: c.chainOk, dataOk: c.dataOk, foreign: c.foreign, offline: c.offline,
    beyond: c.beyond, missing: c.missing, problems: c.chain.problems.map(p => p.what + " " + p.n)}));
})();
"""


def run_js(tmp_path, chain, text, cited, server):
    script = tmp_path / "harness_u1.js"
    script.write_text(HARNESS, encoding="utf-8")
    data = tmp_path / "input_u1.json"
    data.write_text(json.dumps({"chain": chain, "csv": text, "cited": cited, "server": server}), encoding="utf-8")
    done = subprocess.run([NODE, str(script), str(STATIC / "seal_check.js"), str(data)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def forge(chain, cases_, when):
    """seal.json with deletion records for cases_ appended by hand, as anyone can (link is public)."""
    chain = json.loads(json.dumps(chain))
    last = chain["entries"][-1]
    for ref in cases_:
        e = {"seq": last["seq"] + 1, "kind": "deleted", "ref": ref, "status": "completed", "content": None,
             "reason": "erasure_self", "at": when.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "prev": last["digest"]}
        e["digest"] = seal_verify.link(last["digest"], e)
        chain["entries"].append(e)
        last = e
    return chain


def dropped(sealed_):  # noqa: F811
    rows = [dict(r) for r in sealed_["rows"]]
    return [int(rows[0]["seal_seq"]), int(rows[1]["seal_seq"])], rows[2:]


FUTURE = datetime.now(timezone.utc) + timedelta(days=3650)
NOW = datetime.now(timezone.utc)


# --- S2: the checker ------------------------------------------------------------------------------------------------

def test_s2_a_time_in_the_future_does_not_hold(sealed):  # noqa: F811
    drop, _ = dropped(sealed)
    result = seal_verify.check_chain(forge(sealed["chain"], drop, FUTURE))
    assert any("its time lies in the future" in p for p in result["problems"])
    assert not seal_verify.check_chain(forge(sealed["chain"], drop, NOW))["problems"]  # now: no finding by itself
    assert seal_verify.stale_tail(forge(sealed["chain"], drop, FUTURE), len(sealed["chain"]["entries"])) is not None


@pytest.mark.parametrize("when", [FUTURE, NOW])
def test_s2_cases_removed_with_appended_records_do_not_hold_offline(sealed, when):  # noqa: F811
    drop, kept = dropped(sealed)
    lines, ok = seal_verify.report(forge(sealed["chain"], drop, when), kept)
    assert not ok and lines[-1] != "RESULT: holds."
    assert any(line.startswith("MISSING without deletion record") for line in lines)
    assert any("deletion record only after entry" in line for line in lines)


def test_s2_appended_entries_are_found_against_the_server(sealed):  # noqa: F811
    drop, kept = dropped(sealed)
    forged = forge(sealed["chain"], drop, NOW)
    lines, ok = seal_verify.report(forged, kept, server=sealed["chain"])
    assert not ok and lines[-1] == "RESULT: does not hold, see above."
    assert any("the chain on the server does not have" in line for line in lines)
    # the true chain, with an end the server confirms, holds with the whole file
    lines, ok = seal_verify.report(sealed["chain"], sealed["rows"], server=sealed["chain"])
    assert ok, lines


def test_s2_the_checker_asks_the_server_named_in_the_file(sealed, tmp_path, monkeypatch, capsys):  # noqa: F811
    drop, kept = dropped(sealed)
    assert sealed["chain"]["url"].endswith(f"/verify/{sealed['code']}/seal.json")  # the address goes with the chain
    path, rows = tmp_path / "seal.json", tmp_path / "data.csv"
    # the address as on the platform (the test app runs on localhost, which the checker does not trust: audit
    # 2026-10-11, H1, tests/exports/test_seal_verify_hosts.py)
    own = f"https://askurious.de/verify/{sealed['code']}/seal.json"
    path.write_text(json.dumps({**forge(sealed["chain"], drop, NOW), "url": own}), encoding="utf-8")
    rows.write_text(to_csv(kept), encoding="utf-8")
    asked = []
    monkeypatch.setattr(seal_verify, "fetch", lambda url, timeout=20: asked.append(url) or sealed["chain"])
    assert seal_verify.main(["x", str(path), str(rows)]) == 1
    assert asked == [own] and "Compared with the chain on the server" in capsys.readouterr().out

    def down(url, timeout=20):
        raise OSError("no connection")

    monkeypatch.setattr(seal_verify, "fetch", down)
    assert seal_verify.main(["x", str(path), str(rows)]) == 1      # offline: the removed cases are missing
    assert "checked offline" in capsys.readouterr().out
    assert seal_verify.main(["x", "--offline", str(path)]) == 3    # the chain alone: up to the stamped state


# --- S2: the browser --------------------------------------------------------------------------------------------------

@needs_node
def test_s2_the_browser_compares_a_seal_json_with_the_servers_chain(tmp_path, sealed):  # noqa: F811
    drop, kept = dropped(sealed)
    forged = forge(sealed["chain"], drop, NOW)
    got = run_js(tmp_path, forged, to_csv(kept), sealed["cited"], sealed["chain"])
    assert not got["chainOk"] and got["foreign"] == [e["seq"] for e in forged["entries"][-2:]]
    # the server not reachable: only the cited state counts, the removed cases are missing
    got = run_js(tmp_path, forged, to_csv(kept), sealed["cited"], None)
    assert got["offline"] and not got["dataOk"] and sorted(map(int, got["missing"])) == sorted(drop)
    # a time in the future is a finding of the chain itself
    got = run_js(tmp_path, forge(sealed["chain"], drop, FUTURE), to_csv(kept), sealed["cited"], sealed["chain"])
    assert not got["chainOk"] and any(p.startswith("future") for p in got["problems"])
    # the true seal.json, confirmed by the server, holds
    got = run_js(tmp_path, sealed["chain"], to_csv(sealed["rows"]), sealed["cited"], sealed["chain"])
    assert got["chainOk"] and got["dataOk"] and got["foreign"] == []
    # a seal.json of another survey
    other = {**sealed["chain"], "survey": "zzzz"}
    got = run_js(tmp_path, sealed["chain"], to_csv(sealed["rows"]), sealed["cited"], other)
    assert not got["chainOk"] and got["foreign"] == ["*"]


# --- S1: what the check shows ---------------------------------------------------------------------------------------

def test_s1_the_texts_say_values_are_not_checked(app, client, sealed):  # noqa: F811
    lines, ok = seal_verify.report(sealed["chain"], sealed["rows"])
    assert ok and seal_verify.VALUES_NOTE in lines
    assert "What it does not check: the answer values" in seal_verify.__doc__
    page = sealed["page"]
    assert "nicht die Antwortwerte" in page and "zu seinem Hashwert passen, außer" not in page
    texts = page.split('id="seal-compare-texts">', 1)[1]
    assert "Vollständigkeit und Herkunft der Fälle" in texts
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert "nicht, ob die Antwortwerte" in seal.readme(s, seal.chain(s))
    readme = open(STATIC.parent.parent / "README.md", encoding="utf-8").read()
    assert "not the answer values" in readme


# --- S5: timestamps checked once, "Jetzt prüfen" limited ------------------------------------------------------------

def test_s5_a_stamp_is_checked_once_per_process(app, client, survey, tsa, monkeypatch):  # noqa: F811
    cases(app, client, ("a", "b"))
    code = cite(app)
    calls = []
    real = seal_verify.verify
    monkeypatch.setattr(seal_verify, "verify", lambda *a, **k: calls.append(1) or real(*a, **k))
    seal._STAMPS.clear()
    seal._CITED.clear()
    assert app.test_client().get(f"/verify/{code}").status_code == 200
    first = len(calls)
    assert first >= 1
    with app.app_context():                                # a new case: the kept result of /verify/ is missed
        db.session.add(Response(survey_id=1, status="completed", answers={"2": "neu"}, completed_at=datetime(2026, 10, 2)))
        db.session.commit()
    assert app.test_client().get(f"/verify/{code}").status_code == 200
    assert len(calls) == first                             # but no stamp is checked again


def test_s5_check_now_is_limited_per_survey(app, client, survey):
    cases(app, client, ("a",))
    app.config.update(SEAL_CHECK_LIMIT=1)
    limits._seal_checks.clear()
    try:
        first = client.post("/surveys/1/export/seal-chain/check", headers={"X-Requested-With": "fetch"})
        assert first.status_code == 200 and "Siegel" in first.data.decode()
        again = client.post("/surveys/1/export/seal-chain/check", headers={"X-Requested-With": "fetch"})
        assert again.status_code == 429 and "gerade mehrmals geprüft" in again.data.decode()
        page = client.post("/surveys/1/export/seal-chain/check", follow_redirects=True).data.decode()
        assert "gerade mehrmals geprüft" in page
    finally:
        limits._seal_checks.clear()


# --- S6/P5: the tab "Export" ------------------------------------------------------------------------------------------

def test_p5_the_export_tab_shows_the_last_check_without_checking(app, client, survey, monkeypatch):
    cases(app, client, ("a",))
    ran = []
    monkeypatch.setattr(seal, "check_quick", lambda s: ran.append(s) or None)
    client.get("/surveys/1/data")
    assert ran == []


# --- S7: the title goes with a take-down or the account ---------------------------------------------------------------

def _title_in_chain(app, uid):
    with app.app_context():
        return db.session.execute(db.select(seal.CHAINS.c.title).where(seal.CHAINS.c.survey_uid == uid)).scalar()


def test_s7_a_survey_taken_down_keeps_its_chain_without_its_title(app, client, survey, tsa):  # noqa: F811
    cases(app, client, ("a", "b"))
    code = cite(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        uid, title, sc = s.uid, s.title, s.code
    assert title and title in app.test_client().get(f"/verify/{code}").data.decode()
    client.post(f"/admin/surveys/{sc}/erase", data={"action": "survey", "reason": "Name einer Person im Titel",
                                                     "code": sc})
    with app.app_context():
        assert db.session.get(Survey, 1) is None
    assert _title_in_chain(app, uid) == ""
    page = app.test_client().get(f"/verify/{code}").data.decode()
    assert title not in page and "Befragung 1 · Eintrag" in page
    chain = json.loads(app.test_client().get(f"/verify/{code}/seal.json").data)
    assert chain["title"] == "" and title not in json.dumps(chain)
    assert seal_verify.check_chain(chain)["problems"] == []          # the title is in no hash


def test_s7_a_survey_deleted_with_its_account_keeps_its_chain_without_its_title(app, client, survey):
    from app import accounts

    cases(app, client, ("a",))
    with app.app_context():
        s = db.session.get(Survey, 1)
        uid, title = s.uid, s.title
        s.status = Survey.CLOSED
        db.session.commit()
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        accounts.delete(user, "self", {"confirm_data": "1"})
        assert db.session.get(Survey, 1) is None
        assert seal_verify.check_chain(seal.chain(type("S", (), {"uid": uid, "title": ""})()))["problems"] == []
    assert title and _title_in_chain(app, uid) == ""


def test_s7_a_survey_its_researchers_delete_keeps_its_title(app, client, survey):
    """Decision 7a names the platform's take-down and the account; a deletion by the researchers is as before."""
    with app.app_context():
        from app import lifecycle

        uid = sealed_survey(app, client)
        take(app.test_client(), uid, "a")
        s = db.session.get(Survey, 1)
        survey_uid, title = s.uid, s.title
        lifecycle.delete(s, force=True)
        db.session.commit()
    assert _title_in_chain(app, survey_uid) == title


def test_p5_the_status_row_keeps_its_fingerprint(app, client, survey):
    cases(app, client, ("a",))
    with app.app_context():
        s = db.session.get(Survey, 1)
        row = seal.check_quick(s)
        db.session.commit()
        assert row.fingerprint == seal.fingerprint(s) and row.looked_at == row.checked_at
        assert db.session.get(SealStatus, s.uid).fingerprint
