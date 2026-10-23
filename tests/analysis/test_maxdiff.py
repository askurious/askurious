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
STATEMENTS = ["Preis", "Qualität", "Nachhaltigkeit", "Marke", "Service", "Design", "Garantie", "Lieferzeit"]


def maxdiff_form(statements=STATEMENTS, set_size=4, sets="", versions=1, anchor=False, explain=False, ids=None):
    rows = [("code", "md"), ("question", "Was ist Ihnen beim Kauf wichtig?"), ("set_size", str(set_size)),
            ("sets", str(sets)), ("versions", str(versions))]
    for n, text in enumerate(statements):
        rows += [("md_id", (ids or {}).get(n, f"s{n}")), ("md_text", text)]
    if anchor:
        rows.append(("anchor", "1"))
    if explain:
        rows.append(("explain", "1"))
    return MultiDict(rows)


def setup(client, **kw):
    fill_consent(client)
    add(client, "maxdiff")                                # item 2
    client.post(E + "/items/2", data=maxdiff_form(**kw))
    client.post(E + "/items/2/conjoint/design")


# --- design generator ----------------------------------------------------------------------


@pytest.mark.parametrize("K,k,T,V", [(8, 4, 6, 1), (12, 4, 9, 3), (20, 5, 12, 2), (7, 3, 7, 1), (30, 6, 15, 4)])
def test_design_is_balanced(K, k, T, V):
    from app.maxdiff_design import diagnostics, generate

    versions = generate(K, k, T, V, seed=7)
    assert len(versions) == V and all(len(sets) == T for sets in versions)
    assert all(len(s) == k and len(set(s)) == k and all(0 <= i < K for i in s) for sets in versions for s in sets)
    d = diagnostics(K, versions)
    assert d["total"]["duplicates"] == 0 and d["connected"] and d["connected_each"]
    assert d["version"]["freq_max"] - d["version"]["freq_min"] <= 1          # 1-way balance within each version
    assert d["total"]["freq_max"] - d["total"]["freq_min"] <= 1              # and over all versions
    assert d["version"]["pos_spread"] <= 2
    assert generate(K, k, T, V, seed=7) == versions                            # seeded: reproducible


def test_design_pair_balance_and_bibd():
    from app.maxdiff_design import diagnostics, generate

    # 7 statements, sets of 3, 7 sets: a balanced incomplete block design exists (each pair exactly once)
    d = diagnostics(7, generate(7, 3, 7, 1, seed=1))
    assert (d["total"]["pair_min"], d["total"]["pair_max"]) == (1, 1)
    d = diagnostics(12, generate(12, 4, 9, 1, seed=3))
    assert d["total"]["pair_max"] - d["total"]["pair_min"] <= 2


def test_design_limits_and_suggestion():
    from app.maxdiff_design import allowed_set_sizes, connected, generate, suggested_sets

    assert allowed_set_sizes(4) == [3] and allowed_set_sizes(6) == [3, 4, 5] and allowed_set_sizes(20) == [3, 4, 5, 6]
    assert suggested_sets(12, 4) == 9 and suggested_sets(10, 4) == 8
    with pytest.raises(ValueError):
        generate(3, 3, 4)
    with pytest.raises(ValueError):
        generate(6, 6, 4)                                                   # set size must be below K
    assert not connected(6, [[[0, 1, 2], [3, 4, 5]]]) and connected(4, [[[0, 1, 2], [2, 3, 0]]])


# --- analysis ---------------------------------------------------------------------------------


