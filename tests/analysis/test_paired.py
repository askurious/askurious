"""Paired comparison (app/items/paired.py, app/paired_design.py, app/paired_analysis.py; concept paarvergleich-konzept.md)."""
import csv
import io
import math
import random

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"
OPTIONS = ["Gehalt", "Homeoffice", "Team", "Weiterbildung", "Arbeitsweg", "Urlaub"]


def paired_form(options=OPTIONS, answer="choice", steps=5, pairs="", versions=1, explain=False, ids=None):
    rows = [("code", "pc"), ("question", "Was ist Ihnen wichtiger?"), ("answer", answer), ("steps", str(steps)),
            ("pairs", str(pairs)), ("versions", str(versions)), ("images_shown", "1")]
    for n, text in enumerate(options):
        rows += [("md_id", (ids or {}).get(n, f"o{n}")), ("md_text", text)]
    if explain:
        rows.append(("explain", "1"))
    return MultiDict(rows)


def setup(client, **kw):
    fill_consent(client)
    add(client, "paired")                                   # item 2
    client.post(E + "/items/2", data=paired_form(**kw))
    client.post(E + "/items/2/conjoint/design")


def simulate(true, versions, persons, cats, seed, delta=0.0, theta=None):
    """Answers [(pid, version, [(first, second, c)])] from the adjacent-categories model of the analysis."""
    rng = random.Random(seed)
    theta = theta or {}
    out = []
    for p in range(persons):
        v = p % len(versions)
        answered = []
        for a, b in versions[v]:
            d = true[a] - true[b] + delta
            w = [math.exp(c / 2 * d + (theta.get(abs(c), 0) if abs(c) < max(cats) else 0)) for c in cats]
            r, acc = rng.random() * sum(w), 0.0
            for c, x in zip(cats, w):
                acc += x
                if r <= acc:
                    break
            answered.append((a, b, c))
        out.append((p, v, answered))
    return out


# --- design --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("n,pairs,versions", [(4, 6, 1), (8, 28, 2), (10, 20, 3), (15, 30, 4), (20, 40, 2), (12, 18, 1)])
def test_design_is_balanced(n, pairs, versions):
    from app.paired_design import diagnostics, generate

    design = generate(n, pairs, versions, seed=5)
    assert len(design) == versions and all(len(v) == pairs for v in design)
    assert all(len(p) == 2 and p[0] != p[1] and all(0 <= i < n for i in p) for v in design for p in v)
    assert all(len({tuple(sorted(p)) for p in v}) == pairs for v in design)        # no pair twice in a version
    d = diagnostics(n, design)
    assert d["connected_each"] and d["freq_max"] - d["freq_min"] <= 1
    assert d["side_spread"] <= 2 and d["pair_max"] - d["pair_min"] <= 1
    assert generate(n, pairs, versions, seed=5) == design                           # seeded
    # no option in two pairs in a row where the design allows it (complete designs from five options on)
    if n >= 5:
        assert sum(1 for v in design for p, q in zip(v, v[1:]) if set(p) & set(q)) <= versions


def test_design_suggestion_and_limits():
    from app.paired_design import all_pairs, connected, generate, suggested_pairs

    assert suggested_pairs(6) == 15 and suggested_pairs(8) == 28 and suggested_pairs(9) == 18 and suggested_pairs(20) == 40
    assert all_pairs(20) == 190
    with pytest.raises(ValueError):
        generate(2, 1)
    with pytest.raises(ValueError):
        generate(5, 11)
    assert not connected(4, [[[0, 1], [2, 3]]]) and connected(4, [[[0, 1], [1, 2], [3, 2]]])


# --- analysis -----------------------------------------------------------------------------------------------------


