"""UI/UX audit 2026-10-09, package D: data collection, quality, results and dashboards (X54 to X66 without X52/X53;
S17 to S19). Names instead of codes for links per person, the package of each person (link, status link, QR), saving
by itself with "Rückgängig", p in APA style everywhere, validity before the result of the SEM, dummies said."""
from app import panel, sem_model, units
from app.extensions import db
from app.models import EditStep, Response, Survey, utcnow
from tests.participation.test_panel_returns import MAXIMILES
from tests.participation.test_end_links import build as build_end


def _survey(app):
    return db.session.get(Survey, 1)


def _values(app, name="w"):
    with app.app_context():
        return next(p for p in panel.config(_survey(app))["params"] if p["name"] == name)["allowed"]


# --- X61: one notation for p ------------------------------------------------------------------------------------


def test_p_is_written_in_apa_style_without_the_leading_zero():
    assert units.p_bare(0.005, "de") == ",005" and units.p_bare(0.005, "en") == ".005"
    assert units.p_bare(0.0004, "de") == "< ,001" and units.p_bare(0.0004, "en") == "< .001"
    assert units.p_bare(0.515, "de") == ",515" and units.p_bare(None) == "–"
    assert units.p_bare(0.04996, "de") == ",04996"                    # just below .05: more places (p_places)
    assert units.p_bare(0.99996, "de") == "1,000"


# --- X54: validity before the result -----------------------------------------------------------------------------


def test_a_construct_with_low_cr_or_a_negative_loading_is_named():
    table = [{"label": "Zufriedenheit", "cr": 0.29, "flags": ["ave", "cr"],
              "items": [{"code": "z_1", "std": 0.6}, {"code": "z_4", "std": -0.34}]},
             {"label": "Nutzen", "cr": 0.85, "flags": [], "items": [{"code": "n_1", "std": 0.8}]},
             {"label": "Preis", "cr": 0.75, "flags": [], "items": [{"code": "p_2", "std": -0.1}, {"code": "p_3", "std": None}]}]
    weak = sem_model.weak_measurement(table)
    assert weak == [{"label": "Zufriedenheit", "cr": 0.29, "low": True, "negative": ["z_4"]},
                    {"label": "Preis", "cr": 0.75, "low": False, "negative": ["p_2"]}]
    assert sem_model.weak_measurement(table[1:2]) == []


# --- X56, X57, X58, X59: links per person --------------------------------------------------------------------------


def test_names_instead_of_codes_where_the_role_may_see_them(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna Schmidt\nBen Müller"})
    a, b = _values(app)
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "3"})
    with app.test_request_context():
        from flask_login import login_user

        from app.models import User

        login_user(db.session.scalar(db.select(User)))
        s = _survey(app)
        assert panel.shown_value(s, "w", a) == f"Anna Schmidt ({a})"
        assert panel.shown_value(s, "w", "zz") == "w = zz"
        quota = panel.cap_quota(s, "w")
        assert quota.name == "Höchstzahl je Link"                      # not "Obergrenze je Link (w)"
        assert panel.cap_cell_label(s, quota, b) == f"Ben Müller ({b})"
        cmp = {"by": "url:w", "groups": [{"code": a, "label": a}], "items": {"1": {"groups": [(a, a), (b, b)], "flat": [b]}}}
        named = panel.named_compare(s, cmp)
        assert named["groups"][0]["label"] == f"Anna Schmidt ({a})"
        assert named["items"]["1"]["groups"] == [(a, f"Anna Schmidt ({a})"), (b, f"Ben Müller ({b})")]
        assert named["items"]["1"]["flat"] == [f"Ben Müller ({b})"]
        assert cmp["groups"][0]["label"] == a                          # the kept comparison keeps the codes
    with app.test_request_context():                                    # nobody signed in: codes only
        s = _survey(app)
        assert panel.shown_value(s, "w", a) == f"w = {a}"
        assert panel.cap_cell_label(s, panel.cap_quota(s, "w"), a) == a
    # the quota on "Quoten" names the person beside the cell
    assert f"Anna Schmidt ({a})" in client.get("/surveys/1/flow/quotas/").data.decode()


def test_results_call_the_parameter_own_links(app, client, survey):
    from app import groupvars

    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna\nBen"})
    with app.test_request_context():
        var = groupvars.all_of(_survey(app))["url:w"]
        assert var.label == "Eigene Links" and var.groups[0][1] == var.groups[0][0]   # codes in what is computed