def test_counting_scores():
    import numpy as np

    from app.maxdiff_analysis import counts, person_scores, scores

    sets = [[0, 1, 2], [1, 2, 3], [0, 2, 3]]
    answers = [(0, {0: (0, 2), 1: (1, 3), 2: (0, 3)}), (0, {0: (1, 0)}), (5, {0: (0, 1)}), (0, {0: (7, 1)})]
    shown, best, worst, persons = counts(4, [sets], answers)
    assert persons == 2                                   # unknown version and impossible choice are ignored
    assert list(shown) == [3, 3, 4, 2] and list(best) == [2, 2, 0, 0] and list(worst) == [1, 0, 1, 2]
    bw, bs, ws = scores(shown, best, worst)
    assert bw[0] == pytest.approx(1 / 3) and bw[3] == -1 and bs[1] == pytest.approx(2 / 3) and ws[2] == 0.25
    assert person_scores(4, sets, {0: (0, 2)}) == [1.0, 0.0, -1.0, None]
    assert np.isnan(scores(np.zeros(1), np.zeros(1), np.zeros(1))[0][0])


def simulate(true, versions, persons, seed, threshold=None, person_sd=0.0):
    """Sequential best-worst choices from a logit model (Gumbel noise), as counts()/mnl() input;
    with a threshold the anchor answers (statement utility + logistic noise above the threshold)."""
    rng = random.Random(seed)

    def gumbel():
        return -math.log(-math.log(max(rng.random(), 1e-12)))

    answers, anchors = [], []
    for p in range(persons):
        v = p % len(versions)
        u = [x + rng.gauss(0, person_sd) for x in true]
        tasks = {}
        for t, s in enumerate(versions[v]):
            b = max(s, key=lambda i: u[i] + gumbel())
            w = max((i for i in s if i != b), key=lambda i: -u[i] + gumbel())
            tasks[t] = (b, w)
        answers.append((v, tasks))
        if threshold is not None:
            anchors.append([int(u[i] + gumbel() > threshold + gumbel()) for i in range(len(true))])
    return answers, anchors


def test_mnl_recovers_known_utilities():
    import numpy as np

    from app.maxdiff_analysis import mnl, prob_scores
    from app.maxdiff_design import generate

    true = [1.2, 0.8, 0.4, 0.1, -0.1, -0.3, -0.7, -1.4]
    versions = generate(8, 4, 6, 3, seed=3)
    answers, anchors = simulate(true, versions, 600, seed=11, threshold=0.3)
    labels = [f"S{i + 1}" for i in range(8)]
    m = mnl(8, versions, answers, anchors, labels, 4)
    assert m["converged"] and not m["ridge"] and m["warnings"] == [] and m["persons"] == 600
    by_n = {r["n"]: r for r in m["rows"]}
    centred = np.array(true) - np.mean(true)
    for i in range(8):
        r = by_n[i + 1]
        assert r["u"] == pytest.approx(centred[i], abs=3 * r["u_se"] + 0.02)     # within about 3 SE
        assert r["lo"] < r["score"] < r["hi"] and r["se"] > 0
    assert [r["n"] for r in m["rows"][:2]] == [1, 2] and m["rows"][-1]["n"] == 8
    assert sum(r["score"] for r in m["rows"]) == pytest.approx(100)
    t = m["threshold"]
    assert t["u"] == pytest.approx(0.3 - np.mean(true), abs=3 * t["u_se"] + 0.02)
    assert t["lo"] < t["score"] < t["hi"]
    assert by_n[1]["above"] and not by_n[8]["above"]
    assert m["anchor_n"] == 600 and m["observations"] == 600 * (6 * 2 + 8)
    assert 0 < m["rho2"] < 1 and m["loglik"] < 0
    # probability scale: p = exp(u) / (exp(u) + a - 1), rescaled to sum 100
    scores, _ = prob_scores(np.array([0.0, 0.0]), 4)
    assert list(scores) == [50, 50]
    c = m["chart"]
    assert c["threshold_x"] is not None and len(c["rows"]) == 8 and c["rows"][0]["w"] > c["rows"][-1]["w"]
    # without anchors: no threshold, no anchor sets; count scores ride along
    m2 = mnl(8, versions, answers, [], labels, 4)
    assert m2["threshold"] is None and m2["observations"] == 600 * 12 and m2["chart"]["threshold_x"] is None
    assert all(-1 <= r["count"] <= 1 for r in m2["rows"])