def test_counts_scores_and_triads():
    from app.paired_analysis import counts, person_scores, random_like, triads

    persons = [(1, 0, [(0, 1, 1), (1, 2, -1), (2, 0, 0)])]
    comp, win, loss, tie, pw, pn, people = counts(3, persons)
    assert people == 1 and list(comp) == [2, 2, 2] and list(win) == [1, 0, 1] and list(tie) == [1, 0, 1]
    assert pw[0, 2] == 0.5 and pn[0, 1] == 1
    assert person_scores(3, persons[0][2]) == [0.5, -1.0, 0.5]
    # 0 > 1 > 2 > 0 is circular; option 3 loses everything
    circ = [(0, 1, 1), (1, 2, 1), (2, 0, 1), (0, 3, 1), (1, 3, 1), (2, 3, 1)]
    assert triads(4, circ) == (1, 4, 0.5)                                       # T_max = (64 - 16) / 24 = 2
    assert triads(4, [(0, 1, 1), (1, 2, 1), (0, 2, 1), (0, 3, 1), (1, 3, 1), (2, 3, 1)]) == (0, 4, 1.0)
    assert triads(3, [(0, 1, 1), (1, 2, 0)])[2] is None                         # not every pair decided
    assert random_like(1, 4) and not random_like(0, 4) and not random_like(0, 0)


@pytest.mark.parametrize("answer,cats,theta", [("choice", [-1, 1], {}), ("tie", [-1, 0, 1], {0: -0.4}),
                                                ("strength", [-2, -1, 0, 1, 2], {0: 0.3, 1: 0.8})])
def test_model_recovers_values_and_position_effect(answer, cats, theta):
    import numpy as np

    from app.paired_analysis import model
    from app.paired_design import generate

    true = [1.2, 0.6, 0.0, -0.3, -0.6, -0.9]
    versions = generate(6, 15, 2, seed=1)
    persons = simulate(true, versions, 600, cats, seed=3, delta=0.4, theta=theta)
    m = model(6, persons, OPTIONS, cats)
    assert m["converged"] and not m["warnings"] and m["robust"]
    assert [r["n"] for r in m["rows"]] == [1, 2, 3, 4, 5, 6]
    u = {r["n"]: r["u"] for r in m["rows"]}
    centred = np.array(true) - np.mean(true)
    assert max(abs(u[i + 1] - centred[i]) for i in range(6)) < 0.25
    assert sum(r["score"] for r in m["rows"]) == pytest.approx(100)
    assert all(r["lo"] < r["score"] < r["hi"] for r in m["rows"])
    assert m["position"]["clear"] and m["position"]["first"] and m["position"]["delta"] == pytest.approx(0.4, abs=0.15)
    # w_i / (w_i + w_j) is the probability of i over j (no tie, no position)
    w = {r["n"]: r["score"] for r in m["rows"]}
    assert w[1] / (w[1] + w[2]) == pytest.approx(1 / (1 + math.exp(-(u[1] - u[2]))), abs=1e-6)


def test_model_separation_and_disconnected():
    from app.paired_analysis import model

    persons = [(p, 0, [(0, 1, 1), (0, 2, 1), (1, 2, 1 if p % 2 else -1), (2, 1, 1)]) for p in range(40)]
    m = model(3, persons, ["A", "B", "C"], [-1, 1])
    assert m["ridge"] and m["rows"][0]["side"] == "top" and m["rows"][0]["score"] is None
    assert sum(r["score"] for r in m["rows"] if r["score"] is not None) == pytest.approx(100)
    split = [(p, 0, [(0, 1, 1), (2, 3, -1)]) for p in range(10)]
    d = model(4, split, list("ABCD"), [-1, 1])
    assert d["disconnected"] and "2 Gruppen" in d["warnings"][0]


def test_agreement():
    from app.paired_analysis import agreement

    same = [(p, 0, [(0, 1, 1), (0, 2, 1), (1, 2, 1)]) for p in range(4)]
    assert agreement(3, same) == (1.0, 4)
    half = [(0, 0, [(0, 1, 1), (0, 2, 1), (1, 2, 1)]), (1, 0, [(0, 1, -1), (0, 2, -1), (1, 2, -1)])]
    assert agreement(3, half) == (-1.0, 2)                    # two persons who disagree on everything


# --- the element ---------------------------------------------------------------------------------------------------


