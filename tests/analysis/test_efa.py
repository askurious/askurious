"""The exploratory factor analysis of a scale (app/efa.py; SEM extensions 2026-10-17, point 19): parallel analysis,
principal axes, the oblique quartimin rotation, and its fold on the results page."""
import math

import numpy as np
from werkzeug.datastructures import MultiDict

from app import efa
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add


def _two_factors(rng, n, r=0.4):
    f1 = rng.normal(size=n)
    f2 = r * f1 + math.sqrt(1 - r * r) * rng.normal(size=n)
    return np.column_stack([0.7 * f1 + math.sqrt(0.51) * rng.normal(size=n) for _ in range(4)]
                           + [0.6 * f2 + 0.8 * rng.normal(size=n) for _ in range(4)])


def test_parallel_analysis_rotation_and_structure():
    rng = np.random.default_rng(2)
    x = _two_factors(rng, 800)
    found = efa.analyze(x)
    assert found["k"] == 2 and found["converged"] and found["cross"] == []
    pattern = np.array(found["pattern"])
    assert found["primary"] == [0, 0, 0, 0, 1, 1, 1, 1]
    assert np.all(np.abs(pattern[:4, 0] - 0.7) < 0.08) and np.all(np.abs(pattern[4:, 1] - 0.6) < 0.08)
    assert np.all(np.abs(pattern[:4, 1]) < 0.1) and abs(found["phi"][0][1] - 0.4) < 0.1
    # the rotation lowers the quartimin criterion of the unrotated loadings and keeps the communalities
    lam, h2, _ = efa.principal_axis(np.corrcoef(x, rowvar=False), 2)
    rotated, phi, _ = efa.oblimin(lam)
    assert efa._criterion(rotated)[0] < efa._criterion(lam)[0]
    assert np.allclose(np.sum((rotated @ phi) * rotated, axis=1), h2, atol=1e-6)
    # one factor in random-free single-factor data, nothing below the minimum size
    one = np.column_stack([0.7 * x[:, 0] + rng.normal(size=800) for _ in range(5)])
    assert efa.analyze(one)["k"] == 1 and efa.analyze(x[:30]) is None and efa.analyze(x[:, :3]) is None


def test_the_fold_on_the_results_page(app, client, survey):
    add(client, "likert")
    form = [("code", "big"), ("question", "Ich bin jemand, der …"), ("points", "7"), ("labeling", "full"),
            ("level", "metric"), ("subscales_shown", "1"), ("checks_shown", "1"), ("subscales", "1")]
    for k in range(8):
        form += [("stmt_id", f"s{k + 1}"), ("stmt_code", ""), ("stmt_text", f"Aussage {k + 1}"), ("stmt_subscale", "AB"[k // 4])]
    client.post("/surveys/1/editor/items/2", data=MultiDict(form))
    rng = np.random.default_rng(4)
    x = _two_factors(rng, 300)
    with app.app_context():
        s = db.session.get(Survey, 1)
        for row in x:
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers={
                "2": {f"s{k + 1}": int(max(1, min(7, round(4 + 1.4 * v)))) for k, v in enumerate(row)}}))
        db.session.commit()
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index("res-efa"):]
    assert "2 Faktoren nach der Parallelanalyse" in part and "<th>Subskala</th>" in part and "F2" in part
    assert "Horn, J. L. (1965)" in page and "Bernaards, C. A., &amp; Jennrich" in page


def test_heywood_case_and_no_factor_are_said(app):
    """Audit 2026-10-21, E1 and E2: a communality at the cap (two almost identical statements) is a Heywood case the
    page names at the table; data without common variance give k 0 and no forced one-factor table."""
    rng = np.random.default_rng(1)
    f1 = rng.standard_normal(300)
    twins = np.column_stack([f1 + 0.01 * rng.standard_normal(300), f1 + 0.01 * rng.standard_normal(300),
                             f1 + rng.standard_normal(300), rng.standard_normal(300), rng.standard_normal(300)])
    found = efa.analyze(twins)
    assert found["heywood"] and max(found["communality"]) == efa.CAP
    assert not efa.analyze(_two_factors(np.random.default_rng(2), 800))["heywood"]
    noise = efa.analyze(np.random.default_rng(1).standard_normal((300, 6)))
    assert noise["k"] == 0 and noise["pattern"] == [] and noise["parallel"]["k"] == 0
    assert noise["parallel"]["observed"][0] <= noise["parallel"]["random"][0]
    assert efa.analyze(np.random.default_rng(1).standard_normal((300, 6)), k=1)["k"] == 1  # asked for: given
    with app.test_request_context():
        from flask import render_template_string

        # the macro alone: the page around it needs a whole survey
        source = open(app.root_path + "/templates/surveys/results.html", encoding="utf-8").read()
        start = source.index("{% macro efa_part(x) %}")
        macro = ('{% from "_macros.html" import num %}{% from "_learn.html" import term %}'
                 + source[start:source.index("{% endmacro %}", start) + 14] + "{{ efa_part(x) }}")
        items = [{"code": f"s{j}", "text": f"Aussage {j}", "sub": None} for j in range(6)]
        page = render_template_string(macro, x={**found, "items": items[:5]})
        assert "prüfen" in page and "Lösung nicht zuverlässig" in page and "Heywood" in page
        page = render_template_string(macro, x={**found, "heywood": False, "converged": False, "items": items[:5]})
        assert "nicht konvergiert" in page and "Heywood" not in page
        page = render_template_string(macro, x={**noise, "items": items})
        assert "Die Parallelanalyse stützt keinen gemeinsamen Faktor" in page and "<table" not in page
        assert "kein Faktor nach der Parallelanalyse" in page and "Eigenwert 1" in page and "prüfen" not in page