def test_mnl_chart_axis_and_threshold_label():
    from app.maxdiff_analysis import axis, chart

    assert axis(28.3) == (10, 30) and axis(27.4) == (10, 30) and axis(47) == (10, 50)
    assert axis(12) == (2.5, 12.5) and axis(0.8) == pytest.approx((0.2, 0.8)) and axis(3.1) == (1, 4) and axis(0) == (1.0, 1.0)
    assert axis(21) == (5, 25) and axis(100) == (20, 100)

    def row(n, score, lo, hi):
        return {"n": n, "label": f"S{n}", "score": score, "lo": lo, "hi": hi, "above": score > 16}

    model = {"rows": [row(1, 27.4, 26.4, 28.3), row(2, 14.4, 13.4, 15.2), row(3, 1.6, 1.2, 2.0)],
             "threshold": {"score": 16.1}}
    c = chart(model)
    assert [v for _, v in c["ticks"]] == [0, 10, 20, 30]
    r2 = c["rows"][1]                              # 14.4: its value would cross the dashed line at 16.1
    assert r2["text_x"] > c["threshold_x"]
    assert c["rows"][2]["text_x"] < c["threshold_x"] - 30                         # far from the line: unchanged
    assert chart({"rows": model["rows"], "threshold": None})["threshold_x"] is None


def test_mnl_degenerate_cases():
    from app.maxdiff_analysis import mnl
    from app.maxdiff_design import generate

    labels = [f"S{i + 1}" for i in range(8)]
    versions = generate(8, 4, 6, 1, seed=3)
    assert mnl(8, versions, [], [], labels, 4) is None                          # no answers
    assert mnl(8, versions, [(None, {0: (0, 1)})], [], labels, 4) is None       # unusable answers
    # one person who always ranks by statement number: statement 1 always best, 8 always worst
    tasks = {t: (min(s), max(s)) for t, s in enumerate(versions[0])}
    m = mnl(8, versions, [(0, tasks)], [[1] * 8], labels, 4)
    assert m["ridge"] > 0 and m["rows"][0]["n"] == 1 and m["rows"][-1]["n"] == 8
    assert all(abs(r["u"]) < 20 for r in m["rows"])                             # capped, finite
    text = " ".join(m["warnings"])
    assert "Nur 1 Person:" in text and "Aussage 1, 8" in text and "alle oder keine" in text
    assert next(r for r in m["rows"] if r["n"] == 1)["separated"]
    # statements never shown are left out of the model
    few = [[s for s in versions[0] if 7 not in s]]
    ans = [(0, {t: (s[0], s[1]) for t, s in enumerate(few[0])}), (0, {t: (s[1], s[0]) for t, s in enumerate(few[0])})]
    m = mnl(8, few, ans, [], labels, 4)
    assert 8 not in [r["n"] for r in m["rows"]] and any("nie gezeigt" in w for w in m["warnings"])


def test_choice_model_unchanged_without_ridge():
    import numpy as np

    from app.choice_model import ChoiceSet, fit

    rng = np.random.default_rng(1)
    beta_true = np.array([0.8, -0.5, 0.3])
    sets = []
    for _ in range(400):
        X = rng.normal(size=(3, 3))
        p = np.exp(X @ beta_true)
        sets.append(ChoiceSet(X, int(rng.choice(3, p=p / p.sum()))))
    b0, cov0, ll0, conv0, it0 = fit(sets, 3)
    b1, cov1, ll1, conv1, it1 = fit(sets, 3, ridge=0.0)
    assert (b0 == b1).all() and (cov0 == cov1).all() and ll0 == ll1 and conv0 and it0 == it1
    b2, _, ll2, conv2, _ = fit(sets, 3, ridge=5.0)
    assert conv2 and np.linalg.norm(b2) < np.linalg.norm(b0) and ll2 < ll0      # shrunk; unpenalised log-lik


# --- editor, validation, answers ------------------------------------------------------------------


