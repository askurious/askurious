"""Country and federal state estimated from the IP address (survey setting "geo_ip", app/geoip.py)."""
import csv
import io

from app import geoip
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions

E = "/surveys/1/editor"
META = {"title": "Test", "languages": ["de", "en"], "default_language": "de", "allow_back": "1"}
AACHEN, GOOGLE = "134.130.4.1", "8.8.8.8"  # RWTH Aachen (Nordrhein-Westfalen = 5), a US address


def build(app, client, on=True):
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/meta", data={**META, **({"geo_ip": "1"} if on else {})})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def from_(address):
    return {"environ_overrides": {"REMOTE_ADDR": address}}


def rows(app):
    with app.app_context():
        return [(r.geo_country, r.geo_state) for r in db.session.scalars(
            db.select(Response).filter_by(survey_id=1, is_preview=False).order_by(Response.id))]


def test_lookup_knows_country_and_state_and_what_says_nothing():
    assert geoip.month()
    assert geoip.lookup(AACHEN) == ("DE", "5")
    assert geoip.lookup("::ffff:" + AACHEN) == ("DE", "5")      # an IPv4 address in IPv6 clothes
    assert geoip.lookup("2001:638:408::1") == ("DE", "5")       # the same network over IPv6
    assert geoip.lookup(GOOGLE) == ("US", "")                   # a state only inside Germany
    for nothing in ("127.0.0.1", "10.1.2.3", "192.168.0.1", "::1", "fe80::1", "", None, "nonsense", "255.255.255.255"):
        assert geoip.lookup(nothing) == ("", "")
    # every state of the table is one of the element Bundesland
    from app.items.federal_state import STATE_CODES

    states = {c.partition("-")[2] for c in geoip._table()[0]["codes"] if c.startswith("DE-")}
    assert states == set(STATE_CODES)


def test_looked_up_once_with_the_agreement_and_only_the_result_is_kept(app, client, survey):
    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}", **from_(AACHEN))
    assert rows(app) == []                                       # opening the link stores nothing
    p.post(f"/s/{uid}", data=CONSENT_OK, **from_(AACHEN))
    assert rows(app) == [("DE", "5")]
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "darum"}, **from_(GOOGLE))
    assert rows(app) == [("DE", "5")]                            # not again: a later page from elsewhere changes nothing
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(survey_id=1, is_preview=False))
        assert AACHEN not in str({c.name: getattr(r, c.name) for c in Response.__table__.columns})
    # an address that says nothing (the local proxy): looked up, without a result
    q = app.test_client()
    q.get(f"/s/{uid}")
    q.post(f"/s/{uid}", data=CONSENT_OK)
    assert rows(app)[1] == ("", "")
    # who declines is not looked up (and not stored at all)
    d = app.test_client()
    d.get(f"/s/{uid}", **from_(AACHEN))
    d.post(f"/s/{uid}", data={"i1": "0"}, **from_(AACHEN))
    assert len(rows(app)) == 2


def test_without_the_setting_nothing_is_looked_up(app, client, survey):
    uid = build(app, client, on=False)
    p = app.test_client()
    p.get(f"/s/{uid}", **from_(AACHEN))
    p.post(f"/s/{uid}", data=CONSENT_OK, **from_(AACHEN))
    assert rows(app) == [(None, None)]
    assert "geo_country" not in client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]
    assert "IP-Adresse wird einmalig" not in client.get(f"/s/{uid}/privacy").data.decode()


def test_export_and_codebook_carry_the_two_columns(app, client, survey):
    uid = build(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}", **from_(AACHEN))
    p.post(f"/s/{uid}", data=CONSENT_OK, **from_(AACHEN))
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "darum"}, **from_(AACHEN))
    text = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig")
    row = next(csv.DictReader(io.StringIO(text)))
    assert (row["geo_country"], row["geo_state"]) == ("DE", "5")
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "geo_state" in book and "DB-IP" in book and "10.1145/3131365.3131380" in book