def test_removing_a_link_asks_nothing_and_can_be_undone(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna\nBen"})
    a, b = _values(app)
    html = client.get("/surveys/1/field").data.decode()
    assert "data-confirm-ok" not in html.split('id="value-remove"')[1].split("</table>")[0]
    r = client.post("/surveys/1/value-links/remove", data={"remove": f"w:{a}"}, follow_redirects=True)
    assert "Link für „Anna“ entfernt" in r.data.decode() and "flash-undo" in r.data.decode()
    assert _values(app) == [b]
    r = client.post("/surveys/1/editor/undo")
    assert r.headers["Location"].endswith("/field#value-links")
    assert _values(app) == [a, b]
    with app.app_context():
        assert panel.value_labels(_survey(app))["w"][a] == "Anna"


def test_the_maximum_per_link_saves_by_itself(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "count": "2"})
    client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "3"})
    html = client.get("/surveys/1/field").data.decode()
    form = html.split('class="stack value-links-limit"')[1].split("</form>")[0]
    assert "data-autosave-form" in html.split('class="stack value-links-limit"')[1][:40]
    assert ">Speichern</button>" not in form.replace("<noscript>", "§").split("§")[0]
    r = client.post("/surveys/1/value-links/limit", data={"param": "w", "limit": "4", "autosave": "1"})
    assert r.get_json()["status"] == "saved"
    with app.app_context():
        assert {c.target for c in panel.cap_quota(_survey(app), "w").cells} == {4}
        assert db.session.scalar(db.select(EditStep).order_by(EditStep.id.desc())).label == "Höchstzahl je Link geändert"


def test_status_link_to_copy_and_the_cards_carry_title_and_status(app, client, survey):
    client.post("/surveys/1/value-links", data={"param": "w", "names": "Anna", "board": "1"})
    (a,) = _values(app)
    html = client.get("/surveys/1/field").data.decode()
    assert "Ihr Stand" in html and 'id="vb-1"' in html and 'data-copy="#vb-1"' in html
    cards = client.get("/surveys/1/value-links/cards").data.decode()
    assert "Zurück zur Datenerhebung" in cards and "back-link" in cards and 'class="value-card-survey">Test<' in cards
    assert f'<wbr><span class="qr-url-query">?w={a}</span>' in cards and "Ihr Stand:" in cards and "/d/" in cards


# --- X55: what the paste does not take over -------------------------------------------------------------------------


def test_speeder_and_geo_links_stay_to_copy_after_taking_over(app, client, survey):
    build_end(app, client)
    client.post("/surveys/1/panel/paste", data={"action": "preview", "text": MAXIMILES + "\nAndere Zeile ohne Link"})
    part = client.get("/surveys/1/field").data.decode().split('id="panel-paste"')[1]
    assert "nicht übernommen: selbst an ein Ende im Ablauf setzen" in part and "1 Zeile ohne Adresse ignoriert." in part
    r = client.post("/surveys/1/panel/paste", data={"action": "apply"}, follow_redirects=True)
    page = r.data.decode()
    assert "Nicht übernommen: Speeder link, Geoip link" in page
    left = page.split('class="panel-paste-left"')[1].split('value="dismiss"')[0]
    assert "static-speeder?p={{p}}" in left and "static-geoip?p={{p}}" in left and "data-copy" in left
    client.post("/surveys/1/panel/paste", data={"action": "dismiss"})
    assert 'class="panel-paste-left"' not in client.get("/surveys/1/field").data.decode()


# --- X60, X63, S18: dummies ---------------------------------------------------------------------------------------


def test_the_dummy_button_names_the_error_that_stops_it(app, client, survey):
    html = client.get("/surveys/1/field").data.decode()               # an empty questionnaire: an error of the draft
    assert 'id="dummy-blocked"' in html and "Dummy-Daten brauchen eine Startprüfung ohne Fehler:" in html
    assert "außer der Einwilligung noch keine Elemente" in html.split('id="dummy-blocked"')[1]
    r = client.post("/surveys/1/dummy", headers={"Accept": "application/json"}, data={"n": "1"})
    assert r.status_code == 400 and "außer der Einwilligung noch keine Elemente" in r.get_json()["error"]


def test_the_card_of_a_draft_tells_real_from_dummy(app, client, survey):
    with app.app_context():
        for k in range(3):
            db.session.add(Response(survey_id=1, is_preview=True, source="dummy", answers={}, completed_at=utcnow()))
        db.session.commit()
    html = client.get("/dashboard/").data.decode()
    assert "0 abgeschlossen (echt) · 3 Dummy" in html


# --- X64 ----------------------------------------------------------------------------------------------------------


def test_the_duplicate_page_says_the_dashboards_go_with_it(app, client, survey):
    client.post("/surveys/1/dashboards/new", data={"name": "Bericht"})
    html = client.get("/surveys/1/duplicate").data.decode()
    assert "Dashboards mit ihren Kacheln, ohne Links" in html