def test_editor_config_design_and_validation(app, client, survey):
    fill_consent(client)
    add(client, "maxdiff")
    html = client.get(E + "/items/2").data.decode()
    assert "1 · Inhalt" in html and "2 · Ablauf" in html and "3 · Anker" in html  # as CBC and ACBC (W25)
    assert "data-md-editor" in html and "Am wichtigsten" in html
    client.post(E + "/items/2", data=maxdiff_form())
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.labels(item) == STATEMENTS and item.config["set_size"] == 4
        assert item.kind.n_sets(item) == 6                                     # suggestion ceil(3 * 8 / 4)
        assert "veraltet" in item.kind.validate(item)[0]
    client.post(E + "/items/2/conjoint/design")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.design_current(item) and item.kind.validate(item) == []
        assert item.config["design"]["stats"]["connected_each"]
    html = client.get(E + "/items/2").data.decode()
    assert "Häufigkeit je Aussage" in html and "Versuchsplan ansehen" in html
    # more statements: design outdated; reordering keeps ids and texts
    client.post(E + "/items/2", data=maxdiff_form(statements=STATEMENTS + ["Bewertungen"]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert not item.kind.design_current(item) and item.kind.n_items(item) == 9
    client.post(E + "/items/2", data=maxdiff_form(statements=STATEMENTS[:3], set_size=4))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == ["mindestens vier Aussagen"]
    client.post(E + "/items/2", data=maxdiff_form(statements=STATEMENTS[:5], set_size=5))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert "Aussagen je Set: 3 bis 4" in item.kind.validate(item)[0]


def test_parse_answer_checks_best_and_worst(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        s = kind.task_set(item, 0, 1)
        outside = next(i for i in range(8) if i not in s)
        meta = {"task": 1, "v": 0}

        def parse(b, w, lang="de"):
            return kind.parse_answer(item, MultiDict({"i2_t1_b": b, "i2_t1_w": w}), lang, meta=meta)

        assert parse(str(s[0]), str(s[2])) == {"t1": {"b": s[0], "w": s[2]}, "v": 0}
        with pytest.raises(Exception, match="zugleich"):
            parse(str(s[0]), str(s[0]))
        with pytest.raises(Exception, match="je eine Aussage"):
            parse(str(s[0]), "")
        with pytest.raises(Exception, match="Ungültige"):
            parse(str(outside), str(s[0]))
        with pytest.raises(Exception, match="both most and least"):
            parse(str(s[1]), str(s[1]), "en")
        assert parse("", "") == {"t1": None, "v": 0}                        # optional: skipped
        assert kind.is_missing(item, {"t1": None, "v": 0}) and not kind.is_missing(item, {"t1": {"b": 1, "w": 2}})
        assert kind.parse_answer(item, MultiDict(), meta={"explain": True}) == {"explained": True}
        anchor = kind.parse_answer(item, MultiDict([("i2_a", "3"), ("i2_a", "0"), ("i2_a", "99")]), meta={"anchor": True})
        assert anchor == {"anchor": [0, 3]} and not kind.is_missing(item, {"anchor": []})
        item.answer_mode = "required"
        with pytest.raises(Exception, match="je eine Aussage"):
            parse("", "")
        db.session.rollback()


def test_pages_participation_and_exports(app, client, survey):
    setup(client, anchor=True, explain=True, versions=2)
    with app.app_context():
        layout = __import__("app.runtime", fromlist=["x"]).build_layout(db.session.get(Survey, 1), random.Random(2))
        md = [p for p in layout["pages"] if p.get("items") == [2]]
        assert md[0].get("explain") and md[-1].get("anchor") and [p.get("task") for p in md[1:-1]] == list(range(6))
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
        sets = db.session.get(Item, 2).config["design"]["versions"][v]
    chosen = {}
    for t, s in enumerate(sets):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Set {t + 1} von 6" in html and f'name="i2_t{t}_b"' in html and "Am wenigsten wichtig" in html
        assert html.index(STATEMENTS[s[0]]) < html.index(STATEMENTS[s[-1]])     # design order
        if t == 0:  # same statement twice: stays on the page
            p.post(f"/s/{uid}", data={"nav": "next", "i2_t0_b": str(s[0]), "i2_t0_w": str(s[0])})
            assert "Set 1 von 6" in p.get(f"/s/{uid}").data.decode()
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}_b": str(s[0]), f"i2_t{t}_w": str(s[1])})
        chosen[t] = (s[0], s[1])
    assert "wirklich wichtig" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={"nav": "next", "i2_a": ["0", "2"]})
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        a = r.answers["2"]
        assert a["v"] == v and a["anchor"] == [0, 2] and a["t3"] == {"b": chosen[3][0], "w": chosen[3][1]}
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide["md_version"] == str(v + 1)
    assert wide["md_t1_best"] == str(chosen[0][0] + 1) and wide["md_t1_worst"] == str(chosen[0][1] + 1)
    assert wide["md_anchor1"] == "1" and wide["md_anchor2"] == "0" and wide["md_anchor3"] == "1"
    first = sets[0][0]
    expected = (sum(1 for b, _ in chosen.values() if b == first) - sum(1 for _, w in chosen.values() if w == first)) \
        / sum(1 for s in sets if first in s)
    assert float(wide[f"md_score{first + 1}"]) == pytest.approx(expected, abs=1e-4)
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert len(long) == 6 * 4 and list(long[0]) == ["resp_id", "source", "version", "set", "position", "statement",
                                                     "statement_label", "best", "worst"]
    set1 = [row for row in long if row["set"] == "1"]
    assert [row["position"] for row in set1] == ["1", "2", "3", "4"] and set1[0]["best"] == "1" and set1[1]["worst"] == "1"
    assert set1[0]["statement_label"] == STATEMENTS[sets[0][0]]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "md_t1_best" in book and "1 = Preis; 2 = Qualität" in book and "md_score1" in book
    data_page = client.get("/surveys/1/data").data.decode()
    assert "MaxDiff (Best-Worst) „md“ (CSV)" in data_page and "Person × Set × Aussage" in data_page


def test_results_page_scores(app, client, survey):
    setup(client, anchor=True)
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        sets = item.config["design"]["versions"][0]
        answers = {"v": 0, "anchor": [1], **{f"t{t}": {"b": min(st), "w": max(st)} for t, st in enumerate(sets)}}
        s.responses.append(Response(answers={"2": answers}, layout={"cj": {"2": {"version": 0}}},
                                    status=Response.COMPLETED))
        db.session.commit()
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        m = entry["maxdiff"]
        assert entry["answered"] == 1 and m["persons"] == 1 and m["sets"] == 6 and m["anchor_n"] == 1
        top = m["rows"][0]
        assert top["n"] == 1 and top["score"] == 1 and top["best_share"] == 1     # statement 1 always wins
        assert m["rows"][-1]["n"] == 8 and m["rows"][-1]["score"] == -1
        assert next(r for r in m["rows"] if r["n"] == 2)["anchor"] == 1
        x = m["mnl"]                  # one person, fully separated: capped model
        assert x["ridge"] and x["rows"][0]["n"] in (1, 2) and x["rows"][-1]["n"] == 8 and x["threshold"]
    html = client.get("/surveys/1/results").data.decode()
    assert "md-res" in html and "+1,00" in html and "Individuelle Nutzen (Hierarchical Bayes)" in html
    assert "Aggregiertes Logit-Modell (MNL)" in html and "md-threshold" in html and "begrenzt" in html
    assert "über allen" in html and "unter allen" in html           # separated statements stand off the score scale
    # the sources of MaxDiff, anchoring and the logit model in the one fold of the page (C-4); no HB estimated yet
    assert "Louviere, J. J., Flynn, T. N., &amp; Marley" in html and "Lattery, K. (2010)" in html
    assert "McFadden, D. (1974)" in html and "Cameron, A. C., &amp; Miller" in html and "Rossi, P. E." not in html


def test_dummy_data_follow_utilities(app, client, survey):
    setup(client, anchor=True, versions=2)
    with app.app_context():
        from app import dummy, results

        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 30, seed=5) == 30
        db.session.commit()
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(len([k for k in a if k.startswith("t")]) == 6 and isinstance(a["anchor"], list) for a in answers)
        assert all(a[f"t{t}"]["b"] != a[f"t{t}"]["w"] for a in answers for t in range(6))
        m = next(e for e in results.compute(s, sources=("dummy",))["items"] if e["item"].id == 2)["maxdiff"]
        scores = [r["score"] for r in m["rows"]]
        assert m["persons"] == 30 and scores[0] > 0.3 and scores[-1] < -0.3      # utilities show through
        # true utilities: the statement with the highest one should be near the top
        true = random.Random("maxdiff:2")
        base = [true.gauss(0, 1) for _ in range(8)]
        assert m["rows"][0]["n"] - 1 in sorted(range(8), key=lambda i: -base[i])[:3]
        x = m["mnl"]                                       # the MNL finds the same order
        assert x["converged"] and x["persons"] == 30 and x["rows"][0]["n"] - 1 in sorted(range(8), key=lambda i: -base[i])[:3]
        assert x["threshold"] and x["rows"][-1]["n"] - 1 in sorted(range(8), key=lambda i: base[i])[:3]