def test_setting_is_named_in_consent_privacy_and_fixed_after_the_start(app, client, survey):
    from app import design, duplicate, transfer

    fill_consent(client)
    settings = client.get("/surveys/1").data.decode()
    assert 'name="geo_ip"' in settings and "IP Geolocation by DB-IP" in settings and "Poese" in settings
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not s.geo_ip and "IP-Adresse schätzen" not in s.consent_item.kind.template_body(s.consent_item, "de")
    client.post("/surveys/1/meta", data={**META, "geo_ip": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        consent = s.consent_item
        assert s.geo_ip
        assert "Aus Ihrer IP-Adresse schätzen wir einmalig" in consent.kind.template_body(consent, "de")
        assert "From your IP address we estimate once" in consent.kind.template_body(consent, "en")
        assert consent.kind.facts(consent, "de")["ip_region"]
        # the edited consent text ("Text") does not name it: the pre-launch check says so
        assert any("geschätzt aus der IP-Adresse" in f.message for f in design.check(s))
        assert '"geo_ip": true' in str(transfer.dumps(s, with_files=False))
        copy = duplicate.create(s, s.members[0], "Kopie")
        assert (copy[0] if isinstance(copy, tuple) else copy).geo_ip
    assert "Land und Bundesland aus der IP-Adresse: werden geschätzt" in client.get(E + "/items/1").data.decode()
    with app.app_context():
        uid = db.session.get(Survey, 1).public_key
    privacy = client.get(f"/s/{uid}/privacy").data.decode()
    assert "Aus Ihrer IP-Adresse wird einmalig geschätzt" in privacy and "IP Geolocation by DB-IP" in privacy
    assert "it is estimated once which country" in client.get(f"/s/{uid}/privacy?lang=en").data.decode()
    # fixed once the survey runs: the consent named it
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    fill_questions(app)  # every element needs a question before the start (pre-launch check, V11)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    client.post("/surveys/1/meta", data=META)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING and s.geo_ip


def test_admin_page_shows_what_the_server_finds_for_the_own_address(app, client):
    page = client.get("/admin/database", **from_(AACHEN)).data.decode()
    assert f"Stand {geoip.month()}" in page and "DE, Nordrhein-Westfalen" in page
    local = client.get("/admin/database").data.decode()
    assert "Die App sieht keine öffentliche Adresse" in local and "PROXY_COUNT" in local
    assert app.test_client().get("/admin/ip-region").status_code in (302, 401, 403)


# --- conditions of the flow and of Teilquoten (stage 2) --------------------------------------------------------

def test_conditions_offer_country_and_state_only_with_the_setting(app, client, survey):
    from app import conditions

    fill_consent(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not [r for r in conditions.describe(s, everything=True) if str(r).startswith("geo:")]
    client.post("/surveys/1/meta", data={**META, "geo_ip": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        for everything in (True, False):                             # the flow and the Teilquoten
            d = conditions.describe(s, everything=everything)
            assert ("DE", "Deutschland") in d["geo:country"]["options"]
            assert ("5", "Nordrhein-Westfalen") in d["geo:state"]["options"] and len(d["geo:state"]["options"]) == 16
        assert conditions.text(s, {"item": "geo:state", "op": "in", "value": "5, 9"}) == "IP-Bundesland: Nordrhein-Westfalen oder Bayern"
        assert conditions.text(s, {"item": "geo:country", "op": "ne", "value": "DE"}).startswith("IP-Land")
        assert "nicht ermittelt" in conditions.text(s, {"item": "geo:country", "op": "empty", "value": ""})


def screen_out_abroad(app, client):
    """Block 2 (a question) only for Germany; everybody else ends at a screen-out right behind the consent."""
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post(E + "/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/meta", data={**META, "geo_ip": "1"})
    with app.app_context():
        from app import flow

        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1},
            {"t": "branch", "id": "b1", "name": "Ausland", "cond": {"item": "geo:country", "op": "ne", "value": "DE"},
             "children": [{"t": "end", "id": "e1", "kind": "screenout"}]},
            {"t": "block", "block": 2},
        ]}
        flow.normalize(s)
        db.session.commit()
        assert not [p for p in flow.check(s) if p[0] == "error"], flow.check(s)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING
        return s.code


def test_a_condition_right_behind_the_consent_sees_the_country(app, client, survey):
    uid = screen_out_abroad(app, client)
    home = app.test_client()
    home.get(f"/s/{uid}", **from_(AACHEN))
    page = home.post(f"/s/{uid}", data=CONSENT_OK, follow_redirects=True, **from_(AACHEN)).data.decode()
    assert "Warum?" in page
    abroad = app.test_client()
    abroad.get(f"/s/{uid}", **from_(GOOGLE))
    abroad.post(f"/s/{uid}", data=CONSENT_OK, follow_redirects=True, **from_(GOOGLE))
    with app.app_context():
        got = {(r.geo_country, r.status) for r in db.session.scalars(db.select(Response).filter_by(survey_id=1, is_preview=False))}
    assert got == {("DE", Response.ACTIVE), ("US", Response.SCREENOUT)}
    # the Feld page counts both, the most frequent first, in the database
    field = client.get("/surveys/1/field").data.decode()
    assert "Herkunft nach IP-Adresse" in field and "Nordrhein-Westfalen" in field and "USA" in field
    with app.app_context():
        counts = geoip.field_counts(db.session.get(Survey, 1))
    assert sorted(counts["countries"]) == [("Deutschland", 1, 0), ("USA", 1, 0)] and counts["states"] == [("Nordrhein-Westfalen", 1, 0)]


def test_condition_without_the_setting_is_named_by_the_design_check(app, client, survey):
    from app import flow

    fill_consent(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": "geo:state", "op": "eq", "value": "5"},
             "children": [{"t": "end", "id": "e1", "kind": "screenout"}]}]}
        flow.normalize(s)
        db.session.commit()
        assert any("aus der IP-Adresse" in message and severity == "error" for severity, message, _ in flow.check(s))


