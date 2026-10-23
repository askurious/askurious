"""Participants delete their own answers (app/erasure.py): in the browser that took part, with the deletion code,
or through the researchers. What was deleted is counted per day and nothing else of it is kept."""
import os
import re

from app import erasure
from app.extensions import db
from app.models import ContactEntry, Erasure, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
META = {"title": "Test", "languages": ["de", "en"], "default_language": "de", "allow_back": "1"}
CODE = re.compile(r'class="p-erase-code" dir="ltr">(\d+-[2-9A-Z]{4}-[2-9A-Z]{4}-[2-9A-Z]{4})<')  # with the survey's number (2026-10-08)


def build(app, client, codes=True, start=True):
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/meta", data={**META, "erase_code_shown": "1", **({"erase_code": "1"} if codes else {})})
    if start:
        client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def take(p, uid, answer="darum"):
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p.post(f"/s/{uid}", data={"nav": "next", "i2": answer}, follow_redirects=True).data.decode()


def stored(app):
    with app.app_context():
        return [r.answers.get("2") for r in db.session.scalars(db.select(Response).filter_by(survey_id=1, is_preview=False))]


def log(app):
    with app.app_context():
        return [(e.way, e.responses, e.contacts) for e in db.session.scalars(db.select(Erasure).filter_by(survey_id=1))]


def test_the_code_from_the_end_page_deletes_from_another_browser(app, client, survey):
    uid = build(app, client)
    first, other = app.test_client(), app.test_client()
    end = take(first, uid, "meins")
    take(other, uid, "anderes")
    code = CODE.search(end).group(1)
    assert CODE.search(first.get(f"/s/{uid}").data.decode()).group(1) == code  # the same when the page is opened again
    assert code not in first.get(f"/s/{uid}/privacy").data.decode()
    with app.app_context():  # the row holds a hash, never the code
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=1)).all()
        assert all(r.erase_hash and len(r.erase_hash) == 64 for r in rows) and len({r.erase_hash for r in rows}) == 2
        assert all(code not in repr(vars(r)) and code.replace("-", "") not in repr(vars(r)) for r in rows)
    stranger = app.test_client()
    page = stranger.get(f"/s/{uid}/delete").data.decode()
    assert "Löschcode" in page and "Meine Antworten endgültig löschen" not in page
    wrong = stranger.post(f"/s/{uid}/delete", data={"sure": "1", "code": "2222-2222-2222"}).data.decode()
    assert "keine Teilnahme" in wrong and sorted(stored(app)) == ["anderes", "meins"]
    done = stranger.post(f"/s/{uid}/delete", data={"sure": "1", "code": code.lower().replace("-", " ")}).data.decode()
    assert "unwiederbringlich gelöscht" in done
    assert stored(app) == ["anderes"] and log(app) == [("self", 1, 0)]
    assert "keine Teilnahme" in stranger.post(f"/s/{uid}/delete", data={"sure": "1", "code": code}).data.decode()  # only once


def test_the_browser_that_took_part_needs_no_code(app, client, survey):
    uid = build(app, client, codes=False)
    p, other = app.test_client(), app.test_client()
    end = take(p, uid, "meins")
    take(other, uid, "anderes")
    assert not CODE.search(end) and "Löschcode" not in end
    privacy = p.get(f"/s/{uid}/privacy").data.decode()
    assert "Antworten löschen" in privacy and "nicht mehr einzeln löschen" in privacy
    page = p.get(f"/s/{uid}/delete").data.decode()
    assert "Meine Antworten endgültig löschen" in page and 'name="code"' not in page
    assert "unwiederbringlich gelöscht" in p.post(f"/s/{uid}/delete", data={"sure": "1", "here": "1"}).data.decode()
    assert stored(app) == ["anderes"] and log(app) == [("self", 1, 0)]
    # the browser has forgotten the participation: nothing more to delete, and the link starts afresh
    assert "keine Teilnahme" in p.post(f"/s/{uid}/delete", data={"sure": "1", "here": "1"}).data.decode()
    assert "vergibt keine Löschcodes" in p.get(f"/s/{uid}/delete").data.decode()
    assert stored(app) == ["anderes"]
    assert "Einwilligung" in p.get(f"/s/{uid}").data.decode()


