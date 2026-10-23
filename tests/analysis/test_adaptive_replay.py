"""The replay of the adaptive conjoints against fixed vectors (audit data storage and calculations 2026-10-21, D1).

ACBC answers stored before the shown concepts and tournament groups were written into the answer, and every ACA
answer, are still rebuilt from the seed, the configuration and the code (ACBC.plan / ACBC.tournament, ACA.plan). A
change of that code would silently assign stored judgements to other concepts, so these tests compare the replay
with vectors written once (tests/adaptive_replay_vectors.json): the item configuration, the stored answer and what
the replay gave. A failing test here means a code change moved the replay of answers already collected.

To write the vectors anew (only after a deliberate change, and then the stored answers no longer match):
`UPDATE_REPLAY_VECTORS=1 pytest tests/analysis/test_adaptive_replay.py -k write`.
"""
import json
import os
import random
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Item
from tests.conftest import experiments_on, fill_consent

VECTORS = Path(__file__).parents[1] / "adaptive_replay_vectors.json"


def _acbc_item(client):
    from tests.analysis.test_acbc import setup

    setup(client, rules=True, price=True, screens=4)


def _aca_item(client):
    from tests.analysis.test_aca import aca_form
    from tests.conftest import add

    fill_consent(client)
    experiments_on(client, "aca")
    add(client, "aca")
    client.post("/surveys/1/editor/items/2", data=aca_form(cal=3))


def _acbc_expected(item, answer):
    kind = item.kind
    plan = kind.plan(item, answer)
    rounds, _, winner = kind.tournament(item, answer, plan)
    return {"concepts": plan.concepts,
            "questions": {k: {"phase": q["phase"], "after": q["after"],
                              **({"options": [list(o) if isinstance(o, (list, tuple)) else o for o in q["options"]]}
                                 if "options" in q else {}),
                              **({"level": list(q["level"])} if "level" in q else {})}
                          for k, q in sorted(plan.questions.items())},
            "nogo": sorted(list(x) for x in plan.nogo), "must": {str(a): lv for a, lv in plan.must.items()},
            "cap": plan.cap, "rounds": [[r, list(g), c] for r, g, c in rounds], "winner": winner,
            "possible": kind.possible(item, answer, plan)}


def _aca_expected(item, answer):
    plan = item.kind.plan(item, answer)
    return {"pairs": [[{str(a): lv for a, lv in side.items()} for side in pair] for pair in plan["pairs"]],
            "cal": [{str(a): lv for a, lv in concept.items()} for concept in plan["cal"]],
            "final": [[round(x, 9) for x in row] for row in plan["final"]], "answered": plan["answered"]}


def _simulate_acbc(item, seed):
    """A deterministic participant: screens at random, every rule question answered, the tournament to its end."""
    kind = item.kind
    rng = random.Random(seed)
    attrs = item.config["attributes"]
    profile = [rng.randrange(len(a["levels"])) for a in attrs]
    answer = {"seed": seed, "k": item.config["per_screen"],
              "byo": {a["id"]: a["levels"][p]["id"] for a, p in zip(attrs, profile)},
              "concepts": kind.make_concepts(item, profile, seed), "byo_price": kind.byo_price(item, profile)}
    for s in range(item.config["screens"]):
        plan = kind.plan(item, answer)
        answer.setdefault("s", {})
        for i in kind.screen_concepts(item, s):
            answer["s"][str(i)] = 0 if plan.concepts[i].get("auto") else int(rng.random() < 0.6)
        while True:
            open_ = [(k, q) for k, q in kind.plan(item, answer).questions.items() if k not in answer.get("rules", {})]
            if not open_:
                break
            key, q = open_[0]
            rules = answer.setdefault("rules", {})
            if q["phase"] == "nogo":
                rules[key] = [kind.level_ref(item, *o) for o in q["options"] if rng.random() < 0.6]
            elif q["phase"] == "must":
                rules[key] = {"level": kind.level_ref(item, *q["level"]), "yes": rng.random() < 0.6}
            else:
                rules[key] = {"cap": q["options"][-1] if rng.random() < 0.7 else None}
    for _ in range(30):
        rounds, _, winner = kind.tournament(item, answer)
        if winner is not None or not rounds:
            break
        r, group, _ = rounds[-1]
        answer.setdefault("t", {})[str(r)] = rng.choice(group)
    return answer


