import math
import random

import numpy as np

from app import choice_model
from app.extensions import db
from app.models import Item, Response, Survey
from tests.analysis.test_acbc import setup

TRUE = {"look": [0.8, 0.0, -0.8], "speed": [-0.6, 0.1, 0.5], "privacy": [0.7, -0.7], "voice": [0.2, 0.3, -0.5]}


def test_conditional_logit_recovers_known_parameters():
    rng = np.random.default_rng(1)
    coding = choice_model.Coding([3, 2], price_scale=100.0)
    true = np.array([1.0, -0.5, 0.6, -0.3, -1.2])   # levels (effects), threshold, price per 100
    sets = []
    for _ in range(4000):
        size = rng.integers(2, 4)
        X = [coding.row([rng.integers(3), rng.integers(2)], rng.uniform(50, 250)) for _ in range(size)]
        if size == 2 and rng.random() < 0.5:
            X[1] = coding.asc_row()                   # screening-like: concept vs. threshold
        u = np.array(X) @ true + rng.gumbel(size=len(X))
        sets.append(choice_model.ChoiceSet(X, int(np.argmax(u))))
    beta, cov, ll, converged, _ = choice_model.fit(sets, coding.k)
    assert converged and cov is not None
    se = np.sqrt(np.diag(cov))
    assert np.all(np.abs(beta - true) < 4 * se) and np.all(np.abs(beta - true) < 0.2)
    pw = coding.part_worths(beta, cov)
    look, privacy = pw[0][0], pw[1][0]
    # effects coding: the last level is minus the sum of the others (true: 1.0, -0.5, -0.5)
    assert abs(sum(look)) < 1e-9 and abs(look[2] + 0.5) < 0.2 and look[0] > look[1] and privacy[0] > 0


def simulate(item, n, seed=3, price_beta=-0.004):
    """ACBC answers of n persons choosing by TRUE utilities (+ Gumbel noise), without rule questions."""
    kind = item.kind
    rng = random.Random(seed)
    codes = [a["code"] for a in item.config["attributes"]]
    comps = kind.price_components(item)

    def utility(profile, price=None):
        u = sum(TRUE[c][lv] for c, lv in zip(codes, profile))
        base = (item.config.get("price") or {}).get("base") or 0
        return u + (price_beta * (price - base) if price is not None and kind.price_on(item) else 0)

    def gumbel():
        return -math.log(-math.log(rng.random()))

    out = []
    for p in range(n):
        byo = [max(range(len(TRUE[c])), key=lambda lv: TRUE[c][lv] + price_beta * comps[a][lv] * kind.price_on(item)
                   + 0.5 * gumbel()) for a, c in enumerate(codes)]
        attrs = item.config["attributes"]
        seed_p = rng.randrange(2 ** 31)
        answer = {"seed": seed_p, "k": item.config["per_screen"],
                  "byo": {a["id"]: a["levels"][lv]["id"] for a, lv in zip(attrs, byo)},
                  "concepts": kind.make_concepts(item, byo, seed_p)}
        threshold = 0.3
        answer["s"] = {str(i): int(utility(x["p"], x.get("price")) + gumbel() > threshold + gumbel())
                       for i, x in enumerate(answer["concepts"])}
        answer["t"] = {}
        for _ in range(20):
            rounds, _, win = kind.tournament(item, answer)
            if win is not None or not rounds or rounds[-1][2] is not None:
                break
            r, group, _ = rounds[-1]
            best = max(group, key=lambda c: utility(answer["concepts"][c]["p"], answer["concepts"][c].get("price")) + gumbel())
            answer["t"][str(r)] = best
        out.append(answer)
    return out


def add_responses(survey, answers):
    from app.models import new_pid

    for a in answers:
        survey.responses.append(Response(answers={"2": a}, layout={}, status=Response.COMPLETED,
                                         pid=new_pid(survey.code)))
    db.session.commit()