def test_duration_loops_and_timing(app, client, survey):
    setup(client, anchor=True, explain=True)
    with app.app_context():
        from app import duration

        item = db.session.get(Item, 2)
        assert duration.item_seconds(item, "de") >= 6 * 20
        assert [c for c, _, _ in item.kind.timing_pages(item)] == ["_x"] + [f"_t{n}" for n in range(1, 7)] + ["_a"]
        assert not item.kind.allowed_in_loop and not item.kind.allowed_in_separated
        assert item.kind.long_export == "maxdiff"


def test_mnl_refuses_a_disconnected_design():
    from app.maxdiff_analysis import components, mnl

    labels = [f"S{i + 1}" for i in range(8)]
    versions = [[[0, 1, 2, 3], [0, 2, 1, 3], [4, 5, 6, 7], [5, 7, 4, 6]]]   # {1..4} and {5..8} never meet
    answers, _ = simulate([1.75, 1.25, 0.75, 0.25, -0.25, -0.75, -1.25, -1.75], versions, 60, seed=2)
    assert sorted(map(sorted, components(8, versions, answers))) == [[0, 1, 2, 3], [4, 5, 6, 7]]
    m = mnl(8, versions, answers, [], labels, 4)
    assert m["disconnected"] and "2 Gruppen" in m["warnings"][0] and "rows" not in m     # no common scale


