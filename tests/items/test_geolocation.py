"""Location element (app/items/geolocation.py): the position of the device after the person shares it."""
import json
import os
import random
from datetime import timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app import retention
from app.extensions import db
from app.items import geolocation
from app.items.base import AnswerError
from app.models import Item, Response, Survey, utcnow
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
AACHEN = (50.776351, 6.083862)


def setup(client, **extra):
    add(client, "geolocation")                                       # 2
    client.post(E + "/items/2", data={"code": "ort", "question": "Wo sind Sie gerade?", "answer_mode": "required",
                                      "store": "exact", "fallback": "none", "correct": "1", **extra})


def parse(item, data, required=True, answers=None):
    item.answer_mode = "required" if required else "optional"
    return item.kind.parse_answer(item, MultiDict({"i2": json.dumps(data) if data is not None else ""}), answers=answers)


def test_editor_saves_the_choices(app, client, survey):
    setup(client, store="1km", fallback="ip")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config == {"store": "1km", "fallback": "ip", "correct": True}
        client.post(E + "/items/2", data={"code": "ort", "question": "Wo?", "store": "bogus", "fallback": "x"})
        db.session.expire_all()
        item = db.session.get(Item, 2)
        assert item.config == {"store": "1km", "fallback": "ip", "correct": False}   # bad values keep what was there


def test_the_federal_state_of_a_point():
    assert geolocation.state_of(*AACHEN) == "5"
    assert geolocation.state_of(52.52, 13.405) == "11"                 # Berlin, the hole in Brandenburg
    assert geolocation.state_of(52.4, 13.06) == "12"                   # Potsdam
    assert geolocation.state_of(48.137, 11.575) == "9"
    assert geolocation.state_of(48.8566, 2.3522) == "0"                # Paris