def test_aggregate_logit_recovers_simulated_part_worths(app, client, survey):
    setup(client, screens=5, per_screen=4)
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        add_responses(s, simulate(item, 150))
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        m = entry["acbc"]["model"]
        assert entry["answered"] == 150 and m["converged"] and not m["warnings"]
        for attr in m["attributes"]:
            est = [lv["beta"] for lv in attr["levels"]]
            true = TRUE[attr["code"]]
            assert abs(sum(est)) < 1e-9
            # same order of the clearly different levels, and close in value (effects of true minus mean)
            centred = [t - sum(true) / len(true) for t in true]
            assert all(abs(e - t) < 0.35 for e, t in zip(est, centred)), (attr["code"], est, centred)
        imp = {a["code"]: a["importance"] for a in m["attributes"]}
        assert imp["look"] > imp["voice"] and abs(sum(imp.values()) - 1) < 1e-9
        c = entry["acbc"]["counts"]
        look = next(a for a in c["attributes"] if a["code"] == "look")["levels"]
        assert look[0]["byo_share"] > look[2]["byo_share"] and look[0]["accept_rate"] > look[2]["accept_rate"]
        assert abs(sum(lv["byo_share"] for lv in look) - 1) < 1e-9
        assert abs(sum(lv["win_share"] for lv in look) - 1) < 1e-9
    html = client.get("/surveys/1/results").data.decode()
    assert "Zählwerte je Stufe" in html and "Aggregiertes Logit-Modell" in html and "Hierarchical Bayes" in html
    assert html.count('class="pw-chart pw-wide"') == 4 and "Wichtigkeit der Merkmale" in html  # one chart per attribute


def test_price_enters_as_linear_term_with_wtp(app, client, survey):
    setup(client, screens=5, per_screen=4, price=True)
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        add_responses(s, simulate(item, 200, price_beta=-0.01))
        m = next(e for e in results.compute(s)["items"] if e["item"].id == 2)["acbc"]["model"]
        assert m["price"]["beta"] < 0 and m["price"]["se"] is not None and 0 < m["price"]["importance"] < 1
        look = next(a for a in m["attributes"] if a["code"] == "look")["levels"]
        assert look[0]["wtp"] > 0 > look[2]["wtp"]
        assert abs(look[0]["wtp"] - look[0]["beta"] / -m["price"]["beta"]) < 1e-9
    html = client.get("/surveys/1/results").data.decode()
    assert "Zahlungsbereitschaft" in html and "Preiskoeffizient" in html


def test_small_samples_warn_and_dummy_counts_are_consistent(app, client, survey):
    setup(client, rules=True, price=True, screens=4)
    with app.app_context():
        from app import dummy, results

        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 12, seed=2) == 12
        entry = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 2)
        c, m = entry["acbc"]["counts"], entry["acbc"]["model"]
        assert c["persons"] == 12 and entry["answered"] == 12
        assert sum(c["sources"].values()) == c["winners"] == 12
        for attr in c["attributes"]:
            assert abs(sum(lv["byo_share"] for lv in attr["levels"]) - 1) < 1e-9
            assert all(lv["accept_rate"] is None or 0 <= lv["accept_rate"] <= 1 for lv in attr["levels"])
            assert all(lv["nogo"] <= lv["nogo_offered"] and lv["must"] <= lv["must_asked"] for lv in attr["levels"])
        assert c["possible"]["n"] == 12 and c["price"]["asked"] >= 1
        assert any("Nur 12 Personen" in w for w in m["warnings"])
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "acbc-results" in html


def test_no_answers_no_model(app, client, survey):
    setup(client)
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        assert entry["acbc"]["counts"]["persons"] == 0 and entry["acbc"]["model"] is None
    # no participation at all: the page offers its way in instead of empty panels (UI audit 2026-10-12, U26)
    assert "Noch keine Teilnahmen in der Grundlage" in client.get("/surveys/1/results").data.decode()