def test_editor_config_design_and_validation(app, client, survey):
    fill_consent(client)
    add(client, "paired")
    html = client.get(E + "/items/2").data.decode()
    assert "1 · Inhalt" in html and "2 · Ablauf" in html and "data-md-editor" in html and "Antwortform" in html
    client.post(E + "/items/2", data=paired_form())
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.labels(item) == OPTIONS and item.kind.n_pairs(item) == 15 and item.kind.complete(item)
        assert "veraltet" in item.kind.validate(item)[0]
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.design_current(item) and item.kind.validate(item) == []
    html = client.get(E + "/items/2").data.decode()
    assert "Links/rechts" in html and "Versuchsplan ansehen" in html
    client.post(E + "/items/2", data=paired_form(options=OPTIONS + ["Kantine", "Dienstwagen", "Kita"], pairs=12))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert not item.kind.design_current(item) and item.kind.n_pairs(item) == 12 and not item.kind.complete(item)
    client.post(E + "/items/2", data=paired_form(options=OPTIONS[:2]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == ["mindestens drei Optionen"]
    client.post(E + "/items/2", data=paired_form(answer="strength", steps=7))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["answer"] == "strength" and not item.config["auto_next"]      # a slip would move on (E6)
        assert [c for c, _ in item.kind.scale_steps(item)] == [3, 2, 1, 0, -1, -2, -3]


def test_parse_answer(app, client, survey):
    setup(client, answer="tie")
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        meta = {"task": 2, "v": 0}

        def parse(value, lang="de"):
            return kind.parse_answer(item, MultiDict({"i2_t2": value}), lang, meta=meta)

        assert parse("1") == {"t2": 1, "v": 0} and parse("0") == {"t2": 0, "v": 0} and parse("-1") == {"t2": -1, "v": 0}
        with pytest.raises(Exception, match="Ungültige"):
            parse("2")
        with pytest.raises(Exception, match="Invalid"):
            parse("x", "en")
        assert parse("") == {"t2": None, "v": 0}
        assert kind.is_missing(item, {"t2": None, "v": 0}) and not kind.is_missing(item, {"t2": 0, "v": 0})
        item.config = {**item.config, "answer": "choice"}
        with pytest.raises(Exception, match="Ungültige"):
            parse("0")                                         # no "same" without the tie form
        db.session.rollback()


def test_pages_participation_and_exports(app, client, survey):
    setup(client, answer="strength", explain=True, versions=2)
    with app.app_context():
        layout = __import__("app.runtime", fromlist=["x"]).build_layout(db.session.get(Survey, 1), random.Random(2))
        pages = [p for p in layout["pages"] if p.get("items") == [2]]
        assert pages[0].get("explain") and [p.get("task") for p in pages[1:]] == list(range(15))
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert "Bevor es losgeht" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next"})
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        v = r.layout["cj"]["2"]["version"]
        pairs = db.session.get(Item, 2).config["design"]["versions"][v]
    chosen = {}
    for t, (a, b) in enumerate(pairs):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Paar {t + 1} von 15" in html and f'name="i2_t{t}"' in html and 'value="-2"' in html
        assert html.index(OPTIONS[a]) < html.index(OPTIONS[b])                 # the first at the start of the line
        c = [2, 1, 0, -1, -2][t % 5]
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}": str(c)})
        chosen[(a, b)] = c
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"]["v"] == v and r.answers["2"]["t0"] == 2
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide["pc_version"] == str(v + 1)
    for (a, b), c in chosen.items():
        lo, hi = min(a, b), max(a, b)
        want = 0 if c == 0 else (a + 1 if c > 0 else b + 1)
        assert wide[f"pc_{lo + 1}_{hi + 1}"] == str(want) and wide[f"pc_{lo + 1}_{hi + 1}_st"] == str(abs(c))
    assert wide["pc_zeta"] != "" or wide["pc_triads"] != ""
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert len(long) == 15 and list(long[0])[2:] == ["version", "page", "first", "second", "first_label", "second_label",
                                                     "category", "preferred", "win1", "win2"]
    assert long[0]["category"] == "2" and long[0]["win1"] == "1" and long[2]["preferred"] == "0"
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "pc_1_2" in book and "-77" in book and "pc_zeta" in book
    assert "Paarvergleich „pc“ (CSV)" in client.get("/surveys/1/data").data.decode()


def test_export_marks_pairs_not_shown(app, client, survey):
    setup(client, options=OPTIONS + ["Kantine", "Dienstwagen", "Kita"], pairs=10)
    with app.app_context():
        item = db.session.get(Item, 2)
        a, b = item.config["design"]["versions"][0][0]
        values = item.kind.export_values(item, {"v": 0, "t0": -1})
        assert values[f"pc_{min(a, b) + 1}_{max(a, b) + 1}"] == b + 1
        shown = {tuple(sorted(p)) for p in item.config["design"]["versions"][0]}
        missing = next((i, j) for i in range(9) for j in range(i + 1, 9) if (i, j) not in shown)
        assert values[f"pc_{missing[0] + 1}_{missing[1] + 1}"] == -77
        assert "pc_zeta" not in values                         # incomplete design: no consistency


def test_results_page_and_sources(app, client, survey):
    setup(client, answer="tie")
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        pairs = db.session.get(Item, 2).config["design"]["versions"][0]
        rng = random.Random(4)
        for _ in range(12):
            answer = {"v": 0, **{f"t{t}": (1 if a < b else -1) if rng.random() < 0.8 else 0 for t, (a, b) in enumerate(pairs)}}
            s.responses.append(Response(answers={"2": answer}, layout={"cj": {"2": {"version": 0}}}, status=Response.COMPLETED))
        db.session.commit()
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        pc = entry["paired"]
        assert entry["answered"] == 12 and pc["persons"] == 12 and pc["model"]["rows"][0]["n"] == 1
        assert pc["consistency"]["persons"] == 12 and pc["matrix"]["order"][0] == 1
    html = client.get("/surveys/1/results").data.decode()
    assert "Werte der Optionen" in html and "Wer gewinnt gegen wen" in html and "Konsistenz je Person" in html
    assert "Bradley, R. A., &amp; Terry, M. E. (1952)" in html and "Davidson, R. R. (1970)" in html
    assert "Kendall, M. G., &amp; Babington Smith" in html and "Davidson, R. R., &amp; Beaver" in html
    methods = client.get("/surveys/1/methods").data.decode()
    assert "war ein Paarvergleich mit 6 Optionen" in methods and "alle 15 Paare" in methods


def test_dummy_data_follow_values(app, client, survey):
    setup(client, versions=2)
    with app.app_context():
        from app import dummy, results

        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 40, seed=5) == 40
        db.session.commit()
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(len([k for k in a if k.startswith("t")]) == 15 and all(a[f"t{t}"] in (1, -1) for t in range(15))
                   for a in answers)
        m = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 2)["paired"]["model"]
        true = random.Random("paired:2")
        base = [true.gauss(0, 1) for _ in range(6)]
        assert m["converged"] and m["rows"][0]["n"] - 1 in sorted(range(6), key=lambda i: -base[i])[:2]