def test_anchor_only_from_persons_with_sets_and_best_equals_worst_skipped():
    from app.maxdiff_analysis import counts, mnl
    from app.maxdiff_design import generate

    labels = [f"S{i + 1}" for i in range(8)]
    versions = generate(8, 4, 6, 1, seed=3)
    answers, anchors = simulate([1.2, 0.8, 0.4, 0.1, -0.1, -0.3, -0.7, -1.4], versions, 50, seed=4, threshold=0.3)
    base = mnl(8, versions, answers, anchors, labels, 4)
    answers += [(None, {})] * 20                                   # skipped every set ...
    anchors += [[1] * 8] * 20                                      # ... but ticked every statement as important
    m = mnl(8, versions, answers, anchors, labels, 4)
    assert m["anchor_n"] == 50 and m["threshold"]["u"] == pytest.approx(base["threshold"]["u"])  # was 70, shifted
    shown, best, worst, _ = counts(4, [[[0, 1, 2, 3]]], [(0, {0: (0, 0)})])
    assert shown.sum() == 0 and best.sum() == 0                    # best == worst: not counted (as in the MNL)


def test_mnl_standard_errors_are_clustered_by_person():
    import numpy as np

    from app.choice_model import fit
    from app.maxdiff_analysis import choice_sets
    from app.maxdiff_design import generate

    versions = generate(8, 4, 6, 3, seed=3)
    answers, _ = simulate([1.2, 0.8, 0.4, 0.1, -0.1, -0.3, -0.7, -1.4], versions, 200, seed=6, person_sd=1.0)
    sets, _, k, _ = choice_sets(8, versions, answers, [])
    assert {s.person for s in sets} == set(range(200))
    robust = fit(sets, k)[1]
    for s in sets:
        s.person = None
    naive = fit(sets, k)[1]
    assert (np.sqrt(np.diag(robust)) > np.sqrt(np.diag(naive))).mean() > 0.7      # heterogeneity widens them