def test_answers_are_rounded_to_the_level_chosen(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        shared = {"way": "device", "lat": AACHEN[0], "lon": AACHEN[1], "acc": 23.6}
        assert parse(item, shared) == {"way": "device", "lat": 50.776351, "lon": 6.083862, "acc": 24, "state": "5", "src": "pos",
                                       "plz": "52062"}
        item.config = {**item.config, "store": "100m"}
        assert parse(item, shared)["lat"] == 50.776 and parse(item, shared)["lon"] == 6.084
        item.config = {**item.config, "store": "1km"}
        assert (parse(item, shared)["lat"], parse(item, shared)["lon"]) == (50.78, 6.08)
        item.config = {**item.config, "store": "plz"}
        kept = parse(item, shared)
        assert kept == {"way": "device", "acc": 24, "state": "5", "src": "pos", "plz": "52062"}
        assert parse(item, kept, answers={2: kept}) == kept             # the stored answer sent again (Zurück)
        # state and postal code come from the stored answer, never from the form (audit 2026-10-10, N-7)
        assert parse(item, {**kept, "state": "9", "plz": "00000"}, answers={2: kept}) == kept
        with pytest.raises(AnswerError, match="nicht gelesen"):
            parse(item, {"way": "device", "state": "9", "plz": "52062", "acc": 5})  # nothing stored: not taken
        item.config = {**item.config, "store": "state"}
        kept = parse(item, shared)
        assert kept == {"way": "device", "acc": 24, "state": "5", "src": "pos"}
        assert parse(item, kept, answers={2: kept}) == kept             # the stored answer sent again (Zurück)
        with pytest.raises(AnswerError, match="nicht gelesen"):
            parse(item, {**kept, "state": "77"})                        # no state the element knows, no coordinates
        item.config = {**item.config, "store": "exact"}
        with pytest.raises(AnswerError, match="nicht gelesen"):
            parse(item, {"way": "device", "lat": 95, "lon": 6})


def test_a_refusal_is_an_answer_and_required_means_choosing(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        with pytest.raises(AnswerError, match="Standort"):
            parse(item, None)
        with pytest.raises(AnswerError, match="Standort"):
            parse(item, {"way": "teleport"})
        assert parse(item, None, required=False) is None
        for way in ("refused", "denied", "unavailable", "timeout"):
            answer = parse(item, {"way": way, "lat": 1, "lon": 2})
            assert answer == {"way": way}                               # no coordinates without sharing
            assert not item.kind.is_missing(item, answer)
        assert item.kind.is_missing(item, None)


def test_the_ip_address_stands_in_on_a_refusal(app, client, survey, monkeypatch):
    setup(client, fallback="ip")
    from app import geoip

    monkeypatch.setattr(geoip, "lookup", lambda address: ("DE", "9"))
    with app.test_request_context("/s/1", environ_base={"REMOTE_ADDR": "203.0.113.5"}):
        item = db.session.get(Item, 2)
        assert parse(item, {"way": "denied"}) == {"way": "denied", "state": "9", "src": "ip"}
        monkeypatch.setattr(geoip, "lookup", lambda address: ("AT", ""))
        assert parse(item, {"way": "refused"}) == {"way": "refused", "state": "0", "src": "ip"}
        monkeypatch.setattr(geoip, "lookup", lambda address: ("", ""))
        assert parse(item, {"way": "refused"}) == {"way": "refused"}
        values = item.kind.export_values(item, {"way": "denied", "state": "9", "src": "ip"})
        assert values == {"ort_way": 4, "ort_lat": None, "ort_lon": None, "ort_acc": None, "ort_state": 9, "ort_state_src": 2,
                          "ort_plz": None}


def test_export_columns_codebook_and_open_columns(app, client, survey):
    setup(client)
    with app.app_context():
        from app import export

        item = db.session.get(Item, 2)
        cols = [c for c, _ in item.kind.export_columns(item)]
        assert cols == ["ort_way", "ort_lat", "ort_lon", "ort_acc", "ort_state", "ort_state_src", "ort_plz"]
        assert export.open_item_columns(item) == ["ort_lat", "ort_lon", "ort_plz"]  # left out of public data without the tick
        assert "1 = Position des Geräts" in item.kind.codebook_info(item, "ort_way", "de")[1]
        assert "Postleitzahl und Bundesland bleiben" in " ".join(item.kind.codebook_info(item, "ort_lat", "de")[2])
        item.config = {**item.config, "store": "state"}
        assert [c for c, _ in item.kind.export_columns(item)] == ["ort_way", "ort_acc", "ort_state", "ort_state_src"]
        assert export.open_item_columns(item) == []


def test_validate_asks_for_a_consent(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == []
        db.session.delete(item.survey.consent_item)
        db.session.flush()
        db.session.expire(item.survey)
        assert "Einwilligung" in item.kind.validate(item)[0]


def run_survey(app, client, answers, personal=6):
    fill_consent(client)
    setup(client)
    client.post("/surveys/1/meta", data={"title": "Test", "languages": ["de", "en"], "default_language": "de",
                                         "allow_back": "1", "retention_shown": "1", "retention_personal": str(personal),
                                         "retention_answers": "120"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    pages = []
    for data in answers:
        p = app.test_client()
        p.get("/s/1")
        p.post("/s/1", data=CONSENT_OK)
        pages.append(p.get("/s/1"))
        p.post("/s/1", data={"nav": "next", "i2": json.dumps(data)})
    return pages


def test_only_the_page_with_the_element_may_ask_for_the_position(app, client, survey):
    pages = run_survey(app, client, [{"way": "device", "lat": AACHEN[0], "lon": AACHEN[1], "acc": 12}])
    page = pages[0]
    assert "geolocation=(self)" in page.headers["Permissions-Policy"]
    assert 'data-geo="2"' in page.data.decode() and "geolocation.js" in page.data.decode()
    assert "geolocation=()" in client.get("/surveys/1/editor/").headers["Permissions-Policy"]
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert r.answers["2"] == {"way": "device", "lat": 50.776351, "lon": 6.083862, "acc": 12, "state": "5", "src": "pos",
                                  "plz": "52062"}


def test_consent_and_privacy_page_name_the_element(app, client, survey):
    run_survey(app, client, [])
    with app.app_context():
        s = db.session.get(Survey, 1)
        facts = s.consent_item.kind.facts(s.consent_item, "de")
        assert "exact position" in facts["location"]
        assert "Genaue Positionen werden 6 Monate nach dem Ende der Befragung auf Postleitzahl" in retention.consent_sentence(s, "de", False, False)
    privacy = app.test_client().get("/s/1/privacy").data.decode()
    assert "die genaue Position" in privacy and "auf Postleitzahl und Bundesland gekürzt" in privacy


def test_the_personal_period_cuts_positions_down_to_the_state(app, client, survey):
    run_survey(app, client, [{"way": "device", "lat": AACHEN[0], "lon": AACHEN[1], "acc": 12}, {"way": "refused"}],
               personal=1)
    client.post("/surveys/1/close")
    client.post("/surveys/1/reopen", data={"confirm": "1"})                # a saved state with the positions
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        from app import snapshots

        s = db.session.get(Survey, 1)
        folder = os.path.join(snapshots.folder(s), snapshots.listing(s)[0][0])
        with open(os.path.join(folder, "data.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("﻿resp_id,ort_way,ort_lat,ort_lon,ort_state\r\n1,1,50.776351,6.083862,5\r\n2,3,,,\r\n")
        assert retention.holds(s)["personal"] == 1
    client.post("/surveys/1/close")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.closed_at = utcnow() - timedelta(days=40)
        db.session.commit()
    client.get("/cron/close", headers={"X-Cron-Key": "k"})
    with app.app_context():
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="real").order_by(Response.id))]
        assert answers == [{"way": "device", "acc": 12, "state": "5", "src": "pos", "plz": "52062"}, {"way": "refused"}]
        assert retention.holds(db.session.get(Survey, 1))["personal"] == 0
    data = open(os.path.join(folder, "data.csv"), encoding="utf-8-sig", newline="").read()
    assert data == "resp_id,ort_way,ort_lat,ort_lon,ort_state\r\n1,1,,,5\r\n2,3,,,\r\n"


def test_dummy_answers_pass_the_check(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        rng = random.Random(3)
        for _ in range(30):
            fields = item.kind.dummy_fields(item, rng, {})
            assert item.kind.parse_answer(item, MultiDict(fields))["way"] in geolocation.WAYS


def test_results_page_shows_the_positions(app, client, survey):
    run_survey(app, client, [{"way": "device", "lat": AACHEN[0], "lon": AACHEN[1], "acc": 12}, {"way": "refused"}])
    page = client.get("/surveys/1/results").data.decode()
    assert "1 geteilte Position<" in page and "data-map-results" in page
    assert "selbst nicht angegeben" in page and "Nordrhein-Westfalen" in page


def test_the_postal_code_of_a_point():
    assert geolocation.plz_of(*AACHEN) == "52062"
    assert geolocation.plz_of(52.52, 13.405) == "10178"
    assert geolocation.plz_of(54.18, 7.89) == "27498"                 # Helgoland
    assert geolocation.plz_of(48.8566, 2.3522) is None


def test_conditions_and_quotas_read_the_state_of_the_location(app, client, survey):
    from app import conditions, flow, quotas

    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        described = conditions.describe(s)
        entry = described["st:2"]
        assert entry["label"].endswith("Bundesland (aus dem Standort)") and ("0", "nicht in Deutschland") in entry["options"]
        assert "2" not in described and conditions.describe(s, everything=True)["2"]["kind"] == "other"
        here = {2: {"way": "device", "state": "5", "plz": "52062", "src": "pos"}}
        assert flow.holds({"item": "st:2", "op": "eq", "value": "5"}, here)
        assert flow.holds({"item": "st:2", "op": "in", "value": "5,9"}, here)
        assert not flow.holds({"item": "st:2", "op": "eq", "value": "9"}, here)
        assert flow.holds({"item": "st:2", "op": "empty", "value": ""}, {2: {"way": "refused"}})
        assert flow.holds({"item": "st:2", "op": "eq", "value": "0"}, {2: {"way": "refused", "state": "0", "src": "ip"}})
        from app.models import Quota, QuotaCell

        quota = Quota(survey_id=1, name="Regionen", code="reg")
        quota.cells = [QuotaCell(code="nrw", name="NRW", target=5, conds=[{"item": "st:2", "op": "eq", "value": "5"}]),
                       QuotaCell(code="aus", name="Ausland", target=5, conds=[{"item": "st:2", "op": "eq", "value": "0"}])]
        db.session.add(quota)
        db.session.flush()
        assert not [p for p in quotas.check(s) if "Regionen" in p[1] and "Bedingung" in p[1]]
        assert quotas.match(quota, here).code == "nrw"


# --- stage 3: quota assistant, quality control, "Meine Position" ------------------------------------------------


def test_the_quota_assistant_takes_the_state_from_the_location(app, client, survey):
    from app import census, flow, quotas
    from tests.quotas.test_census import nodes, opts

    fill_consent(client)
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert [i.id for i in census.state_sources(s)] == [2] and census.questions(s)["state"].id == 2
        said = " ".join(census.steps(s, opts(n=1000)))
        assert "Das Bundesland ergibt sich aus dem Standort in „ort“" in said and "Screen-out" in said
        assert "keinen Standort in Deutschland teilt" in said
    assert "Standort (das Bundesland, in dem er liegt" in client.get("/surveys/1/flow/quotas/assistant?n=1000").data.decode()
    client.post("/surveys/1/flow/quotas/assistant", data={"n": "1000", "cross": "gender_state", "move": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert "federal_state" not in [i.type for b in s.blocks for i in b.items]
        top = flow.tree(s)["children"]
        assert nodes(s)[2:4] == [("branch", "Standort ohne Bundesland"), ("branch", "Standort nicht in Deutschland")]
        assert top[3]["cond"] == {"item": "st:2", "op": "eq", "value": "0"}
        assert s.quotas[0].cells[0].conds[0]["item"] == "st:2" and census.facts(s)["state"] == "geolocation"
        assert not [m for severity, m in quotas.check(s) if severity == "error"]
        assert not [f for f in flow.check(s) if f[0] == "error"]
        from app import weights

        assert weights.census_questions(s)["state"].id == 2


def test_quality_control_compares_the_location_with_the_ip_region(app, client, survey):
    from app import geoip

    run_survey(app, client, [{"way": "device", "lat": AACHEN[0], "lon": AACHEN[1], "acc": 12},
                             {"way": "device", "lat": 52.52, "lon": 13.405, "acc": 30}, {"way": "refused"}])
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.geo_ip = True
        for r, (country, state) in zip(db.session.scalars(db.select(Response).filter_by(source="real").order_by(Response.id)),
                                       [("DE", "5"), ("DE", "12"), ("DE", "5")]):
            r.geo_country, r.geo_state = country, state
        db.session.commit()
        rows = geoip.comparison(s)
        assert rows == [{"code": "ort", "what": "Standort", "n": 2, "same": 1, "other": 1, "abroad": 0, "unknown": 0}]


def test_the_map_marker_may_move_to_the_own_location(app, client, survey):
    fill_consent(client)
    add(client, "map_marker")                                        # 2
    client.post(E + "/items/2", data={"code": "site", "question": "Wo?", "min": "1", "max": "2", "center_lat": "50.77",
                                      "center_lon": "6.08", "zoom": "12"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    p = app.test_client()
    p.get("/s/1")
    p.post("/s/1", data=CONSENT_OK)
    page = p.get("/s/1")
    assert "geolocation=(self)" in page.headers["Permissions-Policy"] and "data-map-locate" in page.data.decode()
    assert "gespeichert wird davon nichts" in p.get("/s/1/privacy").data.decode()


# --- dashboards and the comparison with the own answer ---------------------------------------------------------


def test_a_dashboard_shows_states_and_a_heatmap_never_single_positions(app, client, survey):
    from app import boards
    from app.models import Board
    from tests.dashboards.test_boards import _answer, _board, _link, _stranger

    app.config["BOARD_CHECK_SECONDS"] = 0
    setup(client)
    _board(client)
    with app.app_context():
        rows = {r["key"]: r for g in boards.candidates(db.session.get(Survey, 1)) for r in g["rows"]}
        assert [v for v, _ in rows["item:2"]["views"]] == ["map", "heat", "bars", "table"]
    token = _link(client)
    # 8 people in one square of Aachen (exact positions differ), 6 in Munich, 1 in Berlin, 2 refusals
    for k in range(8):
        _answer(app, {"2": {"way": "device", "lat": 50.7763 + k / 10000, "lon": 6.0839, "acc": 10, "state": "5", "src": "pos"}})
    _answer(app, {"2": {"way": "device", "lat": 48.137, "lon": 11.575, "acc": 10, "state": "9", "src": "pos"}}, n=6)
    _answer(app, {"2": {"way": "device", "lat": 52.52, "lon": 13.405, "acc": 10, "state": "11", "src": "pos"}})
    _answer(app, {"2": {"way": "refused"}}, n=2)
    with app.app_context():
        board = db.session.get(Board, 1)
        board.tiles = [{**t, "view": "heat"} if t["key"] == "item:2" else t for t in board.tiles]
        db.session.commit()
        tile = {t["key"]: t for t in boards.state(db.session.get(Board, 1), fresh=True)["tiles"]}["item:2"]
    assert tile["n"] == 15 and tile["from_location"]
    assert sorted(c["count"] for c in tile["heat"]) == [6, 8] and tile["heat_left"] == 1    # Berlin alone is left out
    html = _stranger(app).get(f"/d/{token}/state").get_json()["html"]
    assert "b-heat" in html and "50.7763" not in html and "Raster von etwa 10 km" in html


def test_quality_control_compares_the_location_with_the_own_answer(app, client, survey):
    fill_consent(client)
    setup(client)
    add(client, "postal_code")                                       # 3
    client.post(E + "/items/3", data={"code": "plz", "question": "Ihre Postleitzahl?"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        for location, plz in [({"way": "device", "state": "5", "src": "pos"}, "52062"),
                              ({"way": "device", "state": "9", "src": "pos"}, "52062"),
                              ({"way": "device", "state": "0", "src": "pos"}, "10115"),
                              ({"way": "refused", "state": "5", "src": "ip"}, "52062")]:
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"2": location, "3": plz},
                                    started_at=utcnow(), completed_at=utcnow()))
        db.session.commit()
        rows = geolocation.comparison(db.session.get(Survey, 1))
    assert rows == [{"code": "ort", "against": "plz", "what": "Postleitzahl", "n": 3, "same": 1, "other": 1, "abroad": 1}]
    assert "Standort im Vergleich" in client.get("/surveys/1/quality").data.decode()


def test_the_method_draft_names_the_element_and_the_share(app, client, survey):
    """Concept "Lernhilfen für die neuen Funktionen" (L10): Standort in the method page and the study plan draft."""
    from app import methods

    run_survey(app, client, [{"way": "device", "lat": AACHEN[0], "lon": AACHEN[1], "acc": 12}, {"way": "refused"}])
    with app.app_context():
        f = methods.facts(db.session.get(Survey, 1))
        assert [(x["shared"], x["n"]) for x in f["location"]] == [(1, 2)]
        assert "1 von 2 Personen (50 %) teilten ihre Position" in methods.draft(f)
        assert "gespeichert wurde die genaue Position" in methods.draft(f)


def test_coarser_storage_raises_the_version_so_an_open_request_cannot_write_back(app, client, survey):
    """Audit 2026-10-21, B5: coarsen_item and coarsen write past the rows; without a new version a participant's
    request that had loaded the row before wrote the exact position back. Now its save fails as stale."""
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.exc import StaleDataError

    run_survey(app, client, [{"way": "device", "lat": AACHEN[0], "lon": AACHEN[1], "acc": 12}], personal=1)
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        rid, version, fine = r.id, r.version, dict(r.answers)
        assert geolocation.coarsen(db.session.get(Survey, 1)) == 1              # the period "personal" ends
        db.session.commit()
        db.session.expire_all()
        r = db.session.get(Response, rid)
        assert r.version == version + 1 and "lat" not in r.answers["2"]
        r.answers = fine                                                        # exact again, for the next step
        db.session.commit()
        before = r.version
        with Session(db.engine) as participant:                                 # a request that loaded the row
            open_row = participant.get(Response, rid)
            client.post(E + "/items/2", data={"code": "ort", "question": "Wo sind Sie gerade?", "store": "state",
                                              "correct": "1"})                 # coarser meanwhile
            open_row.answers = {**fine, "3": "late"}                            # its page saved with the old row
            with pytest.raises(StaleDataError):
                participant.commit()
        db.session.expire_all()
        r = db.session.get(Response, rid)
        assert r.version == before + 1 and "lat" not in r.answers["2"]
