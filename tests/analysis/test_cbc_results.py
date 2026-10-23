import math
import random

from app.extensions import db
from app.items.attributes import parse_number
from app.models import Item, Response, Survey, new_pid
from tests.analysis.test_conjoint import E, conjoint_form

TRUE = {"look": [0.6, 0.0, -0.6], "privacy": [0.5, -0.5]}
PRICES = [50, 100, 150]


def setup(client, price_attr=False, none=False, tasks=10, versions=4):
    from tests.conftest import add, fill_consent

    fill_consent(client)
    add(client, "conjoint")                                  # item 2
    form = conjoint_form(none=none, versions=versions)
    form.setlist("tasks", [str(tasks)])
    form.setlist("alternatives", ["3"])
    if price_attr:
        form.add("attr_price", "a2")                         # "Preis": 50 €, 100 €, 150 €
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")


def simulate(app, n, price_beta=-0.02, none_asc=None, seed=4):
    """n persons choosing by TRUE utilities + price_beta * price (+ Gumbel noise)."""
    rng = random.Random(seed)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        kind, c = item.kind, item.config
        codes = [a["code"] for a in c["attributes"]]

        def utility(profile):
            u = 0.0
            for code, lv in zip(codes, profile):
                u += PRICES[lv] * price_beta if code == "price" else TRUE[code][lv]
            return u - PRICES[1] * price_beta  # centred price, like a constant

        def gumbel():
            return -math.log(-math.log(rng.random()))

        for _ in range(n):
            v = rng.randrange(len(c["design"]["versions"]))
            answer = {}
            for t in range(c["tasks"]):
                u = [utility(kind.profile(item, v, t, a)) + gumbel() for a in range(c["alternatives"])]
                if none_asc is not None:
                    u.append(none_asc + gumbel())
                best = max(range(len(u)), key=u.__getitem__)
                answer[f"t{t}"] = 0 if best == c["alternatives"] else best + 1
            s.responses.append(Response(answers={"2": answer}, layout={"cj": {"2": {"version": v, "order": []}}},
                                        status=Response.COMPLETED, pid=new_pid(s.code)))
        db.session.commit()


def entry_of(app):
    from app import results

    with app.app_context():
        s = db.session.get(Survey, 1)
        return next(e for e in results.compute(s)["items"] if e["item"].id == 2)


def test_parse_number():
    assert [parse_number(t) for t in ("50 €", "1.299,90 €", "$49.99", "1,299", "2,5", "gratis")] == \
        [50, 1299.9, 49.99, 1.299, 2.5, None]


def test_parse_number_reads_like_conditions_and_names_unclear_prices():
    """M-9: minus signs, apostrophes and German thousands points; several numbers are no price."""
    from app.items.attributes import price_problem

    assert parse_number("−5 €") == -5 and parse_number("-5 €") == -5            # U+2212 was read as 5
    assert parse_number("1'000 CHF") == 1000 and parse_number("1 000 €") == 1000  # was 1
    assert parse_number("0.500") == 0.5                                               # was 500
    assert parse_number("5.000 €") == 5000 and parse_number("1.000.000 €") == 1000000
    assert parse_number("ab 3 Jahre 20 €") is None and parse_number("2 x 9,99 €") is None   # were 3 and 2
    assert "mehrere Zahlen (3, 20)" in price_problem("ab 3 Jahre 20 €")
    assert "nicht eindeutig" in price_problem("1,299 €") and price_problem("1.299 €") is None
    assert "keine Zahl" in price_problem("gratis") and price_problem("49,99 €") is None


def test_parse_number_reads_thousands_and_millions():
    """Audit 2026-10-03 (rest of M-9): "10k €" was read as 10; units like kg or km stay what they are."""
    assert parse_number("10k €") == 10000 and parse_number("10 K") == 10000
    assert parse_number("2,5 Tsd. €") == 2500 and parse_number("1,2 Mio. €") == 1200000
    assert parse_number("3 Mrd. €") == 3e9 and parse_number("10 Tausend Euro") == 10000
    assert parse_number("10 kg") == 10 and parse_number("5 km") == 5 and parse_number("10 kWh") == 10