def test_design_locked_once_real_answers_exist(app, client, survey):
    setup(client, anchor=True)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
        sets = db.session.get(Item, 2).config["design"]["versions"][0]
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2_t0_b": str(sets[0][0]), "i2_t0_w": str(sets[0][1])})
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    with app.app_context():
        before = db.session.get(Item, 2).config
    assert "bereits echte Antworten" in client.get(E + "/items/2").data.decode()
    client.post(E + "/items/2/conjoint/design")                       # refused
    wording = ["Preis (inkl. Rabatte)"] + STATEMENTS[1:]
    client.post(E + "/items/2", data=maxdiff_form(statements=wording))            # wording: allowed
    client.post(E + "/items/2", data=maxdiff_form(statements=STATEMENTS[1:], ids={n: f"s{n + 1}" for n in range(7)},
                                                  versions=3))               # removing a statement: refused
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["design"] == before["design"] and item.config["statements"] == before["statements"]
        assert item.config["versions"] == 1 and item.config["anchor"] and item.kind.design_current(item)
        assert item.kind.labels(item)[0] == "Preis (inkl. Rabatte)"


def test_long_export_and_results_skip_unknown_design_versions(app, client, survey):
    setup(client)
    with app.app_context():
        from app import results

        s = db.session.get(Survey, 1)
        sets = db.session.get(Item, 2).config["design"]["versions"][0]
        for v in (0, 2):  # version 3 is not in the (one-version) design
            answers = {"v": v, **{f"t{t}": {"b": st[0], "w": st[1]} for t, st in enumerate(sets)}}
            s.responses.append(Response(answers={"2": answers}, layout={"cj": {"2": {"version": v}}},
                                        status=Response.COMPLETED))
        db.session.commit()
        entry = next(e for e in results.compute(s)["items"] if e["item"].id == 2)
        assert entry["maxdiff"]["persons"] == 1 and entry["maxdiff"]["skipped"] == 1
    r = client.get("/surveys/1/export/conjoint/2.csv?all=1")
    long = list(csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert r.status_code == 200 and len(long) == 6 * 4 and {row["version"] for row in long} == {"1"}
    assert "1 Person ausgelassen" in client.get("/surveys/1/results").data.decode()


def test_anchor_none_of_these_is_an_answer_left_open_is_not(app, client, survey):
    from app import runtime
    from app.maxdiff_analysis import persons_from_table

    setup(client, anchor=True)
    meta = {"anchor": True}
    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind

        def run(values, ack=False):
            return runtime.evaluate([item], MultiDict([("i2_a", v) for v in values]), "de", nudge_acknowledged=ack, meta=meta)

        assert run(["none"]).answers[2] == {"anchor": []} and not kind.is_missing(item, {"anchor": []})
        assert run([]).answers[2] == {"anchor": None} and kind.is_missing(item, {"anchor": None})
        assert run(["1", "none"]).answers[2] == {"anchor": [1]}            # a ticked statement counts
        item.answer_mode = "nudge"
        assert 2 in run([]).nudges and run([], ack=True).ok and run(["none"]).ok
        item.answer_mode = "required"
        assert "Keine davon" in run([]).errors[2] and run(["none"]).ok and run(["3"]).ok
        assert kind.display_value(item, MultiDict(), meta) == {"anchor": None}
        db.session.rollback()
        tasks = {"v": 0, "t0": {"b": 0, "w": 1}}
        ticked = kind.export_values(item, {**tasks, "anchor": []})
        skipped = kind.export_values(item, {**tasks, "anchor": None})
        assert [ticked[f"md_anchor{i}"] for i in range(1, 9)] == [0] * 8
        assert [skipped[f"md_anchor{i}"] for i in range(1, 9)] == [None] * 8
        table = [{"pid": "a", **ticked}, {"pid": "b", **skipped}]
        assert [a for _, _, _, a in persons_from_table(item, table)] == [[0] * 8, None]  # no anchor: not in the threshold
        # dummy persons without an important statement tick "none of these"
        fields = kind.dummy_fields(item, random.Random(1), {"anchor": True, "respondent": 1})
        assert fields["i2_a"] and all(v == "none" or v.isdigit() for v in fields["i2_a"])
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
        sets = db.session.get(Item, 2).config["design"]["versions"][0]
    for anchor in (["none"], []):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        for t, s in enumerate(sets):
            p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}_b": str(s[0]), f"i2_t{t}_w": str(s[1])})
        html = p.get(f"/s/{uid}").data.decode()
        assert 'name="i2_a" value="none" data-exclusive' in html and "Keine davon" in html and "data-exclusive checked" not in html
        p.post(f"/s/{uid}", data={"nav": "next", "i2_a": anchor})
        assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert [w["md_anchor1"] for w in wide] == ["0", ""] and [w["md_anchor8"] for w in wide] == ["0", ""]
    with app.app_context():
        from app import results

        m = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)["maxdiff"]
        assert m["persons"] == 2 and m["anchor_n"] == 1


