"""Adaptive conjoint (ACA, experimental "aca": app/items/aca.py, app/aca_design.py, app/aca_analysis.py; concept
aca-cva-konzept.md, A1 to A6)."""
import csv
import io
import random

import numpy as np
import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, experiments_on, fill_consent

E = "/surveys/1/editor"
ATTRS = {"a1": ("akku", "Akku", ["1 Tag", "2 Tage", "3 Tage"]),
         "a2": ("farbe", "Farbe", ["Schwarz", "Weiß", "Blau", "Rot"]),
         "a3": ("kamera", "Kamera", ["einfach", "gut", "sehr gut"]),
         "a4": ("gewicht", "Gewicht", ["leicht", "schwer"])}


def aca_form(pairs="", cal=0, orders=None):
    rows = [("code", "ac"), ("n_pairs", str(pairs)), ("cal", str(cal)), ("size_start", "2"), ("size_end", "3"),
            ("pref_points", "7"), ("imp_points", "4"), ("pair_points", "9"), ("order_shown", "1")]
    for aid, (code, label, levels) in ATTRS.items():
        rows += [("attr_id", aid), ("attr_code", code), ("attr_label", label)]
        for n, lv in enumerate(levels):
            rows += [(f"lvl_{aid}_id", f"{aid}l{n}"), (f"lvl_{aid}_label", lv), (f"lvl_{aid}_hint", ""), (f"lvl_{aid}_def", "")]
    for aid, o in (orders or {"a1": "best_last", "a3": "best_last"}).items():
        rows.append((f"order_{aid}", o))
    return MultiDict(rows)


def setup(client, **kw):
    fill_consent(client)
    experiments_on(client, "aca")
    add(client, "aca")                                       # item 2
    client.post(E + "/items/2", data=aca_form(**kw))


TRUE = [[-1.0, 0.0, 1.0], [0.6, -0.2, 0.1, -0.5], [-0.8, 0.1, 0.7], [0.3, -0.3]]


def test_interview_arithmetic_recovers_preferences():
    from app import aca_design

    attrs = [{"id": aid, "levels": [{"id": f"{aid}l{n}"} for n in range(len(levels))],
              "aca_order": "best_last" if aid in ("a1", "a3") else ""} for aid, (_, _, levels) in ATTRS.items()]
    assert aca_design.suggested_pairs([3, 4, 3, 2]) == 9               # 3 (12 - 4 - 1) - 12
    assert aca_design.suggested_pairs([5] * 8) == 50 and aca_design.suggested_pairs([4] * 6) == 27
    answer = {"r": {"a2": [6, 3, 4, 2], "a4": [5, 3]}, "i": {"a1": 4, "a2": 2, "a3": 3, "a4": 1}}
    prior = aca_design.prior(attrs, answer, 4)
    assert prior[0] == pytest.approx([-2.0, 0.0, 2.0])                 # range 1 times importance 4
    assert max(prior[3]) - min(prior[3]) == pytest.approx(1.0)         # importance 1 keeps the range 1
    rng = random.Random(4)
    pairs, ys = [], []
    for k in range(20):
        u = aca_design.update(attrs, prior, pairs, ys)
        p = aca_design.choose_pair(attrs, u, pairs, 2 if k < 10 else 3, 11, k, lambda c: True)
        assert all(p[0][a] != p[1][a] for a in p[0]) and len(p[0]) == (2 if k < 10 else 3)
        pairs.append(p)
        ys.append(max(-4, min(4, round(2 * sum(TRUE[a][p[0][a]] - TRUE[a][p[1][a]] for a in p[0]) + rng.gauss(0, 0.3)))))
    assert aca_design.choose_pair(attrs, aca_design.update(attrs, prior, pairs[:5], ys[:5]), pairs[:5], 2, 11, 5,
                                  lambda c: True) == pairs[5]                                 # replayable
    final = aca_design.final(attrs, prior, pairs, ys)
    assert all(abs(sum(row)) < 1e-9 for row in final)
    flat = np.array([x for r in final for x in r])
    true = np.array([x for r in TRUE for x in r])
    assert np.corrcoef(flat, true)[0, 1] > 0.8
    concepts = aca_design.calibration_concepts(attrs, final, 4, 3, 11, lambda c: True)
    assert len(concepts) == 4 and len(concepts[0]) == 3
    likelihood = [100 / (1 + np.exp(-2 * sum(final[a][lv] for a, lv in c.items()))) for c in concepts]
    b, a, r2 = aca_design.calibrate(final, concepts, likelihood)
    assert r2 > 0.8 and b > 0
    assert aca_design.calibrate(final, concepts[:2], likelihood[:2]) is None


def test_experimental_gate_editor_and_pages(app, client, survey):
    fill_consent(client)
    assert add(client, "aca").status_code == 404                     # without the experimental feature
    with app.app_context():
        assert db.session.get(Item, 2) is None
    setup(client, cal=4)
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        assert item.config["orders"] == {"a1": "best_last", "a3": "best_last"}
        assert [a["id"] for a in kind.rated(item)] == ["a2", "a4"] and kind.n_pairs(item) == 9 and kind.n_cal(item) == 4
        specs = kind.page_specs(item)
        assert [s.get("phase") for s in specs] == ["pref", "pref", "imp"] + ["pair"] * 9 + ["cal"] * 4
        assert kind.validate(item) == [] and kind.check_warnings(item) == []
    html = client.get(E + "/items/2").data.decode()
    assert "Reihenfolge der Stufen" in html and "feste Reihenfolge: letzte Stufe am besten" in html