def test_duration_timing_dashboard_and_fact_sheet(app, client, survey):
    setup(client, explain=True)
    with app.app_context():
        from app import boards, duration

        item = db.session.get(Item, 2)
        assert duration.item_seconds(item, "de") >= 15 * 4
        assert [c for c, _, _ in item.kind.timing_pages(item)] == ["_x"] + [f"_t{n}" for n in range(1, 16)]
        assert not item.kind.allowed_in_loop and not item.kind.allowed_in_separated and item.kind.long_export == "paired"
        assert boards.shape_of(item)[0] == "score"
        assert item.kind.fact_sheet.group == "choice"


def test_model_with_a_category_nobody_used_has_standard_errors():
    """Code review 2026-10-15, C6: an answer form "mit gleich" where nobody answered "gleich" made theta_0 run to
    minus infinity (the ridge does not bound it): no standard errors, no intervals. Only the steps answered are in the
    model now."""
    from app.paired_analysis import model
    from app.paired_design import generate

    true = [1.0, 0.5, 0.0, -0.5]
    versions = generate(4, 6, 1, seed=2)
    persons = simulate(true, versions, 80, [-1, 1], seed=4)  # without a tie
    m = model(4, persons, list("ABCD"), [-1, 0, 1])
    assert m["converged"] and m["robust"]
    assert all(r["u_se"] is not None and r["lo"] is not None for r in m["rows"])
    assert not any("Standardfehler" in w for w in m["warnings"])
    # strength with the strongest step unused: the strongest step answered is the reference
    strong = simulate(true, versions, 80, [-1, 0, 1], seed=5, theta={0: -0.3})
    s = model(4, strong, list("ABCD"), [-2, -1, 0, 1, 2])
    assert s["robust"] and all(r["u_se"] is not None for r in s["rows"])