def test_counts_and_model_recover_part_worths(app, client, survey):
    setup(client)
    simulate(app, 200, price_beta=-0.012)
    e = entry_of(app)
    c, m = e["cbc"]["counts"], e["cbc"]["model"]
    assert e["answered"] == 200 and c["tasks"] == 2000 and not c["none_offered"]
    look = next(a for a in c["attributes"] if a["code"] == "look")["levels"]
    assert look[0]["share"] > 1 / 3 > look[2]["share"]
    assert m["converged"] and not m["warnings"] and m["asc"] is None and m["price"] is None
    est = {a["code"]: [lv["beta"] for lv in a["levels"]] for a in m["attributes"]}
    for code, true in TRUE.items():
        assert all(abs(x - t) < 0.2 for x, t in zip(est[code], true)), (code, est[code])
    # the price attribute without the flag: effects-coded, falling with the price
    assert est["price"][0] > est["price"][1] > est["price"][2]
    html = client.get("/surveys/1/results").data.decode()
    assert "Wahlanteile je Stufe" in html and "Aggregiertes Logit-Modell" in html and "Zahlungsbereitschaft" not in html


def test_price_attribute_gives_linear_term_and_wtp(app, client, survey):
    setup(client, price_attr=True, none=True)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.price_attribute(item) == 1 and item.kind.validate(item) == []
    simulate(app, 250, price_beta=-0.02, none_asc=-0.5)
    e = entry_of(app)
    c, m = e["cbc"]["counts"], e["cbc"]["model"]
    assert c["none_offered"] and 0 < c["none_share"] < 0.5
    assert m["converged"] and not m["warnings"]
    assert abs(m["price"]["beta"] + 0.02) < 0.006 and m["price"]["se"] < 0.004
    assert [a["code"] for a in m["attributes"]] == ["look", "privacy"]   # price is the linear term
    # the price enters centred on the mean price, so the constant is the one against an offer at that price
    assert m["asc"] is not None and abs(m["asc"] - (-0.5)) < 0.4 and m["price_centered"]
    look = m["attributes"][0]["levels"]
    assert look[0]["wtp"] > 15 and look[2]["wtp"] < -15                  # 0.6 / 0.02 = 30 currency units
    assert abs(sum(a["importance"] for a in m["attributes"]) + m["price"]["importance"] - 1) < 1e-9
    html = client.get("/surveys/1/results").data.decode()
    assert "Zahlungsbereitschaft" in html and "Konstante „Keine davon“" in html and "Preis <span" in html


def test_price_attribute_checks_and_editor(app, client, survey):
    setup(client, price_attr=True)
    html = client.get(E + "/items/2").data.decode()
    assert 'name="attr_price" value="a2" checked' in html
    form = conjoint_form()
    form.add("attr_price", "a1")                             # "humanoid" is not a number
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert any("nicht jede Stufe ist eindeutig eine Zahl" in p and "„humanoid“ enthält keine Zahl" in p
                   for p in item.kind.validate(item))
    form.add("attr_price", "a2")
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert any("Höchstens ein Merkmal" in p for p in item.kind.validate(item))


def test_small_sample_warning_and_empty(app, client, survey):
    setup(client)
    e = entry_of(app)
    assert e["cbc"]["counts"]["persons"] == 0 and e["cbc"]["model"] is None
    # no participation at all: the page offers its way in instead of empty panels (UI audit 2026-10-12, U26)
    assert "Noch keine Teilnahmen in der Grundlage" in client.get("/surveys/1/results").data.decode()
    simulate(app, 8)
    m = entry_of(app)["cbc"]["model"]
    assert any("Nur 8 Personen" in w for w in m["warnings"])


def test_part_worth_charts_are_zero_centred_on_one_scale():
    from app.choice_model import charts

    model = {"attributes": [
        {"label": "A", "levels": [{"label": "a1", "beta": 0.8, "se": 0.1}, {"label": "a2", "beta": -0.8, "se": 0.1}]},
        {"label": "B", "levels": [{"label": "b1", "beta": 0.2, "se": None}, {"label": "b2", "beta": 0.1, "se": 0.05},
                                  {"label": "b3", "beta": -0.3, "se": 0.05}]}]}
    a, b = charts(model)
    assert a["axis_x"] == b["axis_x"] and a["ticks"] == b["ticks"] and a["ticks"][-1][1] == 1  # nice end 1.0 >= 0.996
    pos, neg = a["rows"]
    assert pos["positive"] and pos["x"] == a["axis_x"] and not neg["positive"]
    assert abs(neg["x"] + neg["w"] - a["axis_x"]) < 0.2 and abs(pos["w"] - neg["w"]) < 0.2
    # one scale: 0.2 in B is a quarter of 0.8 in A
    assert abs(b["rows"][0]["w"] * 4 - pos["w"]) < 0.5
    assert b["rows"][0]["lo"] is None and pos["lo"] < pos["x"] + pos["w"] < pos["hi"]