def test_merge_drops_what_follows_a_change():
    from app.items import get

    kind = get("aca")
    old = {"seed": 1, "r": {"a2": [1, 2, 3, 4]}, "i": {"a1": 2}, "p": {"0": 1, "1": -2, "2": 0}, "c": {"0": 50}}
    assert kind.merge_answer(old, {"p": {"1": -2}}) == old                              # the same answer again
    changed = kind.merge_answer(old, {"p": {"1": 3}})
    assert changed["p"] == {"0": 1, "1": 3} and "c" not in changed
    again = kind.merge_answer(old, {"i": {"a1": 4}})
    assert "p" not in again and "c" not in again and again["i"] == {"a1": 4}


def test_participation_export_results_and_simulator(app, client, survey):
    setup(client, cal=3)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.get(f"/s/{uid}").data.decode()
    assert "Wie sehr sagen Ihnen diese Ausprägungen von „Farbe“ zu?" in html and 'name="i2_r3"' in html
    p.post(f"/s/{uid}", data={"nav": "next", "i2_r0": "7", "i2_r1": "2", "i2_r2": "5", "i2_r3": "1"})
    p.post(f"/s/{uid}", data={"nav": "next", "i2_r0": "6", "i2_r1": "2"})
    html = p.get(f"/s/{uid}").data.decode()
    assert "„Blau“ statt „Rot“" not in html and "„Schwarz“ statt „Rot“" in html and "„3 Tage“ statt „1 Tag“" in html
    p.post(f"/s/{uid}", data={"nav": "next", "i2_i0": "4", "i2_i1": "2", "i2_i2": "3", "i2_i3": "1"})
    for k in range(9):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Paar {k + 1} von 9" in html and f'name="i2_p{k}"' in html and "Angebot 1 viel lieber" in html
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_p{k}": str([3, -2, 1, 0, 2, -1, 1, 0, -3][k])})
    for k in range(3):
        html = p.get(f"/s/{uid}").data.decode()
        assert "Wie wahrscheinlich würden Sie dieses Angebot wählen (0 bis 100)?" in html
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_c{k}": str([10, 90, 50][k])})
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        a = r.answers["2"]
        assert a["r"]["a2"] == [7, 2, 5, 1] and a["i"]["a1"] == 4 and a["p"]["0"] == 3 and a["c"]["1"] == 90
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide["ac_r_farbe_1"] == "7" and wide["ac_i_akku"] == "4" and wide["ac_p1"] == "3" and wide["ac_c2"] == "90"
    assert float(wide["ac_pw_akku_3"]) > float(wide["ac_pw_akku_1"]) and wide["ac_cal_r2"] != ""
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert len(long) == 9 * 2 + 3 and long[0]["phase"] == "pair" and long[-1]["phase"] == "cal"
    assert sum(1 for c in ("akku", "farbe", "kamera", "gewicht") if long[0][c]) == 2      # two attributes in the first pairs
    with app.app_context():
        from app import dummy, results, simulator
        from werkzeug.datastructures import ImmutableMultiDict

        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 20, seed=3) == 20
        db.session.commit()
        entry = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 2)
        m = entry["aca"]["model"]
        assert entry["aca"]["persons"] == 20 and len(m["charts"]) == 4 and m["attributes"][0]["prior_importance"] > 0
        view = simulator.view(entry["item"], entry["aca"], ImmutableMultiDict({"sim": "2"}), "de")
        assert view["basis"] == "persons" and view["persons"] == 20 and len(view["rows"]) == 2
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "laut Selbstauskunft" in html and "Johnson, R. M. (1987)" in html and "Marktsimulator" in html
    methods = client.get("/surveys/1/methods?source=dummy").data.decode()
    assert "war ein Adaptive Conjoint (ACA" in methods


def test_price_hint():
    from types import SimpleNamespace

    from app.items import get

    kind = get("aca")
    item = SimpleNamespace(config={"attributes": [{"id": "a", "code": "preis", "levels": [{"id": "x"}, {"id": "y"}]}]},
                           t=lambda lang=None: {"attributes": {"a": "Preis"}, "levels": {"x": "9 €", "y": "12 €"}},
                           assets=[])
    item.kind = kind
    assert kind.price_like(item) == ["Preis"]


def test_uncalibrated_people_stay_out_of_the_logit_means(app, client, survey):
    """Calibration on: a person without calibration answers keeps part-worths in the prior's units, so the means,
    importances and simulator take only the calibrated ones and the page names the others (audit data storage and
    calculations 2026-10-21, D2)."""
    from types import SimpleNamespace

    from app import aca_analysis

    setup(client, cal=3)
    base = {"seed": 7, "r": {"a2": [7, 2, 5, 1], "a4": [6, 2]}, "i": {"a1": 4, "a2": 2, "a3": 3, "a4": 1},
            "p": {str(k): [3, -2, 1, 0, 2, -1, 1, 0, -3][k] for k in range(9)}}
    with app.app_context():
        item = db.session.get(Item, 2)
        people = [SimpleNamespace(pid="cal", answers={"2": {**base, "c": {"0": 10, "1": 90, "2": 50}}}),
                  SimpleNamespace(pid="open", answers={"2": dict(base)})]
        found = aca_analysis.analyse(item, people, "de")
        assert found["persons"] == 1 and found["uncalibrated"] == 1 and found["calibrated"]
        assert [pid for pid, _ in found["person_pw"]] == ["cal"]
        # nobody calibrated yet: everyone in the prior's units, said as such
        found = aca_analysis.analyse(item, people[1:], "de")
        assert found["persons"] == 1 and found["uncalibrated"] == 1 and not found["calibrated"]
