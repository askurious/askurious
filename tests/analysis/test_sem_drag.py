"""Moving the variables of the path diagram on the results page (static/sem_drag.js, Ralf 2026-10-12): the places go
into the drawing of the study plan, one step for "Rückgängig", only for "Bearbeiten"; the lines drawn in the browser
are those of sem_model.diagram."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import sem_model, study_plan
from app.extensions import db
from app.models import Survey, SurveyMember, User
from tests.analysis.test_sem_survey import _survey

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
URL = "/surveys/1/results/sem-layout"
OTHER = "0000-0001-0000-0009"


def _places(app):
    with app.app_context():
        return study_plan.plan_of(db.session.get(Survey, 1))["layout"]["nodes"]


def test_moving_saves_the_drawing_of_the_plan_and_is_one_step_back(app, client, survey):
    _survey(app, client, n=150)
    page = client.get("/surveys/1/results?go=1").data.decode()
    part = page[page.index('id="sem"'):]
    assert "data-sem-drag" in part and "sem_drag.js" in page and 'tabindex="0"' in part
    data = json.loads(part.split("data-sem-drag-data>", 1)[1].split("</script>", 1)[0])
    assert data["shift"] is None and {n["code"] for n in data["nodes"]} == {"tb", "einst", "absicht"}
    assert {e["kind"] for e in data["edges"]} == {"path"}
    with app.app_context():
        s = db.session.get(Survey, 1)
        before = json.dumps(s.study_plan, sort_keys=True)
    moved = {"tb": [120, 96], "einst": [440, 40], "absicht": [760, 96]}
    r = client.post(URL, json={"nodes": {**moved, "bad": ["x", 1]}, "name": "Einstellung"})
    assert r.status_code == 200 and r.json["undo"] == "Pfaddiagramm: Einstellung verschoben" and r.json["undo_id"] == r.json["step"]
    assert _places(app) == moved
    with app.app_context():  # nothing else of the plan changed, and no deviation from the pre-registration
        plan = dict(db.session.get(Survey, 1).study_plan)
        assert {k: v for k, v in plan.items() if k != "layout"} == {k: v for k, v in json.loads(before).items() if k != "layout"}
    # the page draws the new arrangement, with its shift back to the places of the plan
    page = client.get("/surveys/1/results?go=1").data.decode()
    data = json.loads(page.split("data-sem-drag-data>", 1)[1].split("</script>", 1)[0])
    centre = {n["code"]: [n["x"] + 100 + data["shift"][0], n["y"] + 32 + data["shift"][1]] for n in data["nodes"]}
    assert centre == moved
    # "Rückgängig" from the page's script: only its own step, as JSON, without loading the page
    step = r.json["step"]
    stale = client.post("/surveys/1/editor/undo", data={"only": step + 1})
    assert stale.json["stale"] and _places(app) == moved
    back = client.post("/surveys/1/editor/undo", data={"only": step})
    assert back.json["done"] and back.json["redo_id"] == step and _places(app) == {}
    again = client.post("/surveys/1/editor/redo", data={"only": step})
    assert again.json["done"] and _places(app) == moved
    # the usual arrow lands on the results page
    r = client.post("/surveys/1/editor/undo")
    assert r.status_code == 302 and r.headers["Location"].endswith("/surveys/1/results#sem")


def test_moving_computes_nothing_anew(app, client, survey, monkeypatch):
    _survey(app, client, n=150)
    client.get("/surveys/1/results?go=1")
    calls = []
    real = sem_model.estimate
    monkeypatch.setattr(sem_model, "estimate", lambda *a, **k: calls.append(1) or real(*a, **k))
    client.post(URL, json={"nodes": {"tb": [120, 96], "einst": [440, 40], "absicht": [760, 400]}})
    page = client.get("/surveys/1/results?go=1").data.decode()
    assert not calls  # the kept model, with the arrangement saved now
    data = json.loads(page.split("data-sem-drag-data>", 1)[1].split("</script>", 1)[0])
    assert data["shift"] is not None


def _member(app, role):
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER, name="V", role=role, position=1))
        db.session.commit()
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=V")
    other.post("/terms/", data={"accept": "1"})
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        db.session.scalar(db.select(SurveyMember).filter_by(orcid=OTHER)).user_id = user.id
        db.session.commit()
    return other


def test_reading_people_see_the_picture_fixed(app, client, survey):
    _survey(app, client, n=150)
    viewer = _member(app, "viewer")
    page = viewer.get("/surveys/1/results?go=1").data.decode()
    assert 'id="sem"' in page and "Pfaddiagramm" in page and "data-sem-drag" not in page
    assert viewer.post(URL, json={"nodes": {"tb": [8, 8]}}).status_code != 200
    assert viewer.get(URL).status_code != 200 and _places(app) == {}


def test_editing_people_move_too(app, client, survey):
    _survey(app, client, n=150)
    editor = _member(app, "editor")
    assert "data-sem-drag" in editor.get("/surveys/1/results?go=1").data.decode()
    r = editor.post(URL, json={"nodes": {"tb": [8, 8]}, "name": "TB"})
    assert r.status_code == 200 and _places(app) == {"tb": [8, 8]}
    assert editor.get(URL).json == {"nodes": {"tb": [8, 8]}}


_HARNESS = r"""
const source = require("fs").readFileSync(process.argv[2], "utf8");
const api = new Function(source.slice(0, source.indexOf("(function () {")) + "; return semGeometry;")();
const cases = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
process.stdout.write(JSON.stringify(cases.map(c => api(c.data, c.centres))));
"""


@pytest.mark.skipif(NODE is None, reason="needs Node")
def test_the_browser_draws_the_lines_of_the_server(tmp_path):
    """semGeometry (sem_drag.js) and sem_model.diagram give the same picture for a drawing: paths that bend around a
    variable, a relation without a direction, a moderation onto a path, subscales of one instrument."""
    nodes = {"a": ("a", "a"), "b": ("b", "b"), "c": ("c", "c"), "w": ("w", "w"), "s1": ("s", "S · eins"),
             "s2": ("s", "S · zwei"), "a:w": ("a:w", "a:w")}
    fit = {"admissible": True, "r2": {}, "paths": [{"from": x, "to": y, "std": 0.3, "p": 0.01} for x, y in
                                                   (("a", "c"), ("b", "c"), ("a:w", "c"), ("s1", "c"), ("s2", "b"))],
           "covariances": [{"a": "a", "b": "b", "std": 0.2, "p": 0.04}]}
    spec = {"paths": [("a", "c"), ("b", "c"), ("a:w", "c"), ("s1", "c"), ("s2", "b")], "latent": {"s1": [], "s2": []},
            "cov": [("a", "b")], "products": {"a:w": {"x": "a", "w": "w"}}}
    cases, expected = [], []
    for layout in ({"a": [100, 100], "b": [400, 100], "c": [700, 100], "w": [400, 300], "s": [120, 400]},
                   {"a": [104, 96], "b": [104, 400], "c": [704, 248], "w": [400, 40], "s": [400, 600]}):
        result = {"nodes": nodes, "labels": {}, "spec": spec, "fit": fit, "layout": layout}
        d = sem_model.diagram(result)
        data = sem_model.drag_data(d)
        centres = {code: [x - data["shift"][0], y - data["shift"][1]] for code, (x, y) in layout.items()}
        cases.append({"data": data, "centres": centres})
        expected.append(d)
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    harness = tmp_path / "geo.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    done = subprocess.run([NODE, str(harness), str(ROOT / "app" / "static" / "sem_drag.js"), str(tmp_path / "cases.json")],
                          capture_output=True, check=True, timeout=60)
    for d, got in zip(expected, json.loads(done.stdout.decode("utf-8"))):
        assert [e["d"] for e in d["edges"]] == [e["d"] for e in got["edges"]]
        assert all(abs(e["lx"] - g["lx"]) < 0.05 and abs(e["ly"] - g["ly"]) < 0.05 for e, g in zip(d["edges"], got["edges"]))
        assert {n["name"]: [n["x"], n["y"]] for n in d["nodes"]} == {k: [round(v[0], 6), round(v[1], 6)] for k, v in got["pos"].items()}
        assert [round(v) for v in got["box"]] == [0, round(d["top"]), round(d["width"]), round(d["height"])]


def test_one_save_at_a_time_and_no_second_undo():
    """Code review 2026-10-15: a move during a save goes out after its answer (I2); once the server answered the undo,
    a later failure only loads the page anew instead of sending the form, which would take back a second step (I4)."""
    from pathlib import Path

    js = (Path(__file__).parents[2] / "app" / "static" / "sem_drag.js").read_text(encoding="utf-8")
    assert "if (saving) { again = code; return saving; }" in js
    assert "if (answered) location.reload(); else form.submit();" in js
    assert ".catch(function () { form.submit(); })" not in js


def test_values_keep_off_the_variables_and_long_names_take_two_lines(tmp_path):
    """Ralf, 2026-10-17: a value on an arrow lay on a box. It moves along its line or just beside it, the same in the
    browser as on the server; long names break onto a second line instead of being cut."""
    pos = {"a": (0.0, 0.0), "b": (400.0, 0.0), "m": (220.0, 20.0)}
    edges = [{"_q": (200.0, 32.0, 300.0, 32.0, 400.0, 32.0), "lx": 300.0, "ly": 26.0},
             {"_q": (200.0, 32.0, 300.0, 32.0, 400.0, 32.0), "lx": 300.0, "ly": 26.0}]
    sem_model._free_labels(pos, edges)
    def hits(e):
        return any(vx - 4 < e["lx"] + sem_model.LABEL_W / 2 and e["lx"] - sem_model.LABEL_W / 2 < vx + sem_model.NODE_W + 4
                   and vy - 4 < e["ly"] + 4 and e["ly"] - 12 < vy + sem_model.NODE_H + 4 for vx, vy in pos.values())
    assert not hits(edges[0]) and not hits(edges[1]) and (edges[0]["lx"], edges[0]["ly"]) != (edges[1]["lx"], edges[1]["ly"])
    script = tmp_path / "free.js"
    script.write_text("""const fs = require("fs"); eval(fs.readFileSync(process.argv[2], "utf-8").split("(function () {")[0]);
const pos = {a: [0, 0], b: [400, 0], m: [220, 20]};
const edges = [{q: [200, 32, 300, 32, 400, 32], lx: 300, ly: 26}, {q: [200, 32, 300, 32, 400, 32], lx: 300, ly: 26}];
semFreeLabels(pos, edges); console.log(JSON.stringify(edges.map(e => [e.lx, e.ly])));""", encoding="utf-8")
    done = subprocess.run([NODE, str(script), str(ROOT / "app" / "static" / "sem_drag.js")], capture_output=True, check=True, timeout=60)
    got = json.loads(done.stdout.decode("utf-8"))
    assert all(abs(g[0] - e["lx"]) < 1e-9 and abs(g[1] - e["ly"]) < 1e-9 for g, e in zip(got, edges))
    assert sem_model._name_lines("Technikbereitschaft") == ["Technikbereitschaft"]
    assert sem_model._name_lines("Wahrgenommene Nützlichkeit im Studium und Beruf") == ["Wahrgenommene", "Nützlichkeit im Studium…"]
