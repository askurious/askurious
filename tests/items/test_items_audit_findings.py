"""Items: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.helpers.audit_audit13_rest import Fake, SimpleNamespace, kind_of, np  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_c import _fresh_count, io, pytest  # noqa: F401
from tests.helpers.audit_audit_small import E, Item, MultiDict, Response, Survey, add, datetime, db, export, ranking  # noqa: F401
from tests.helpers.audit_cr8_e import _asset_selects, fill_consent, os, re  # noqa: F401
from tests.helpers.audit_cr8_g import NODE, STATIC as STATIC__cr8_g, _js, subprocess  # noqa: F401
from tests.helpers.audit_cr8_j import STATIC as STATIC__cr8_j, all_app_text, read  # noqa: F401


# --- from tests/audits/test_audit13_rest.py: Audit Daten und Berechnungen 2026-10-12, package I (exports, choice models, elements, rest): E-3, E-4, E-5, E-6, ---


def test_w3_nms_peak_reaches_the_curve():
    """300 cases, some with C = E (a jump, R-1): the reported peaks are those of the curve the page draws."""
    from app import psm_analysis as pa

    rng = np.random.default_rng(2)
    cases = []
    for i in range(300):
        c = float(rng.choice([5, 8, 10, 12, 15]))
        tc = c - float(rng.choice([2, 3, 5]))
        e = c + float(rng.choice([0, 3, 5, 8]))
        te = e + float(rng.choice([2, 4, 6]))
        cases.append({"tc": tc, "c": c, "e": e, "te": te, "nms_c": int(rng.integers(1, 6)),
                      "nms_e": int(rng.integers(1, 6)), "person": i})
    good = [x for x in cases if pa.consistent(x)]
    out = pa.nms(good, pa.curves(good)["prices"], [70, 50, 30, 10, 0])
    curve = out["curve"]
    assert abs(out["max_trial"]["value"] - max(curve["trial"])) < 1e-9
    assert abs(out["max_revenue"]["value"] - max(curve["revenue"])) < 1e-9
    assert out["max_trial"]["side"] == "below" and out["max_trial"]["price"] == 12
    assert out["max_revenue"]["side"] == "below" and out["max_revenue"]["price"] == 15


def test_m5_pool_of_joins_by_participation(monkeypatch):
    from app.items.sample_map import SampleMap

    kind = SampleMap()
    sx, sy = SimpleNamespace(id=10), SimpleNamespace(id=20)
    monkeypatch.setattr(SampleMap, "sources", lambda self, item: [sx, sy])
    xs = [(1, "x1"), (3, "x3")]
    ys = [(1, "y1"), (2, "y2"), (3, "y3")]
    assert kind.pool_of(None, [xs, ys]) == [{"10": "x1", "20": "y1"}, {"10": "x3", "20": "y3"}]


def test_m6_crowd_reversed_slider_stands_by_its_answer():
    from app.items.sample_crowd import SampleCrowd

    cfg = {"style": "slider", "min": 0, "max": 100, "step": 1, "reverse": True, "show_value": True, "points": 5}
    src = Fake("rating", "r", cfg, {"de": {"question": "Wie?", "left": "schlecht", "right": "gut"}})
    crowd = SampleCrowd()
    scale = crowd.scale(src, "de")
    assert scale["ends"] == ("schlecht", "gut")
    kind_, x = crowd.base(src, scale, 90)
    assert kind_ == "val" and abs(x - 0.90) < 1e-9


def test_m12_row_of_numbers_rule():
    from app.items.rating import NUMBERS_POINTS

    kind = kind_of("rating")
    ok = Fake("rating", "r", {"style": "numbers", "min": 0, "max": 10, "points": 5})
    assert not kind._numbers_problem(ok.config)
    assert kind._numbers_problem({"min": 0.5, "max": 5.5})
    assert kind._numbers_problem({"min": 0, "max": NUMBERS_POINTS})
    assert not kind._numbers_problem({"min": 0, "max": NUMBERS_POINTS - 1})


# --- from tests/audits/test_audit_2026_10_06_c.py: Audit of 2026-10-06, package C (uploads and resources): voice messages written only when the page goes on, with a ---


def test_the_images_of_an_upload_share_one_budget(app, client, survey):
    from PIL import Image

    from app import transfer

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(buf, "PNG")
    png = buf.getvalue()
    with app.app_context():
        budget = transfer.Budget()
        budget.pixels = 150
        first = transfer._Files({}, lambda name, most: png, budget)
        assert first.image("a.png")
        second = transfer._Files({}, lambda name, most: png, budget)              # the next survey of the same ZIP
        with pytest.raises(transfer.Refused):
            second.image("a.png")
        assert transfer._Files({}, lambda name, most: png).image("a.png")           # a file of its own: its own budget


# --- from tests/audits/test_audit_small.py: Smaller points of the data audit: codebook values, orders in loops, screen-out times, long JSON in .sav. ---


def test_codebook_values_for_ranking_slider_and_labels_with_semicolon(app, client, survey):
    ranking(client)                                                 # 2
    add(client, "slider")                                           # 3
    add(client, "single_choice")                                    # 4
    client.post(E + "/items/4", data=MultiDict([
        ("code", "job"), ("question", "Beruf?"), ("option_id", "a"), ("option_code", "1"),
        ("option_label", "angestellt; Vollzeit"), ("option_id", "b"), ("option_code", "2"), ("option_label", "selbstständig")]))
    with app.app_context():
        s = db.session.get(Survey, 1)
        book = {row[0]: row for row in export.codebook(s)}
        assert book["prio_1"][2:4] == ("Rang", "1 bis 3") and "prio_order" in book["prio_1"][4] and "1 = oben" in book["prio_1"][4]
        slider = db.session.get(Item, 3)
        assert book[slider.code][3] == "0 bis 100" and "Schrittweite 1" in book[slider.code][4]
        assert book["job"][3] == "1 = angestellt, Vollzeit; 2 = selbstständig"  # ";" would end the label for SPSS
        s.responses.append(Response(status=Response.COMPLETED, completed_at=datetime(2026, 9, 2),
                                    started_at=datetime(2026, 9, 2), answers={"4": "1"}))
        db.session.commit()
        _, _, meta = export._spss_dictionary(s, False, ("real",), 50, True, None)
        assert meta["job"]["pairs"] == [("1", "angestellt, Vollzeit"), ("2", "selbstständig")]


def test_map_question_code_ending_in_lat_is_not_a_coordinate(app, client, survey):
    from tests.items.test_map_marker import setup

    setup(client, mq_code=["why", "x_lat", ""])
    with app.app_context():
        s = db.session.get(Survey, 1)
        book = {row[0]: row for row in export.codebook(s)}
        assert book["site_1_x_lat"][2] == "Skala" and book["site_1_lat"][3] == "-90 bis 90"
        hidden = export.open_columns(s)
        assert "site_1_lat" in hidden and "site_json" in hidden and "site_1_x_lat" not in hidden


# --- from tests/audits/test_audit_small2.py: Small fixes from the audit of 2026-10-01 (S4, S6, S7, S8, S10, S11, D-1, D-3, D-4). ---


def test_position_map_and_word_list_inside_a_loop():
    pos, assoc = kind_of("position2d"), kind_of("association")
    item = SimpleNamespace(code="tech", config={"options": [{"id": "a", "code": "ki"}], "slots": 1},
                           t=lambda lang: {"options": {"a": "KI"}})
    table = [{"tech_ki_x_1": 10.0, "tech_ki_y_1": 20.0, "tech_ki_x_2": 30.0, "tech_ki_y_2": 40.0,
              "tech_1_1": "Angst", "tech_1_val_1": -2, "tech_1_2": "angst", "tech_1_val_2": -1}]
    assert pos.map_points(item, table, "de") == []                                    # no columns without a pass
    (label, mx, my, _, _, n), = pos.map_points(item, table, "de", passes=(1, 2))
    assert (label, mx, my, n) == ("KI", 20.0, 30.0, 2)
    assert assoc.terms(item, table, passes=(1, 2)) == [("angst", 2, -1.5, 1)]


# --- from tests/audits/test_audit_small3.py: Small things from the audit of 2026-10-01 (package 7): patterns that keep a server busy, a content policy, ---


def test_content_policy(app, client):
    for c, url in ((app.test_client(), "/"), (client, "/dashboard/")):
        policy = c.get(url).headers["Content-Security-Policy"]
        rules = dict(part.strip().split(" ", 1) for part in policy.split(";"))
        assert rules["default-src"] == "'self'" and rules["object-src"] == "'none'" and rules["base-uri"] == "'self'"
        assert rules["frame-ancestors"] == "'self'"
        assert rules["connect-src"] == "'self' https://nominatim.openstreetmap.org"  # the place search of the map item
        assert "https://tile.openstreetmap.org" in rules["img-src"].split() and "data:" in rules["img-src"].split()  # map tiles
        # the ORCID form answers with a redirect to orcid.org: allowed by name, not every https address (INJ-1)
        assert rules.get("form-action").startswith("'self'") and "https://orcid.org" in rules["form-action"].split()


# --- from tests/audits/test_cr8_e.py: Code review 2026-10-07, package E: computing time of results and exports. ---


def test_d5_design_check_reads_the_images_once(app, client, survey):
    from app import design, image_styles
    from app.models import Asset

    for _ in range(6):
        add(client, "single_choice")
    small = len(_asset_selects(app, client, "/surveys/1/flow/"))
    for _ in range(12):
        add(client, "single_choice")
    assert len(_asset_selects(app, client, "/surveys/1/flow/")) == small <= 2
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = s.blocks[-1].items[0]
        db.session.add(Asset(item_id=item.id, filename="a.png", source="ai", status="approved", style="altstil"))
        db.session.add(Asset(item_id=item.id, filename="b.png", source="ai", status="pending", style="altstil"))
        db.session.add(Asset(item_id=item.id, filename="c.png", source="upload", status="approved", style="altstil"))
        db.session.commit()
        found = [f for f in design.check(s) if f.item_id == item.id and "KI-Bild" in str(f.message)]
        assert len(found) == 1 and "1 KI-Bild" in str(found[0].message)
        assert image_styles.style_id(s) != "altstil"


def test_f9_map_scripts_once_per_results_page(app, client, survey):
    from app import dummy

    fill_consent(client)
    add(client, "map_marker")
    add(client, "map_marker")
    with app.app_context():
        s = db.session.get(Survey, 1)
        dummy.generate(s, 12, seed=1)
        db.session.execute(db.update(Response).values(source="real"))
        db.session.commit()
    page = client.get("/surveys/1/results").get_data(as_text=True)
    assert page.count("data-map-results") >= 2
    for script in ("vendor/leaflet/leaflet.js", "map_marker.js", "map_results.js", "leaflet.css"):
        assert page.count(script) == 1, script
    assert re.search(r"leaflet.js[^\"]*\" defer", page) and re.search(r"map_results.js[^\"]*\" defer", page)


def test_f9_participant_js_and_richtext_only_where_needed(app, client, survey):
    add(client, "single_choice")
    with app.app_context():
        item = db.session.scalars(db.select(Item).filter_by(type="single_choice")).first().id
    for url in ("/dashboard/", "/surveys/1/results", "/surveys/1/editor/", "/surveys/1"):
        html = client.get(url, follow_redirects=True).get_data(as_text=True)
        assert "participant.js" not in html, url
    assert "richtext.js" not in client.get("/dashboard/", follow_redirects=True).get_data(as_text=True)
    for url in (f"/surveys/1/editor/items/{item}", "/surveys/1", "/surveys/1/flow/", "/surveys/1/translation/"):
        assert "richtext.js" in client.get(url).get_data(as_text=True), url
    js = open(os.path.join(app.root_path, "static", "app.js"), encoding="utf-8").read()
    pjs = open(os.path.join(app.root_path, "static", "participant.js"), encoding="utf-8").read()
    for hook in ("data-submit-on-change", "data-select-all", "data-print", "form[data-inert]"):
        assert hook in js and hook in pjs
    assert js.count("window.qSmallHelpers = true") == 1 == pjs.count("window.qSmallHelpers = true")


# --- from tests/audits/test_cr8_g.py: Code review 2026-10-07, package G "Live, Editor-Skripte, Rechner": F1, F2, F7, F12, F16, F17, F20, F22, F23, F24, ---


@pytest.mark.parametrize("name", ["map_results.js", "postal_map.js"])
def test_f22_map_scripts_have_their_own_de_en(name):
    src = _js(name)
    assert 'function de_en(de, en) { return typeof window.de_en === "function"' in src


@pytest.mark.skipif(NODE is None, reason="needs Node")
def test_f22_map_results_draws_markers_without_app_js():
    script = """
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const layer = () => ({ addTo() { return this; }, bindPopup() { return this; } });
const box = { dataset: { mapResults: JSON.stringify({ view: "markers", shape: "pin", color: "#d00", points: [{ lat: 50, lon: 6, p: 7, k: 1, a: [] }] }) },
  querySelectorAll: () => [], querySelector: () => null };