def test_preview_tries_out_a_region_instead_of_the_own_address(app, client, survey):
    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post("/surveys/1/meta", data={**META, "geo_ip": "1"})
    bar = client.get("/surveys/1/editor/preview", follow_redirects=True).data.decode()
    assert "IP-Region" in bar and "Deutschland · Bayern" in bar and "wie diese Anfrage" in bar
    client.get("/surveys/1/editor/preview?geo=DE-9", follow_redirects=True)
    client.post("/surveys/1/editor/preview", data=CONSENT_OK, **from_(AACHEN))
    with app.app_context():
        run = db.session.scalar(db.select(Response).filter_by(survey_id=1, source="test").order_by(Response.id.desc()))
        assert (run.geo_country, run.geo_state) == ("DE", "9")       # the choice, not the address of the request
    client.get("/surveys/1/editor/preview?geo=auto", follow_redirects=True)
    client.post("/surveys/1/editor/preview", data=CONSENT_OK, **from_(AACHEN))
    with app.app_context():
        run = db.session.scalar(db.select(Response).filter_by(survey_id=1, source="test").order_by(Response.id.desc()))
        assert (run.geo_country, run.geo_state) == ("DE", "5")
    assert geoip.simulated("DE-99") is None and geoip.simulated("AT-5") is None and geoip.simulated("none") == ("", "")


# --- comparison with what people say, the map, dummy data (stage 3) --------------------------------------------

def test_quality_page_compares_the_estimate_with_postal_code_and_state(app, client, survey):
    fill_consent(client)
    add(client, "postal_code")                                  # 2
    add(client, "federal_state")                                # 3
    client.post(E + "/items/2", data={"code": "plz", "question": "Postleitzahl?", "answer_mode": "optional"})
    client.post(E + "/items/3", data={"code": "land", "question": "Bundesland?", "answer_mode": "optional"})
    client.post("/surveys/1/meta", data={**META, "geo_ip": "1"})
    assert "keine Frage nach Postleitzahl" not in client.get("/surveys/1/quality").data.decode()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    # (address, postal code, state): Aachen from Aachen, Munich from Aachen, Aachen from the USA, Aachen from nowhere
    for address, plz, state in ((AACHEN, "52062", "5"), (AACHEN, "80331", "9"), (GOOGLE, "52062", "5"), ("127.0.0.1", "52062", "5")):
        p = app.test_client()
        p.get(f"/s/{uid}", **from_(address))
        p.post(f"/s/{uid}", data=CONSENT_OK, **from_(address))
        p.post(f"/s/{uid}", data={"nav": "next", "i2": plz, "i3": state}, follow_redirects=True, **from_(address))
    with app.app_context():
        s = db.session.get(Survey, 1)
        rows = geoip.comparison(s, ("real",), True)
        assert [(r["code"], r["n"], r["same"], r["other"], r["abroad"], r["unknown"]) for r in rows] == [
            ("plz", 4, 1, 1, 1, 1), ("land", 4, 1, 1, 1, 1)]
        assert geoip.comparison(s, ("dummy",), True)[0]["n"] == 0
    page = client.get("/surveys/1/quality").data.decode()
    assert "IP-Region im Vergleich" in page and "gleiches Bundesland" in page and "(25 %)" in page
    assert "Komosny" in page                                     # the sources in the one fold of the page
    # the Feld page draws the states of the started participations
    field = client.get("/surveys/1/field").data.decode()
    assert "res-state-map" in field and "Karte der begonnenen Teilnahmen" in field


def test_quality_page_without_the_setting_or_without_a_question(app, client, survey):
    fill_consent(client)
    assert "IP-Region im Vergleich" not in client.get("/surveys/1/quality").data.decode()
    client.post("/surveys/1/meta", data={**META, "geo_ip": "1"})
    assert "keine Frage nach Postleitzahl oder Bundesland und kein Element Standort" in client.get("/surveys/1/quality").data.decode()


def test_dummy_data_draw_a_region_instead_of_the_researchers_address(app, client, survey):
    import random

    from app import dummy

    fill_consent(client)
    add(client, "text_input")                                   # 2
    client.post("/surveys/1/meta", data={**META, "geo_ip": "1"})
    with app.test_request_context(environ_overrides={"REMOTE_ADDR": GOOGLE}), app.app_context():
        s = db.session.get(Survey, 1)
        dummy.generate(s, 40, seed=7)
        got = [(r.geo_country, r.geo_state) for r in db.session.scalars(db.select(Response).filter_by(survey_id=1, source="dummy"))]
    assert len(got) == 40 and ("US", "") not in got
    assert sum(1 for country, state in got if country == "DE" and state) >= 30
    rng = random.Random(1)
    drawn = [geoip.dummy(rng) for _ in range(3000)]
    north_rhine = sum(1 for d in drawn if d == ("DE", "5")) / sum(1 for d in drawn if d[0] == "DE")
    assert 0.18 < north_rhine < 0.26                             # 21.5 % of the population