def test_an_unfinished_participation_can_be_withdrawn(app, client, survey):
    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    assert "Meine Antworten" not in p.get(f"/s/{uid}/delete").data.decode()   # nothing stored yet
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert not CODE.search(p.get(f"/s/{uid}/privacy").data.decode())
    with app.app_context():
        code = erasure.code_for(db.session.get(Survey, 1), db.session.scalar(db.select(Response)))
    assert len(stored(app)) == 1
    app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": code})
    assert stored(app) == [] and log(app) == [("self", 1, 0)]
    # the page still open in the first browser belongs to no participation any more
    assert p.post(f"/s/{uid}", data={"nav": "next", "i2": "x"}).status_code in (302, 303) and stored(app) == []


def test_tries_of_codes_are_limited(app, client, survey):
    uid = build(app, client)
    app.config["ERASE_LIMIT"] = 3
    p = app.test_client()
    sender = {"REMOTE_ADDR": "203.0.113.9"}
    for _ in range(3):
        assert p.post(f"/s/{uid}/delete", data={"sure": "1", "code": "2222-2222-2222"}, environ_base=sender).status_code == 200
    late = p.post(f"/s/{uid}/delete", data={"sure": "1", "code": "2222-2222-2222"}, environ_base=sender)
    assert late.status_code == 429 and "Zu viele Versuche" in late.data.decode()