global.window = { L: { marker: layer, featureGroup: layer, latLngBounds: () => ({ isValid: () => false }), heatLayer: layer },
  AskuriousMap: { baseMap: () => ({ hasLayer: () => false, removeLayer() {}, fitBounds() {} }), icon: () => ({}) } };
global.document = { documentElement: { lang: "de" }, querySelectorAll: (s) => (s === "[data-map-results]" ? [box] : []) };
eval(src);
console.log("ok");
"""
    done = subprocess.run([NODE, "-e", script, str(STATIC__cr8_g / "map_results.js")], capture_output=True, timeout=60)
    assert done.returncode == 0 and done.stdout.decode().strip() == "ok", done.stderr.decode()


def test_g3_slider_tap_unticks_dont_know():
    src = _js("participant.js")
    tap = src[src.index('range.addEventListener("pointerdown"'):]
    tap = tap[:tap.index("if (hidden.value")]
    assert 'querySelector("[data-dont-know]")' in tap and "dk.checked = false" in tap


# --- from tests/audits/test_cr8_i.py: Code review 2026-10-07, package I "Kleinere Fehler": R6, E3, E5, E6, E12, K5, K6, K7, T6, G11. ---


def test_r6_reversed_rating_is_drawn_mirrored(app):
    from app import dummy_plan

    with app.app_context():
        item = Item(id=5, type="rating", code="x", config={"style": "slider", "min": 0, "max": 10, "step": 1,
                                                            "reverse": True})
        draw = dummy_plan._writers({"vars": {"x": {"item": item, "kind": "observed"}}}, {"x"})["x"][2]
        # the export flips the answer (10 - x): a high z must arrive as a high exported value
        assert int(draw(2.0)) <= 1 and int(draw(-2.0)) >= 9
        item.config = {**item.config, "reverse": False}
        draw = dummy_plan._writers({"vars": {"x": {"item": item, "kind": "observed"}}}, {"x"})["x"][2]
        assert int(draw(2.0)) >= 9


# --- from tests/audits/test_cr8_j.py: Code review 2026-10-07, package J (tidying up): dead code, dead templates and CSS, one counter for the limits. ---


def test_f5_state_map_and_its_exports_are_gone():
    assert not (STATIC__cr8_j / "state_map.js").exists()
    postal = read("postal_map.js")
    assert "AskuriousAreas" not in postal and "AskuriousToken" not in postal
    assert "state_map.js" not in all_app_text((".py", ".js", ".css", ".html"))
    assert "p-assign-navstart" not in read("participant.js")