def test_above_threshold_is_unclear_when_the_interval_includes_it():
    from app.maxdiff_analysis import mnl
    from app.maxdiff_design import generate

    labels = [f"S{i + 1}" for i in range(8)]
    true = [1.2, 0.8, 0.4, 0.1, -0.1, -0.3, -0.7, -1.4]
    versions = generate(8, 4, 6, 3, seed=3)
    answers, anchors = simulate(true, versions, 300, seed=11, threshold=0.1)     # statement 4 sits on the threshold
    m = mnl(8, versions, answers, anchors, labels, 4)
    rows = {r["n"]: r for r in m["rows"]}
    assert rows[1]["above"] and rows[1]["above_sure"] and not rows[8]["above"] and rows[8]["above_sure"]
    assert rows[4]["above_sure"] is False                                        # was a plain ja/nein
    assert mnl(8, versions, answers, [], labels, 4)["rows"][0]["above_sure"] is None


def test_anchor_question_in_a_random_order_per_person(app, client, survey):
    setup(client, anchor=True)
    with app.app_context():
        runtime = __import__("app.runtime", fromlist=["x"])
        orders = set()
        for seed in range(6):
            layout = runtime.build_layout(db.session.get(Survey, 1), random.Random(seed))
            page = [p for p in layout["pages"] if p.get("items") == [2]][-1]
            assert page["anchor"] and sorted(page["order"]) == list(range(8))
            assert page["order"] == layout["cj"]["2"]["anchor_order"]
            orders.add(tuple(page["order"]))
        assert len(orders) > 1                                       # was always 1..8
        item = db.session.get(Item, 2)
        assert item.kind.export_state_values(item, {"version": 0, "anchor_order": [2, 0, 1]})["md_anchor_order"] == "3|1|2"
        assert item.kind.export_state_values(item, {"version": 0})["md_anchor_order"] is None   # older answers
        assert ("md_anchor_order" in [c for c, _ in item.kind.export_columns(item)])
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        state = r.layout["cj"]["2"]
        sets = db.session.get(Item, 2).config["design"]["versions"][state["version"]]
    for t, s in enumerate(sets):
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}_b": str(s[0]), f"i2_t{t}_w": str(s[1])})
    html = p.get(f"/s/{uid}").data.decode()
    shown = [int(v) for v in __import__("re").findall(r'name="i2_a" value="(\d+)"', html)]
    assert shown == state["anchor_order"]
    first = state["anchor_order"][0]
    p.post(f"/s/{uid}", data={"nav": "next", "i2_a": [str(first)]})
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert wide[f"md_anchor{first + 1}"] == "1" and wide["md_anchor_order"] == "|".join(str(i + 1) for i in shown)