def test_previews_and_other_surveys_are_out_of_reach(app, client, survey):
    uid = build(app, client)
    end = take(app.test_client(), uid, "meins")
    code = CODE.search(end).group(1)
    client.post("/surveys/new", data={"title": "Zwei", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        other = db.session.get(Survey, 2)
        assert erasure.by_code(other, code) is None and erasure.find(other, code) == ([], [])
        mine = erasure.by_code(db.session.get(Survey, 1), code)
        assert mine.answers["2"] == "meins"
        preview = Response(survey_id=1, is_preview=True, source="test", pid="1-x", answers={})
        db.session.add(preview)
        db.session.commit()
        assert erasure.code_for(db.session.get(Survey, 1), preview) is None and preview.erase_hash is None


def test_researchers_delete_on_request(app, client, survey):
    uid = build(app, client)
    take(app.test_client(), uid, "eins")
    code = CODE.search(take(app.test_client(), uid, "zwei")).group(1)
    take(app.test_client(), uid, "drei")
    with app.app_context():
        rows = {r.answers["2"]: r for r in db.session.scalars(db.select(Response).filter_by(survey_id=1))}
        pid = rows["eins"].pid
        rows["drei"].params = {"pid": "panel-77"}
        db.session.add(ContactEntry(survey_id=1, answers={"9": " Anna@Example.org "}))
        db.session.add(ContactEntry(survey_id=1, answers={"9": "bernd@example.org"}))
        db.session.commit()
    page = client.get("/surveys/1/data").data.decode()
    assert 'id="erasure"' in page and "danach mit ihrem Löschcode" in page
    nothing = client.post("/surveys/1/erasure", data={"term": "unbekannt"}, follow_redirects=True).data.decode()
    assert "nichts gefunden" in nothing and len(stored(app)) == 3
    for term, left in ((pid, ["drei", "zwei"]), (code, ["drei"]), ("panel-77", [])):
        found = client.post("/surveys/1/erasure", data={"term": term}).data.decode()
        assert "Gefunden: 1 Teilnahme" in found and 'name="confirm" value="1"' in found
        assert len(stored(app)) == len(left) + 1                       # asked first, nothing deleted yet
        done = client.post("/surveys/1/erasure", data={"term": term, "confirm": "1"}, follow_redirects=True).data.decode()
        assert "1 Teilnahme endgültig gelöscht" in done and sorted(stored(app)) == left
    found = client.post("/surveys/1/erasure", data={"term": "anna@example.org"}).data.decode()
    assert "Gefunden: 1 Eintrag des getrennten Abschnitts" in found
    # a confirmation for another number than what is found now deletes nothing
    client.post("/surveys/1/erasure", data={"term": "anna@example.org", "confirm": "2"})
    client.post("/surveys/1/erasure", data={"term": "anna@example.org", "confirm": "1"})
    with app.app_context():
        assert [e.answers["9"] for e in db.session.scalars(db.select(ContactEntry))] == ["bernd@example.org"]
    assert log(app) == [("request", 3, 1)]
    page = client.get("/surveys/1/data").data.decode()
    assert "<strong>3</strong> Teilnahmen gelöscht" in page and "<strong>1</strong> Eintrag des getrennten Abschnitts gelöscht" in page


def test_a_request_needs_the_admin_role(app, client, survey):
    uid = build(app, client)
    code = CODE.search(take(app.test_client(), uid, "eins")).group(1)
    with app.app_context():
        member = db.session.get(Survey, 1).members[0]
        member.role = "editor"
        db.session.commit()
    client.post("/surveys/1/erasure", data={"term": code, "confirm": "1"})
    assert stored(app) == ["eins"] and 'id="erasure"' not in client.get("/surveys/1/data").data.decode()


def test_saved_states_results_and_estimates_forget_the_participation(app, client, survey):
    uid = build(app, client)
    take(app.test_client(), uid, "bleibt")
    code = CODE.search(take(app.test_client(), uid, "geht, mit Komma")).group(1)
    client.post("/surveys/1/close")
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        gone = erasure.by_code(s, code).pid
        from app import snapshots

        folder = os.path.join(snapshots.folder(s), snapshots.listing(s)[0][0])
        before = open(os.path.join(folder, "data.csv"), encoding="utf-8-sig", newline="").read()
        assert "geht, mit Komma" in before and gone in before
        number = [line.split(",")[0] for line in before.splitlines() if gone in line][0]
        with open(os.path.join(folder, "long_cj.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("﻿resp_id,source,task\r\n1,real,1\r\n2,real,1\r\n2,real,2\r\n")
        hb = os.path.join(app.config["UPLOAD_FOLDER"], "hb", s.uid)
        os.makedirs(hb, exist_ok=True)
        import json

        for name, pids in (("item_5.json", [gone, "x"]), ("item_6.json", ["x"]), ("item_7.run.json", [gone])):
            with open(os.path.join(hb, name), "w", encoding="utf-8") as f:
                json.dump({"pids": pids}, f)
        open(os.path.join(hb, "results.bin"), "wb").close()
    app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": code})
    after = open(os.path.join(folder, "data.csv"), encoding="utf-8-sig", newline="").read()
    assert "geht, mit Komma" not in after and gone not in after and "bleibt" in after
    assert after.splitlines()[0] == before.splitlines()[0] and len(after.splitlines()) == len(before.splitlines()) - 1
    long = open(os.path.join(folder, "long_cj.csv"), encoding="utf-8-sig", newline="").read().splitlines()
    assert len(long) == 4 - (1 if number == "1" else 2) and all(not line.startswith(number + ",") for line in long[1:])
    assert sorted(name for name in os.listdir(hb) if not name.endswith(".claim")) == ["item_6.json"]  # a running estimation is stopped (code review 2026-10-07, C3)
    assert stored(app) == ["bleibt"]


def test_the_consent_names_the_code_and_the_setting_is_fixed_after_the_start(app, client, survey):
    client.post("/surveys/1/consent-facts", data={"min_age": "", "researchers": "0000-0000-0000-001X"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.erase_code is False                                    # off for new surveys too (pre-test 2026-10-16)
        s.erase_code = True
        consent = s.consent_item
        de, en = consent.kind.template_body(consent, "de"), consent.kind.template_body(consent, "en")
        assert "Löschcode" in de and "nicht gelöscht werden" not in de
        assert "deletion code" in en and "cannot be deleted" not in en
        assert consent.kind.facts(consent, "en")["deletion_code"]
        s.erase_code = False
        de = consent.kind.template_body(consent, "de")
        assert "Löschcode" not in de and "bis 15 Minuten nach der Teilnahme" in de   # the browser way, not "never"
        assert consent.kind.facts(consent, "en")["deletion_code"] is None
        db.session.rollback()
    # the untouched standard text follows the setting
    client.post("/surveys/1/meta", data={**META, "erase_code_shown": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.erase_code is False and "Löschcode" not in s.consent_item.texts["de"]["body"]
    client.post("/surveys/1/meta", data={**META, "erase_code_shown": "1", "erase_code": "1"})
    client.post("/surveys/1/meta", data=META)                           # a form without the field changes nothing
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.erase_code is True and "Löschcode" in s.consent_item.texts["de"]["body"]
    # an own text that does not name the code: participants read the sentence on the code all the same, it comes from
    # the setting when the page is shown (X43), so the check of the questionnaire has nothing to remind of
    fill_consent(client)
    from app import design

    with app.app_context():
        consent = db.session.get(Survey, 1).consent_item
        assert "Löschcode" not in consent.texts["de"]["body"] and "Löschcode" in consent.kind.shown_body(consent, "de")
        messages = [f.message for f in design.check(db.session.get(Survey, 1))]
        assert not any("Löschcode für ihre Antworten" in m for m in messages)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/meta", data={**META, "erase_code_shown": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).erase_code is True


def test_duplicate_and_file_take_the_setting_but_not_the_log(app, client, survey):
    uid = build(app, client)
    code = CODE.search(take(app.test_client(), uid, "eins")).group(1)
    app.test_client().post(f"/s/{uid}/delete", data={"sure": "1", "code": code})
    client.post("/surveys/1/duplicate", data={"title": "Kopie"})
    with app.app_context():
        copy = db.session.get(Survey, 2)
        assert copy.erase_code is True and copy.erasures == [] and len(db.session.get(Survey, 1).erasures) == 1
        from app import transfer

        assert transfer.dump(db.session.get(Survey, 1), with_files=False)["survey"]["erase_code"] is True
        assert transfer._settings({"title": "Alt", "languages": ["de"]}, "")["erase_code"] is False  # a file from before


def warnings_about_the_code(app):
    from app import design

    with app.app_context():
        return [f.message for f in design.check(db.session.get(Survey, 1)) if f.message.startswith("Löschcode:")]


def test_an_immediate_completion_link_is_kept_and_the_check_warns(app, client, survey):
    """Ralf, 2026-10-03: the researchers decide. A link that redirects at once still does (the code goes unseen),
    the check of the questionnaire says so; with "Endseite mit Knopf" the page shows the code with a copy button."""
    client.post("/surveys/1/panel", data={"redirect_completed": "https://panel.example/done"})
    uid = build(app, client, start=False)
    assert warnings_about_the_code(app) and "abgeschlossen" in warnings_about_the_code(app)[0]
    client.post("/surveys/1/panel", data={"redirect_completed": "https://panel.example/done", "mode_completed": "button"})
    with app.app_context():
        assert db.session.get(Survey, 1).panel["modes"].get("completed") == "button"
    assert warnings_about_the_code(app) == []
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.post(f"/s/{uid}", data={"nav": "next", "i2": "x"}, follow_redirects=True).data.decode()
    assert CODE.search(html) and 'href="https://panel.example/done"' in html and "data-copy=" in html


def test_without_codes_there_is_no_warning(app, client, survey):
    client.post("/surveys/1/panel", data={"redirect_completed": "https://panel.example/done"})
    build(app, client, codes=False, start=False)
    assert warnings_about_the_code(app) == []


def test_the_code_names_its_survey_and_works_on_the_page_of_the_platform(app, client, survey):
    """Codes carry the survey's number in front ("1-XXXX-XXXX-XXXX"); the privacy page of the platform links a page
    where the code alone deletes, without knowing the survey (Philipp, 2026-10-08)."""
    from app import erasure

    uid = build(app, client)
    end = take(app.test_client(), uid, "meins")
    code = CODE.search(end).group(1)
    assert code.startswith("1-")
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert erasure.by_code(s, code[2:]) is not None                  # also without the number (codes of before)
        assert erasure.by_code(s, "7-" + code[2:]) is not None          # a mistyped number: the twelve characters decide
    assert "/privacy/delete" in client.get("/imprint").data.decode()
    guest = app.test_client()
    assert "142-K7MP-2QXD-9RTW" in guest.get("/privacy/delete").data.decode()
    assert "keine Teilnahme" in guest.post("/privacy/delete", data={"code": "1-2222-2222-2222", "sure": "1"}).data.decode()
    assert "bestätigen" in guest.post("/privacy/delete", data={"code": code}).data.decode()   # the tick first
    assert "unwiederbringlich gelöscht" in guest.post("/privacy/delete", data={"code": code.lower(), "sure": "1"}).data.decode()
    assert stored(app) == []


def test_a_mistyped_number_still_finds_the_code_on_the_page_of_the_platform(app, client, survey):
    """The page of the platform looks the twelve characters up in all surveys, also with a wrong number in front;
    the lookup has its own index (Ralf, 2026-10-08)."""
    from sqlalchemy import inspect

    uid = build(app, client)
    code = CODE.search(take(app.test_client(), uid, "meins")).group(1)
    with app.app_context():
        assert "ix_response_erase_hash" in {i["name"] for i in inspect(db.engine).get_indexes("response")}
    guest = app.test_client()
    assert "unwiederbringlich gelöscht" in guest.post("/privacy/delete", data={"code": "99-" + code[2:], "sure": "1"}).data.decode()
    assert stored(app) == []