def test_price_attribute_levels_as_centred_bars(app, client, survey):
    setup(client, price_attr=True)
    simulate(app, 120, price_beta=-0.02)
    m = entry_of(app)["cbc"]["model"]
    price = m["price_levels"]["levels"]
    assert [round(p["beta"] / m["price"]["beta"]) for p in price] == [-50, 0, 50]   # beta x (price - 100)
    assert len(m["charts"]) == len(m["attributes"]) + 1
    html = client.get("/surveys/1/results").data.decode()
    assert html.count('class="pw-chart pw-wide"') == 3 and 'class="pw-neg"' in html and 'class="pw-pos"' in html
    assert "Koeffizient × (Preis − mittlerer Preis der Stufen)" in html and "nullzentriert" in html


def test_importance_bars_most_important_first(app, client, survey):
    import re

    setup(client, price_attr=True)
    simulate(app, 120, price_beta=-0.02)
    html = client.get("/surveys/1/results").data.decode()
    bars = re.search(r'<ul class="plain res-bars cm-importance-bars">(.*?)</ul>', html, re.S).group(1)
    shares = [int(x) for x in re.findall(r'class="res-bar-n">(\d+)\s*%', bars)]
    labels = re.findall(r'class="res-bar-label">([^<]+?)\s*(?:<|$)', bars)
    assert len(shares) == 3 and shares == sorted(shares, reverse=True)
    m = entry_of(app)["cbc"]["model"]
    expected = sorted([(a["importance"], a["label"]) for a in m["attributes"]] + [(m["price"]["importance"], "Preis")],
                      reverse=True)
    assert labels == [label for _, label in expected]


def test_part_worth_bars_sorted_largest_first():
    from app.choice_model import charts

    model = {"attributes": [{"label": "A", "levels": [
        {"label": "low", "beta": -0.5, "se": 0.1}, {"label": "high", "beta": 0.7, "se": 0.1},
        {"label": "mid", "beta": -0.2, "se": 0.1}]}]}
    rows = charts(model)[0]["rows"]
    assert [r["label"] for r in rows] == ["high", "mid", "low"]
    assert [r["y"] for r in rows] == sorted(r["y"] for r in rows)           # top to bottom
    assert [lv["label"] for lv in model["attributes"][0]["levels"]] == ["low", "high", "mid"]   # input untouched


def test_large_prices_show_a_readable_coefficient(app, client, survey):
    """Audit 2026-10-03, C-2: with prices of 20.000 to 30.000 the page showed "−0,0001 (SE 0,0000)"; now the
    coefficient and its SE are given per 1.000 currency units."""
    import re

    from tests.conftest import add, fill_consent

    fill_consent(client)
    add(client, "conjoint")
    form = conjoint_form(versions=4)
    form.setlist("tasks", ["10"])
    form.setlist("alternatives", ["3"])
    form.setlist("lvl_a2_label", ["20.000 €", "25.000 €", "30.000 €"])
    form.add("attr_price", "a2")
    client.post(E + "/items/2", data=form)
    client.post(E + "/items/2/conjoint/design")
    simulate(app, 150, price_beta=-0.02)
    html = client.get("/surveys/1/results").data.decode()
    found = re.search(r"Preiskoeffizient je 1\.000 Währungseinheiten: (\S+) \(SE (\S+)\)", html)
    assert found and found.group(2) != "0,0000" and float(found.group(2).replace(",", ".")) > 0.001


def test_none_switched_off_leaves_old_none_answers_out(app, client, survey):
    """Code review 2026-10-15, C5: answers "Keine davon" (0) from before the option was switched off made the results
    page fail (IndexError) and HB read them as another alternative. They are left out now, as choices outside the
    alternatives are."""
    from app import cbc_analysis, choice_model, hb

    setup(client, none=True)
    simulate(app, 60, none_asc=0.0)
    with app.app_context():
        item = db.session.get(Item, 2)
        cfg = dict(item.config)
        cfg["none"] = False
        item.config = cfg
        s = db.session.get(Survey, 1)
        r0 = s.responses[0]
        r0.answers = {"2": {**r0.answers["2"], "t0": 7}}  # a choice the design does not have
        db.session.commit()
        nones = sum(1 for r in s.responses for v in r.answers["2"].values() if v == 0)
        sets, _, _, _ = cbc_analysis.choice_sets(item, list(s.responses))
        assert nones and len(sets) == 60 * 10 - nones - 1
        assert all(0 <= x.chosen < len(x.X) == 3 for x in sets)
    e = entry_of(app)
    assert e["cbc"]["model"]["converged"]
    assert client.get("/surveys/1/results").status_code == 200
    # the models themselves leave out a set whose choice lies outside it
    bad = [choice_model.ChoiceSet([[1.0], [0.0]], 2, person=0), choice_model.ChoiceSet([[1.0], [0.0]], 0, person=0)]
    assert [len(c) for _, c in choice_model._groups(bad)] == [1]
    assert hb.Data([bad], 1).sets_per_person.tolist() == [1.0]