def _simulate_aca(item, seed):
    kind = item.kind
    rng = random.Random(seed)
    c = item.config
    answer = {"seed": seed, "r": {a["id"]: [rng.randint(1, c["pref_points"]) for _ in a["levels"]]
                                  for a in kind.rated(item)},
              "i": {a["id"]: rng.randint(1, c["imp_points"]) for a in c["attributes"]}, "p": {}, "c": {}}
    for k in range(kind.n_pairs(item)):
        answer["p"][str(k)] = rng.randint(-4, 4) if rng.random() < 0.9 else None
    for k in range(kind.n_cal(item)):
        answer["c"][str(k)] = rng.randint(0, 100)
    return answer


@pytest.mark.skipif(not os.environ.get("UPDATE_REPLAY_VECTORS"), reason="writes the vectors anew; on request only")
def test_write_vectors(app, client, survey):
    out = {}
    _acbc_item(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        cases = []
        for seed in (3, 11, 27, 101, 202, 4711, 9001, 31337):
            answer = _simulate_acbc(item, seed)
            cases.append({"answer": answer, "expected": _acbc_expected(item, answer)})
        out["acbc"] = {"config": item.config, "cases": cases}
        db.session.delete(item)
        db.session.commit()
    _aca_item(client)
    with app.app_context():
        item = db.session.scalars(db.select(Item).filter_by(type="aca")).first()
        cases = []
        for seed in (5, 17, 123):
            answer = _simulate_aca(item, seed)
            cases.append({"answer": answer, "expected": _aca_expected(item, answer)})
        out["aca"] = {"config": item.config, "cases": cases}
    VECTORS.write_text(json.dumps(json.loads(json.dumps(out)), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _norm(value):
    return json.loads(json.dumps(value))


def test_acbc_replay_matches_the_fixed_vectors(app, client, survey):
    data = json.loads(VECTORS.read_text(encoding="utf-8"))["acbc"]
    _acbc_item(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = data["config"]
        replaced = auto = tour = 0
        for case in data["cases"]:
            assert "shown" not in case["answer"] and "g" not in case["answer"]  # answers of the old form: replay
            assert _norm(_acbc_expected(item, case["answer"])) == case["expected"]
            replaced += sum(1 for x in case["expected"]["concepts"] if x.get("r"))
            auto += sum(1 for x in case["expected"]["concepts"] if x.get("auto"))
            tour += len(case["expected"]["rounds"]) >= 2
        # the vectors cover screening with replacements and automatic rejections and tournaments of several rounds
        assert replaced and auto and tour


def test_acbc_stored_shown_concepts_and_groups_win_over_the_replay(app, client, survey, monkeypatch):
    """With the shown concepts and groups in the answer, a changed replacement or pool order changes nothing."""
    from app import acbc_design
    from app.items import acbc

    data = json.loads(VECTORS.read_text(encoding="utf-8"))["acbc"]
    _acbc_item(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = data["config"]
        kind = item.kind
        case = next(c for c in data["cases"] if any(x.get("r") for x in c["expected"]["concepts"])
                    and len(c["expected"]["rounds"]) >= 2)
        answer = dict(case["answer"])
        k = data["config"]["per_screen"]
        concepts = case["expected"]["concepts"]
        answer["shown"] = {str(s): {str(i): kind.shown_entry(concepts[i])
                                    for i in range(s * k, (s + 1) * k) if concepts[i].get("r") or concepts[i].get("auto")}
                           for s in range(data["config"]["screens"])}
        answer["g"] = {str(r): g for r, g, _ in case["expected"]["rounds"]}
        assert _norm(_acbc_expected(item, answer)) == case["expected"]
        # a code change in the replay: other replacements and another tournament order
        monkeypatch.setattr(acbc.ACBC, "replacement", lambda self, *a, **kw: None)
        monkeypatch.setattr(acbc_design, "tournament_pool", lambda possible, cap, seed=0: list(reversed(possible))[:cap])
        got = _norm(_acbc_expected(item, answer))
        assert got["concepts"] == case["expected"]["concepts"] and got["rounds"] == case["expected"]["rounds"]
        assert got["winner"] == case["expected"]["winner"]
        assert _norm(_acbc_expected(item, case["answer"]))["concepts"] != case["expected"]["concepts"]  # old form moves


def test_aca_replay_matches_the_fixed_vectors(app, client, survey):
    data = json.loads(VECTORS.read_text(encoding="utf-8"))["aca"]
    _aca_item(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = data["config"]
        for case in data["cases"]:
            got = _norm(_aca_expected(item, case["answer"]))
            expected = case["expected"]
            assert got["pairs"] == expected["pairs"] and got["cal"] == expected["cal"]
            assert got["answered"] == expected["answered"]
            assert [x for row in got["final"] for x in row] == pytest.approx(
                [x for row in expected["final"] for x in row], abs=1e-8)


def test_acbc_pages_write_the_shown_concepts_and_groups(app, client, survey, monkeypatch):
    """A participation writes the replaced or rejected concepts per screen and each tournament group into the
    answer; the analysis then reads them, not the replay (D1)."""
    from app import acbc_design
    from app.items import acbc
    from tests.analysis.test_acbc import start, walk

    _acbc_item(client)
    uid = start(app, client)
    nogo = ("a1:a1l1", "a1:a1l2", "a2:a2l0", "a2:a2l2", "a4:a4l0", "a4:a4l1")
    walk(app, uid, screen_answer=lambda c: "1" if c % 2 else "0", nogo=nogo)
    from app.models import Response

    with app.app_context():
        item = db.session.get(Item, 2)
        kind = item.kind
        a = db.session.scalar(db.select(Response).filter_by(is_preview=False)).answers["2"]
        assert sorted(a["shown"], key=int) == [str(s) for s in range(4)]
        assert any(a["shown"][s] for s in a["shown"])  # the confirmed rules replaced or rejected something
        rounds, _, winner = kind.tournament(item, a)
        assert [g for _, g, _ in rounds] == [a["g"][str(r)] for r, _, _ in rounds] and winner is not None
        before = _norm(_acbc_expected(item, a))
        old_form = {k: v for k, v in a.items() if k not in ("shown", "g")}
        assert _norm(_acbc_expected(item, old_form)) == before  # the replay agrees with what was written
        monkeypatch.setattr(acbc.ACBC, "replacement", lambda self, *x, **kw: None)
        monkeypatch.setattr(acbc_design, "tournament_pool", lambda possible, cap, seed=0: list(reversed(possible))[:cap])
        after = _norm(_acbc_expected(item, a))
        assert after["concepts"] == before["concepts"] and after["rounds"] == before["rounds"]
        assert after["winner"] == before["winner"]
    # D3: the confirmed rules do not enter the estimation, the results page says so next to the model
    html = client.get("/surveys/1/results").data.decode()
    assert "Bestätigte Regeln (1 von 1 Personen" in html


def test_acbc_results_without_rules_have_no_rule_note(app, client, survey):
    from tests.analysis.test_acbc import run_participant, setup, start

    setup(client)
    uid = start(app, client)
    run_participant(app, uid, possible={0, 2, 3, 5, 7})
    html = client.get("/surveys/1/results").data.decode()
    assert "Robo" in html or "robo" in html
    assert "Bestätigte Regeln" not in html


def test_acbc_merge_keeps_shown_and_groups_in_step():
    from app.items import get

    kind = get("acbc")
    old = {"k": 3, "concepts": [{"p": [0]}] * 9, "s": {str(i): 1 for i in range(9)},
           "rules": {"n1": ["a1:a1l1"]}, "shown": {"0": {}, "1": {}, "2": {"7": {"auto": 1}}},
           "t": {"0": 1, "1": 4}, "g": {"0": [0, 1, 3], "1": [4, 5, 1]}}
    # a changed rule after screen 1: screen 2 and the tournament go, with what they showed
    new = kind.merge_answer(old, {"rules": {"n1": []}, "_after": 1})
    assert new["shown"] == {"0": {}, "1": {}} and "t" not in new and "g" not in new
    # a changed tournament choice drops the later rounds and their groups
    new = kind.merge_answer(old, {"t": {"0": 3}, "g": {"0": [0, 1, 3]}})
    assert new["t"] == {"0": 3} and new["g"] == {"0": [0, 1, 3]}
    # a screen saved again replaces its entry only
    new = kind.merge_answer(old, {"s": {"6": 1, "7": 0, "8": 1}, "_screen": 2, "shown": {"2": {"7": {"p": [1]}}}})
    assert new["shown"]["2"] == {"7": {"p": [1]}} and new["shown"]["0"] == {}
