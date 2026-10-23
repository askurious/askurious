"""CBC designs with attributes not shown (partial profiles, level None) and tasks with fewer concepts (alternative None
at the end): what the conjoint import (an add-on module, app/plugins.py) brings in from other programs and adaptive
designs. The analyses read them without error and still recover the part-worths."""
import math
import random

from app.extensions import db
from app.models import Item, Response, Survey, new_pid
from tests.analysis.test_cbc_results import TRUE, setup


def _thin_design(app, seed=3):
    """Every third profile hides "privacy", every second task offers only two of the three concepts."""
    rng = random.Random(seed)
    with app.app_context():
        item = db.session.get(Item, 2)
        c = dict(item.config)
        codes = [a["code"] for a in c["attributes"]]
        hide = codes.index("privacy")
        versions = []
        for version in c["design"]["versions"]:
            tasks = []
            for t, task in enumerate(version):
                profiles = [list(p) for p in task]
                for p in profiles:
                    if rng.random() < 1 / 3:
                        p[hide] = None
                if t % 2:
                    profiles[-1] = None
                tasks.append(profiles)
            versions.append(tasks)
        c["design"] = {**c["design"], "versions": versions}
        item.config = c
        db.session.commit()


def _simulate(app, n, seed=5):
    rng = random.Random(seed)
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        c = item.config
        codes = [a["code"] for a in c["attributes"]]

        def utility(profile):
            return sum(TRUE[code][lv] for code, lv in zip(codes, profile) if code in TRUE and lv is not None)

        for _ in range(n):
            v = rng.randrange(len(c["design"]["versions"]))
            answer = {}
            for t in range(c["tasks"]):
                offered = [p for p in c["design"]["versions"][v][t] if p is not None]
                u = [utility(p) - math.log(-math.log(rng.random())) for p in offered]
                answer[f"t{t}"] = max(range(len(u)), key=u.__getitem__) + 1
            s.responses.append(Response(answers={"2": answer}, layout={"cj": {"2": {"version": v, "order": []}}},
                                        status=Response.COMPLETED, pid=new_pid(s.code)))
        db.session.commit()


def test_partial_profiles_and_fewer_concepts(app, client, survey):
    from app import results

    setup(client)
    _thin_design(app)
    _simulate(app, 300)
    with app.app_context():
        e = next(x for x in results.compute(db.session.get(Survey, 1))["items"] if x["item"].id == 2)
    counts, model = e["cbc"]["counts"], e["cbc"]["model"]
    assert counts["tasks"] == 3000
    shown = sum(lv["shown"] for a in counts["attributes"] if a["code"] == "privacy" for lv in a["levels"])
    looks = sum(lv["shown"] for a in counts["attributes"] if a["code"] == "look" for lv in a["levels"])
    assert shown < looks  # hidden "privacy" is not counted as shown
    assert model["converged"]
    est = {a["code"]: [lv["beta"] for lv in a["levels"]] for a in model["attributes"]}
    for code, true in TRUE.items():
        assert all(abs(x - t) < 0.25 for x, t in zip(est[code], true)), (code, est[code])
    assert client.get("/surveys/1/results").status_code == 200
    with app.app_context():  # the long format: two rows where a concept was not offered, level 0 where hidden
        item = db.session.get(Item, 2)
        response = db.session.scalars(db.select(Response)).first()
        rows = list(item.kind.long_rows(item, response))
    assert len(rows) == 5 * 3 + 5 * 2
    assert any(0 in row[4:7] for row in rows)
